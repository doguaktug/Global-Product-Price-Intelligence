"""End-to-end orchestrator pipeline invariants."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from gp_price_intel.adapters.base import SourceAdapter, SourceFetchError
from gp_price_intel.adapters.fixture import FixtureAdapter
from gp_price_intel.adapters.registry import load_sources
from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.config import Settings
from gp_price_intel.domain.models import (
    AcquisitionMethod,
    HighlightKind,
    ItemCondition,
    MatchKind,
    Money,
    NormalizedSpec,
    Offer,
    Seller,
    SessionStatus,
    Source,
    SourceKind,
    StockStatus,
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
    highlight_ids = [h.offer_id for h in page.highlights]
    assert 1 <= len(set(highlight_ids)) <= 5


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
    assert {offer.id for offer in page.alternative_offers} == alternative_ids
    assert 1 <= len(page.alternatives) <= 3

    upgrade = next(alt for alt in page.alternatives if alt.offer_id == "fixture-de-mba-m4-1024")
    assert upgrade.kind.value == "spec_variant"
    assert "storage_gb" in upgrade.differing_attributes
    assert "memory_gb" in upgrade.differing_attributes
    assert "processor" not in upgrade.differing_attributes  # same chip, bigger build
    storage_reason = next(r for r in upgrade.explanation.reasons if r.factor == "storage_gb")
    assert storage_reason.detail == "512 GB → 1024 GB"

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


@pytest.mark.asyncio
async def test_catalog_families_without_legacy_fixtures_still_produce_a_page(
    pipeline_orchestrator: SearchOrchestrator,
) -> None:
    """Families that used to depend on live eBay now have fixture Decision Pages."""
    queries = (
        "Apple iPhone 16 Plus 256 GB White",
        "Samsung Galaxy S25 Ultra 256 GB Black",
        "ASUS Zenbook 14 OLED 512GB 16GB Intel Core Ultra 7 Foggy Silver",
        "iPad 11 A16 128GB Wi-Fi Blue",
    )
    for query in queries:
        session = pipeline_orchestrator.start_session(
            query,
            UserPreferences(destination_country="TR", reference_currency="TRY"),
        )
        assert session.normalized_query is not None, query
        assert session.normalized_query.needs_confirmation is False, query
        page = await pipeline_orchestrator.run(session)
        assert page.offers, query
        assert page.confirmed_variant is not None, query
        assert page.highlights, query


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


@pytest.mark.asyncio
async def test_a_source_that_refused_the_request_is_named_not_hidden(
    pipeline_orchestrator: SearchOrchestrator,
) -> None:
    """A dead source must not be reported as an empty shelf."""

    class RefusingAdapter(FixtureAdapter):
        async def search(self, scope, destination_country, **kwargs):  # type: ignore[no-untyped-def]
            raise SourceFetchError("eBay OAuth rejected the credentials (HTTP 401)")

    refusing = RefusingAdapter(
        catalog=CatalogRepository(catalog_dir=pipeline_orchestrator.catalog.catalog_dir),
        sources=load_sources(),
    )
    refusing.source = refusing.source.model_copy(update={"id": "ebay"})
    pipeline_orchestrator.adapters = [refusing]

    session = pipeline_orchestrator.start_session(
        "Samsung Galaxy S26 Ultra 512 GB Black",
        UserPreferences(destination_country="TR", reference_currency="TRY"),
    )
    with pytest.raises(SearchFailed) as caught:
        await pipeline_orchestrator.run(session)

    assert "Sources failed" in caught.value.reason
    assert "HTTP 401" in caught.value.reason


@pytest.mark.asyncio
async def test_an_unsearched_source_is_disclosed_on_an_empty_page(
    pipeline_orchestrator: SearchOrchestrator,
) -> None:
    """
    "No offers" should not imply the product is unlisted everywhere.

    With eBay switched off, the only honest empty page says which source was skipped.
    """

    class SwitchedOffAdapter(FixtureAdapter):
        async def search(self, scope, destination_country, **kwargs):  # type: ignore[no-untyped-def]
            return []

        def unavailable_reason(self) -> str | None:
            return "eBay was not searched: EBAY_APP_ID / EBAY_CERT_ID are not set"

    pipeline_orchestrator.adapters = [
        SwitchedOffAdapter(
            catalog=CatalogRepository(catalog_dir=pipeline_orchestrator.catalog.catalog_dir),
            sources=load_sources(),
        )
    ]

    session = pipeline_orchestrator.start_session(
        "Samsung Galaxy S26 Ultra 512 GB Black",
        UserPreferences(destination_country="TR", reference_currency="TRY"),
    )
    with pytest.raises(SearchFailed) as caught:
        await pipeline_orchestrator.run(session)

    assert "EBAY_APP_ID" in caught.value.reason


class _StaticAdapter(SourceAdapter):
    """Return a fixed offer list so a search can be live-shaped without eBay."""

    def __init__(self, offers: list[Offer], source: Source) -> None:
        self._offers = offers
        self.source = source

    async def search(self, scope, destination_country, **kwargs):  # type: ignore[no-untyped-def]
        return list(self._offers)

    def known_sources(self) -> list[Source]:
        return [self.source]


def _g14_listing(
    offer_id: str,
    *,
    storage_gb: int,
    memory_gb: int,
    price: str,
) -> Offer:
    return Offer(
        id=offer_id,
        source_id="live-test",
        seller=Seller(name="swingcomputers", reliability=0.996, review_count=68507),
        country="TR",
        listing_title=(
            f"ASUS ROG Zephyrus G14 AMD Ryzen 9 {storage_gb}GB SSD {memory_gb}GB RAM"
        ),
        listing_url=f"https://example.com/{offer_id}",
        list_price=Money(amount=Decimal(price), currency="TRY"),
        stock_status=StockStatus.IN_STOCK,
        condition=ItemCondition.NEW,
        data_confidence=0.85,
        collected_at=datetime.now(timezone.utc),
        raw_specs=[
            NormalizedSpec(key="storage_gb", value=storage_gb, raw_text=str(storage_gb)),
            NormalizedSpec(key="memory_gb", value=memory_gb, raw_text=str(memory_gb)),
            NormalizedSpec(key="processor", value="AMD Ryzen 9", raw_text="AMD Ryzen 9"),
            NormalizedSpec(key="region_version", value="US", raw_text="US"),
        ],
    )


@pytest.mark.asyncio
async def test_similar_only_live_listings_still_get_highlight_cards(
    pipeline_orchestrator: SearchOrchestrator,
) -> None:
    """
    The G14 2 TB search: eBay has 1 TB listings, none of the confirmed SKU.

    Those similar offers used to land only in "list all other options" because
    nothing was identical (so alternatives were emptied) and they failed the
    highlight floor. The Decision Page must still recommend the closest build.
    """
    source = Source(
        id="live-test",
        display_name="Live test",
        country="TR",
        kind=SourceKind.MARKETPLACE,
        reliability=0.72,
        acquisition_method=AcquisitionMethod.API,
    )
    pipeline_orchestrator.adapters = [
        _StaticAdapter(
            [
                _g14_listing("g14-1tb-a", storage_gb=1024, memory_gb=32, price="10000"),
                _g14_listing("g14-1tb-b", storage_gb=1024, memory_gb=32, price="12000"),
            ],
            source,
        )
    ]

    session = pipeline_orchestrator.start_session(
        "ROG Zephyrus G14 2048GB",
        UserPreferences(destination_country="TR", reference_currency="TRY"),
    )
    assert session.normalized_query is not None
    assert session.normalized_query.needs_confirmation is False

    page = await pipeline_orchestrator.run(session)

    assert page.confirmed_variant is not None
    assert page.confirmed_variant.storage_gb == 2048
    assert page.offers
    assert {offer.match_kind for offer in page.offers} == {MatchKind.SIMILAR}
    assert page.highlights
    assert {h.offer_id for h in page.highlights} <= {offer.id for offer in page.offers}
    for highlight in page.highlights:
        assert is_highlight_eligible(page.offer_scores[highlight.offer_id])
        assert "closest available match" in " ".join(highlight.explanation.caveats).casefold()
