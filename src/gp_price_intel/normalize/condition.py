"""Detect new vs used / refurbished / open-box listings."""

from __future__ import annotations

import re
import unicodedata

from gp_price_intel.domain.models import ItemCondition, Offer

# Marketplace condition strings and title keywords → ItemCondition.
# Longer / more specific phrases are checked first.
_CONDITION_PHRASES: tuple[tuple[str, ItemCondition], ...] = (
    # Refurbished / renewed (before bare "used")
    ("manufacturer refurbished", ItemCondition.REFURBISHED),
    ("seller refurbished", ItemCondition.REFURBISHED),
    ("certified refurbished", ItemCondition.REFURBISHED),
    ("refurbished", ItemCondition.REFURBISHED),
    ("renewed", ItemCondition.REFURBISHED),
    ("refurbish", ItemCondition.REFURBISHED),
    ("generalüberholt", ItemCondition.REFURBISHED),
    ("generaluberholt", ItemCondition.REFURBISHED),
    ("yenilenmiş", ItemCondition.REFURBISHED),
    ("yenilenmis", ItemCondition.REFURBISHED),
    ("整備済", ItemCondition.REFURBISHED),
    ("リファービッシュ", ItemCondition.REFURBISHED),
    # Open box
    ("open box", ItemCondition.OPEN_BOX),
    ("open-box", ItemCondition.OPEN_BOX),
    ("opened box", ItemCondition.OPEN_BOX),
    # Used / 2nd hand
    ("for parts", ItemCondition.USED),
    ("not working", ItemCondition.USED),
    ("second hand", ItemCondition.USED),
    ("second-hand", ItemCondition.USED),
    ("2nd hand", ItemCondition.USED),
    ("2nd-hand", ItemCondition.USED),
    ("pre-owned", ItemCondition.USED),
    ("preowned", ItemCondition.USED),
    ("pre owned", ItemCondition.USED),
    ("used", ItemCondition.USED),
    ("gebraucht", ItemCondition.USED),
    ("ikinci el", ItemCondition.USED),
    ("2. el", ItemCondition.USED),
    ("2.el", ItemCondition.USED),
    ("中古", ItemCondition.USED),
    ("中古品", ItemCondition.USED),
    ("occasion", ItemCondition.USED),
    ("d'occasion", ItemCondition.USED),
    ("usado", ItemCondition.USED),
    ("usato", ItemCondition.USED),
    # New
    ("brand new", ItemCondition.NEW),
    ("new with tags", ItemCondition.NEW),
    ("new with box", ItemCondition.NEW),
    ("new other", ItemCondition.NEW),
    ("new – other", ItemCondition.NEW),
    ("new - other", ItemCondition.NEW),
    ("neu", ItemCondition.NEW),
    ("neuf", ItemCondition.NEW),
    ("nuovo", ItemCondition.NEW),
    ("nuevo", ItemCondition.NEW),
    ("yeni", ItemCondition.NEW),
    ("新品", ItemCondition.NEW),
    ("new", ItemCondition.NEW),
)

_NON_NEW = frozenset(
    {
        ItemCondition.USED,
        ItemCondition.REFURBISHED,
        ItemCondition.OPEN_BOX,
    }
)


def _fold(text: str) -> str:
    lowered = text.casefold().strip()
    decomposed = unicodedata.normalize("NFKD", lowered)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_accents).strip()


def parse_item_condition(*texts: str | None) -> ItemCondition:
    """
    Classify listing condition from marketplace strings and/or the title.

    First explicit hit wins; phrases are ordered so "manufacturer refurbished"
    beats a later bare "new" that might appear in "like new".
    """
    blobs = [_fold(text) for text in texts if text and str(text).strip()]
    if not blobs:
        return ItemCondition.UNKNOWN

    for phrase, condition in _CONDITION_PHRASES:
        folded = _fold(phrase)
        for blob in blobs:
            if folded in blob:
                # Latin tokens need a soft boundary so "renewed" ≠ inside another word
                # accidentally; CJK / short codes use substring.
                if folded.isascii() and re.fullmatch(r"[a-z0-9][a-z0-9 \-./']*", folded):
                    if not re.search(
                        rf"(?<![a-z0-9]){re.escape(folded)}(?![a-z0-9])", blob
                    ):
                        continue
                return condition
    return ItemCondition.UNKNOWN


def is_non_new_condition(condition: ItemCondition) -> bool:
    """True for used, refurbished, and open-box — excluded from ranking."""
    return condition in _NON_NEW


def condition_label(condition: ItemCondition) -> str:
    return {
        ItemCondition.NEW: "New",
        ItemCondition.USED: "Used / second-hand",
        ItemCondition.REFURBISHED: "Refurbished",
        ItemCondition.OPEN_BOX: "Open box",
        ItemCondition.UNKNOWN: "Condition not stated",
    }[condition]


def offer_condition_from_specs_and_title(offer: Offer) -> ItemCondition:
    """Infer condition from raw_specs + listing title when adapters left it unknown."""
    if offer.condition != ItemCondition.UNKNOWN:
        return offer.condition
    spec_value = None
    for spec in offer.raw_specs:
        if spec.key == "condition" and spec.value is not None:
            spec_value = str(spec.value)
            break
    return parse_item_condition(spec_value, offer.listing_title)
