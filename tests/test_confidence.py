"""Confidence, ranking, and highlight-gate invariants."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from gp_price_intel.domain.models import (
    ConvertedMoney,
    FxQuote,
    HighlightKind,
    LandedCost,
    LandedCostCompleteness,
    Money,
    Offer,
    Seller,
    UserPreferences,
)
from gp_price_intel.ranking.confidence import (
    HIGHLIGHT_MIN_CONFIDENCE,
    compute_data_confidence,
    effective_confidence,
    is_highlight_eligible,
    reliability_warning,
    review_volume_score,
)
from gp_price_intel.ranking.engine import RankingEngine
from gp_price_intel.ranking.highlights import pick_highlights


def _offer(
    *,
    offer_id: str,
    price: str,
    data_confidence: float,
    completeness: LandedCostCompleteness = LandedCostCompleteness.COMPLETE,
    seller_reliability: float | None = 0.9,
    review_count: int | None = 1000,
    delivery_time: str | None = "1-2 days",
) -> Offer:
    amount = Decimal(price)
    money = Money(amount=amount, currency="TRY")
    return Offer(
        id=offer_id,
        source_id="test",
        seller=Seller(
            name=offer_id,
            reliability=seller_reliability,
            review_count=review_count,
        ),
        country="TR",
        listing_title=offer_id,
        listing_url=f"https://example.com/{offer_id}",
        list_price=money,
        converted_list_price=ConvertedMoney(
            original=money,
            reference=money,
            fx=FxQuote(
                base_currency="TRY",
                quote_currency="TRY",
                rate=Decimal("1"),
                as_of=datetime.now(timezone.utc),
                provider="test",
            ),
        ),
        landed_cost=LandedCost(
            list_in_reference=money,
            total=money,
            completeness=completeness,
            destination_country="TR",
        ),
        delivery_time=delivery_time,
        collected_at=datetime.now(timezone.utc),
        data_confidence=data_confidence,
    )


def test_review_volume_is_monotonic() -> None:
    assert review_volume_score(0) < review_volume_score(10) < review_volume_score(1000)


def test_weaker_source_and_reviews_yield_lower_confidence() -> None:
    trusted = compute_data_confidence(
        source_reliability=1.0,
        seller_reliability=1.0,
        review_count=100_000,
    )
    obscure = compute_data_confidence(
        source_reliability=0.0,
        seller_reliability=0.0,
        review_count=0,
    )
    assert 0.0 <= obscure < trusted <= 1.0


def test_confidence_penalty_mirrors_multiplier() -> None:
    offer = _offer(offer_id="a", price="1000", data_confidence=0.5)
    _, breakdown = RankingEngine().score([offer], UserPreferences())[0]
    assert abs((1.0 - breakdown.confidence_penalty) - effective_confidence(breakdown)) < 1e-9


def test_boundary_confidence_controls_highlight_eligibility() -> None:
    below = _offer(offer_id="below", price="1000", data_confidence=0.0)
    above = _offer(offer_id="above", price="1000", data_confidence=1.0)

    scored = RankingEngine().score([below, above], UserPreferences())
    by_id = {offer.id: breakdown for offer, breakdown in scored}

    assert not is_highlight_eligible(by_id["below"])
    assert by_id["below"].reliability_warning is not None
    assert is_highlight_eligible(by_id["above"])
    assert by_id["above"].reliability_warning is None
    assert reliability_warning(by_id["below"]) is not None
    assert reliability_warning(by_id["above"]) is None


def test_ranked_output_is_sorted_by_final_score_descending() -> None:
    offers = [
        _offer(offer_id="a", price="5000", data_confidence=0.8),
        _offer(offer_id="b", price="8000", data_confidence=0.5),
        _offer(offer_id="c", price="3000", data_confidence=1.0),
    ]
    scored = RankingEngine().score(offers, UserPreferences())
    finals = [breakdown.final_score for _, breakdown in scored]
    assert finals == sorted(finals, reverse=True)


def test_equal_signals_higher_confidence_outranks_lower() -> None:
    """Extreme confidence gap with identical commercial signals."""
    low = _offer(
        offer_id="low-conf",
        price="10000",
        data_confidence=0.0,
        seller_reliability=0.8,
        review_count=1000,
    )
    high = _offer(
        offer_id="high-conf",
        price="10000",
        data_confidence=1.0,
        seller_reliability=0.8,
        review_count=1000,
    )
    scored = RankingEngine().score([low, high], UserPreferences())
    assert scored[0][0].id == "high-conf"
    assert scored[0][1].final_score >= scored[1][1].final_score


def test_equal_confidence_extreme_price_gap_favors_cheaper() -> None:
    cheap = _offer(offer_id="cheap", price="1", data_confidence=1.0)
    expensive = _offer(offer_id="expensive", price="1000000", data_confidence=1.0)
    scored = RankingEngine().score([expensive, cheap], UserPreferences())
    assert scored[0][0].id == "cheap"


def test_highlights_never_include_offers_below_confidence_floor() -> None:
    low = _offer(offer_id="low", price="100", data_confidence=0.0)
    mid = _offer(offer_id="mid", price="500", data_confidence=0.5)
    high = _offer(offer_id="high", price="1000", data_confidence=1.0)

    scored = RankingEngine().score([low, mid, high], UserPreferences())
    highlights = pick_highlights(scored, UserPreferences())
    by_id = {offer.id: breakdown for offer, breakdown in scored}

    assert highlights
    highlight_ids = {item.offer_id for item in highlights}
    assert "low" not in highlight_ids
    assert "mid" not in highlight_ids
    for highlight in highlights:
        assert is_highlight_eligible(by_id[highlight.offer_id])
        assert effective_confidence(by_id[highlight.offer_id]) >= HIGHLIGHT_MIN_CONFIDENCE


def test_all_ineligible_pool_yields_no_highlights() -> None:
    scored = RankingEngine().score(
        [
            _offer(offer_id="a", price="100", data_confidence=0.0),
            _offer(offer_id="b", price="200", data_confidence=0.1),
        ],
        UserPreferences(),
    )
    assert pick_highlights(scored, UserPreferences()) == []


def test_highlight_kinds_are_from_eligible_pool_only() -> None:
    low = _offer(offer_id="unreliable-cheap", price="1", data_confidence=0.0)
    high = _offer(offer_id="reliable", price="99999", data_confidence=1.0)
    scored = RankingEngine().score([low, high], UserPreferences())
    highlights = pick_highlights(scored, UserPreferences())

    assert all(h.offer_id == "reliable" for h in highlights)
    kinds = {h.kind for h in highlights}
    assert HighlightKind.BEST_OVERALL in kinds
