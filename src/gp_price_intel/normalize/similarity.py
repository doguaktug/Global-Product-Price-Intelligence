"""String similarity helpers for fuzzy catalog matching."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


def normalize_text(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    lowered = text.casefold().strip()
    decomposed = unicodedata.normalize("NFKD", lowered)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_accents)


def similarity(a: str, b: str) -> float:
    """Return 0–1 similarity between two strings."""
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.92
    return SequenceMatcher(None, na, nb).ratio()


def best_fuzzy_match(query: str, candidates: list[str]) -> tuple[str | None, float]:
    """Pick the candidate with highest similarity to query."""
    if not candidates:
        return None, 0.0
    scored = [(candidate, similarity(query, candidate)) for candidate in candidates]
    best = max(scored, key=lambda item: item[1])
    return best
