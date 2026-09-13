"""End-to-end orchestrator pipeline invariants."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from gp_price_intel.adapters.fixture import FixtureAdapter
from gp_price_intel.adapters.registry import load_sources
from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.config import Settings
from gp_price_intel.domain.models import (
    HighlightKind,
    LandedCostCompleteness,
    MatchKind,
    PropertyChoice,
    PropertyChoiceKind,
    SessionStatus,
    UserPreferences,
)
from gp_price_intel.fx.service import FxService
from gp_price_intel.orchestrator.search import SearchFailed, SearchOrchestrator
from gp_price_intel.ranking.confidence import HIGHLIGHT_MIN_CONFIDENCE, effective_confidence, is_highlight_eligible

_RATES_TO_TRY = {
    "EUR": Decimal("35"),
    "GBP": Decimal("41"),
    "JPY": Decimal("0.22"),
    "USD": Decimal("32"),
}


@pytest.fixture
def pipeline_orchestrator(tmp_path: Path) -> SearchOrchestrator:
    data_dir = tmp_path / "data"
    (data_dir / "catalog").mkdir(parents=True)
    (data_dir / "fixtures").mkdir(parents=True)
    (data_dir / "sources").mkdir(parents=True)

    repo_root = Path(__file__).resolve().parents[1]
    for name in ("categories.json", "families.json", "variants.json"):
        src = repo_root / "data" / "catalog" / name
        (data_dir / "catalog" / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    for relative in ("fixtures/offers.json", "sources/sources.json"):
        src = repo_root / "data" / relative
        (data_dir / relative).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    settings = Settings(data_dir=data_dir)
    catalog = CatalogRepository(catalog_dir=data_dir / "catalog")

    def frankfurter_handler(request: httpx.Request) -> httpx.Response:
        base = request.url.params.get("from", "EUR")
        quote = request.url.params.get("to", "TRY")
        rate = float(_RATES_TO_TRY.get(base, Decimal("1")))
        return httpx.Response(
            200,
            json={"amount": 1.0, "base": base, "date": "2026-08-31", "rates": {quote: rate}},
        )

    fx = FxService(settings=settings, client=httpx.AsyncClient(transport=httpx.MockTransport(frankfurter_handler)))
    fixture_sources = [
        source
        for source in load_sources(data_dir)
        if source.id.startswith("fixture-")
    ]
    fixture_adapter = FixtureAdapter(
        catalog=catalog,
        settings=settings,
        fixture_path=data_dir / "fixtures" / "offers.json",
        sources=fixture_sources,
    )

    return SearchOrchestrator(
        catalog=catalog,
        adapters=[fixture_adapter],
        fx=fx,
    )


@pytest.mark.asyncio
async def test_pipeline_produces_decision_page_structure(
    pipeline_orchestrator: SearchOrchestrator,
) -> None:
    session = pipeline_orchestrator.start_session(
        "Samsung Galaxy S26 Ultra 512 GB Black",
        UserPreferences(destination_country="TR", reference_currency="TRY"),
    )
    assert session.normalized_query is not None
    assert session.normalized_query.needs_confirmation is False

    page = await pipeline_orchestrator.run(session)

    assert page.offers
    assert page.confirmed_variant is not None
    assert page.offer_scores
    assert set(page.offer_scores) == {offer.id for offer in page.offers}

    # Original currency preserved; conversion attached for ranking display.
    for offer in page.offers:
        assert offer.list_price.currency
        assert offer.converted_list_price is not None
        assert offer.converted_list_price.reference.currency == "TRY"
        assert offer.landed_cost is not None
        assert offer.match_kind != MatchKind.UNMATCHED

    # Ranked list is ordered by attached final scores.
    finals = [page.offer_scores[offer.id].final_score for offer in page.offers]
    assert finals == sorted(finals, reverse=True)

    assert page.highlights
    kinds = {highlight.kind for highlight in page.highlights}
    assert HighlightKind.BEST_OVERALL in kinds
    assert "best_specification" not in {k.value for k in kinds}
    best_id = next(h.offer_id for h in page.highlights if h.kind == HighlightKind.BEST_OVERALL)
    for highlight in page.highlights:
        if highlight.kind != HighlightKind.BEST_OVERALL:
            assert highlight.offer_id != best_id
    highlight_ids = [h.offer_id for h in page.highlights]
    assert len(highlight_ids) == len(set(highlight_ids))


@pytest.mark.asyncio
async def test_low_confidence_offer_stays_listed_but_not_recommended(
    pipeline_orchestrator: SearchOrchestrator,
) -> None:
    session = pipeline_orchestrator.start_session(
        "Samsung Galaxy S26 Ultra 512 GB Black",
        UserPreferences(destination_country="TR", reference_currency="TRY"),
    )
    page = await pipeline_orchestrator.run(session)

    obscure_id = "fixture-obscure-s26-512-black"
    assert any(offer.id == obscure_id for offer in page.offers)
    obscure_score = page.offer_scores[obscure_id]

    assert not is_highlight_eligible(obscure_score)
    assert effective_confidence(obscure_score) < HIGHLIGHT_MIN_CONFIDENCE
    assert obscure_score.reliability_warning is not None
    assert obscure_score.explanation is not None
    assert obscure_score.explanation.caveats
    assert obscure_id not in {h.offer_id for h in page.highlights}

    for highlight in page.highlights:
        assert is_highlight_eligible(page.offer_scores[highlight.offer_id])


@pytest.mark.asyncio
async def test_conversion_failure_drops_that_offer_not_the_search(
    pipeline_orchestrator: SearchOrchestrator,
) -> None:
    inner = pipeline_orchestrator.fx

    class FlakyFx:
        async def convert(self, money, reference_currency):
            if money.currency.upper() == "GBP":
                raise RuntimeError("simulated FX failure")
            return await inner.convert(money, reference_currency)

    pipeline_orchestrator.fx = FlakyFx()  # type: ignore[assignment]
    session = pipeline_orchestrator.start_session(
        "Samsung Galaxy S26 Ultra 512 GB Black",
        UserPreferences(destination_country="TR", reference_currency="TRY"),
    )
    page = await pipeline_orchestrator.run(session)

    assert page.offers
    assert all(offer.id != "fixture-uk-s26-512-black" for offer in page.offers)
    assert all(offer.converted_list_price is not None for offer in page.offers)
    assert any(offer.list_price.currency == "EUR" for offer in page.offers)
    assert session.status == SessionStatus.RANKED
    assert session.failure_reason is None


@pytest.mark.asyncio
async def test_all_conversion_failures_fail_the_search_with_reason(
    pipeline_orchestrator: SearchOrchestrator,
) -> None:
    class DeadFx:
        async def convert(self, money, reference_currency):
            raise RuntimeError(f"no rate for {money.currency}→{reference_currency}")

    pipeline_orchestrator.fx = DeadFx()  # type: ignore[assignment]
    session = pipeline_orchestrator.start_session(
        "Samsung Galaxy S26 Ultra 512 GB Black",
        UserPreferences(destination_country="TR", reference_currency="TRY"),
    )
    with pytest.raises(SearchFailed) as caught:
        await pipeline_orchestrator.run(session)

    assert session.status == SessionStatus.FAILED
    assert session.failure_reason is not None
    assert "currency conversion" in session.failure_reason
    assert "TRY" in session.failure_reason
    assert "no rate" in session.failure_reason
    assert str(caught.value) == session.failure_reason


@pytest.mark.asyncio
async def test_laptop_search_ranks_the_confirmed_build_and_offers_spec_variants(
    pipeline_orchestrator: SearchOrchestrator,
) -> None:
    session = pipeline_orchestrator.start_session(
        "MacBook Air M4 512GB 16GB RAM Sky Blue",
        UserPreferences(destination_country="TR", reference_currency="TRY"),
    )
    assert session.normalized_query is not None
    assert session.normalized_query.needs_confirmation is False

    page = await pipeline_orchestrator.run(session)

    assert page.confirmed_variant is not None
    assert page.confirmed_variant.id == "apple-macbook-air-m4-512-16-us-sky-blue"
    assert page.confirmed_variant.processor == "M4"
    assert page.offers
    # Only the confirmed build is ranked; the 256 GB and 1 TB machines are alternatives.
    for offer in page.offers:
        assert offer.match_kind == MatchKind.IDENTICAL
        assert offer.matched_variant_id == page.confirmed_variant.id

    assert page.alternatives
    alternative_ids = {alt.offer_id for alt in page.alternatives}
    assert alternative_ids & {"fixture-de-mba-m4-256", "fixture-de-mba-m4-1024"}
    assert not alternative_ids & {offer.id for offer in page.offers}

    upgrade = next(alt for alt in page.alternatives if alt.offer_id == "fixture-de-mba-m4-1024")
    assert upgrade.kind.value == "spec_variant"
    assert "storage_gb" in upgrade.differing_attributes
    assert "memory_gb" in upgrade.differing_attributes
    assert "processor" not in upgrade.differing_attributes  # same chip, bigger build
    storage_reason = next(r for r in upgrade.explanation.reasons if r.factor == "storage_gb")
    assert storage_reason.detail == "512 → 1024"

    # landedCostDelta is the difference against the top pick, not the alternative's
    # own total, because the card renders it as "+X vs your pick". A 1 TB machine
    # costs more than a 512 GB one, so the delta is positive and far smaller than
    # either total.
    top_pick = page.offers[0].landed_cost
    assert top_pick is not None
    assert upgrade.landed_cost_delta is not None
    assert upgrade.landed_cost_delta.currency == "TRY"
    assert upgrade.landed_cost_delta.amount > 0
    assert upgrade.landed_cost_delta.amount < top_pick.total.amount

    cost_reason = next(r for r in upgrade.explanation.reasons if r.factor == "cost")
    assert "TRY" in cost_reason.detail
    assert "more than your top pick" in cost_reason.detail


TR_TRY = UserPreferences(destination_country="TR", reference_currency="TRY")

# One family per category, queried the way someone actually types it: by name, with
# the build left out. Each category asks for a different set of identity properties,
# which is the part of the flow that is category-specific.
CONFIRMATION_CASES = [
    pytest.param(
        "smartphone",
        "Samsung Galaxy S26 Ultra",
        {"storage_gb": 512, "colour": "Black"},
        "samsung-galaxy-s26-ultra-512-12-eu-black",
        id="smartphone",
    ),
    pytest.param(
        "laptop",
        "MacBook Air M4",
        {
            "storage_gb": 512,
            "memory_gb": 16,
            "region_version": "US",
            "colour": "Sky Blue",
        },
        "apple-macbook-air-m4-512-16-us-sky-blue",
        id="laptop",
    ),
    pytest.param(
        "tablet",
        "iPad Air 11 M3",
        {
            "storage_gb": 256,
            "connectivity": "Wi-Fi",
            "region_version": "EU",
            "colour": "Space Gray",
        },
        "apple-ipad-air-11-m3-256-8-wifi-eu-space-gray",
        id="tablet",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("category,query,answers,expected_variant", CONFIRMATION_CASES)
async def test_every_category_completes_the_confirm_then_run_flow(
    pipeline_orchestrator: SearchOrchestrator,
    category: str,
    query: str,
    answers: dict,
    expected_variant: str,
) -> None:
    """
    The whole pipeline, per category, from a half-specified query to a Decision Page.

    The other end-to-end tests start from queries that resolve on their own, so they
    never cross the confirm boundary. This one does, and asserts the prompts each
    category raises: a laptop is identified by its processor, a tablet by its
    connectivity, and neither question makes sense for the other.
    """
    session = pipeline_orchestrator.start_session(query, TR_TRY)
    assert session.status is SessionStatus.NEEDS_CONFIRMATION
    assert session.normalized_query is not None
    assert session.normalized_query.extracted["category_id"] == category
    assert {p.property_key for p in session.normalized_query.pending_properties} == set(answers)

    confirmed = pipeline_orchestrator.apply_choices(
        session,
        [
            PropertyChoice(property_key=key, kind=PropertyChoiceKind.VALUE, value=value)
            for key, value in answers.items()
        ],
    )
    assert confirmed.status is SessionStatus.RECEIVED
    assert confirmed.confirmed_variant_id == expected_variant

    page = await pipeline_orchestrator.run(confirmed)

    assert confirmed.status is SessionStatus.RANKED
    assert page.confirmed_variant is not None
    assert page.confirmed_variant.id == expected_variant
    family = pipeline_orchestrator.catalog.get_family(page.confirmed_variant.family_id)
    assert family is not None and family.category_id == category

    # Only the build the user confirmed is ranked, and every offer is priced through
    # to a landed total in their currency — the two things the page is built on.
    assert page.offers
    assert len(page.offer_scores) == len(page.offers)
    for offer in page.offers:
        assert offer.match_kind is MatchKind.IDENTICAL
        assert offer.matched_variant_id == expected_variant
        assert offer.converted_list_price is not None
        assert offer.converted_list_price.reference.currency == "TRY"
        assert offer.landed_cost is not None
        assert offer.landed_cost.total.currency == "TRY"
        assert offer.landed_cost.completeness is not LandedCostCompleteness.UNKNOWN

    scores = [page.offer_scores[offer.id].final_score for offer in page.offers]
    assert scores == sorted(scores, reverse=True)

    assert page.highlights
    best = next(h for h in page.highlights if h.kind is HighlightKind.BEST_OVERALL)
    assert best.offer_id in {offer.id for offer in page.offers}
    assert best.explanation.headline
    assert best.explanation.reasons


@pytest.mark.asyncio
async def test_an_unimportant_property_widens_the_search_instead_of_pinning_a_build(
    pipeline_orchestrator: SearchOrchestrator,
) -> None:
    """
    "Not important" on an optional property is answered by searching wider.

    Colour does not pin a build, so no single variant is confirmed and offers for
    more than one colour are allowed to compete.
    """
    session = pipeline_orchestrator.start_session("Samsung Galaxy S26 Ultra", TR_TRY)

    confirmed = pipeline_orchestrator.apply_choices(
        session,
        [
            PropertyChoice(
                property_key="storage_gb", kind=PropertyChoiceKind.VALUE, value=512
            ),
            PropertyChoice(property_key="colour", kind=PropertyChoiceKind.NOT_IMPORTANT),
        ],
    )

    assert confirmed.search_scope is not None
    assert "colour" in confirmed.search_scope.unconstrained_keys
    assert "colour" not in confirmed.search_scope.constraints

    page = await pipeline_orchestrator.run(confirmed)
    assert page.offers


@pytest.mark.asyncio
async def test_tablet_search_produces_a_decision_page(
    pipeline_orchestrator: SearchOrchestrator,
) -> None:
    session = pipeline_orchestrator.start_session(
        "iPad Air 11 256GB Wi-Fi Space Gray",
        UserPreferences(destination_country="TR", reference_currency="TRY"),
    )
    assert session.normalized_query is not None
    assert session.normalized_query.extracted["connectivity"] == "Wi-Fi"

    page = await pipeline_orchestrator.run(session)

    assert page.confirmed_variant is not None
    assert page.confirmed_variant.id == "apple-ipad-air-11-m3-256-8-wifi-eu-space-gray"
    assert page.offers
    assert page.highlights
    assert page.alternatives


@pytest.mark.asyncio
async def test_import_costs_differ_by_category(
    pipeline_orchestrator: SearchOrchestrator,
) -> None:
    """A phone into Türkiye is registered and dutied; a laptop is neither."""
    preferences = UserPreferences(destination_country="TR", reference_currency="TRY")

    phone_page = await pipeline_orchestrator.run(
        pipeline_orchestrator.start_session(
            "Samsung Galaxy S26 Ultra 512 GB Black", preferences
        )
    )
    laptop_page = await pipeline_orchestrator.run(
        pipeline_orchestrator.start_session(
            "MacBook Air M4 512GB 16GB RAM Sky Blue", preferences
        )
    )

    phone = next(o for o in phone_page.offers if o.id == "fixture-de-s26-512-black")
    laptop = next(o for o in laptop_page.offers if o.id == "fixture-de-mba-m4-512")
    assert phone.landed_cost is not None
    assert laptop.landed_cost is not None

    assert phone.landed_cost.registration_fees is not None
    assert laptop.landed_cost.registration_fees is None

    assert phone.landed_cost.import_duties is not None
    assert laptop.landed_cost.import_duties is not None
    assert phone.landed_cost.import_duties.amount.amount > Decimal("0")
    assert laptop.landed_cost.import_duties.amount.amount == Decimal("0.00")

    assert phone.landed_cost.shipping is not None
    assert laptop.landed_cost.shipping is not None
    assert laptop.landed_cost.shipping.amount.amount > phone.landed_cost.shipping.amount.amount


@pytest.mark.asyncio
async def test_no_offers_fails_the_search_with_reason(
    pipeline_orchestrator: SearchOrchestrator,
) -> None:
    pipeline_orchestrator.adapters = []
    session = pipeline_orchestrator.start_session(
        "Samsung Galaxy S26 Ultra 512 GB Black",
        UserPreferences(destination_country="TR", reference_currency="TRY"),
    )
    with pytest.raises(SearchFailed, match="No offer sources are configured"):
        await pipeline_orchestrator.run(session)
    assert session.status == SessionStatus.FAILED
    assert session.failure_reason is not None
