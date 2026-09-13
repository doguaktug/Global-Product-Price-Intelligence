"""
Normalize measurement specs that sources write in their own units and punctuation.

The assignment's own example is battery capacity: "5,000 mAh", "5000mAh" and "5 Ah"
are the same battery, and a comparison that treats them as three different values is
worse than useless. Screens are the same story across locales — `6.9"`, `6,9 inç`
and `17.5 cm` are one phone.

This module turns those strings into a single canonical unit per spec key, keeping the
original text as provenance so the Decision Page can show what the source actually said.
"""

from __future__ import annotations

import re
from typing import Any

# Grouped thousands ("5,000" / "5.000") must be told apart from a decimal comma
# ("6,9"). Only a run of exactly three digits after every separator is grouping.
_GROUPED = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")
_NUMBER = re.compile(r"\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?")

_MILLIAMP_HOURS = re.compile(r"m\.?\s*a\.?\s*h", re.IGNORECASE)
_AMP_HOURS = re.compile(r"(?<![a-z])a\.?\s*h", re.IGNORECASE)

_INCHES = re.compile(r'"|”|inch(?:es)?|inç|in\b|zoll|pouces?|型', re.IGNORECASE)
_CENTIMETRES = re.compile(r"\bcm\b|santim", re.IGNORECASE)
_MILLIMETRES = re.compile(r"\bmm\b", re.IGNORECASE)

# No leading \b — sources write "1TB" as often as "1 TB", and a digit is a word
# character, so a boundary there would never match the unseparated form.
_TERABYTES = re.compile(r"t\.?\s*b\b", re.IGNORECASE)
_MEGABYTES = re.compile(r"m\.?\s*b\b", re.IGNORECASE)

_MM_PER_INCH = 25.4
_GB_PER_TB = 1024

# Screen sizes are universally quoted to one decimal ("6.9 inch", "17.5 cm"), so a
# converted metric figure is rounded to the same precision. Without this, a source
# quoting 17.5 cm would yield 6.89" and "conflict" with a catalog value of 6.9".
_DISPLAY_PRECISION = 1


def parse_number(text: str) -> float | None:
    """First number in the text, reading `,` as grouping or decimal point as appropriate."""
    found = _NUMBER.search(text)
    if not found:
        return None
    raw = found.group(0)
    if _GROUPED.fullmatch(raw):
        return float(re.sub(r"[.,]", "", raw))
    return float(raw.replace(",", "."))


def parse_charge_mah(text: str | None) -> int | None:
    """
    Battery capacity in mAh. Accepts "5,000 mAh", "5000mAh", "5 Ah", "5.0 Ah".

    A bare number is read as mAh: no phone battery is quoted in single-digit mAh, and
    every source that omits the unit here means mAh. A figure in Ah is scaled up.
    """
    if text is None:
        return None
    value = parse_number(text)
    if value is None:
        return None
    # mAh must be tested first — "mAh" also contains "Ah".
    if _MILLIAMP_HOURS.search(text):
        return round(value)
    if _AMP_HOURS.search(text):
        return round(value * 1000)
    return round(value)


def parse_length_inch(text: str | None) -> float | None:
    """
    Screen size in inches. Accepts `6.9"`, "6,9 inç", "6.9 inch", "17.5 cm", "170 mm".

    A bare number is read as inches, which is how every source in scope quotes a
    display. Metric figures are converted so a `cm` listing compares to an `inch` one.
    """
    if text is None:
        return None
    value = parse_number(text)
    if value is None:
        return None
    # Millimetres before centimetres, and both before inches, because "in" is a
    # substring of plenty of words that appear beside a metric unit.
    if _MILLIMETRES.search(text):
        return round(value / _MM_PER_INCH, _DISPLAY_PRECISION)
    if _CENTIMETRES.search(text):
        return round(value * 10 / _MM_PER_INCH, _DISPLAY_PRECISION)
    return round(value, _DISPLAY_PRECISION)


def parse_capacity_gb(text: str | None) -> int | None:
    """
    Storage or memory in GB. Accepts "512 GB", "512GB", "1 TB", "1024 MB".

    The unit decides the scale, so a terabyte drive does not come back as the
    number 1 and then fail to match a catalog value of 1024.
    """
    if text is None:
        return None
    value = parse_number(text)
    if value is None:
        return None
    if _TERABYTES.search(text):
        return round(value * _GB_PER_TB)
    if _MEGABYTES.search(text):
        return round(value / 1024)
    return round(value)


#: Spec keys whose source text needs a unit conversion before it can be compared.
#: Anything not listed here is passed through unchanged as a string.
UNIT_PARSERS: dict[str, Any] = {
    "battery_mah": parse_charge_mah,
    "display_inch": parse_length_inch,
    "storage_gb": parse_capacity_gb,
    "memory_gb": parse_capacity_gb,
}


def parse_spec_value(key: str, text: str | None) -> Any:
    """
    Normalize one source-written spec string for `key`.

    Returns None when the text carries no usable number, so an unreadable spec becomes
    missing data rather than a wrong value.
    """
    parser = UNIT_PARSERS.get(key)
    if parser is None:
        return None if text is None else str(text).strip() or None
    return parser(text)


def parse_source_specs(raw: dict[str, Any] | None) -> dict[str, Any]:
    """
    Normalize a source's whole spec block, dropping keys it wrote unreadably.

    Values that already arrive typed (a real `int` from a fixture column or a JSON
    API) are kept as they are — only strings need parsing.
    """
    if not raw:
        return {}
    parsed: dict[str, Any] = {}
    for key, value in raw.items():
        if value is None:
            continue
        normalized = value if not isinstance(value, str) else parse_spec_value(key, value)
        if normalized is not None:
            parsed[key] = normalized
    return parsed
