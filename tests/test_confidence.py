"""Confidence, ranking, and highlight-gate invariants."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from gp_price_intel.domain.models import (
    DEFAULT_WEIGHTS,
    AcquisitionMethod,
    ConvertedMoney,
    FxQuote,
    HighlightKind,
    LandedCost,
    LandedCostCompleteness,
    Money,
    Offer,
    Seller,
    Source,
    SourceKind,
    StockStatus,
    UserPreferences,
)
from gp_price_intel.ranking.confidence import (
    HIGHLIGHT_MIN_CONFIDENCE,
    UNKNOWN_STOCK_CONFIDENCE_FACTOR,
    compute_data_confidence,
    effective_confidence,
    is_highlight_eligible,
    reliability_warning,
    review_volume_score,
)
from gp_price_intel.ranking.engine import (
    SOURCE_RELIABILITY_WEIGHT,
    RankingEngine,
    delivery_days,
    warranty_months,
)
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
    warranty: str | None = None,
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
        warranty=warranty,
        collected_at=datetime.now(timezone.utc),
        data_confidence=data_confidence,
    )


def test_review_volume_is_monotonic() -> None:
    assert review_volume_score(0) < review_volume_score(10) < review_volume_score(1000)


def test_unknown_stock_lowers_confidence() -> None:
    """We could not establish the item is purchasable — that is missing data."""
    signals = {"source_reliability": 0.9, "seller_reliability": 0.9, "review_count": 1000}
    known = compute_data_confidence(**signals, stock_status=StockStatus.IN_STOCK)
    unknown = compute_data_confidence(**signals, stock_status=StockStatus.UNKNOWN)

    assert unknown < known
    assert abs(unknown - known * UNKNOWN_STOCK_CONFIDENCE_FACTOR) < 1e-9


def test_stock_states_the_source_actually_reported_are_not_discounted() -> None:
    """Confidence rates the data, not the offer. 'Limited' is knowledge, not a gap."""
    signals = {"source_reliability": 0.9, "seller_reliability": 0.9, "review_count": 1000}
    in_stock = compute_data_confidence(**signals, stock_status=StockStatus.IN_STOCK)

    assert compute_data_confidence(**signals, stock_status=StockStatus.LIMITED) == in_stock
    assert compute_data_confidence(**signals, stock_status=StockStatus.OUT_OF_STOCK) == in_stock
    assert compute_data_confidence(**signals) == in_stock


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
    # Sole eligible offer is "best for you"; overlapping lenses are dropped.
    assert kinds == {HighlightKind.BEST_OVERALL}
    assert "best_specification" not in {k.value for k in HighlightKind}
    assert HighlightKind.BEST_WARRANTY.value == "best_warranty"


def test_default_weights_include_warranty() -> None:
    assert UserPreferences().weights == DEFAULT_WEIGHTS
    assert DEFAULT_WEIGHTS == {
        "price": 0.40,
        "seller": 0.20,
        "reviews": 0.15,
        "delivery": 0.10,
        "warranty": 0.15,
    }
    assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9


def test_warranty_months_parses_years_and_days() -> None:
    assert warranty_months("2 years") == 24
    assert warranty_months("24 months") == 24
    assert warranty_months("30 days") == 1
    assert warranty_months(None) is None
    assert warranty_months("  ") is None


def test_warranty_months_ignores_who_issued_the_cover() -> None:
    """Duration is the criterion; 'manufacturer' is a seller claim, not extra months."""
    assert warranty_months("2 years manufacturer") == 24
    assert warranty_months("24 months official") == 24


def test_warranty_months_returns_none_for_unparseable_text() -> None:
    assert warranty_months("manufacturer warranty") is None
    assert warranty_months("see listing") is None


def test_delivery_days_uses_the_unit_word_in_the_source_language() -> None:
    assert delivery_days("2-4 Werktage") == 4
    assert delivery_days("1-3 iş günü") == 3
    assert delivery_days("2-5日") == 5
    assert delivery_days("7-14 days") == 14
    assert delivery_days("3 weeks") == 21
    assert delivery_days("1 month") == 30


def test_delivery_days_reads_a_range_as_its_slowest_end() -> None:
    """A quoted range is a promise of its far end — that is what the buyer waits for."""
    assert delivery_days("2-4 days") == 4
    assert delivery_days("2 days") == 2


def test_delivery_days_understands_same_and_next_day_phrasing() -> None:
    assert delivery_days("Same day") == 0
    assert delivery_days("Next day") == 1
    assert delivery_days("aynı gün teslimat") == 0


def test_delivery_days_returns_none_when_no_estimate_is_given() -> None:
    assert delivery_days(None) is None
    assert delivery_days("   ") is None
    assert delivery_days("ships soon") is None
    # A bare number carries no unit, so it is not an estimate we can trust.
    assert delivery_days("48") is None


def test_unparseable_delivery_drops_the_criterion_instead_of_guessing() -> None:
    quoted = _offer(offer_id="quoted", price="1000", data_confidence=1.0, delivery_time="2 days")
    silent = _offer(offer_id="silent", price="1000", data_confidence=1.0, delivery_time=None)
    scored = RankingEngine().score([quoted, silent], UserPreferences())
    by_id = {offer.id: breakdown for offer, breakdown in scored}

    assert "delivery" in by_id["quoted"].criterion_scores
    assert "delivery" not in by_id["silent"].criterion_scores
    assert "delivery" in by_id["silent"].missing_criteria
    assert abs(sum(by_id["silent"].weights_used.values()) - 1.0) < 1e-9


def test_faster_delivery_scores_higher() -> None:
    fast = _offer(offer_id="fast", price="1000", data_confidence=1.0, delivery_time="Next day")
    slow = _offer(offer_id="slow", price="1000", data_confidence=1.0, delivery_time="3-4 weeks")
    scored = RankingEngine().score([fast, slow], UserPreferences())

    assert scored[0][0].id == "fast"
    by_id = {offer.id: breakdown for offer, breakdown in scored}
    assert by_id["fast"].criterion_scores["delivery"] > by_id["slow"].criterion_scores["delivery"]


def _source(source_id: str, reliability: float) -> Source:
    return Source(
        id=source_id,
        display_name=source_id,
        country="TR",
        kind=SourceKind.MARKETPLACE,
        reliability=reliability,
        acquisition_method=AcquisitionMethod.FIXTURE,
    )


def test_site_reputation_moves_the_seller_criterion_without_dominating_it() -> None:
    """A strong seller on a mid-tier site is not capped by the site (see item: 30% effect)."""
    assert SOURCE_RELIABILITY_WEIGHT == 0.30

    on_weak_site = _offer(
        offer_id="weak-site", price="1000", data_confidence=1.0, seller_reliability=1.0
    )
    on_strong_site = _offer(
        offer_id="strong-site", price="1000", data_confidence=1.0, seller_reliability=1.0
    ).model_copy(update={"source_id": "trusted"})

    sources = {"test": _source("test", 0.2), "trusted": _source("trusted", 1.0)}
    scored = RankingEngine().score([on_weak_site, on_strong_site], UserPreferences(), sources)
    by_id = {offer.id: breakdown for offer, breakdown in scored}

    assert by_id["strong-site"].criterion_scores["seller"] == 1.0
    assert by_id["weak-site"].criterion_scores["seller"] == 0.0
    assert scored[0][0].id == "strong-site"


def test_seller_criterion_falls_back_to_site_reputation_when_seller_is_unrated() -> None:
    unrated = _offer(offer_id="unrated", price="1000", data_confidence=1.0, seller_reliability=None)
    rated = _offer(offer_id="rated", price="1000", data_confidence=1.0, seller_reliability=0.9)
    sources = {"test": _source("test", 0.4)}
    scored = RankingEngine().score([unrated, rated], UserPreferences(), sources)
    by_id = {offer.id: breakdown for offer, breakdown in scored}

    assert by_id["rated"].criterion_scores["seller"] > by_id["unrated"].criterion_scores["seller"]


def test_missing_warranty_is_dropped_and_weights_renormalized() -> None:
    with_warranty = _offer(offer_id="covered", price="1000", data_confidence=1.0, warranty="24 months")
    without = _offer(offer_id="bare", price="1000", data_confidence=1.0, warranty=None)
    scored = RankingEngine().score([with_warranty, without], UserPreferences())
    by_id = {offer.id: breakdown for offer, breakdown in scored}

    assert "warranty" in by_id["covered"].criterion_scores
    assert "warranty" not in by_id["bare"].criterion_scores
    assert by_id["bare"].missing_criteria == ["warranty"]
    assert abs(sum(by_id["bare"].weights_used.values()) - 1.0) < 1e-9
    assert "warranty" not in by_id["bare"].weights_used
    assert by_id["covered"].final_score >= by_id["bare"].final_score


def test_best_for_you_overlap_drops_the_other_highlight() -> None:
    """Cheapest eligible offer that also wins the weighted score keeps only 'best for you'."""
    cheap_trusted = _offer(
        offer_id="cheap-trusted",
        price="100",
        data_confidence=1.0,
        seller_reliability=1.0,
        warranty="24 months",
    )
    expensive = _offer(
        offer_id="expensive",
        price="9000",
        data_confidence=1.0,
        seller_reliability=0.5,
        warranty="6 months",
    )
    scored = RankingEngine().score([cheap_trusted, expensive], UserPreferences())
    highlights = pick_highlights(scored, UserPreferences())

    assert scored[0][0].id == "cheap-trusted"
    kinds_by_offer: dict[str, set[HighlightKind]] = {}
    for highlight in highlights:
        kinds_by_offer.setdefault(highlight.offer_id, set()).add(highlight.kind)

    assert kinds_by_offer["cheap-trusted"] == {HighlightKind.BEST_OVERALL}
    assert HighlightKind.LOWEST_LIST_PRICE not in {h.kind for h in highlights}
    offer_ids = [h.offer_id for h in highlights]
    assert len(offer_ids) == len(set(offer_ids))


def test_distinct_best_for_you_keeps_other_highlights() -> None:
    cheap = _offer(
        offer_id="cheap-weak-seller",
        price="100",
        data_confidence=1.0,
        seller_reliability=0.2,
        review_count=10,
        warranty="6 months",
    )
    balanced = _offer(
        offer_id="balanced",
        price="400",
        data_confidence=1.0,
        seller_reliability=1.0,
        review_count=50_000,
        warranty="24 months manufacturer",
    )
    scored = RankingEngine().score([cheap, balanced], UserPreferences())
    highlights = pick_highlights(scored, UserPreferences())
    by_kind = {h.kind: h.offer_id for h in highlights}

    assert HighlightKind.BEST_OVERALL in by_kind
    assert HighlightKind.LOWEST_LIST_PRICE in by_kind
    assert by_kind[HighlightKind.BEST_OVERALL] != by_kind[HighlightKind.LOWEST_LIST_PRICE]
    assert "best_specification" not in {h.kind.value for h in highlights}


def test_best_warranty_highlight_picks_longest_warranty() -> None:
    cheap = _offer(
        offer_id="cheap-short-warranty",
        price="100",
        data_confidence=1.0,
        seller_reliability=1.0,
        warranty="6 months",
    )
    covered = _offer(
        offer_id="long-warranty",
        price="800",
        data_confidence=1.0,
        seller_reliability=0.4,
        warranty="24 months",
    )
    scored = RankingEngine().score([cheap, covered], UserPreferences())
    highlights = pick_highlights(scored, UserPreferences())
    by_kind = {h.kind: h.offer_id for h in highlights}

    assert by_kind[HighlightKind.BEST_OVERALL] == "cheap-short-warranty"
    assert by_kind[HighlightKind.BEST_WARRANTY] == "long-warranty"
    assert HighlightKind.BEST_WARRANTY in HighlightKind
