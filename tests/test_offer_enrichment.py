"""
Listings whose titles do not state the build must still resolve to one variant.

Fixture titles spell the build out ("... 512 GB 16 GB Sky Blue"), so the attribute
matcher has everything it needs. Marketplace and scraped titles are written to sell,
not to specify, and the specs often live only in the listing's item-specifics table.
These tests cover that second source of evidence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from gp_price_intel.adapters.base import SourceAdapter
from gp_price_intel.adapters.ebay import MAX_DETAIL_LOOKUPS, EbayAdapter
from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.config import Settings
from gp_price_intel.domain.models import (
    AcquisitionMethod,
    MatchKind,
    Money,
    Offer,
    SearchScope,
    Seller,
    Source,
    SourceKind,
    StockStatus,
)
from gp_price_intel.matching.matcher import ProductMatcher, needs_more_evidence

CONFIRMED = "samsung-galaxy-s26-ultra-512-12-eu-black"
SCOPE = SearchScope(
    family_id="samsung-galaxy-s26-ultra",
    constraints={"storage_gb": 512, "memory_gb": 12, "colour": "Black"},
    variant_ids=[CONFIRMED],
)


def _summary(item_id: str, title: str) -> dict:
    return {
        "itemId": item_id,
        "title": title,
        "itemWebUrl": f"https://www.ebay.com/itm/{item_id}",
        "price": {"value": "1000.00", "currency": "USD"},
        "seller": {"username": "seller", "feedbackPercentage": "99.0", "feedbackScore": 500},
        "estimatedAvailabilities": [{"estimatedAvailabilityStatus": "IN_STOCK"}],
    }


def _transport(summaries: list[dict], details: dict[str, dict]) -> httpx.MockTransport:
    """Stub Browse: `item_summary/search` carries no aspects, `item/{id}` does."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if "/item/" in path:
            return httpx.Response(200, json=details.get(path.split("/item/")[-1], {}))
        return httpx.Response(200, json={"itemSummaries": summaries})

    return httpx.MockTransport(handler)


def _adapter(transport: httpx.MockTransport) -> EbayAdapter:
    return EbayAdapter(
        settings=Settings(ebay_app_id="app", ebay_cert_id="cert"),
        client=httpx.AsyncClient(transport=transport),
        catalog=CatalogRepository(),
    )


async def _resolve(summaries: list[dict], details: dict[str, dict]) -> list[Offer]:
    """Run search → match → enrich unresolved → re-match, as the orchestrator does."""
    adapter = _adapter(_transport(summaries, details))
    matcher = ProductMatcher(CatalogRepository())
    try:
        matched = matcher.match(await adapter.search(SCOPE, "TR"), SCOPE)
        stuck = [offer for offer in matched if needs_more_evidence(offer)]
        replaced = {o.id: o for o in matcher.match(await adapter.enrich(stuck, SCOPE), SCOPE)}
        return [replaced.get(offer.id, offer) for offer in matched]
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_a_title_with_no_specs_is_placed_from_the_item_specifics() -> None:
    """The seller typed storage and RAM into named fields, just not into the title."""
    offers = await _resolve(
        [_summary("v1|1|0", "Samsung Galaxy S26 Ultra")],
        {
            "v1|1|0": {
                "localizedAspects": [
                    {"name": "Storage Capacity", "value": ["512 GB"]},
                    {"name": "RAM", "value": ["12 GB"]},
                    {"name": "Colour", "value": ["Black"]},
                ]
            }
        },
    )

    assert offers[0].match_kind == MatchKind.IDENTICAL
    assert offers[0].matched_variant_id == CONFIRMED


