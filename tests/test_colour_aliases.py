"""Localized marketplace colour words resolve to English catalog colours."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.domain.models import (
    MatchKind,
    Money,
    NormalizedSpec,
    Offer,
    Seller,
)
from gp_price_intel.matching.matcher import ProductMatcher
from gp_price_intel.normalize.attribute_parser import (
    LISTING_COLOUR_FUZZY_THRESHOLD,
    parse_colour,
    parse_listing_attributes,
)
from gp_price_intel.normalize.colour_aliases import canonicalize_colour, resolve_colour_alias
from gp_price_intel.normalize.offer_labels import (
    english_variant_label,
    original_listing_name,
    primary_offer_name,
)


def test_turkish_german_japanese_colours_map_to_english() -> None:
    colours = ["Black", "Sky Blue", "Silver", "Space Gray", "Starlight"]
    assert resolve_colour_alias("Gök Mavisi", colours) == "Sky Blue"
    assert resolve_colour_alias("Schwarz", colours) == "Black"
    assert resolve_colour_alias("ブラック", colours) == "Black"
    assert resolve_colour_alias("Silber", colours) == "Silver"
    assert resolve_colour_alias("Uzay Grisi", colours) == "Space Gray"
    assert resolve_colour_alias("スペースグレイ", colours) == "Space Gray"
    assert resolve_colour_alias("Polarstern", colours) == "Starlight"


def test_listing_colour_threshold_does_not_guess_black() -> None:
    """
    Off-catalog colours used to fuzzy-match Black at 0.60 via partial_ratio.

    Listing titles are seller copy, not user typos, so the residue fallback is
    0.85. Token/alias hits still read a real Black / Schwarz / Phantom Black.
    """
    colours = ["Black", "Silver", "White"]
    sky_blue = "Samsung Galaxy S26 Ultra 512GB Sky Blue Unlocked"
    colourless = "Samsung Galaxy S26 Ultra 512GB Unlocked Global"
    assert parse_colour(sky_blue, colours, min_score=LISTING_COLOUR_FUZZY_THRESHOLD) is None
    assert parse_colour(colourless, colours, min_score=LISTING_COLOUR_FUZZY_THRESHOLD) is None
    assert parse_colour(
        "Samsung Galaxy S26 Ultra 512GB Phantom Black",
        colours,
        min_score=LISTING_COLOUR_FUZZY_THRESHOLD,
    ) == "Black"


def test_parse_colour_reads_localized_words_in_titles() -> None:
    colours = ["Black", "Sky Blue", "Space Gray", "Starlight", "Silver"]
    assert parse_colour("Apple MacBook Air 13\" M4 512 GB Gök Mavisi", colours) == "Sky Blue"
    assert parse_colour("Samsung Galaxy S26 Ultra 512 GB Schwarz", colours) == "Black"
    assert parse_colour("Apple iPad Air スペースグレイ", colours) == "Space Gray"


def test_parse_listing_attributes_keeps_english_canonical_colour() -> None:
    attrs = parse_listing_attributes(
        "Apple MacBook Air M4 512GB 16GB RAM Gök Mavisi",
        {
            "storage_gb": [256, 512, 1024],
            "memory_gb": [16, 24, 32],
            "colour": ["Midnight", "Starlight", "Sky Blue", "Silver"],
        },
    )
    assert attrs["colour"] == "Sky Blue"
    assert attrs["storage_gb"] == 512


def test_canonicalize_colour_for_aspect_values() -> None:
    assert canonicalize_colour("Schwarz", ["Black", "Silver"]) == "Black"
    assert canonicalize_colour("Siyah", ["Black"]) == "Black"
    assert canonicalize_colour("Black", ["Black"]) == "Black"


def test_matched_offer_gets_english_display_name_and_keeps_original_title() -> None:
    catalog = CatalogRepository()
    family = catalog.get_family("apple-macbook-air-m4")
    assert family is not None
    variant = next(
        v
        for v in catalog.list_variants(family.id)
        if v.colour == "Sky Blue" and v.storage_gb == 512
    )

    offer = Offer(
        id="tr-mba",
        source_id="fixture-tr",
        seller=Seller(name="TR Shop"),
        country="TR",
        listing_title='Apple MacBook Air 13" M4 512 GB 16 GB Gök Mavisi',
        listing_url="https://example.com/mba",
        list_price=Money(amount=Decimal("45000"), currency="TRY"),
        raw_specs=[
            NormalizedSpec(key="storage_gb", value=512, raw_text="512 GB"),
            NormalizedSpec(key="memory_gb", value=16, raw_text="16 GB"),
            NormalizedSpec(key="colour", value="Sky Blue", raw_text="Gök Mavisi"),
            NormalizedSpec(key="region_version", value="US", raw_text="US"),
        ],
        collected_at=datetime.now(timezone.utc),
    )

    from gp_price_intel.domain.models import SearchScope

    matched = ProductMatcher(catalog).match(
        [offer],
        SearchScope(family_id=family.id, variant_ids=[variant.id]),
    )[0]

    assert matched.matched_variant_id == variant.id
    assert matched.match_kind in {MatchKind.IDENTICAL, MatchKind.SIMILAR}
    assert matched.listing_title.endswith("Gök Mavisi")
    assert matched.display_name is not None
    assert "MacBook Air" in matched.display_name
    assert "Sky Blue" in matched.display_name
    assert "Gök Mavisi" not in matched.display_name
    assert primary_offer_name(matched) == matched.display_name
    assert original_listing_name(matched) == matched.listing_title
    assert english_variant_label(variant) == matched.display_name
