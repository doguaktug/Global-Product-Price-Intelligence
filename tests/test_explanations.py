"""Explanations must state the reason an offer actually won, not a score dump."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from gp_price_intel.domain.models import (
    ConvertedMoney,
    CostLine,
    CostOrigin,
    FxQuote,
    LandedCost,
    LandedCostCompleteness,
    Money,
    Offer,
    Seller,
    UserPreferences,
)
from gp_price_intel.explanation.builder import ExplanationBuilder
from gp_price_intel.ranking.engine import RankingEngine


def _offer(
    *,
    offer_id: str,
    price: str,
    seller_name: str | None = None,
    seller_reliability: float | None = 0.9,
    review_count: int | None = 1000,
    delivery_time: str | None = "2 days",
    warranty: str | None = "24 months",
    completeness: LandedCostCompleteness = LandedCostCompleteness.COMPLETE,
    data_confidence: float = 1.0,
) -> Offer:
    money = Money(amount=Decimal(price), currency="TRY")
    return Offer(
        id=offer_id,
        source_id="test",
        seller=Seller(
            name=seller_name or offer_id,
            reliability=seller_reliability,
            review_count=review_count,
        ),
        country="TR",
        listing_title=f"Listing {offer_id}",
        listing_url=f"https://example.com/{offer_id}",
        list_price=money,
        converted_list_price=ConvertedMoney(
            original=money,
            reference=money,
            fx=FxQuote(
                base_currency="TRY",
                quote_currency="TRY",
                rate=Decimal("1"),
                as_of=None,
                provider="identity",
            ),
        ),
        landed_cost=LandedCost(
            list_in_reference=money,
            total=money,
            completeness=completeness,
            destination_country="TR",
        ),
        delivery_time=delivery_time,
        warranty=warranty,
        collected_at=datetime.now(timezone.utc),
        data_confidence=data_confidence,
    )


def _explain(offers: list[Offer]) -> dict[str, object]:
    scored = RankingEngine().score(offers, UserPreferences())
    builder = ExplanationBuilder()
    return {
        offer.id: builder.build(offer, breakdown, "Best for you", scored)
        for offer, breakdown in scored
    }


def test_winner_that_is_not_cheapest_says_why_the_cheaper_one_lost() -> None:
    """The question a buyer actually asks of a recommendation that is not cheapest."""
    winner = _offer(
        offer_id="trusted",
        price="1000",
        seller_name="Trusted Shop",
        seller_reliability=1.0,
        review_count=50_000,
        warranty="24 months",
    )
    cheaper = _offer(
        offer_id="bargain",
        price="800",
        seller_name="Bargain Bin",
        seller_reliability=0.1,
        review_count=3,
        warranty=None,
    )

    explanation = _explain([winner, cheaper])["trusted"]
    comparison = [r for r in explanation.reasons if r.factor == "comparison"]  # type: ignore[attr-defined]

    assert len(comparison) == 1
    detail = comparison[0].detail
    assert "Bargain Bin" in detail
    assert "200" in detail
    assert "cheaper" in detail
    # It must name what the cheaper listing gave up, not just that it lost.
    assert "seller trust" in detail or "warranty" in detail


def test_no_comparison_reason_when_the_winner_is_also_cheapest() -> None:
    """Nothing was passed over, so there is nothing to justify."""
    winner = _offer(offer_id="cheap-and-good", price="500", seller_reliability=1.0)
    other = _offer(offer_id="dearer", price="900", seller_reliability=0.4)

    explanation = _explain([winner, other])["cheap-and-good"]

    assert [r for r in explanation.reasons if r.factor == "comparison"] == []  # type: ignore[attr-defined]


def test_reasons_name_the_criterion_that_decided_it() -> None:
    """
    Two offers differing only on delivery: delivery must be the stated reason.

    Everything else is equal, so a reason list that omitted delivery would not be
    explaining the result at all.
    """
    fast = _offer(offer_id="fast", price="1000", delivery_time="Next day")
    slow = _offer(offer_id="slow", price="1000", delivery_time="3-4 weeks")

    explanation = _explain([fast, slow])["fast"]
    factors = [r.factor for r in explanation.reasons]  # type: ignore[attr-defined]

    assert "delivery" in factors
    delivery = next(r for r in explanation.reasons if r.factor == "delivery")  # type: ignore[attr-defined]
    assert "Next day" in delivery.detail


def test_reasons_are_stated_in_real_units_not_normalized_scores() -> None:
    offers = [
        _offer(offer_id="a", price="1000", warranty="24 months", review_count=12_000),
        _offer(offer_id="b", price="1400", warranty="6 months", review_count=20),
    ]
    explanation = _explain(offers)["a"]
    details = " ".join(r.detail for r in explanation.reasons)  # type: ignore[attr-defined]

    assert "24 months" in details
    assert "12,000" in details
    # No bare 0-1 score should be presented as a reason.
    assert "0.7" not in details


def test_missing_criteria_are_disclosed_as_caveats() -> None:
    offers = [
        _offer(offer_id="complete", price="1000"),
        _offer(offer_id="bare", price="1000", warranty=None, delivery_time=None),
    ]
    explanation = _explain(offers)["bare"]
    caveats = " ".join(explanation.caveats)  # type: ignore[attr-defined]

    assert "warranty" in caveats
    assert "delivery" in caveats
    assert "did not state" in caveats


def test_identity_conversion_does_not_advertise_a_rate_lookup() -> None:
    explanation = _explain([_offer(offer_id="domestic", price="1000")])["domestic"]
    conversion = next(r for r in explanation.reasons if r.factor == "conversion")  # type: ignore[attr-defined]

    assert "no conversion applied" in conversion.detail
    assert "rate 1" not in conversion.detail


def test_unknown_landed_cost_is_flagged_more_strongly_than_partial() -> None:
    partial = _offer(offer_id="p", price="1000", completeness=LandedCostCompleteness.PARTIAL)
    unknown = _offer(offer_id="u", price="1000", completeness=LandedCostCompleteness.UNKNOWN)
    explanations = _explain([partial, unknown])

    assert any("estimated, not quoted" in c for c in explanations["p"].caveats)  # type: ignore[attr-defined]
    assert any("could not be looked up" in c for c in explanations["u"].caveats)  # type: ignore[attr-defined]


def test_other_fees_reach_the_explanation() -> None:
    """Origin-VAT removal changes the total, so the user has to see it."""
    money = Money(amount=Decimal("1000"), currency="TRY")
    offer = _offer(offer_id="import", price="1000")
    offer = offer.model_copy(
        update={
            "landed_cost": LandedCost(
                list_in_reference=money,
                other_fees=[
                    CostLine(
                        amount=Money(amount=Decimal("-190"), currency="TRY"),
                        origin=CostOrigin.ESTIMATED,
                        label="DE VAT removed on export (19%)",
                    )
                ],
                total=Money(amount=Decimal("810"), currency="TRY"),
                completeness=LandedCostCompleteness.PARTIAL,
                destination_country="TR",
            )
        }
    )

    explanation = _explain([offer])["import"]
    fees = [r for r in explanation.reasons if r.factor == "fees"]  # type: ignore[attr-defined]

    assert len(fees) == 1
    assert "VAT removed on export" in fees[0].detail