@pytest.mark.asyncio
async def test_a_partly_specified_title_stops_being_ambiguous() -> None:
    """
    "512GB" alone fits every 512 GB build in the family, in either colour.

    That lands as `similar`, which keeps the offer out of the ranked list even though
    it is the confirmed build. The declared RAM and colour settle it.
    """
    summaries = [_summary("v1|2|0", "Samsung Galaxy S26 Ultra 512GB")]

    without = await _resolve(summaries, {})
    assert without[0].match_kind == MatchKind.SIMILAR
    assert without[0].matched_variant_id is not None

    with_detail = await _resolve(
        summaries,
        {
            "v1|2|0": {
                "localizedAspects": [
                    {"name": "RAM", "value": ["12 GB"]},
                    {"name": "Colour", "value": ["Black"]},
                ]
            }
        },
    )
    assert with_detail[0].match_kind == MatchKind.IDENTICAL
    assert with_detail[0].matched_variant_id == CONFIRMED


@pytest.mark.asyncio
async def test_localized_aspect_values_are_mapped_to_catalog_colours() -> None:
    """A German listing declares "Schwarz"; the catalog knows "Black"."""
    offers = await _resolve(
        [_summary("v1|3|0", "Samsung Galaxy S26 Ultra")],
        {
            "v1|3|0": {
                "localizedAspects": [
                    {"name": "Storage Capacity", "value": ["512 GB"]},
                    {"name": "RAM", "value": ["12 GB"]},
                    {"name": "Farbe", "value": ["Schwarz"]},
                ]
            }
        },
    )

    colour = next(s for s in offers[0].raw_specs if s.key == "colour")
    assert colour.value == "Black"
    assert offers[0].match_kind == MatchKind.IDENTICAL


@pytest.mark.asyncio
async def test_catalog_side_aspect_groups_are_read_too() -> None:
    """Listings tied to an eBay catalogue product carry specs under product.aspectGroups."""
    offers = await _resolve(
        [_summary("v1|4|0", "Samsung Galaxy S26 Ultra")],
        {
            "v1|4|0": {
                "product": {
                    "aspectGroups": [
                        {
                            "aspects": [
                                {"localizedName": "Storage Capacity", "localizedValues": ["512 GB"]},
                                {"localizedName": "RAM", "localizedValues": ["12 GB"]},
                                {"localizedName": "Colour", "localizedValues": ["Black"]},
                            ]
                        }
                    ]
                }
            }
        },
    )

    assert offers[0].match_kind == MatchKind.IDENTICAL


@pytest.mark.asyncio
async def test_an_identifier_from_the_detail_beats_guessing_at_specs() -> None:
    """
    A GTIN settles identity outright, which is the strongest tier available.

    Search summaries never carry one, so without the detail request this listing has
    no identifier at all.
    """
    offers = await _resolve(
        [_summary("v1|5|0", "Samsung Galaxy S26 Ultra")],
        {"v1|5|0": {"gtin": "8806095123456"}},
    )

    assert offers[0].gtin == "8806095123456"
    assert offers[0].match_kind == MatchKind.IDENTICAL
    assert any("GTIN" in note for note in offers[0].match_notes)


@pytest.mark.asyncio
async def test_a_declared_spec_overrides_one_inferred_from_the_title() -> None:
    """
    Prose loses to a named field.

    The title reads "Black", the seller's own Colour field says Silver. The typed
    field is what eBay shows the buyer as the item's colour, so it decides — and it
    sends the offer to a different catalog variant than the title implied.
    """
    offers = await _resolve(
        [_summary("v1|6|0", "Samsung Galaxy S26 Ultra Black")],
        {
            "v1|6|0": {
                "localizedAspects": [
                    {"name": "Storage Capacity", "value": ["512 GB"]},
                    {"name": "RAM", "value": ["12 GB"]},
                    {"name": "Colour", "value": ["Silver"]},
                ]
            }
        },
    )

    colour = next(s for s in offers[0].raw_specs if s.key == "colour")
    assert colour.value == "Silver"
    assert offers[0].matched_variant_id == "samsung-galaxy-s26-ultra-512-12-eu-silver"
    # A real but different build, so it is a candidate alternative rather than a
    # member of the confirmed ranked list.
    assert offers[0].match_kind == MatchKind.SIMILAR


