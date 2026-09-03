"""Plain-language explanations for highlights and alternatives."""

from __future__ import annotations

from gp_price_intel.domain.models import (
    Explanation,
    ExplanationReason,
    LandedCostCompleteness,
    Offer,
    ScoreBreakdown,
)
from gp_price_intel.ranking.confidence import HIGHLIGHT_MIN_CONFIDENCE, effective_confidence


class ExplanationBuilder:
    def build(self, offer: Offer, score: ScoreBreakdown, label: str) -> Explanation:
        reasons: list[ExplanationReason] = []
        caveats: list[str] = []

        if offer.converted_list_price:
            reasons.append(
                ExplanationReason(
                    factor="price",
                    detail=(
                        f"List price {offer.list_price.amount} {offer.list_price.currency} "
                        f"→ {offer.converted_list_price.reference.amount} "
                        f"{offer.converted_list_price.reference.currency} "
                        f"(rate {offer.converted_list_price.fx.rate} as of "
                        f"{offer.converted_list_price.fx.as_of.date()})."
                    ),
                )
            )

        if offer.landed_cost:
            reasons.append(
                ExplanationReason(
                    factor="landed_cost",
                    detail=(
                        f"Estimated total landed cost {offer.landed_cost.total.amount} "
                        f"{offer.landed_cost.total.currency} to {offer.landed_cost.destination_country}."
                    ),
                )
            )
            if offer.landed_cost.completeness != LandedCostCompleteness.COMPLETE:
                caveats.append("Shipping or import fees are estimated, not quoted by the seller.")

        if score.criterion_scores.get("seller", 0) >= 0.7:
            reasons.append(
                ExplanationReason(
                    factor="seller",
                    detail=f"Seller {offer.seller.name} (reliability signal {offer.seller.reliability or 'n/a'}).",
                )
            )

        if score.missing_criteria:
            caveats.append(f"Missing data: {', '.join(score.missing_criteria)}.")

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
