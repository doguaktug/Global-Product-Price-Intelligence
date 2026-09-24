"""Domain and orchestrator smoke tests."""

from datetime import datetime, timezone
from decimal import Decimal

from gp_price_intel.domain.models import (
    MatchKind,
    Money,
    Offer,
    ScoreBreakdown,
    Seller,
    UserPreferences,
)
from gp_price_intel.orchestrator.search import SearchOrchestrator, split_ranked_offers


def test_money_is_immutable_value_object() -> None:
    price = Money(amount=Decimal("1399.00"), currency="EUR")
    assert price.currency == "EUR"
    assert price.amount == Decimal("1399.00")


def test_start_session_creates_received_or_confirm_status() -> None:
    orch = SearchOrchestrator()
    session = orch.start_session(
        "Samsung Galaxy S26 Ultra 512 GB",
        UserPreferences(),
    )
    assert session.id
    assert session.raw_query.startswith("Samsung")
    assert session.normalized_query is not None
    assert session.preferences.destination_country == "TR"
    assert session.preferences.reference_currency == "TRY"
    assert session.preferences.weights["warranty"] == 0.15
    assert session.preferences.weights["price"] == 0.40


def _scored(
    offer_id: str,
    kind: MatchKind,
    variant: str | None,
    score: float,
) -> tuple[Offer, ScoreBreakdown]:
    offer = Offer(
        id=offer_id,
        source_id="t",
        seller=Seller(name=offer_id),
        country="TR",
        listing_title=offer_id,
        listing_url=f"https://example.com/{offer_id}",
        list_price=Money(amount=Decimal("1"), currency="TRY"),
        collected_at=datetime.now(timezone.utc),
        match_kind=kind,
        matched_variant_id=variant,
    )
    return (offer, ScoreBreakdown(final_score=score))


def test_identical_matches_stay_on_the_decision_page() -> None:
    ranked = [
        _scored("exact", MatchKind.IDENTICAL, "v-2048", 0.9),
        _scored("near", MatchKind.SIMILAR, "v-1024", 0.8),
    ]
    confirmed, near = split_ranked_offers(ranked)
    assert [item[0].id for item in confirmed] == ["exact"]
    assert [item[0].id for item in near] == ["near"]


def test_in_scope_colour_siblings_are_alternatives_not_a_second_ranked_list() -> None:
    """Colour 'not important' marks every 512 GB S26 IDENTICAL — still split by build."""
    ranked = [
        _scored("black", MatchKind.IDENTICAL, "v-512-black", 0.9),
        _scored("silver", MatchKind.IDENTICAL, "v-512-silver", 0.8),
        _scored("1tb", MatchKind.SIMILAR, "v-1024", 0.7),
    ]
    confirmed, near = split_ranked_offers(ranked)
    assert [item[0].id for item in confirmed] == ["black"]
    assert [item[0].id for item in near] == ["silver", "1tb"]


def test_similar_only_results_keep_the_closest_variant_as_highlights() -> None:
    """
    Live eBay often has the family but not the exact SKU (1 TB vs a 2 TB search).

    Dumping every similar listing into the ranked list and leaving alternatives
    empty hid those offers behind "list all other options" with no cards on the
    Decision Page.
    """
    ranked = [
        _scored("best-1tb", MatchKind.SIMILAR, "v-1024", 0.9),
        _scored("other-1tb", MatchKind.SIMILAR, "v-1024", 0.7),
        _scored("16gb", MatchKind.SIMILAR, "v-1024-16", 0.6),
    ]
    confirmed, near = split_ranked_offers(ranked)
    assert {item[0].id for item in confirmed} == {"best-1tb", "other-1tb"}
    assert [item[0].id for item in near] == ["16gb"]
