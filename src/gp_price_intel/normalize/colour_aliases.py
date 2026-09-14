"""Map marketplace colour words (TR / DE / JP / …) onto English catalog colours."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache

from gp_price_intel.config import get_settings
from gp_price_intel.normalize.similarity import normalize_text

# Latin aliases match as whole phrases so "blau" does not steal "himmelblau".
_LATIN_CHARS = re.compile(r"[a-z0-9]")


def _fold(text: str) -> str:
    """
    Case- and accent-fold while keeping non-Latin letters (e.g. Japanese colour words).

    ``normalize_text`` is Latin-oriented for fuzzy matching; colour aliases also need
    CJK and other scripts to survive folding.
    """
    lowered = text.casefold().strip()
    decomposed = unicodedata.normalize("NFKD", lowered)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    # Keep letters/numbers from any script; collapse other punctuation to spaces.
    kept: list[str] = []
    for ch in unicodedata.normalize("NFKC", without_accents):
        if ch.isalnum():
            kept.append(ch)
        else:
            kept.append(" ")
    return re.sub(r"\s+", " ", "".join(kept)).strip()


def _is_latin_alias(alias: str) -> bool:
    return bool(_LATIN_CHARS.search(alias)) and not any(
        ord(ch) > 0x024F and ch.isalpha() for ch in alias
    )


@lru_cache(maxsize=1)
def _alias_pairs() -> tuple[tuple[str, str], ...]:
    """
    (folded_alias, canonical_english) pairs, longest alias first.

    Longer phrases win so "gök mavisi" maps to Sky Blue before a lone "mavi" → Blue.
    """
    path = get_settings().data_dir / "catalog" / "colour_aliases.json"
    if not path.exists():
        return ()
    raw = json.loads(path.read_text(encoding="utf-8"))
    pairs: list[tuple[str, str]] = []
    for canonical, aliases in raw.items():
        pairs.append((_fold(canonical), str(canonical)))
        for alias in aliases:
            folded = _fold(str(alias))
            if folded:
                pairs.append((folded, str(canonical)))
    pairs.sort(key=lambda item: len(item[0]), reverse=True)
    return tuple(pairs)


def reload_colour_aliases() -> None:
    """Drop the cached alias table (tests that rewrite the JSON file)."""
    _alias_pairs.cache_clear()


def resolve_colour_alias(
    text: str,
    valid_colours: list[str] | None = None,
) -> str | None:
    """
    Map a localized colour word or phrase to an English catalog colour.

    When ``valid_colours`` is set, only aliases whose English target is in that
    family's option list are accepted — so a phone that does not sell Sky Blue
    will not claim a hit from "gök mavisi".
    """
    if not text or not text.strip():
        return None

    allowed = {colour for colour in (valid_colours or [])}
    # Exact English catalog hit (any casing) before alias walk.
    folded_text = _fold(text)
    if valid_colours:
        for colour in valid_colours:
            if _fold(colour) == folded_text:
                return colour

    haystack = _fold(text)
    # Also try the accent-stripped Latin form used elsewhere in the parser.
    haystack_latin = normalize_text(text)

    for alias, canonical in _alias_pairs():
        if allowed and canonical not in allowed:
            continue
        if _alias_in_text(alias, haystack) or _alias_in_text(alias, haystack_latin):
            if valid_colours:
                return next(c for c in valid_colours if c == canonical)
            return canonical
    return None


def _alias_in_text(alias: str, haystack: str) -> bool:
    if not alias or not haystack:
        return False
    if alias == haystack:
        return True
    if alias not in haystack:
        return False
    if not _is_latin_alias(alias):
        # CJK / mixed scripts: substring is enough ("スペースグレイ" inside a title).
        return True
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", haystack))


def canonicalize_colour(
    value: str,
    valid_colours: list[str] | None = None,
) -> str | None:
    """
    Turn a seller-written colour (aspect value or title fragment) into catalog English.

    Tries aliases first, then an exact/folded match against ``valid_colours``.
    """
    aliased = resolve_colour_alias(value, valid_colours)
    if aliased is not None:
        return aliased
    if not valid_colours:
        folded = _fold(value)
        for alias, canonical in _alias_pairs():
            if alias == folded:
                return canonical
        return value.strip() or None
    folded = _fold(value)
    for colour in valid_colours:
        if _fold(colour) == folded:
            return colour
    return None
