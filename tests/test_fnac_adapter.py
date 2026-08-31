"""Fnac ES adapter tests."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from gp_price_intel.adapters.fnac import FnacEsAdapter, default_fnac_es_source
from gp_price_intel.config import Settings
from gp_price_intel.domain.models import SearchScope, StockStatus


@pytest.mark.asyncio
async def test_fnac_without_credentials_returns_empty() -> None:
    adapter = FnacEsAdapter(settings=Settings(fnac_api_key=None, fnac_api_base_url=None))
    scope = SearchScope(family_id="samsung-galaxy-s26-ultra", constraints={"storage_gb": 512})

    offers = await adapter.search(scope, destination_country="TR")

    assert offers == []
    assert adapter.is_configured() is False


@pytest.mark.asyncio
async def test_fnac_live_search_parses_offers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/search")
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "product_id": "live-1",
                        "title": "Samsung Galaxy S26 Ultra 512 GB",
                        "url": "https://www.fnac.es/live-1",
                        "price": 1299.0,
                        "availability": "in_stock",
                    },
                    {
                        "product_id": "live-2",
                        "title": "Samsung Galaxy S26 Ultra 512 GB Plata",
                        "url": "https://www.fnac.es/live-2",
                        "price": 1319.0,
                        "availability": "out_of_stock",
                    },
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://partner.example")
    adapter = FnacEsAdapter(
        source=default_fnac_es_source(),
        settings=Settings(
            fnac_api_key="test-key",
            fnac_api_base_url="https://partner.example",
        ),
        client=client,
    )
    scope = SearchScope(family_id="samsung-galaxy-s26-ultra", constraints={"storage_gb": 512})

    offers = await adapter.search(scope, destination_country="TR")

    assert len(offers) == 1
    assert offers[0].id == "fnac-es-live-1"
    assert offers[0].country == "ES"
    assert offers[0].list_price.amount == Decimal("1299.0")
    assert offers[0].stock_status == StockStatus.IN_STOCK
