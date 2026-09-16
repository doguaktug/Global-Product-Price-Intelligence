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
from gp_price_intel.ranking.engine import warranty_months


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
    # The floor exists so a cheap unreliable listing cannot steal a lens from a
    # trustworthy one. It is not a licence to leave the Decision Page blank: if
    # every offer is a marketplace import below 0.7, the user still needs a
    # recommendation, and the reliability warning already on the breakdown is
    # how that uncertainty is shown.
    pool = eligible or scored

    builder = explanations or ExplanationBuilder()
    highlights: list[DecisionHighlight] = []

    def add_highlight(kind: HighlightKind, label: str, offer: Offer, breakdown: ScoreBreakdown) -> None:
        # Lenses stay independent. If two lenses name the same offer, the UI collapses
        # those rows into one card rather than dropping a winning lens.
        highlights.append(
            DecisionHighlight(
                kind=kind,
                offer_id=offer.id,
                # The whole scored set, not just the eligible pool: "why the cheaper
                # option lost" has to be able to name a cheap offer that was itself
                # too unreliable to be recommended.
                explanation=builder.build(offer, breakdown, label, scored),
            )
        )

    best_for_you = max(pool, key=lambda item: item[1].final_score)
    add_highlight(HighlightKind.BEST_OVERALL, "Best for you", best_for_you[0], best_for_you[1])

    lowest_list = min(
        pool,
        key=lambda item: float(
            item[0].converted_list_price.reference.amount
            if item[0].converted_list_price
            else item[0].list_price.amount
        ),
    )
    add_highlight(HighlightKind.LOWEST_LIST_PRICE, "Lowest list price", lowest_list[0], lowest_list[1])

    complete_landed = [
        item
        for item in pool
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

    best_seller = max(pool, key=lambda item: item[1].criterion_scores.get("seller", 0.0))
    add_highlight(HighlightKind.BEST_SELLER, "Most trusted seller", best_seller[0], best_seller[1])

    with_warranty = [
        item for item in pool if warranty_months(item[0].warranty) is not None
    ]
    if with_warranty:
        best_warranty = max(
            with_warranty,
            key=lambda item: warranty_months(item[0].warranty) or 0.0,
        )
        add_highlight(
            HighlightKind.BEST_WARRANTY,
            "Best warranty",
            best_warranty[0],
            best_warranty[1],
        )

    return highlights


def collapse_highlights(highlights: list[DecisionHighlight]) -> list[list[DecisionHighlight]]:
    """
    Group highlight lenses that point at the same offer.

    Order is first-seen (best-for-you first). The Decision Page spreads 1–5 of
    these groups equally; a single offer that wins several lenses becomes one card.
    """
    groups: dict[str, list[DecisionHighlight]] = {}
    order: list[str] = []
    for highlight in highlights:
        if highlight.offer_id not in groups:
            order.append(highlight.offer_id)
            groups[highlight.offer_id] = []
        groups[highlight.offer_id].append(highlight)
    return [groups[offer_id] for offer_id in order]
