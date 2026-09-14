"""Alternatives are shown only when they clear a value test."""

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


def test_more_storage_at_a_steep_premium_is_hidden() -> None:
    """Spec gain clears the bar but price does not — omit, do not show for comparison."""
    best = _scored("pick", "1000", CONFIRMED, final_score=0.9)
    bigger = _scored("bigger", "1330", BIGGER_STORAGE)

    assert _select([bigger], best) == []


def test_halving_storage_to_save_a_fifth_is_a_downgrade() -> None:
    """Saving 20% while giving up 50% of storage sits inside both thresholds."""
    best = _scored("pick", "1000", BIGGER_STORAGE, final_score=0.9)
    smaller = _scored("smaller", "800", CONFIRMED)

    alternatives = _select([smaller], best, confirmed_id=BIGGER_STORAGE)

    assert [a.badge for a in alternatives] == [AlternativeBadge.DOWNGRADE]
    assert alternatives[0].landed_cost_delta == Money(amount=Decimal("-200"), currency="TRY")


def test_a_saving_too_small_to_matter_is_hidden() -> None:
    best = _scored("pick", "1000", BIGGER_STORAGE, final_score=0.9)
    smaller = _scored("smaller", "950", CONFIRMED)

    assert _select([smaller], best, confirmed_id=BIGGER_STORAGE) == []


def test_a_different_product_that_scores_close_and_shares_specs_is_a_rival() -> None:
    """Last year's flagship: same storage, memory, screen and battery, lower price."""
    best = _scored("pick", "1000", CONFIRMED, final_score=0.90)
    older = _scored(
        "older", "900", LAST_GENERATION, final_score=0.86, match_kind=MatchKind.DIFFERENT
    )

    alternatives = _select([older], best)

    assert [a.badge for a in alternatives] == [AlternativeBadge.RIVAL]


def test_a_different_product_scoring_well_below_the_pick_is_hidden() -> None:
    """Same shared specs, but below the score floor — omit entirely."""
    best = _scored("pick", "1000", CONFIRMED, final_score=0.90)
    older = _scored(
        "older", "820", LAST_GENERATION, final_score=0.70, match_kind=MatchKind.DIFFERENT
    )

    assert _select([older], best) == []


def test_a_different_product_sharing_few_specs_is_hidden() -> None:
    """A smaller phone scores well but agrees on too few core specs."""
    best = _scored("pick", "1000", CONFIRMED, final_score=0.90)
    smaller_phone = _scored(
        "plus", "900", SMALLER_PHONE, final_score=0.89, match_kind=MatchKind.DIFFERENT
    )

    assert _select([smaller_phone], best) == []


def test_similar_colour_variant_with_close_price_and_score_is_shown() -> None:
    """Cosmetic / near-identical build with close price and score earns similar."""
    best = _scored("pick", "1000", CONFIRMED, final_score=0.95)
    twin = _scored("twin", "1020", OTHER_COLOUR, final_score=0.92)

    alternatives = _select([twin], best)

    assert [a.badge for a in alternatives] == [AlternativeBadge.SIMILAR]
    assert alternatives[0].offer_id == "twin"


def test_selection_prefers_one_of_each_badge_over_three_of_a_kind() -> None:
    """
    Prefer a spread of reasons; failed value tests never fill remaining slots.
    """
    best = _scored("pick", "1000", BIGGER_STORAGE, final_score=0.95)
    near = [
        _scored("same-a", "1000", BIGGER_STORAGE, final_score=0.90),  # similar
        _scored("same-b", "1330", BIGGER_STORAGE, final_score=0.88),  # fails upgrade
        _scored("cheaper", "800", CONFIRMED, final_score=0.40),  # downgrade
    ]

    alternatives = _select(near, best, confirmed_id=BIGGER_STORAGE, max_alternatives=2)

    assert [a.offer_id for a in alternatives] == ["same-a", "cheaper"]
    assert {a.badge for a in alternatives} == {
        AlternativeBadge.SIMILAR,
        AlternativeBadge.DOWNGRADE,
    }


def test_every_shown_alternative_carries_a_badge_reason_and_cost_delta() -> None:
    best = _scored("pick", "1000", CONFIRMED, final_score=0.9)
    near = [
        _scored("colour", "1000", OTHER_COLOUR, final_score=0.88),
        _scored("bigger", "1050", BIGGER_STORAGE, final_score=0.6),
        _scored("steep", "1400", BIGGER_STORAGE, final_score=0.7),  # hidden
    ]

    alternatives = _select(near, best)
    assert {a.offer_id for a in alternatives} == {"colour", "bigger"}
    for alternative in alternatives:
        assert alternative.badge is not None
        assert alternative.explanation.reasons
        assert any(r.factor == "cost" for r in alternative.explanation.reasons)
        assert alternative.landed_cost_delta is not None
        assert alternative.explanation.caveats == []


def test_spec_change_reasons_include_the_unit() -> None:
    best = _scored("pick", "1000", CONFIRMED, final_score=0.9)
    bigger = _scored("bigger", "1050", BIGGER_STORAGE)
    colour = _scored("colour", "1000", OTHER_COLOUR, final_score=0.88)

    alternatives = {item.offer_id: item for item in _select([bigger, colour], best)}

    storage = next(r for r in alternatives["bigger"].explanation.reasons if r.factor == "storage_gb")
    assert storage.detail == "512 GB → 1024 GB"
    colour_reason = next(
        r for r in alternatives["colour"].explanation.reasons if r.factor == "colour"
    )
    assert colour_reason.detail == "Black → Silver"


def test_the_top_pick_is_never_offered_as_its_own_alternative() -> None:
    best = _scored("pick", "1000", CONFIRMED, final_score=0.9)

    assert _select([best], best) == []


def test_no_alternatives_without_a_top_pick_to_compare_against() -> None:
    near = [_scored("colour", "1000", OTHER_COLOUR)]

    assert AlternativeScout().select(near, None, _confirmed_variant()) == []
