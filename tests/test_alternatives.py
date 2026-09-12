"""Alternatives are ranked and badged, never silently dropped."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from gp_price_intel.alternatives.scout import AlternativeScout
from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.domain.models import (
    AlternativeBadge,
    LandedCost,
    LandedCostCompleteness,
    MatchKind,
    Money,
    Offer,
    ScoreBreakdown,
    Seller,
)

CONFIRMED = "samsung-galaxy-s26-ultra-512-12-eu-black"
BIGGER_STORAGE = "samsung-galaxy-s26-ultra-1024-12-eu-black"
OTHER_COLOUR = "samsung-galaxy-s26-ultra-512-12-eu-silver"
LAST_GENERATION = "samsung-galaxy-s25-ultra-512-12-eu-silver"
SMALLER_PHONE = "samsung-galaxy-s26-plus-256-12-eu-black"

Scored = tuple[Offer, ScoreBreakdown]


def _offer(
    offer_id: str,
    price: str,
    variant_id: str,
    match_kind: MatchKind = MatchKind.SIMILAR,
) -> Offer:
    money = Money(amount=Decimal(price), currency="TRY")
    return Offer(
        id=offer_id,
        source_id="test",
        seller=Seller(name=offer_id),
        country="TR",
        listing_title=f"Listing {offer_id}",
        listing_url=f"https://example.com/{offer_id}",
        list_price=money,
        landed_cost=LandedCost(
            list_in_reference=money,
            total=money,
            completeness=LandedCostCompleteness.COMPLETE,
            destination_country="TR",
        ),
        matched_variant_id=variant_id,
        match_kind=match_kind,
        collected_at=datetime.now(timezone.utc),
    )


def _score(final_score: float) -> ScoreBreakdown:
    return ScoreBreakdown(
        criterion_scores={}, weights_used={}, missing_criteria=[], final_score=final_score
    )


def _scored(
    offer_id: str,
    price: str,
    variant_id: str,
    final_score: float = 0.5,
    match_kind: MatchKind = MatchKind.SIMILAR,
) -> Scored:
    return (_offer(offer_id, price, variant_id, match_kind), _score(final_score))


def _confirmed_variant():
    variant = CatalogRepository().get_variant(CONFIRMED)
    assert variant is not None
    return variant


def _select(near: list[Scored], best: Scored, confirmed_id: str = CONFIRMED, **kwargs):
    catalog = CatalogRepository()
    confirmed = catalog.get_variant(confirmed_id)
    assert confirmed is not None
    return AlternativeScout(catalog).select(near, best, confirmed, **kwargs)


def test_more_storage_for_a_little_more_money_is_an_upgrade() -> None:
    """100% more storage for 5% more money is exactly the case the badge is for."""
    best = _scored("pick", "1000", CONFIRMED, final_score=0.9)
    bigger = _scored("bigger", "1050", BIGGER_STORAGE)

    alternatives = _select([bigger], best)

    assert [a.badge for a in alternatives] == [AlternativeBadge.UPGRADE]
    assert alternatives[0].landed_cost_delta == Money(amount=Decimal("50"), currency="TRY")


def test_more_storage_at_a_steep_premium_earns_no_badge_but_is_still_shown() -> None:
    """
    The spec gain clears the bar; the price does not. It stays in the list.

    Badges are claims about value, so an alternative that fails a value test loses
    the claim, not its place — the user still gets to see the option exists.
    """
    best = _scored("pick", "1000", CONFIRMED, final_score=0.9)
    bigger = _scored("bigger", "1330", BIGGER_STORAGE)

    alternatives = _select([bigger], best)

    assert len(alternatives) == 1
    assert alternatives[0].badge is None
    assert any("does not clear a value test" in c for c in alternatives[0].explanation.caveats)


def test_halving_storage_to_save_a_fifth_is_a_downgrade() -> None:
    """Saving 20% while giving up 50% of storage sits inside both thresholds."""
    best = _scored("pick", "1000", BIGGER_STORAGE, final_score=0.9)
    smaller = _scored("smaller", "800", CONFIRMED)

    alternatives = _select([smaller], best, confirmed_id=BIGGER_STORAGE)

    assert [a.badge for a in alternatives] == [AlternativeBadge.DOWNGRADE]
    assert alternatives[0].landed_cost_delta == Money(amount=Decimal("-200"), currency="TRY")


def test_a_saving_too_small_to_matter_is_not_a_downgrade() -> None:
    best = _scored("pick", "1000", BIGGER_STORAGE, final_score=0.9)
    smaller = _scored("smaller", "950", CONFIRMED)

    alternatives = _select([smaller], best, confirmed_id=BIGGER_STORAGE)

    assert alternatives[0].badge is None


def test_a_different_product_that_scores_close_and_shares_specs_is_a_rival() -> None:
    """Last year's flagship: same storage, memory, screen and battery, lower price."""
    best = _scored("pick", "1000", CONFIRMED, final_score=0.90)
    older = _scored(
        "older", "820", LAST_GENERATION, final_score=0.86, match_kind=MatchKind.DIFFERENT
    )

    alternatives = _select([older], best)

    assert [a.badge for a in alternatives] == [AlternativeBadge.RIVAL]


