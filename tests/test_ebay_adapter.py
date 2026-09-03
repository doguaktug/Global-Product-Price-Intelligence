"""eBay adapter structural tests."""

from __future__ import annotations

import base64

import httpx
import pytest

from gp_price_intel.adapters.ebay import EbayAdapter
from gp_price_intel.config import Settings
from gp_price_intel.domain.models import SearchScope, StockStatus


def _adapter(client: httpx.AsyncClient) -> EbayAdapter:
    return EbayAdapter(
        settings=Settings(ebay_app_id="app", ebay_cert_id="cert"),
        client=client,
    )


@pytest.mark.asyncio
async def test_ebay_search_maps_required_offer_fields() -> None:
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
                        "seller": {
                            "username": "phone-deals",
                            "feedbackPercentage": "98.5",
                            "feedbackScore": 4200,
                        },
                        "estimatedAvailabilities": [
                            {"estimatedAvailabilityStatus": "IN_STOCK"}
                        ],
                    }
                ]
            },
        )

    offers = await _adapter(httpx.AsyncClient(transport=httpx.MockTransport(handler))).search(
        SearchScope(
            family_id="samsung-galaxy-s26-ultra",
            constraints={"storage_gb": 512},
            variant_ids=["samsung-galaxy-s26-ultra-512-12-eu-black"],
        ),
        destination_country="TR",
    )

    assert len(offers) == 1
    offer = offers[0]
    assert offer.retailer_sku == "v1|123|0"
    assert offer.list_price.currency == "USD"
    assert offer.stock_status == StockStatus.IN_STOCK
    assert offer.seller.review_count == 4200
    assert offer.seller.reliability is not None
    assert 0.0 <= offer.data_confidence <= 1.0


@pytest.mark.asyncio
async def test_ebay_sparse_reviews_reduce_confidence_vs_established_seller() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "token-123", "expires_in": 3600})
        return httpx.Response(
            200,
            json={
                "itemSummaries": [
                    {
                        "itemId": "v1|new|0",
                        "title": "S26 Ultra new seller",
                        "itemWebUrl": "https://www.ebay.com/itm/new",
                        "price": {"value": "900.00", "currency": "USD"},
                        "seller": {
                            "username": "brand-new",
                            "feedbackPercentage": "50.0",
                            "feedbackScore": 1,
                        },
                    },
                    {
                        "itemId": "v1|vet|0",
                        "title": "S26 Ultra established seller",
                        "itemWebUrl": "https://www.ebay.com/itm/vet",
                        "price": {"value": "1100.00", "currency": "USD"},
                        "seller": {
                            "username": "vet-seller",
                            "feedbackPercentage": "99.0",
                            "feedbackScore": 50_000,
                        },
                    },
                ]
            },
        )

    offers = await _adapter(httpx.AsyncClient(transport=httpx.MockTransport(handler))).search(
        SearchScope(family_id="samsung-galaxy-s26-ultra"),
        "TR",
    )
    by_id = {offer.retailer_sku: offer for offer in offers}
    assert by_id["v1|new|0"].data_confidence < by_id["v1|vet|0"].data_confidence


@pytest.mark.asyncio
async def test_ebay_oauth_uses_basic_auth() -> None:
    seen_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            seen_auth.append(request.headers["Authorization"])
            return httpx.Response(200, json={"access_token": "t", "expires_in": 60})
        return httpx.Response(200, json={"itemSummaries": []})

    await EbayAdapter(
        settings=Settings(ebay_app_id="my-app", ebay_cert_id="my-cert"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ).search(SearchScope(family_id="samsung-galaxy-s26-ultra"), "TR")

    expected = "Basic " + base64.b64encode(b"my-app:my-cert").decode()
    assert seen_auth == [expected]
