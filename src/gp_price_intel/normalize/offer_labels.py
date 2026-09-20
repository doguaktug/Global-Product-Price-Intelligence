"""English display labels for offers, keeping the original listing title intact."""

from __future__ import annotations

from gp_price_intel.domain.models import Offer, ProductVariant
from gp_price_intel.normalize.spec_parser import format_capacity_gb


def english_variant_label(variant: ProductVariant) -> str:
    """
    Build the English name shown in the app for a catalog build.

    Uses the catalog ``model_name`` and appends storage/colour when they are not
    already part of that name, so a localized listing can still be presented as
    e.g. ``MacBook Air M4 — 512 GB, Sky Blue``.
    """
    name = variant.model_name.strip()
    lowered = name.casefold()
    extras: list[str] = []

    if variant.storage_gb is not None:
        label = format_capacity_gb(variant.storage_gb)
        compact_name = lowered.replace(" ", "")
        already = (
            f"{variant.storage_gb}gb" in compact_name
            or label.casefold().replace(" ", "") in compact_name
        )
        if not already:
            extras.append(label)

    if variant.colour and variant.colour.casefold() not in lowered:
        extras.append(variant.colour)

    if extras:
        return f"{name} — {', '.join(extras)}"
    return name


def primary_offer_name(offer: Offer) -> str:
    """App-language name when known; otherwise the original listing title."""
    if offer.display_name and offer.display_name.strip():
        return offer.display_name.strip()
    return offer.listing_title


def original_listing_name(offer: Offer) -> str | None:
    """Original marketplace title when it differs from the English display name."""
    display = (offer.display_name or "").strip()
    original = (offer.listing_title or "").strip()
    if display and original and display != original:
        return original
    return None
