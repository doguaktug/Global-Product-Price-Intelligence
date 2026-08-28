"""String similarity helpers for fuzzy catalog matching (rapidfuzz-backed)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

# Stripped from product-name residue before family matching.
SPEC_TOKEN_PATTERN = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:gb|tb|g|t)\b|\b\d+\s*(?:gb\s*)?(?:ram|memory)\b",
    re.IGNORECASE,
)
REGION_TOKEN_PATTERN = re.compile(
    r"\b(?:eu|us|tr|uk|jp|europe|turkey|türkiye)\b",
    re.IGNORECASE,
)
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    {
        "the",
        "and",
        "with",
        "for",
        "a",
        "an",
        "new",
        "phone",
        "laptop",
        "tablet",
        "buy",
        "price",
    }
)

# Compact-alias hits below this length (after normalizing) are "farfetch" — confirm via popup.
COMPACT_QUERY_MAX_LEN = 8
COMPACT_ALIAS_MATCH_THRESHOLD = 80  # rapidfuzz 0–100 scale


@dataclass(frozen=True)
class FamilyMatchScore:
    """Score for one catalog family, plus whether the hit is a stretch abbreviation."""

    score: float  # 0–1
    farfetch: bool
    matched_label: str | None = None


def normalize_text(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    lowered = text.casefold().strip()
    decomposed = unicodedata.normalize("NFKD", lowered)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_accents)


def compact_form(text: str) -> str:
    """Remove spaces for compact alias matching (s26u ↔ S26U)."""
    return normalize_text(text).replace(" ", "")


def strip_spec_tokens(text: str) -> str:
    """
    Remove storage/RAM/region tokens so family matching focuses on the product name.

    Example: "Samsung Galaxy S26 Ultra 512 GB EU" → "Samsung Galaxy S26 Ultra"
    """
    cleaned = SPEC_TOKEN_PATTERN.sub(" ", text)
    cleaned = REGION_TOKEN_PATTERN.sub(" ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def tokenize(text: str) -> list[str]:
    normalized = normalize_text(text)
    return [
        token
        for token in TOKEN_PATTERN.findall(normalized)
        if len(token) > 1 and token not in STOPWORDS
    ]


def token_similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    return _to_unit_score(fuzz.ratio(left, right))


def _to_unit_score(rapidfuzz_score: float) -> float:
    return rapidfuzz_score / 100.0


def similarity(left: str, right: str) -> float:
    """
    Combined fuzzy score in 0–1 using rapidfuzz.

    token_set_ratio handles word order / extra tokens; partial_ratio handles
    near-substrings; WRatio blends both for messy product names.
    """
    if not left or not right:
        return 0.0

    left_norm, right_norm = normalize_text(left), normalize_text(right)
    if left_norm == right_norm:
        return 1.0

    scores = [
        _to_unit_score(fuzz.token_set_ratio(left_norm, right_norm)),
        _to_unit_score(fuzz.partial_ratio(left_norm, right_norm)),
        _to_unit_score(fuzz.WRatio(left_norm, right_norm)),
    ]
    return max(scores)


def best_fuzzy_match(query: str, candidates: list[str]) -> tuple[str | None, float]:
    """Pick the candidate with highest similarity to query."""
    if not candidates:
        return None, 0.0
    scored = [(candidate, similarity(query, candidate)) for candidate in candidates]
    return max(scored, key=lambda item: item[1])


def is_compact_alias(label: str) -> bool:
    """Labels like S26U or MBA — short, no spaces — are stretch abbreviations."""
    stripped = label.strip()
    return " " not in stripped and len(stripped) <= 12


def score_compact_alias(query: str, label: str) -> tuple[float, bool]:
    """
    Match compressed query text against a catalog alias.

    Returns (score 0–1, is_farfetch). Farfetch when the alias itself is compact
    (S26U) or the query is very short (s26u).
    """
    query_compact = compact_form(strip_spec_tokens(query))
    label_compact = compact_form(label)
    if not query_compact or not label_compact:
        return 0.0, False

    ratio = _to_unit_score(fuzz.ratio(query_compact, label_compact))
    if ratio * 100 < COMPACT_ALIAS_MATCH_THRESHOLD:
        # Also accept if one compact form contains the other (s26u in galaxys26u).
        if query_compact not in label_compact and label_compact not in query_compact:
            return 0.0, False

    farfetch = is_compact_alias(label) or len(query_compact) <= COMPACT_QUERY_MAX_LEN
    return max(ratio, 0.85 if query_compact == label_compact else ratio), farfetch


def score_label_against_query(query: str, label: str) -> FamilyMatchScore:
    """Score one catalog label; strips specs from query first."""
    residue = strip_spec_tokens(query)
    fuzzy = similarity(residue, label)
    compact_score, compact_farfetch = score_compact_alias(query, label)

    if compact_farfetch and compact_score >= _to_unit_score(COMPACT_ALIAS_MATCH_THRESHOLD):
        return FamilyMatchScore(
            score=max(fuzzy, compact_score),
            farfetch=True,
            matched_label=label,
        )

    return FamilyMatchScore(score=max(fuzzy, compact_score), farfetch=False, matched_label=None)


def score_query_against_labels(query: str, labels: list[str]) -> FamilyMatchScore:
    """Best score across all labels/aliases for one catalog family."""
    if not labels:
        return FamilyMatchScore(score=0.0, farfetch=False)

    results = [score_label_against_query(query, label) for label in labels]
    best = max(results, key=lambda item: item.score)
    # If any alias was a stretch hit (e.g. s26u), the whole family match is farfetch.
    farfetch = any(result.farfetch for result in results)
    return FamilyMatchScore(
        score=best.score,
        farfetch=farfetch,
        matched_label=next((r.matched_label for r in results if r.farfetch), best.matched_label),
    )


# Back-compat for tests that import token_set_ratio directly.
def token_set_ratio(left: str, right: str) -> float:
    return _to_unit_score(fuzz.token_set_ratio(normalize_text(left), normalize_text(right)))
