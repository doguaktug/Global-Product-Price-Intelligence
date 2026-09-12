"""Extract structured attributes from free-text product queries."""

from __future__ import annotations

import re
from typing import Any

from gp_price_intel.normalize.similarity import (
    best_fuzzy_match,
    normalize_text,
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
# "16 GB RAM" also matches STORAGE_PATTERN — drop it before reading storage sizes.
MEMORY_PHRASE_PATTERN = re.compile(r"\d+\s*(?:gb\s*)?(?:ram|memory)\b", re.IGNORECASE)
REGION_PATTERN = re.compile(r"\b(eu|us|tr|uk|jp|europe|turkey|türkiye)\b", re.IGNORECASE)
CELLULAR_PATTERN = re.compile(r"\b(cellular|5g|lte|sim)\b", re.IGNORECASE)
WIFI_PATTERN = re.compile(r"\bwi[\s-]?fi\b", re.IGNORECASE)

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


def _capacity_candidates(text: str) -> list[int]:
    """Every GB/TB figure in the text, normalized to GB, in reading order."""
    values: list[int] = []
    for match in STORAGE_PATTERN.finditer(text):
        amount = float(match.group("amount"))
        unit = match.group("unit").lower()
        gigabytes = int(amount * 1024) if unit in {"tb", "t"} else int(amount)
        if gigabytes not in values:
            values.append(gigabytes)
    return values


def parse_capacities(
    text: str,
    storage_options: list[Any] | None = None,
    memory_options: list[Any] | None = None,
) -> tuple[int | None, int | None]:
    """
    Read storage and RAM together, using the family's options to tell them apart.

    Laptop and tablet listings routinely carry two capacities ("16GB 512GB"), so
    reading storage from the first match alone would return the RAM size.
    Returns ``(storage_gb, memory_gb)``; either may be None.
    """
    storage_choices = list(storage_options or [])
    memory_choices = list(memory_options or [])

    explicit_memory = MEMORY_PATTERN.search(text)
    memory: int | None = int(explicit_memory.group("amount")) if explicit_memory else None

    candidates = _capacity_candidates(MEMORY_PHRASE_PATTERN.sub(" ", text))

    storage: int | None = None
    known_storage = [value for value in candidates if value in storage_choices]
    if known_storage:
        storage = max(known_storage)
    else:
        # A capacity the catalog only sells as RAM is RAM, not an invalid disk size.
        unexplained = [value for value in candidates if value not in memory_choices]
        storage = max(unexplained) if unexplained else None

    if memory is None:
        leftover = [
            value
            for value in candidates
            if value in memory_choices and value != storage
        ]
        if len(leftover) == 1:
            memory = leftover[0]

    return storage, memory


def parse_region_version(text: str) -> str | None:
    match = REGION_PATTERN.search(text)
    if not match:
        return None
    token = match.group(1).casefold()
    return REGION_ALIASES.get(token, token.upper())


def parse_connectivity(text: str, valid_options: list[Any] | None = None) -> str | None:
    """Tablet builds split on Wi-Fi vs cellular; the option labels come from the catalog."""
    if not valid_options:
        return None
    if CELLULAR_PATTERN.search(text):
        return next(
            (option for option in valid_options if "cellular" in str(option).casefold()),
            None,
        )
    if WIFI_PATTERN.search(text):
        return next(
            (option for option in valid_options if "cellular" not in str(option).casefold()),
            None,
        )
    return None


def _contains_phrase(haystack: str, phrase: str) -> bool:
    if not phrase:
        return False
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", haystack))


def parse_processor(text: str, valid_options: list[Any] | None = None) -> str | None:
    """
    Pick the chip from the family's option list, longest label first.

    Longest-first is what separates "M4 Pro" from "M4"; the trailing-token pass
    catches the way people actually type Intel and AMD parts ("ryzen 9", "i7").
    """
    if not valid_options:
        return None

    normalized = normalize_text(text)
    by_length = sorted(valid_options, key=lambda option: len(str(option)), reverse=True)

    for option in by_length:
        if _contains_phrase(normalized, normalize_text(str(option))):
            return option

    for option in by_length:
        tokens = normalize_text(str(option)).split()
        for size in (2, 1):
            if len(tokens) < size:
                continue
            tail = " ".join(tokens[-size:])
            # The tail must carry a digit. A bare "pro" or "max" would otherwise read
            # the chip off the family name: "MacBook Pro 14" is not an M4 Pro.
            if len(tail) < 2 or not any(char.isdigit() for char in tail):
                continue
            if _contains_phrase(normalized, tail):
                return option
    return None


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


def parse_listing_attributes(
    text: str,
    valid_options: dict[str, list[Any]],
) -> dict[str, Any]:
    """
    Read every catalog attribute this text states, against a family's option lists.

    A marketplace listing title is the same kind of string as a user's query — both
    are free text naming a build ("Galaxy S26 Ultra 512GB 12GB RAM EU Black") — so
    query normalization and listing normalization share one parser instead of
    drifting apart. Keys the text does not mention come back as None.
    """
    storage, memory = parse_capacities(
        text,
        valid_options.get("storage_gb", []),
        valid_options.get("memory_gb", []),
    )
    return {
        "storage_gb": storage,
        "memory_gb": memory,
        "region_version": parse_region_version(text),
        "colour": parse_colour(text, valid_options.get("colour", [])),
        "processor": parse_processor(text, valid_options.get("processor", [])),
        "connectivity": parse_connectivity(text, valid_options.get("connectivity", [])),
    }
