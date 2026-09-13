"""Plain-language explanations for highlights and alternatives."""

from __future__ import annotations

from gp_price_intel.domain.models import (
    Explanation,
    ExplanationReason,
    LandedCostCompleteness,
    Money,
    Offer,
    ScoreBreakdown,
)
from gp_price_intel.ranking.confidence import HIGHLIGHT_MIN_CONFIDENCE, effective_confidence

Scored = tuple[Offer, ScoreBreakdown]

#: A criterion is worth stating when the offer scored at least this well on it, even
#: if it was not what decided the comparison.
STRONG_CRITERION_SCORE = 0.7

#: How many criteria a headline argument should rest on. More than this reads as a
#: score dump rather than a purchasing argument.
MAX_DECISIVE_REASONS = 3

_CRITERION_PHRASE = {
    "price": "total cost",
    "seller": "seller trust",
    "reviews": "review history",
    "delivery": "delivery speed",
    "warranty": "warranty",
}


def _reference_amount(offer: Offer) -> Money | None:
    if offer.landed_cost:
        return offer.landed_cost.total
    if offer.converted_list_price:
        return offer.converted_list_price.reference
    return None


def _weighted(breakdown: ScoreBreakdown, criterion: str) -> float:
    return breakdown.weights_used.get(criterion, 0.0) * breakdown.criterion_scores.get(
        criterion, 0.0
    )