def test_a_different_product_scoring_well_below_the_pick_is_not_a_rival() -> None:
    """Same shared specs, but 85% of the top score is the floor and this misses it."""
    best = _scored("pick", "1000", CONFIRMED, final_score=0.90)
    older = _scored(
        "older", "820", LAST_GENERATION, final_score=0.70, match_kind=MatchKind.DIFFERENT
    )

    alternatives = _select([older], best)

    assert alternatives[0].badge is None


def test_a_different_product_sharing_few_specs_is_not_a_rival() -> None:
    """A smaller phone scores well but agrees on only half the core specs."""
    best = _scored("pick", "1000", CONFIRMED, final_score=0.90)
    smaller_phone = _scored(
        "plus", "900", SMALLER_PHONE, final_score=0.89, match_kind=MatchKind.DIFFERENT
    )

    alternatives = _select([smaller_phone], best)

    assert alternatives[0].badge is None


def test_alternatives_are_ordered_by_score_not_by_arrival() -> None:
    best = _scored("pick", "1000", CONFIRMED, final_score=0.95)
    near = [
        _scored("weak", "1000", OTHER_COLOUR, final_score=0.30),
        _scored("strong", "1000", OTHER_COLOUR, final_score=0.80),
        _scored("middling", "1000", OTHER_COLOUR, final_score=0.55),
    ]

    alternatives = _select(near, best)

    assert [a.offer_id for a in alternatives] == ["strong", "middling", "weak"]


def test_selection_prefers_one_of_each_badge_over_three_of_a_kind() -> None:
    """
    Three cheaper-but-smaller options tell the user one thing three times.

    Two unbadged listings outrank the downgrade on score, but a slot is kept for a
    second kind of reason so the shortlist is not a single theme repeated.
    """
    best = _scored("pick", "1000", BIGGER_STORAGE, final_score=0.95)
    near = [
        _scored("same-a", "1000", BIGGER_STORAGE, final_score=0.90),
        _scored("same-b", "1000", BIGGER_STORAGE, final_score=0.88),
        _scored("cheaper", "800", CONFIRMED, final_score=0.40),
    ]

    alternatives = _select(near, best, confirmed_id=BIGGER_STORAGE, max_alternatives=2)

    assert [a.offer_id for a in alternatives] == ["cheaper", "same-a"]
    assert alternatives[0].badge is AlternativeBadge.DOWNGRADE


def test_every_alternative_carries_a_reason_and_a_cost_delta() -> None:
    best = _scored("pick", "1000", CONFIRMED, final_score=0.9)
    near = [
        _scored("colour", "1000", OTHER_COLOUR, final_score=0.7),
        _scored("bigger", "1050", BIGGER_STORAGE, final_score=0.6),
    ]

    for alternative in _select(near, best):
        assert alternative.explanation.reasons
        assert any(r.factor == "cost" for r in alternative.explanation.reasons)
        assert alternative.landed_cost_delta is not None


def test_the_top_pick_is_never_offered_as_its_own_alternative() -> None:
    best = _scored("pick", "1000", CONFIRMED, final_score=0.9)

    assert _select([best], best) == []


def test_no_alternatives_without_a_top_pick_to_compare_against() -> None:
    near = [_scored("colour", "1000", OTHER_COLOUR)]

    assert AlternativeScout().select(near, None, _confirmed_variant()) == []
