"""String similarity helpers for fuzzy catalog matching (rapidfuzz-backed)."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence
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
CONNECTIVITY_TOKEN_PATTERN = re.compile(
    r"\bwi[\s-]?fi\b|\bcellular\b|\b5g\b|\blte\b",
    re.IGNORECASE,
)
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
# Generation/model codes: s26, m4, a16, g14, i7, ux3405, 14. Short, and digit-bearing.
GENERATION_TOKEN_PATTERN = re.compile(r"^[a-z]{0,3}\d{1,4}[a-z]?$")

# Generic tier words, used when no catalog vocabulary is supplied. Inventory-specific
# tokens (zenbook, rog, ipad, …) are derived from the catalog by
# ``build_distinctive_vocabulary`` so new brands need no code change.
FALLBACK_MODIFIER_TOKENS = frozenset(
    {
        "ultra",
        "pro",
        "plus",
        "max",
        "air",
        "mini",
        "lite",
        "fe",
    }
)

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

# Compact-alias hits below this length (after normalizing) are "shorthand" — confirm via popup.
COMPACT_QUERY_MAX_LEN = 8
COMPACT_ALIAS_MATCH_THRESHOLD = 80  # rapidfuzz 0–100 scale


@dataclass(frozen=True)
class FamilyMatchScore:
    """Score for one catalog family, plus whether the hit is a shorthand/abbreviation."""

    score: float  # 0–1
    shorthand: bool
    matched_label: str | None = None


@dataclass(frozen=True)
class DistinctiveVocabulary:
    """Tokens that separate one family from its siblings, derived from the catalog."""

    tokens: frozenset[str] = FALLBACK_MODIFIER_TOKENS

    def holds(self, token: str) -> bool:
        return token in self.tokens or bool(GENERATION_TOKEN_PATTERN.fullmatch(token))


DEFAULT_VOCABULARY = DistinctiveVocabulary()


def build_distinctive_vocabulary(
    families: Iterable[tuple[str, Sequence[str]]],
) -> DistinctiveVocabulary:
    """
    Learn the discriminating tokens from ``(brand, labels)`` pairs.

    A token discriminates when it appears in some — but not all — of a brand's
    families: "ultra" splits the Samsung line, "galaxy" and "apple" do not.
    Adding a Dell XPS or an iPad Air to the catalog therefore needs no code edit.
    """
    per_brand: dict[str, list[set[str]]] = defaultdict(list)
    for brand, labels in families:
        family_tokens: set[str] = set()
        for label in labels:
            family_tokens.update(tokenize(label))
        per_brand[normalize_text(brand)].append(family_tokens)

    distinctive: set[str] = set()
    for token_sets in per_brand.values():
        if len(token_sets) < 2:
            # A sole family for the brand: every token of it separates it from other brands.
            distinctive.update(*token_sets)
            continue
        counts: dict[str, int] = defaultdict(int)
        for tokens in token_sets:
            for token in tokens:
                counts[token] += 1
        distinctive.update(
            token for token, count in counts.items() if count < len(token_sets)
        )

    return DistinctiveVocabulary(tokens=frozenset(distinctive | FALLBACK_MODIFIER_TOKENS))


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
    cleaned = CONNECTIVITY_TOKEN_PATTERN.sub(" ", cleaned)
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


def shares_distinctive_token(
    query: str,
    labels: Sequence[str],
    vocabulary: DistinctiveVocabulary | None = None,
) -> bool:
    """
    Whether the query and a family have a naming token in common.

    Fuzzy scores alone will rank *something* first for any input — "Dyson V15"
    drifts towards "iPhone 15". Requiring a shared distinctive token keeps an
    unrelated query from being offered another family's products.
    """
    vocab = vocabulary or DEFAULT_VOCABULARY
    query_tokens = {token for token in tokenize(strip_spec_tokens(query)) if vocab.holds(token)}
    if not query_tokens:
        return False
    for label in labels:
        if query_tokens & set(tokenize(label)):
            return True
    return False


def score_compact_alias(query: str, label: str) -> tuple[float, bool]:
    """
    Match compressed query text against a catalog alias.

    Returns (score 0–1, is_shorthand). Shorthand is a property of what the user
    typed — one token, or very short once compacted — not of the alias that
    happened to match. "iPad Air 11" compacts onto the alias "iPadAir11" without
    being an abbreviation, and must not trigger a family popup.
    """
    residue = strip_spec_tokens(query)
    query_compact = compact_form(residue)
    label_compact = compact_form(label)
    if not query_compact or not label_compact:
        return 0.0, False

    ratio = _to_unit_score(fuzz.ratio(query_compact, label_compact))
    if ratio * 100 < COMPACT_ALIAS_MATCH_THRESHOLD:
        # Also accept if one compact form contains the other (s26u in galaxys26u).
        if query_compact not in label_compact and label_compact not in query_compact:
            return 0.0, False

    shorthand = len(tokenize(residue)) <= 1 or len(query_compact) <= COMPACT_QUERY_MAX_LEN
    return max(ratio, 0.85 if query_compact == label_compact else ratio), shorthand


def distinctive_token_adjustment(
    query: str,
    label: str,
    vocabulary: DistinctiveVocabulary | None = None,
) -> float:
    """
    Down-rank a label when tier/generation tokens disagree with the query.

    token_set_ratio treats "iPhone 16" as a perfect subset of "iPhone 16 Pro".
    This penalty keeps those families separable once both exist in the catalog.
    """
    vocab = vocabulary or DEFAULT_VOCABULARY
    query_tokens = set(tokenize(query))
    label_tokens = set(tokenize(label))
    distinctive = {token for token in query_tokens | label_tokens if vocab.holds(token)}
    if not distinctive:
        return 1.0
    mismatch = (query_tokens ^ label_tokens) & distinctive
    if not mismatch:
        return 1.0
    return max(0.35, 1.0 - 0.14 * len(mismatch))


def score_label_against_query(
    query: str,
    label: str,
    vocabulary: DistinctiveVocabulary | None = None,
) -> FamilyMatchScore:
    """Score one catalog label; strips specs from query first."""
    residue = strip_spec_tokens(query)
    adjustment = distinctive_token_adjustment(residue, label, vocabulary)
    fuzzy = similarity(residue, label) * adjustment
    # The compact form drops the spaces that separate "…pro14" from "…pro16", so it
    # needs the same generation-token penalty as the token-based score.
    compact_raw, compact_shorthand = score_compact_alias(query, label)
    compact_score = compact_raw * adjustment

    if compact_shorthand and compact_score >= _to_unit_score(COMPACT_ALIAS_MATCH_THRESHOLD):
        return FamilyMatchScore(
            score=max(fuzzy, compact_score),
            shorthand=True,
            matched_label=label,
        )

    return FamilyMatchScore(score=max(fuzzy, compact_score), shorthand=False, matched_label=None)


def score_query_against_labels(
    query: str,
    labels: list[str],
    vocabulary: DistinctiveVocabulary | None = None,
) -> FamilyMatchScore:
    """Best score across all labels/aliases for one catalog family."""
    if not labels:
        return FamilyMatchScore(score=0.0, shorthand=False)

    results = [score_label_against_query(query, label, vocabulary) for label in labels]
    best = max(results, key=lambda item: item.score)
    # If any alias was a shorthand hit (e.g. s26u), the whole family match is shorthand.
    shorthand = any(result.shorthand for result in results)
    return FamilyMatchScore(
        score=best.score,
        shorthand=shorthand,
        matched_label=next((r.matched_label for r in results if r.shorthand), best.matched_label),
    )


# Back-compat for tests that import token_set_ratio directly.
def token_set_ratio(left: str, right: str) -> float:
    return _to_unit_score(fuzz.token_set_ratio(normalize_text(left), normalize_text(right)))