class ExplanationBuilder:
    """
    Turn a score into a short purchasing argument.

    The reasons are not a dump of whatever scored highly: they are the criteria that
    actually moved this offer ahead of the offer it beat. A reason the reader cannot
    act on ("price 0.83") is worse than no reason, and a reason that was not decisive
    is misleading about why the recommendation is what it is.
    """

    def build(
        self,
        offer: Offer,
        score: ScoreBreakdown,
        label: str,
        peers: list[Scored] | None = None,
    ) -> Explanation:
        reasons: list[ExplanationReason] = []
        caveats: list[str] = []

        if offer.converted_list_price:
            # "conversion", not "price": this states the sticker and the rate that
            # priced it. The price *criterion* gets its own reason below if it was
            # part of why this offer won.
            reasons.append(
                ExplanationReason(factor="conversion", detail=self._price_detail(offer))
            )

        if offer.landed_cost:
            reasons.append(
                ExplanationReason(
                    factor="landed_cost",
                    detail=(
                        f"Estimated total landed cost {offer.landed_cost.total.amount} "
                        f"{offer.landed_cost.total.currency} to "
                        f"{offer.landed_cost.destination_country}."
                    ),
                )
            )
            registration = offer.landed_cost.registration_fees
            if registration is not None:
                reasons.append(
                    ExplanationReason(
                        factor="registration",
                        detail=(
                            f"{registration.label}: {registration.amount.amount} "
                            f"{registration.amount.currency} on top of the sticker price."
                        ),
                    )
                )
            for line in offer.landed_cost.other_fees:
                reasons.append(ExplanationReason(factor="fees", detail=f"{line.label}."))
            if offer.landed_cost.completeness != LandedCostCompleteness.COMPLETE:
                caveats.append(self._completeness_caveat(offer))

        reasons.extend(self._criterion_reasons(offer, score, peers or []))

        comparison = self._why_the_cheaper_option_lost(offer, score, peers or [])
        if comparison is not None:
            reasons.append(comparison)

        if score.missing_criteria:
            caveats.append(
                "Not counted for this offer (the source did not state it): "
                f"{', '.join(score.missing_criteria)}."
            )

        if score.reliability_warning:
            caveats.append(score.reliability_warning)
        elif effective_confidence(score) < HIGHLIGHT_MIN_CONFIDENCE:
            caveats.append(
                "Not reliable enough for a top recommendation — "
                "lesser-known source or limited review history."
            )
        elif offer.data_confidence < 0.8:
            caveats.append("Listing data confidence is limited.")

        if offer.match_notes:
            caveats.extend(offer.match_notes)

        headline = f"{label}: {offer.listing_title}"
        if not reasons:
            reasons.append(
                ExplanationReason(
                    factor="overall",
                    detail=f"Weighted score {score.final_score:.2f} under your preferences.",
                )
            )

        return Explanation(headline=headline, reasons=reasons, caveats=caveats)

    def _criterion_reasons(
        self,
        offer: Offer,
        score: ScoreBreakdown,
        peers: list[Scored],
    ) -> list[ExplanationReason]:
        """
        The criteria that decided this offer's position, strongest margin first.

        Decisiveness is measured against the offer immediately behind it — the one it
        actually had to beat — using weighted contributions, because a criterion the
        user weighted at 5% cannot be the reason for anything. Criteria the offer
        simply scored well on are included afterwards so a strong all-rounder still
        reads as one.
        """
        runner_up = self._runner_up(offer, peers)
        decisive: list[tuple[float, str]] = []
        strong: list[str] = []

        for criterion in score.criterion_scores:
            margin = 0.0
            if runner_up is not None:
                margin = _weighted(score, criterion) - _weighted(runner_up[1], criterion)
            if margin > 0:
                decisive.append((margin, criterion))
            elif score.criterion_scores[criterion] >= STRONG_CRITERION_SCORE:
                strong.append(criterion)

        decisive.sort(reverse=True)
        ordered = [criterion for _, criterion in decisive] + strong

        reasons: list[ExplanationReason] = []
        for criterion in ordered[:MAX_DECISIVE_REASONS]:
            detail = self._criterion_detail(offer, score, criterion, runner_up)
            if detail is not None:
                reasons.append(ExplanationReason(factor=criterion, detail=detail))
        return reasons

    @staticmethod
    def _runner_up(offer: Offer, peers: list[Scored]) -> Scored | None:
        """
        The offer this one had to beat.

        For the winner that is second place; for anyone else it is the offer directly
        above them, which is the comparison that explains their position.
        """
        if not peers:
            return None
        ranked = sorted(peers, key=lambda item: item[1].final_score, reverse=True)
        ids = [item[0].id for item in ranked]
        if offer.id not in ids:
            return ranked[0]
        index = ids.index(offer.id)
        if index == 0:
            return ranked[1] if len(ranked) > 1 else None
        return ranked[index - 1]

    def _criterion_detail(
        self,
        offer: Offer,
        score: ScoreBreakdown,
        criterion: str,
        runner_up: Scored | None,
    ) -> str | None:
        """State the criterion in the offer's own units, not as a 0–1 score."""
        phrase = _CRITERION_PHRASE.get(criterion, criterion)
        versus = ""
        if runner_up is not None and criterion in runner_up[1].criterion_scores:
            rival = runner_up[0].seller.name
            if score.criterion_scores[criterion] > runner_up[1].criterion_scores[criterion]:
                versus = f" — better than {rival}'s listing"
            else:
                versus = f" — comparable to {rival}'s listing"

        if criterion == "price":
            amount = _reference_amount(offer)
            if amount is None:
                return None
            return f"Total cost {amount.amount} {amount.currency}{versus}."
        if criterion == "seller":
            rating = offer.seller.reliability
            rating_text = f"reliability {rating:.2f}" if rating is not None else "unrated seller"
            return f"{offer.seller.name} ({rating_text}){versus}."
        if criterion == "reviews":
            count = offer.seller.review_count
            if count is None:
                return None
            return f"{count:,} seller reviews to judge from{versus}."
        if criterion == "delivery":
            if not offer.delivery_time:
                return None
            return f"Delivery quoted as {offer.delivery_time}{versus}."
        if criterion == "warranty":
            if not offer.warranty:
                return None
            return f"Warranty: {offer.warranty}{versus}."
        return f"Strong on {phrase}{versus}."

    def _why_the_cheaper_option_lost(
        self,
        offer: Offer,
        score: ScoreBreakdown,
        peers: list[Scored],
    ) -> ExplanationReason | None:
        """
        Name the cheaper offer this one beat, and what it gave up.

        This is the question a buyer actually asks of a recommendation that is not
        the cheapest row on the page. Leaving it unanswered makes the ranking look
        arbitrary exactly when it needs to look reasoned.
        """
        if not peers:
            return None
        best = max(peers, key=lambda item: item[1].final_score)
        if best[0].id != offer.id:
            return None

        own_cost = _reference_amount(offer)
        if own_cost is None:
            return None

        cheaper: list[tuple[Money, Scored]] = []
        for peer in peers:
            if peer[0].id == offer.id:
                continue
            cost = _reference_amount(peer[0])
            if cost is not None and cost.amount < own_cost.amount:
                cheaper.append((cost, peer))
        if not cheaper:
            return None

        cost, (rival, rival_score) = min(cheaper, key=lambda item: item[0].amount)
        gap = own_cost.amount - cost.amount

        losses = [
            _CRITERION_PHRASE.get(criterion, criterion)
            for criterion in score.criterion_scores
            if criterion in rival_score.criterion_scores
            and rival_score.criterion_scores[criterion] < score.criterion_scores[criterion]
            and criterion != "price"
        ]
        for criterion in rival_score.missing_criteria:
            phrase = _CRITERION_PHRASE.get(criterion, criterion)
            if phrase not in losses:
                losses.append(f"no stated {phrase}")

        if not losses:
            return None
        return ExplanationReason(
            factor="comparison",
            detail=(
                f"{rival.seller.name}'s listing is {gap} {cost.currency} cheaper but loses on "
                f"{', '.join(losses[:3])}."
            ),
        )

    @staticmethod
    def _completeness_caveat(offer: Offer) -> str:
        assert offer.landed_cost is not None
        if offer.landed_cost.completeness == LandedCostCompleteness.UNKNOWN:
            return (
                "Part of this total could not be looked up and a generic figure stood in — "
                "treat the total as indicative only."
            )
        return "Shipping and import fees are estimated, not quoted by the seller."

    @staticmethod
    def _price_detail(offer: Offer) -> str:
        """
        State the price, and the rate only when one was actually applied.

        A domestic offer already priced in the reference currency was never
        converted, so quoting "rate 1 as of <today>" would invent an FX lookup.
        """
        converted = offer.converted_list_price
        assert converted is not None
        listed = f"List price {offer.list_price.amount} {offer.list_price.currency}"
        if converted.fx.is_identity:
            return f"{listed} — already in your reference currency, no conversion applied."

        rate_date = converted.fx.as_of.date() if converted.fx.as_of else "an undated rate"
        return (
            f"{listed} → {converted.reference.amount} {converted.reference.currency} "
            f"(rate {converted.fx.rate}, published {rate_date} by {converted.fx.provider})."
        )
