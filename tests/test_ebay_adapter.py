"""eBay adapter structural tests."""

from __future__ import annotations

import base64

import httpx
import pytest

from gp_price_intel.adapters.base import SourceFetchError
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
                    "seller": {
                        "username": "s",
                        "feedbackPercentage": "98.5",
                        "feedbackScore": 4200,
                    },
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
    # The family name already ends in "M4"; repeating it searches a phrase no seller
    # writes and quietly narrows the result set.
    assert laptop.count("M4") == 1
    assert laptop == "Apple MacBook Air M4 512GB 16GB RAM"


@pytest.mark.asyncio
async def test_ebay_storage_query_uses_tb_when_sellers_write_tb() -> None:
    """Catalog stores 2048 GB; eBay titles say 2TB. Searching 2048GB misses them."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 60})
        seen.append(request.url.params.get("q", ""))
        return httpx.Response(200, json={"itemSummaries": []})

    adapter = _adapter(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await adapter.search(
        SearchScope(
            family_id="asus-rog-zephyrus-g14",
            constraints={
                "processor": "AMD Ryzen 9",
                "storage_gb": 2048,
                "memory_gb": 32,
            },
        ),
        "TR",
    )
    await adapter.search(
        SearchScope(
            family_id="asus-rog-zephyrus-g14",
            constraints={"storage_gb": 1024, "memory_gb": 16},
        ),
        "TR",
    )

    two_tb, one_tb = seen
    assert two_tb == "ASUS ROG Zephyrus G14 AMD Ryzen 9 2TB 32GB RAM"
    assert "2048GB" not in two_tb
    assert one_tb == "ASUS ROG Zephyrus G14 1TB 16GB RAM"
    assert "1024GB" not in one_tb


@pytest.mark.asyncio
async def test_ebay_search_summary_without_availability_is_in_stock() -> None:
    """
    Browse search summaries omit estimatedAvailabilities.

    Treating that as unknown stock made every live eBay offer miss the highlight
    confidence floor once landed-cost completeness was applied.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "token-123", "expires_in": 3600})
        return httpx.Response(
            200,
            json={
                "itemSummaries": [
                    {
                        "itemId": "v1|bare|0",
                        "title": "Samsung Galaxy S26 Ultra 512GB",
                        "itemWebUrl": "https://www.ebay.com/itm/bare",
                        "price": {"value": "1099.99", "currency": "USD"},
                        "seller": {
                            "username": "phone-deals",
                            "feedbackPercentage": "99.6",
                            "feedbackScore": 68507,
                        },
                    }
                ]
            },
        )

    offers = await _adapter(httpx.AsyncClient(transport=httpx.MockTransport(handler))).search(
        SearchScope(family_id="samsung-galaxy-s26-ultra", constraints={"storage_gb": 512}),
        "TR",
    )
    assert len(offers) == 1
    assert offers[0].stock_status == StockStatus.IN_STOCK


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


