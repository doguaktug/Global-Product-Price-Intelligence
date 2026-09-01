"""Product matcher tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.domain.models import (
    MatchKind,
    Money,
    NormalizedSpec,
    Offer,
    SearchScope,
    Seller,
    StockStatus,
)
from gp_price_intel.matching.identifiers import extract_offer_identifiers, gtin_matches
from gp_price_intel.matching.matcher import ProductMatcher


def _offer(
    *,
    source_id: str = "bestbuy-us",
    retailer_sku: str | None = None,
    gtin: str | None = None,
    model_number: str | None = None,
    raw_specs: list[NormalizedSpec] | None = None,
) -> Offer:
    return Offer(
        id="offer-1",
        source_id=source_id,
        seller=Seller(name="Test"),
        country="US",
        listing_title="Samsung Galaxy S26 Ultra 512 GB",
        listing_url="https://example.com/p/1",
        list_price=Money(amount=Decimal("1199.99"), currency="USD"),
        retailer_sku=retailer_sku,
        gtin=gtin,
        model_number=model_number,
        raw_specs=raw_specs or [],
        stock_status=StockStatus.IN_STOCK,
        collected_at=datetime.now(timezone.utc),
    )


def test_gtin_normalization_allows_leading_zero() -> None:
    assert gtin_matches("08806095123456", "8806095123456")


def test_extract_offer_identifiers_from_raw_specs() -> None:
    offer = _offer(
        raw_specs=[
            NormalizedSpec(key="sku", value="6575028"),
            NormalizedSpec(key="gtin", value="8806095123456"),
        ]
    )
    ids = extract_offer_identifiers(offer)
    assert ids["retailer_sku"] == "6575028"
    assert ids["gtin"] == "8806095123456"


def test_retailer_sku_match_is_identical_in_scope() -> None:
    matcher = ProductMatcher(CatalogRepository())
    scope = SearchScope(
        family_id="samsung-galaxy-s26-ultra",
        constraints={"storage_gb": 512},
        variant_ids=["samsung-galaxy-s26-ultra-512-12-eu-black"],
    )
    offer = _offer(source_id="bestbuy-us", retailer_sku="6575028")

    matched = matcher.match([offer], scope)[0]

    assert matched.match_kind == MatchKind.IDENTICAL
    assert matched.matched_variant_id == "samsung-galaxy-s26-ultra-512-12-eu-black"
    assert "Retailer SKU match" in matched.match_notes[0]


def test_gtin_match_when_sku_missing() -> None:
    matcher = ProductMatcher(CatalogRepository())
    scope = SearchScope(
        family_id="samsung-galaxy-s26-ultra",
        variant_ids=["samsung-galaxy-s26-ultra-512-12-eu-black"],
    )
    offer = _offer(gtin="8806095123456")

    matched = matcher.match([offer], scope)[0]

    assert matched.match_kind == MatchKind.IDENTICAL
    assert "GTIN match" in matched.match_notes[0]


def test_same_family_different_variant_sku_is_similar() -> None:
    matcher = ProductMatcher(CatalogRepository())
    scope = SearchScope(
        family_id="samsung-galaxy-s26-ultra",
        variant_ids=["samsung-galaxy-s26-ultra-512-12-eu-black"],
    )
    offer = _offer(source_id="bestbuy-us", retailer_sku="6575099")  # 1 TB variant SKU

    matched = matcher.match([offer], scope)[0]

    assert matched.match_kind == MatchKind.SIMILAR
    assert matched.matched_variant_id == "samsung-galaxy-s26-ultra-1024-12-eu-black"


def test_no_identifiers_falls_back_to_attribute_match() -> None:
    matcher = ProductMatcher(CatalogRepository())
    scope = SearchScope(
        family_id="samsung-galaxy-s26-ultra",
        variant_ids=["samsung-galaxy-s26-ultra-512-12-eu-black"],
    )
    offer = _offer(
        raw_specs=[
            NormalizedSpec(key="storage_gb", value=512),
            NormalizedSpec(key="memory_gb", value=12),
            NormalizedSpec(key="region_version", value="EU"),
            NormalizedSpec(key="colour", value="Black"),
        ]
    )

    matched = matcher.match([offer], scope)[0]

    assert matched.match_kind == MatchKind.IDENTICAL
    assert matched.matched_variant_id == "samsung-galaxy-s26-ultra-512-12-eu-black"


def test_unmatched_when_nothing_aligns() -> None:
    matcher = ProductMatcher(CatalogRepository())
    scope = SearchScope(
        family_id="samsung-galaxy-s26-ultra",
        variant_ids=["samsung-galaxy-s26-ultra-512-12-eu-black"],
    )
    offer = _offer()

    matched = matcher.match([offer], scope)[0]

    assert matched.match_kind == MatchKind.UNMATCHED