@pytest.mark.asyncio
async def test_a_listing_that_publishes_nothing_stays_unmatched() -> None:
    """
    No evidence anywhere means no match — never an invented one.

    Guessing a build here would corrupt the price comparison, which is the whole
    point of the comparison.
    """
    offers = await _resolve(
        [_summary("v1|7|0", "Samsung Galaxy S26 Ultra brand new sealed")],
        {"v1|7|0": {"localizedAspects": []}},
    )

    assert offers[0].match_kind == MatchKind.UNMATCHED


@pytest.mark.asyncio
async def test_detail_requests_are_capped() -> None:
    """A vague keyword can return twenty vague titles; the search stays bounded."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if "/item/" in path:
            seen.append(path)
            return httpx.Response(200, json={"localizedAspects": []})
        return httpx.Response(
            200,
            json={
                "itemSummaries": [
                    _summary(f"v1|{i}|0", "Samsung Galaxy S26 Ultra") for i in range(25)
                ]
            },
        )

    adapter = _adapter(httpx.MockTransport(handler))
    matcher = ProductMatcher(CatalogRepository())
    matched = matcher.match(await adapter.search(SCOPE, "TR"), SCOPE)
    await adapter.enrich([o for o in matched if needs_more_evidence(o)], SCOPE)
    await adapter.aclose()

    assert len(matched) == 25
    assert len(seen) == MAX_DETAIL_LOOKUPS


@pytest.mark.asyncio
async def test_a_failed_detail_request_leaves_the_offer_as_it_was() -> None:
    """Enrichment is an improvement, not a precondition."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if "/item/" in request.url.path:
            return httpx.Response(500, json={"errors": [{"message": "boom"}]})
        return httpx.Response(
            200, json={"itemSummaries": [_summary("v1|8|0", "Samsung Galaxy S26 Ultra 512GB")]}
        )

    adapter = _adapter(httpx.MockTransport(handler))
    matched = ProductMatcher(CatalogRepository()).match(await adapter.search(SCOPE, "TR"), SCOPE)
    enriched = await adapter.enrich(matched, SCOPE)
    await adapter.aclose()

    assert len(enriched) == 1
    assert enriched[0].id == matched[0].id
    assert {s.key for s in enriched[0].raw_specs} == {s.key for s in matched[0].raw_specs}


def _offer(match_kind: MatchKind, notes: list[str]) -> Offer:
    money = Money(amount=Decimal("1000"), currency="TRY")
    return Offer(
        id="o",
        source_id="s",
        seller=Seller(name="s"),
        country="TR",
        listing_title="t",
        listing_url="https://example.com",
        list_price=money,
        stock_status=StockStatus.IN_STOCK,
        match_kind=match_kind,
        match_notes=notes,
        collected_at=datetime.now(timezone.utc),
    )


def test_only_unplaced_offers_ask_for_more_evidence() -> None:
    """A `similar` offer that is genuinely a different build is already placed."""
    assert needs_more_evidence(_offer(MatchKind.UNMATCHED, ["nothing aligned"])) is True
    assert (
        needs_more_evidence(
            _offer(MatchKind.SIMILAR, ["Attribute match ambiguous across variants."])
        )
        is True
    )
    assert needs_more_evidence(_offer(MatchKind.IDENTICAL, ["GTIN match."])) is False
    assert (
        needs_more_evidence(_offer(MatchKind.SIMILAR, ["Attribute match on catalog fields."]))
        is False
    )


@pytest.mark.asyncio
async def test_a_source_with_nothing_more_to_give_changes_nothing() -> None:
    """The default keeps fixture-style sources free of extra work."""

    class Plain(SourceAdapter):
        source = Source(
            id="plain",
            display_name="Plain",
            country="TR",
            kind=SourceKind.OTHER,
            reliability=0.7,
            acquisition_method=AcquisitionMethod.FIXTURE,
        )

        async def search(self, scope, destination_country):  # type: ignore[no-untyped-def]
            return []

    offers = [_offer(MatchKind.UNMATCHED, ["nothing aligned"])]
    assert await Plain().enrich(offers, SCOPE) == offers
