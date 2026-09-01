"""End-to-end orchestrator pipeline tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from gp_price_intel.adapters.fixture import FixtureAdapter
from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.config import Settings
from gp_price_intel.domain.models import HighlightKind, MatchKind, UserPreferences
from gp_price_intel.fx.service import FxService
from gp_price_intel.orchestrator.search import SearchOrchestrator

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

    repo_root = Path(__file__).resolve().parents[1]
    for name in ("categories.json", "families.json", "variants.json"):
        src = repo_root / "data" / "catalog" / name
        (data_dir / "catalog" / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    offers_src = repo_root / "data" / "fixtures" / "offers.json"
    (data_dir / "fixtures" / "offers.json").write_text(
        offers_src.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

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
    fixture_adapter = FixtureAdapter(
        catalog=catalog,
        settings=settings,
        fixture_path=data_dir / "fixtures" / "offers.json",
        sources=[],
    )

    return SearchOrchestrator(
        catalog=catalog,
        adapters=[fixture_adapter],
        fx=fx,
    )


@pytest.mark.asyncio
async def test_pipeline_run_returns_ranked_offers_and_highlights(
    pipeline_orchestrator: SearchOrchestrator,
) -> None:
    session = pipeline_orchestrator.start_session(
        "Samsung Galaxy S26 Ultra 512 GB Black",
        UserPreferences(destination_country="TR", reference_currency="TRY"),
    )
    assert session.normalized_query is not None
    assert session.normalized_query.needs_confirmation is False

    page = await pipeline_orchestrator.run(session)

    assert len(page.offers) >= 4
    assert all(offer.match_kind == MatchKind.IDENTICAL for offer in page.offers[:4])
    assert page.confirmed_variant is not None
    assert page.highlights
    kinds = {highlight.kind for highlight in page.highlights}
    assert HighlightKind.BEST_OVERALL in kinds
    assert HighlightKind.LOWEST_LIST_PRICE in kinds
