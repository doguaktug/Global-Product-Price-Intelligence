"""eBay adapter structural tests."""

from __future__ import annotations

import base64

import httpx
import pytest

from gp_price_intel.adapters.ebay import EbayAdapter
from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.config import Settings
from gp_price_intel.domain.models import MatchKind, SearchScope, StockStatus
from gp_price_intel.matching.matcher import ProductMatcher


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


def _listing_response(request: httpx.Request, *, title: str, aspects: list | None = None):
    if request.url.path.endswith("/oauth2/token"):
        return httpx.Response(200, json={"access_token": "token-123", "expires_in": 3600})
    return httpx.Response(
        200,
        json={
            "itemSummaries": [
                {
                    "itemId": "v1|999|0",
                    "title": title,
                    "itemWebUrl": "https://www.ebay.com/itm/999",
                    "price": {"value": "1099.99", "currency": "USD"},
                    "seller": {"username": "s", "feedbackPercentage": "98.5", "feedbackScore": 4200},
                    "estimatedAvailabilities": [{"estimatedAvailabilityStatus": "IN_STOCK"}],
                    **({"localizedAspects": aspects} if aspects else {}),
                }
            ]
        },
    )


@pytest.mark.asyncio
async def test_ebay_listing_title_becomes_matchable_specs() -> None:
    """
    Without this the one live adapter contributes nothing.

    eBay publishes no structured specs and no identifier we share, so if the title
    is not parsed the offer reaches the matcher with only a title and a condition,
    matches nothing, and is dropped from every search.
    """
    scope = SearchScope(
        family_id="samsung-galaxy-s26-ultra",
        constraints={"storage_gb": 512},
        variant_ids=["samsung-galaxy-s26-ultra-512-12-eu-black"],
    )
    transport = httpx.MockTransport(
        lambda request: _listing_response(
            request,
            title="Samsung Galaxy S26 Ultra 512GB 12GB RAM EU Unlocked Black",
        )
    )

    offers = await _adapter(httpx.AsyncClient(transport=transport)).search(
        scope, destination_country="TR"
    )

    specs = {spec.key: spec.value for spec in offers[0].raw_specs}
    assert specs["storage_gb"] == 512
    assert specs["memory_gb"] == 12
    assert specs["region_version"] == "EU"
    assert specs["colour"] == "Black"


@pytest.mark.asyncio
async def test_ebay_offer_parsed_from_its_title_survives_matching() -> None:
    """The point of parsing the title: the offer is no longer dropped as unmatched."""
    scope = SearchScope(
        family_id="samsung-galaxy-s26-ultra",
        constraints={"storage_gb": 512},
        variant_ids=["samsung-galaxy-s26-ultra-512-12-eu-black"],
    )
    transport = httpx.MockTransport(
        lambda request: _listing_response(
            request,
            title="Samsung Galaxy S26 Ultra 512GB 12GB RAM EU Unlocked Black",
        )
    )

    offers = await _adapter(httpx.AsyncClient(transport=transport)).search(
        scope, destination_country="TR"
    )
    matched = ProductMatcher(CatalogRepository()).match(offers, scope)[0]

    assert matched.match_kind == MatchKind.IDENTICAL
    assert matched.matched_variant_id == "samsung-galaxy-s26-ultra-512-12-eu-black"


@pytest.mark.asyncio
async def test_ebay_seller_filled_aspects_go_through_the_unit_parser() -> None:
    """Aspects arrive as display strings in whatever unit the seller chose."""
    transport = httpx.MockTransport(
        lambda request: _listing_response(
            request,
            title="Samsung Galaxy S26 Ultra 512GB",
            aspects=[
                {"name": "Battery Capacity", "value": ["5,000 mAh"]},
                {"name": "Screen Size", "value": ["6.9 in"]},
            ],
        )
    )

    offers = await _adapter(httpx.AsyncClient(transport=transport)).search(
        SearchScope(family_id="samsung-galaxy-s26-ultra", constraints={"storage_gb": 512}),
        destination_country="TR",
    )

    specs = {spec.key: spec.value for spec in offers[0].raw_specs}
    assert specs["battery_mah"] == 5000
    assert specs["display_inch"] == 6.9


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
async def test_ebay_query_carries_the_category_identity_specs() -> None:
    """RAM is the main laptop differentiator, so it cannot be dropped from the query."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 60})
        seen.append(request.url.params.get("q", ""))
        return httpx.Response(200, json={"itemSummaries": []})

    adapter = _adapter(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await adapter.search(
        SearchScope(
            family_id="apple-macbook-air-m4",
            constraints={"processor": "M4", "storage_gb": 512, "memory_gb": 16},
        ),
        "TR",
    )
    await adapter.search(
        SearchScope(
            family_id="apple-ipad-air-11-m3",
            constraints={"storage_gb": 256, "connectivity": "Wi-Fi + Cellular"},
        ),
        "TR",
    )

    laptop, tablet = seen
    assert "Apple MacBook Air M4" in laptop
    assert "512GB" in laptop
    assert "16GB RAM" in laptop
    assert "256GB" in tablet
    assert "Wi-Fi + Cellular" in tablet


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