@pytest.mark.asyncio
async def test_ebay_drops_used_and_refurbished_listings() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "token-123", "expires_in": 3600})
        from urllib.parse import unquote

        assert "conditions:{NEW" in unquote(str(request.url))
        return httpx.Response(
            200,
            json={
                "itemSummaries": [
                    {
                        "itemId": "v1|new|0",
                        "title": "Samsung Galaxy S26 Ultra 512GB Black",
                        "condition": "New",
                        "itemWebUrl": "https://www.ebay.com/itm/new",
                        "price": {"value": "1099.99", "currency": "USD"},
                        "seller": {
                            "username": "new-shop",
                            "feedbackPercentage": "99.0",
                            "feedbackScore": 100,
                        },
                        "estimatedAvailabilities": [
                            {"estimatedAvailabilityStatus": "IN_STOCK"}
                        ],
                    },
                    {
                        "itemId": "v1|used|0",
                        "title": "Samsung Galaxy S26 Ultra 512GB Black",
                        "condition": "Used",
                        "itemWebUrl": "https://www.ebay.com/itm/used",
                        "price": {"value": "699.99", "currency": "USD"},
                        "seller": {
                            "username": "used-shop",
                            "feedbackPercentage": "98.0",
                            "feedbackScore": 50,
                        },
                        "estimatedAvailabilities": [
                            {"estimatedAvailabilityStatus": "IN_STOCK"}
                        ],
                    },
                    {
                        "itemId": "v1|refurb|0",
                        "title": "Samsung Galaxy S26 Ultra 512GB",
                        "condition": "Certified refurbished",
                        "itemWebUrl": "https://www.ebay.com/itm/refurb",
                        "price": {"value": "799.99", "currency": "USD"},
                        "seller": {
                            "username": "refurb-shop",
                            "feedbackPercentage": "97.0",
                            "feedbackScore": 40,
                        },
                        "estimatedAvailabilities": [
                            {"estimatedAvailabilityStatus": "IN_STOCK"}
                        ],
                    },
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
    assert "new" in offers[0].id
    assert "used" not in offers[0].id
    assert offers[0].condition.value == "new"


@pytest.mark.asyncio
async def test_ebay_keeps_used_when_include_used() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "token-123", "expires_in": 3600})
        from urllib.parse import unquote

        assert "conditions:{NEW" not in unquote(str(request.url))
        return httpx.Response(
            200,
            json={
                "itemSummaries": [
                    {
                        "itemId": "v1|used|0",
                        "title": "Samsung Galaxy S26 Ultra 512GB Black",
                        "condition": "Used",
                        "itemWebUrl": "https://www.ebay.com/itm/used",
                        "price": {"value": "699.99", "currency": "USD"},
                        "seller": {
                            "username": "used-shop",
                            "feedbackPercentage": "98.0",
                            "feedbackScore": 50,
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
        include_used=True,
    )
    assert len(offers) == 1
    assert offers[0].condition.value == "used"


@pytest.mark.asyncio
async def test_rejected_credentials_raise_instead_of_looking_like_an_empty_shelf() -> None:
    """
    A 401 must not be reported as "no offers found".

    Swallowing it leaves the Decision Page saying the product is unlisted when the
    real problem is the keys, which is the one thing the operator has to be told.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": "invalid_client", "error_description": "client authentication failed"},
        )

    with pytest.raises(SourceFetchError) as caught:
        await _adapter(httpx.AsyncClient(transport=httpx.MockTransport(handler))).search(
            SearchScope(family_id="samsung-galaxy-s26-ultra", constraints={"storage_gb": 512}),
            "TR",
        )

    assert "401" in str(caught.value)
    assert "client authentication failed" in str(caught.value)


@pytest.mark.asyncio
async def test_a_failing_browse_search_reports_ebays_own_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 60})
        return httpx.Response(
            429,
            json={"errors": [{"longMessage": "Application request limit reached"}]},
        )

    with pytest.raises(SourceFetchError, match="Application request limit reached"):
        await _adapter(httpx.AsyncClient(transport=httpx.MockTransport(handler))).search(
            SearchScope(family_id="samsung-galaxy-s26-ultra", constraints={"storage_gb": 512}),
            "TR",
        )


@pytest.mark.asyncio
async def test_an_empty_shelf_is_not_an_error() -> None:
    """eBay answering "nothing here" is a valid answer, not a failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 60})
        return httpx.Response(200, json={"itemSummaries": []})

    offers = await _adapter(httpx.AsyncClient(transport=httpx.MockTransport(handler))).search(
        SearchScope(family_id="samsung-galaxy-s26-ultra", constraints={"storage_gb": 512}),
        "TR",
    )

    assert offers == []


def test_the_sandbox_flag_picks_the_sandbox_host() -> None:
    """
    Sandbox is a separate eBay with its own keys and virtually no inventory, so which
    host was used has to be visible — an empty sandbox result means nothing.
    """
    live = EbayAdapter(settings=Settings(ebay_app_id="a", ebay_cert_id="c"))
    sandbox = EbayAdapter(
        settings=Settings(ebay_app_id="a", ebay_cert_id="c", ebay_sandbox=True)
    )

    assert live.api_host() == "api.ebay.com"
    assert sandbox.api_host() == "api.sandbox.ebay.com"


@pytest.mark.asyncio
async def test_listings_that_arrive_are_countable_before_they_are_filtered() -> None:
    """
    "eBay sent nothing" and "eBay sent listings we threw away" are different faults.

    The offer list alone cannot tell them apart, so the raw summaries stay reachable.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 60})
        return httpx.Response(
            200,
            json={
                "itemSummaries": [
                    # No price block, so it cannot become an Offer.
                    {
                        "itemId": "v1|1|0",
                        "title": "Samsung Galaxy S26 512GB",
                        "itemWebUrl": "https://www.ebay.com/itm/1",
                    }
                ]
            },
        )

    adapter = _adapter(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    scope = SearchScope(family_id="samsung-galaxy-s26-ultra", constraints={"storage_gb": 512})

    assert len(await adapter.fetch_listings(scope)) == 1
    assert await adapter.search(scope, "TR") == []


def test_missing_credentials_are_reported_rather_than_hidden() -> None:
    without = EbayAdapter(settings=Settings(ebay_app_id=None, ebay_cert_id=None))
    assert without.is_configured() is False
    assert "EBAY_APP_ID" in (without.unavailable_reason() or "")

    with_keys = EbayAdapter(settings=Settings(ebay_app_id="app", ebay_cert_id="cert"))
    assert with_keys.unavailable_reason() is None
