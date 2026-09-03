"""Extract and normalize retailer / catalog product identifiers."""

from __future__ import annotations

import re

from gp_price_intel.domain.models import Offer, ProductVariant

# Keys adapters may place in ``Offer.raw_specs`` for identity matching.
IDENTITY_SPEC_KEYS = frozenset(
    {
        "sku",
        "retailer_sku",
        "gtin",
        "ean",
        "upc",
        "model_number",
        "mpn",
    }
)

_NON_DIGITS = re.compile(r"\D+")


def normalize_gtin(value: str) -> str:
    """Digits only — compare EAN/UPC/GTIN without spaces or check digits formatting."""
    return _NON_DIGITS.sub("", value.strip())


def normalize_code(value: str) -> str:
    """Manufacturer / retailer codes: case-insensitive, no surrounding whitespace."""
    return value.strip().casefold()


def extract_offer_identifiers(offer: Offer) -> dict[str, str]:
    """
    Collect identity fields from top-level offer columns and ``raw_specs``.

    Adapters should set ``retailer_sku`` when the site exposes its own SKU
    (e.g. Best Buy ``sku``). ``gtin`` / ``model_number`` cross retailers.
    """
    ids: dict[str, str] = {}

    if offer.retailer_sku:
        ids["retailer_sku"] = offer.retailer_sku.strip()
    if offer.gtin:
        ids["gtin"] = offer.gtin.strip()
    if offer.model_number:
        ids["model_number"] = offer.model_number.strip()

    for spec in offer.raw_specs:
        key = spec.key.casefold()
        if key not in IDENTITY_SPEC_KEYS:
            continue
        value = spec.value if spec.value is not None else spec.raw_text
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if key in {"sku", "retailer_sku"} and "retailer_sku" not in ids:
            ids["retailer_sku"] = text
        elif key in {"gtin", "ean", "upc"} and "gtin" not in ids:
            ids["gtin"] = text
        elif key in {"model_number", "mpn"} and "model_number" not in ids:
            ids["model_number"] = text

    return ids


def variant_retailer_sku(variant: ProductVariant, source_id: str) -> str | None:
    sku = variant.retailer_skus.get(source_id)
    return sku.strip() if sku else None


def gtin_matches(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    a, b = normalize_gtin(left), normalize_gtin(right)
    if not a or not b:
        return False
    # Allow UPC-12 vs EAN-13 (leading zero) comparisons.
    return a == b or a.lstrip("0") == b.lstrip("0")


def code_matches(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return normalize_code(left) == normalize_code(right)
