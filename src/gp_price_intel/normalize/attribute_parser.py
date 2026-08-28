"""Extract structured attributes from free-text product queries."""

from __future__ import annotations

import re

from gp_price_intel.normalize.similarity import (
    best_fuzzy_match,
    strip_spec_tokens,
    token_similarity,
    tokenize,
)

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

    residue = strip_spec_tokens(text)
    query_tokens = tokenize(residue)

    # Multi-word colours: every colour token must fuzzy-match a query token.
    for colour in valid_colours:
        colour_tokens = tokenize(colour)
        if not colour_tokens:
            continue
        if all(
            any(token_similarity(colour_token, query_token) >= 0.85 for query_token in query_tokens)
            for colour_token in colour_tokens
        ):
            return colour

    best_colour, score = best_fuzzy_match(residue, valid_colours)
    if best_colour is not None and score >= 0.55:
        return best_colour

    return None
