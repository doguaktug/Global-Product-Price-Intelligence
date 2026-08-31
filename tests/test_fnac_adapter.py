"""Fnac ES adapter tests."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from gp_price_intel.adapters.fnac import FnacEsAdapter, default_fnac_es_source
from gp_price_intel.config import Settings
from gp_price_intel.domain.models import SearchScope, StockStatus


@pytest.fixture
def fnac_fixture_file(tmp_path: Path) -> Path:
    payload = {
        "results": [
            {
                "product_id": "999",
                "title": "Samsung Galaxy S26 Ultra 512 GB Negro",
                "url": "https://www.fnac.es/a999",
                "price": 1399.99,
                "availability": "in_stock",
                "seller_name": "Fnac",
            },
            {
                "product_id": "998",
                "title": "Samsung Galaxy S26 Ultra 512 GB Plata",
                "url": "https://www.fnac.es/a998",
                "price": 1419.99,
                "availability": "out_of_stock",
                "seller_name": "Fnac",
            },
        ]
    }
    path = tmp_path / "fnac.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_fnac_fixture_mode_returns_eur_offers(fnac_fixture_file: Path) -> None:
    adapter = FnacEsAdapter(
        source=default_fnac_es_source(),
        settings=Settings(fnac_api_key=None, data_dir=fnac_fixture_file.parent),
        fixture_path=fnac_fixture_file,
    )
    scope = SearchScope(
        family_id="samsung-galaxy-s26-ultra",
        constraints={"storage_gb": 512},
        variant_ids=["samsung-galaxy-s26-ultra-512-12-eu-black"],
    )

    offers = await adapter.search(scope, destination_country="TR")

    assert len(offers) == 1
    offer = offers[0]
    assert offer.source_id == "fnac-es"
    assert offer.country == "ES"
    assert offer.list_price.currency == "EUR"
    assert offer.list_price.amount == Decimal("1399.99")
    assert offer.stock_status == StockStatus.IN_STOCK
    assert offer.id == "fnac-es-999"


@pytest.mark.asyncio
async def test_fnac_filters_out_of_stock(fnac_fixture_file: Path) -> None:
    adapter = FnacEsAdapter(
        settings=Settings(fnac_api_key=None, data_dir=fnac_fixture_file.parent),
        fixture_path=fnac_fixture_file,
    )
    scope = SearchScope(family_id="samsung-galaxy-s26-ultra", constraints={})

    offers = await adapter.search(scope, destination_country="TR")

    assert all(o.stock_status != StockStatus.OUT_OF_STOCK for o in offers)


@pytest.mark.asyncio
async def test_fnac_live_mode_uses_http_client(fnac_fixture_file: Path) -> None:
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
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://partner.example")
    adapter = FnacEsAdapter(
        settings=Settings(
            fnac_api_key="test-key",
            fnac_api_base_url="https://partner.example",
            data_dir=fnac_fixture_file.parent,
        ),
        client=client,
        fixture_path=fnac_fixture_file,
    )
    scope = SearchScope(family_id="samsung-galaxy-s26-ultra", constraints={"storage_gb": 512})

    offers = await adapter.search(scope, destination_country="TR")

    assert len(offers) == 1
    assert offers[0].id == "fnac-es-live-1"
    assert offers[0].list_price.amount == Decimal("1299.0")
