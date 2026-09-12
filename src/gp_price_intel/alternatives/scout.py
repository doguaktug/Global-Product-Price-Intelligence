"""Close-alternative scout: rank near-offers and badge the ones that earn it."""

from __future__ import annotations

from decimal import Decimal

from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.domain.models import (
    Alternative,
    AlternativeBadge,
    AlternativeKind,
    Explanation,
    ExplanationReason,
    MatchKind,
    Money,
    Offer,
    ProductVariant,
    ScoreBreakdown,
)

Scored = tuple[Offer, ScoreBreakdown]

# Value tests from docs/proposed-algorithm.md. These decide whether an alternative
# earns a badge — not whether it is shown — so a near-offer that misses every
# threshold still appears, ranked, without a claim attached to it.
UPGRADE_MIN_SPEC_GAIN = 0.25
UPGRADE_MAX_COST_INCREASE = 0.10
DOWNGRADE_MIN_COST_SAVING = 0.15
DOWNGRADE_MAX_SPEC_LOSS = 0.50
COMPARABLE_OVERLAP_RATIO = 0.60
COMPARABLE_SCORE_FLOOR = 0.85
MAX_ALTERNATIVES = 3


def _format(value: object) -> str:
    return "—" if value is None else str(value)


def _as_number(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    return None


class AlternativeScout:
    """
    Choose the alternatives shown beside the top pick.

    Alternatives are scored in the *same* normalization pass as the ranked list, so
    their scores are directly comparable to it — that is what makes the rival test
    ("within 85% of the best score") mean anything. Selection then prefers a spread
    of reasons over three variations on one theme.
    """

    def __init__(self, catalog: CatalogRepository | None = None) -> None:
        self.catalog = catalog or CatalogRepository()

    def select(
        self,
        near_offers: list[Scored],
        best: Scored | None,
        confirmed_variant: ProductVariant | None = None,
        max_alternatives: int = MAX_ALTERNATIVES,
    ) -> list[Alternative]:
        if best is None or not near_offers:
            return []

        best_offer, best_score = best
        base_cost = self._landed_amount(best_offer)
        currency = self._reference_currency(best_offer)

        candidates: list[Alternative] = []
        for offer, score in sorted(
            near_offers, key=lambda item: item[1].final_score, reverse=True
        ):
            if offer.id == best_offer.id:
                continue
            candidates.append(
                self._describe(
                    offer,
                    score,
                    best_offer=best_offer,
                    best_score=best_score,
                    base_cost=base_cost,
                    currency=currency,
                    confirmed_variant=confirmed_variant,
                )
            )

        return self._pick_a_spread(candidates, max_alternatives)

    @staticmethod
    def _pick_a_spread(candidates: list[Alternative], limit: int) -> list[Alternative]:
        """
        Prefer one upgrade, one downgrade and one rival over three of a kind.

        Three cheaper-but-smaller variants tell the user one thing three times.
        Badged alternatives come first (best-scoring within each badge, since the
        input is already ranked), then the strongest unbadged ones fill the slots.
        """
        chosen: list[Alternative] = []
        seen_badges: set[AlternativeBadge] = set()

        for candidate in candidates:
            if candidate.badge is not None and candidate.badge not in seen_badges:
                chosen.append(candidate)
                seen_badges.add(candidate.badge)
            if len(chosen) == limit:
                return chosen

        for candidate in candidates:
            if candidate in chosen:
                continue
            chosen.append(candidate)
            if len(chosen) == limit:
                break
        return chosen

    def _describe(
        self,
        offer: Offer,
        score: ScoreBreakdown,
        *,
        best_offer: Offer,
        best_score: ScoreBreakdown,
        base_cost: Decimal,
        currency: str,
        confirmed_variant: ProductVariant | None,
    ) -> Alternative:
        delta = self._landed_amount(offer) - base_cost
        delta_money = Money(amount=delta, currency=currency)

        if offer.match_kind == MatchKind.DIFFERENT:
            badge = self._rival_badge(offer, score, best_offer, best_score)
            return Alternative(
                offer_id=offer.id,
                kind=AlternativeKind.COMPARABLE_PRODUCT,
                badge=badge,
                landed_cost_delta=delta_money,
                explanation=self._rival_explanation(
                    offer, score, best_score, delta, currency, badge
                ),
            )

        differences = self._spec_differences(confirmed_variant, offer)
        badge = self._variant_badge(
            offer,
            confirmed_variant=confirmed_variant,
            delta=delta,
            base_cost=base_cost,
        )
        return Alternative(
            offer_id=offer.id,
            kind=AlternativeKind.SPEC_VARIANT,
            badge=badge,
            differing_attributes=[key for key, _ in differences],
            landed_cost_delta=delta_money,
            explanation=self._variant_explanation(offer, differences, delta, currency, badge),
        )

    # --- value tests -----------------------------------------------------------

    def _variant_badge(
        self,
        offer: Offer,
        *,
        confirmed_variant: ProductVariant | None,
        delta: Decimal,
        base_cost: Decimal,
    ) -> AlternativeBadge | None:
        variant = (
            self.catalog.get_variant(offer.matched_variant_id)
            if offer.matched_variant_id
            else None
        )
        if variant is None or confirmed_variant is None or base_cost <= 0:
            return None

        gain, loss = self._spec_change_ratios(confirmed_variant, variant)
        cost_ratio = delta / base_cost

        if (
            gain is not None
            and gain >= Decimal(str(UPGRADE_MIN_SPEC_GAIN))
            and cost_ratio <= Decimal(str(UPGRADE_MAX_COST_INCREASE))
        ):
            return AlternativeBadge.UPGRADE

        saving_ratio = -cost_ratio
        if saving_ratio >= Decimal(str(DOWNGRADE_MIN_COST_SAVING)) and (
            loss is None or loss <= Decimal(str(DOWNGRADE_MAX_SPEC_LOSS))
        ):
            return AlternativeBadge.DOWNGRADE
        return None

    def _spec_change_ratios(
        self,
        confirmed: ProductVariant,
        candidate: ProductVariant,
    ) -> tuple[Decimal | None, Decimal | None]:
        """
        Largest relative spec gain and largest relative loss between two builds.

        Measured per numeric identity key rather than on one hand-picked field, so a
        RAM jump counts as an upgrade on a machine where storage is unchanged. The
        biggest move in each direction is what a buyer would notice, so "≥25% gain"
        means some spec improved that much, and "≤50% loss" means nothing fell
        further than that.
        """
        gains: list[Decimal] = []
        losses: list[Decimal] = []

        for key in self._comparable_keys(candidate):
            base = _as_number(confirmed.attribute(key))
            other = _as_number(candidate.attribute(key))
            if base is None or other is None or base <= 0 or base == other:
                continue
            change = (other - base) / base
            if change > 0:
                gains.append(change)
            else:
                losses.append(-change)

        return (max(gains) if gains else None, max(losses) if losses else None)

    def _rival_badge(
        self,
        offer: Offer,
        score: ScoreBreakdown,
        best_offer: Offer,
        best_score: ScoreBreakdown,
    ) -> AlternativeBadge | None:
        """
        A different product only earns the badge if it is genuinely comparable.

        Both halves matter: enough shared attributes that it answers the same need,
        and a score close enough to the top pick to be worth the switch. Scores are
        comparable here only because alternatives were normalized alongside the
        ranked list.
        """
        if best_score.final_score <= 0:
            return None
        if score.final_score <= best_score.final_score * COMPARABLE_SCORE_FLOOR:
            return None
        overlap = self._attribute_overlap(offer, best_offer)
        if overlap is None or overlap < COMPARABLE_OVERLAP_RATIO:
            return None
        return AlternativeBadge.RIVAL

    def _attribute_overlap(self, offer: Offer, best_offer: Offer) -> float | None:
        """Share of comparable specs where the two products agree. None if unknowable."""
        variant = (
            self.catalog.get_variant(offer.matched_variant_id)
            if offer.matched_variant_id
            else None
        )
        best_variant = (
            self.catalog.get_variant(best_offer.matched_variant_id)
            if best_offer.matched_variant_id
            else None
        )
        if variant is None or best_variant is None:
            return None

        keys = self._comparable_keys(variant)
        compared = 0
        agreed = 0
        for key in keys:
            mine = variant.attribute(key)
            theirs = best_variant.attribute(key)
            if mine is None or theirs is None:
                continue
            compared += 1
            if mine == theirs:
                agreed += 1
        if compared == 0:
            return None
        return agreed / compared

    # --- explanations ----------------------------------------------------------

    def _variant_explanation(
        self,
        offer: Offer,
        differences: list[tuple[str, str]],
        delta: Decimal,
        currency: str,
        badge: AlternativeBadge | None,
    ) -> Explanation:
        reasons = [
            ExplanationReason(factor=key, detail=detail) for key, detail in differences
        ]
        reasons.insert(
            0, ExplanationReason(factor="cost", detail=self._cost_phrase(delta, currency))
        )
        if badge is AlternativeBadge.UPGRADE:
            headline = f"Worth the upgrade: {offer.listing_title}"
            reasons.append(
                ExplanationReason(
                    factor="value",
                    detail=(
                        f"A meaningful spec jump for under "
                        f"{UPGRADE_MAX_COST_INCREASE:.0%} more than your pick."
                    ),
                )
            )
        elif badge is AlternativeBadge.DOWNGRADE:
            headline = f"Cheaper and probably still enough: {offer.listing_title}"
            reasons.append(
                ExplanationReason(
                    factor="value",
                    detail=(
                        f"Saves at least {DOWNGRADE_MIN_COST_SAVING:.0%} without giving up "
                        f"more than {DOWNGRADE_MAX_SPEC_LOSS:.0%} of any spec."
                    ),
                )
            )
        else:
            headline = f"Same family, different build: {offer.listing_title}"

        return Explanation(
            headline=headline,
            reasons=reasons,
            caveats=[] if badge else ["Shown for comparison — it does not clear a value test."],
        )

    def _rival_explanation(
        self,
        offer: Offer,
        score: ScoreBreakdown,
        best_score: ScoreBreakdown,
        delta: Decimal,
        currency: str,
        badge: AlternativeBadge | None,
    ) -> Explanation:
        reasons = [
            ExplanationReason(factor="cost", detail=self._cost_phrase(delta, currency)),
            ExplanationReason(
                factor="score",
                detail=(
                    f"Scores {score.final_score:.2f} against your pick's "
                    f"{best_score.final_score:.2f} under your weights."
                ),
            ),
        ]
        if badge is AlternativeBadge.RIVAL:
            headline = f"Different product, real contender: {offer.listing_title}"
            reasons.append(
                ExplanationReason(
                    factor="value",
                    detail=(
                        f"Shares at least {COMPARABLE_OVERLAP_RATIO:.0%} of the core specs and "
                        f"stays within {1 - COMPARABLE_SCORE_FLOOR:.0%} of the top score."
                    ),
                )
            )
            caveats = ["Different product family — check the specs that differ."]
        else:
            headline = f"Comparable product: {offer.listing_title}"
            caveats = [
                (
                    "Different product family, and it does not clear the comparability "
                    "test — review specs carefully."
                )
            ]
        return Explanation(headline=headline, reasons=reasons, caveats=caveats)

    @staticmethod
    def _cost_phrase(delta: Decimal, currency: str) -> str:
        if delta == 0:
            return f"Same landed cost as your top pick ({currency})."
        direction = "more" if delta > 0 else "less"
        return f"{abs(delta)} {currency} {direction} than your top pick, landed."

    # --- spec diffing ----------------------------------------------------------

    def _spec_differences(
        self,
        confirmed_variant: ProductVariant | None,
        offer: Offer,
    ) -> list[tuple[str, str]]:
        """
        Which specs this build changes, as (spec key, "confirmed → alternative").

        Falls back to the matcher's notes when the offer never resolved to a
        catalog variant, so an alternative is never shown without a reason.
        """
        variant = (
            self.catalog.get_variant(offer.matched_variant_id)
            if offer.matched_variant_id
            else None
        )
        if confirmed_variant is None or variant is None:
            return [("match", note) for note in offer.match_notes]

        differences: list[tuple[str, str]] = []
        for key in self._comparable_keys(variant):
            confirmed_value = confirmed_variant.attribute(key)
            variant_value = variant.attribute(key)
            if confirmed_value != variant_value:
                differences.append(
                    (key, f"{_format(confirmed_value)} → {_format(variant_value)}")
                )
        return differences or [("match", note) for note in offer.match_notes]

    def _comparable_keys(self, variant: ProductVariant) -> list[str]:
        family = self.catalog.get_family(variant.family_id)
        category = self.catalog.get_category(family.category_id) if family else None
        if category is None:
            return []
        keys: list[str] = []
        for key in category.identity_keys + category.optional_keys + category.core_spec_keys:
            if key not in keys:
                keys.append(key)
        return keys

    @staticmethod
    def _reference_currency(offer: Offer) -> str:
        if offer.landed_cost:
            return offer.landed_cost.total.currency
        if offer.converted_list_price:
            return offer.converted_list_price.reference.currency
        return offer.list_price.currency

    @staticmethod
    def _landed_amount(offer: Offer) -> Decimal:
        if offer.landed_cost:
            return offer.landed_cost.total.amount
        if offer.converted_list_price:
            return offer.converted_list_price.reference.amount
        return offer.list_price.amount
