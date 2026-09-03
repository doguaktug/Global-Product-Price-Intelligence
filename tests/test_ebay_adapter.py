"""eBay adapter tests."""

from __future__ import annotations

import base64
from decimal import Decimal

import httpx
import pytest

from gp_price_intel.adapters.ebay import EbayAdapter
from gp_price_intel.config import Settings
from gp_price_intel.domain.models import SearchScope, StockStatus


@pytest.mark.asyncio
async def test_ebay_search_parses_item_summary() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "token-123", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer token-123"
        return httpx.Response(
            200,
            json={
                "itemSummaries": [
                    {
                        "itemId": "v1|123|0",
                        "title": "Samsung Galaxy S26 Ultra 512GB",
                        "itemWebUrl": "https://www.ebay.com/itm/123",
                        "price": {"value": "1099.99", "currency": "USD"},
                        "seller": {"username": "phone-deals", "feedbackPercentage": "98.5"},
                        "estimatedAvailabilities": [
                            {"estimatedAvailabilityStatus": "IN_STOCK"}
                        ],
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    adapter = EbayAdapter(
        settings=Settings(ebay_app_id="app", ebay_cert_id="cert"),
        client=client,
    )
    scope = SearchScope(
        family_id="samsung-galaxy-s26-ultra",
        constraints={"storage_gb": 512},
        variant_ids=["samsung-galaxy-s26-ultra-512-12-eu-black"],
    )

    offers = await adapter.search(scope, destination_country="TR")

    assert len(offers) == 1
    assert offers[0].retailer_sku == "v1|123|0"
    assert offers[0].list_price.amount == Decimal("1099.99")
    assert offers[0].stock_status == StockStatus.IN_STOCK


@pytest.mark.asyncio
async def test_ebay_oauth_uses_basic_auth() -> None:
    seen_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            seen_auth.append(request.headers["Authorization"])
            return httpx.Response(200, json={"access_token": "t", "expires_in": 60})
        return httpx.Response(200, json={"itemSummaries": []})

    adapter = EbayAdapter(
        settings=Settings(ebay_app_id="my-app", ebay_cert_id="my-cert"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await adapter.search(SearchScope(family_id="samsung-galaxy-s26-ultra"), "TR")

    expected = "Basic " + base64.b64encode(b"my-app:my-cert").decode()
    assert seen_auth == [expected]
