"""Extract structured attributes from free-text product queries."""

from __future__ import annotations

import re

from gp_price_intel.normalize.similarity import best_fuzzy_match, normalize_text

STORAGE_PATTERN = re.compile(
    r"(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>gb|tb|g\b|t\b)",
    re.IGNORECASE,
)
MEMORY_PATTERN = re.compile(
    r"(?P<amount>\d+)\s*(?:gb\s*)?(?:ram|memory)\b",
    re.IGNORECASE,
)
REGION_PATTERN = re.compile(r"\b(eu|us|tr|uk|jp|europe|turkey|türkiye)\b", re.IGNORECASE)

REGION_ALIASES: dict[str, str] = {
    "eu": "EU",
    "europe": "EU",
    "us": "US",
    "tr": "TR",
    "turkey": "TR",
    "türkiye": "TR",
    "uk": "UK",
    "jp": "JP",
}


def parse_storage_gb(text: str) -> int | None:
    match = STORAGE_PATTERN.search(text)
    if not match:
        return None
    amount = float(match.group("amount"))
    unit = match.group("unit").lower()
    if unit in {"tb", "t"}:
        return int(amount * 1024)
    return int(amount)


def parse_memory_gb(text: str) -> int | None:
    match = MEMORY_PATTERN.search(text)
    if not match:
        return None
    return int(match.group("amount"))


def parse_region_version(text: str) -> str | None:
    match = REGION_PATTERN.search(text)
    if not match:
        return None
    token = match.group(1).casefold()
    return REGION_ALIASES.get(token, token.upper())


def parse_colour(text: str, valid_colours: list[str]) -> str | None:
    if not valid_colours:
        return None
    normalized_query = normalize_text(text)
    best_colour, score = best_fuzzy_match(normalized_query, valid_colours)
    if best_colour is None or score < 0.55:
        # Also try substring match for multi-word colours (e.g. "black titanium")
        for colour in valid_colours:
            if normalize_text(colour) in normalized_query:
                return colour
        return None
    return best_colour
