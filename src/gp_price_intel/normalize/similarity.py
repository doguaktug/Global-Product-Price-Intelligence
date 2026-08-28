"""String similarity helpers for fuzzy catalog matching."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

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


def normalize_text(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    lowered = text.casefold().strip()
    decomposed = unicodedata.normalize("NFKD", lowered)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_accents)


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
    # Short model codes (s26, m3) must match exactly or very closely.
    if len(left) <= 3 or len(right) <= 3:
        return 1.0 if left == right else SequenceMatcher(None, left, right).ratio()
    return SequenceMatcher(None, left, right).ratio()


def token_set_ratio(left: str, right: str) -> float:
    """
    Fuzzy token-set match — robust to word order and extra words in the query.

    "ultra s26 samsung 512gb" vs "samsung galaxy s26 ultra" scores well because
    most catalog tokens find a partner in the query.
    """
    left_tokens = tokenize(left)
    right_tokens = tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0

    left_hits = [max(token_similarity(lt, rt) for rt in right_tokens) for lt in left_tokens]
    right_hits = [max(token_similarity(rt, lt) for lt in left_tokens) for rt in right_tokens]

    left_coverage = sum(left_hits) / len(left_hits)
    right_coverage = sum(right_hits) / len(right_hits)
    if left_coverage + right_coverage == 0:
        return 0.0
    return 2 * left_coverage * right_coverage / (left_coverage + right_coverage)


def partial_ratio(left: str, right: str) -> float:
    """Best local alignment — catches substring and near-substring matches."""
    left_norm, right_norm = normalize_text(left), normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm in right_norm or right_norm in left_norm:
        return 0.95

    short, long = (left_norm, right_norm) if len(left_norm) <= len(right_norm) else (right_norm, left_norm)
    if not short:
        return 0.0

    best = 0.0
    short_len = len(short)
    for window in range(short_len, min(len(long), short_len + 4) + 1):
        for index in range(len(long) - window + 1):
            chunk = long[index : index + window]
            best = max(best, SequenceMatcher(None, short, chunk).ratio())
    return best


def similarity(left: str, right: str) -> float:
    """
    Combined fuzzy score in 0–1.

    Uses the best of token-set (word order / extra words), partial (substring),
    and full-string ratio so typos and messy queries still match catalog labels.
    """
    if not left or not right:
        return 0.0

    left_norm, right_norm = normalize_text(left), normalize_text(right)
    if left_norm == right_norm:
        return 1.0

    token_score = token_set_ratio(left, right)
    partial_score = partial_ratio(left, right)
    sequence_score = SequenceMatcher(None, left_norm, right_norm).ratio()

    return max(token_score, partial_score, sequence_score * 0.85)


def best_fuzzy_match(query: str, candidates: list[str]) -> tuple[str | None, float]:
    """Pick the candidate with highest similarity to query."""
    if not candidates:
        return None, 0.0
    scored = [(candidate, similarity(query, candidate)) for candidate in candidates]
    return max(scored, key=lambda item: item[1])


def score_label_against_query(query: str, label: str) -> float:
    """Score one catalog label; strips specs from query first."""
    residue = strip_spec_tokens(query)
    return similarity(residue, label)


def score_query_against_labels(query: str, labels: list[str]) -> float:
    """Best score across all labels/aliases for one catalog family."""
    if not labels:
        return 0.0
    return max(score_label_against_query(query, label) for label in labels)
