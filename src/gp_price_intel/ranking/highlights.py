"""Highlight selection for the Decision Page."""

from __future__ import annotations

from gp_price_intel.domain.models import (
    DecisionHighlight,
    HighlightKind,
    LandedCostCompleteness,
    Offer,
    ScoreBreakdown,
    UserPreferences,
)
from gp_price_intel.explanation.builder import ExplanationBuilder
from gp_price_intel.ranking.confidence import is_highlight_eligible


def pick_highlights(
    scored: list[tuple[Offer, ScoreBreakdown]],
    preferences: UserPreferences,
    explanations: ExplanationBuilder | None = None,
) -> list[DecisionHighlight]:
    """
    Pick Decision Page recommendations from offers that clear the confidence floor.

    Low-confidence offers still appear in the full ranked list (with a warning);
    they are excluded from these recommendation lenses.
    """
    del preferences  # reserved for preference-aware highlight lenses later
    if not scored:
        return []

    eligible = [item for item in scored if is_highlight_eligible(item[1])]
    if not eligible:
        return []

    builder = explanations or ExplanationBuilder()
    highlights: list[DecisionHighlight] = []
    used_offer_ids: set[str] = set()

    def add_highlight(kind: HighlightKind, label: str, offer: Offer, breakdown: ScoreBreakdown) -> None:
        if offer.id in used_offer_ids and kind != HighlightKind.BEST_OVERALL:
            return
        highlights.append(
            DecisionHighlight(
                kind=kind,
                offer_id=offer.id,
                explanation=builder.build(offer, breakdown, label),
            )
        )
        used_offer_ids.add(offer.id)

    lowest_list = min(
        eligible,
        key=lambda item: float(
            item[0].converted_list_price.reference.amount
            if item[0].converted_list_price
            else item[0].list_price.amount
        ),
    )
    add_highlight(HighlightKind.LOWEST_LIST_PRICE, "Lowest list price", lowest_list[0], lowest_list[1])

    complete_landed = [
        item
        for item in eligible
        if item[0].landed_cost and item[0].landed_cost.completeness != LandedCostCompleteness.UNKNOWN
    ]
    if complete_landed:
        lowest_total = min(
            complete_landed,
            key=lambda item: float(item[0].landed_cost.total.amount),  # type: ignore[union-attr]
        )
        add_highlight(
            HighlightKind.LOWEST_TOTAL_COST,
            "Lowest total landed cost",
            lowest_total[0],
            lowest_total[1],
        )

    best_seller = max(eligible, key=lambda item: item[1].criterion_scores.get("seller", 0.0))
    add_highlight(HighlightKind.BEST_SELLER, "Most trusted seller", best_seller[0], best_seller[1])

    best_for_you = max(eligible, key=lambda item: item[1].final_score)
    add_highlight(HighlightKind.BEST_OVERALL, "Best for you", best_for_you[0], best_for_you[1])

    return highlights
