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
from gp_price_intel.domain.models import HighlightKind, MatchKind, UserPreferences
from gp_price_intel.fx.service import FxService
from gp_price_intel.orchestrator.search import SearchOrchestrator
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
    assert HighlightKind.LOWEST_LIST_PRICE in kinds


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
