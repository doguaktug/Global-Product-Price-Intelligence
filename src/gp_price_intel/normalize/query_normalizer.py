"""Query normalization against the reference catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.domain.models import (
    ConfirmationPrompt,
    ConfirmationReason,
    NormalizedQuery,
    ProductFamily,
    ProductVariant,
    PropertyRole,
)
from gp_price_intel.normalize.attribute_parser import parse_listing_attributes
from gp_price_intel.normalize.confirmation import (
    FAMILY_CHOICE_KEY,
    LINE_CHOICE_KEY,
    SERIES_CHOICE_KEY,
    VARIANT_CHOICE_KEY,
    distinct_values,
    filter_variants,
    rank_closest_variants,
)
from gp_price_intel.normalize.similarity import (
    GENERATION_TOKEN_PATTERN,
    FamilyMatchScore,
    build_distinctive_vocabulary,
    score_query_against_labels,
    shares_distinctive_token,
    strip_spec_tokens,
    tokenize,
)

FAMILY_MATCH_THRESHOLD = 0.45
FAMILY_AMBIGUITY_GAP = 0.06
# Below the match threshold a search never runs; families this close are only ever
# offered as "did you mean" options in the popup.
FAMILY_SUGGESTION_THRESHOLD = 0.30
FAMILY_OPTION_LIMIT = 5
CLOSEST_VARIANT_LIMIT = 3


def _family_labels(family: ProductFamily) -> list[str]:
    return [f"{family.brand} {family.family_name}", family.family_name, *family.aliases]


def _family_tokens(family: ProductFamily) -> set[str]:
    tokens: set[str] = set()
    for label in _family_labels(family):
        tokens.update(tokenize(label))
    return tokens


def _unique_in_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _family_prompt_order(families: list[ProductFamily]) -> list[ProductFamily]:
    """Shorter names first so the base model precedes Plus / Ultra / Pro Max."""
    return sorted(families, key=lambda family: (len(family.family_name), family.family_name))


@dataclass(frozen=True)
class _FamilyResolution:
    family: ProductFamily | None
    score: float
    shorthand: bool
    grouping_prompt: ConfirmationPrompt | None


class QueryNormalizer:
    """Parse user text → structured attributes + confirmation prompts."""

    def __init__(self, catalog: CatalogRepository | None = None) -> None:
        self.catalog = catalog or CatalogRepository()
        self.vocabulary = build_distinctive_vocabulary(
            (family.brand, _family_labels(family)) for family in self.catalog.list_families()
        )

    def normalize(
        self,
        raw_text: str,
        *,
        locked_series: str | None = None,
        locked_line: str | None = None,
        locked_family_id: str | None = None,
    ) -> NormalizedQuery:
        text = raw_text.strip()
        if not text:
            return NormalizedQuery(raw_text=text, needs_confirmation=True)

        resolution = self._resolve_family(
            text,
            locked_series=locked_series,
            locked_line=locked_line,
            locked_family_id=locked_family_id,
        )
        if resolution.family is None and resolution.grouping_prompt is None:
            return NormalizedQuery(
                raw_text=text,
                extracted={},
                needs_confirmation=True,
                pending_properties=[
                    ConfirmationPrompt(
                        property_key=FAMILY_CHOICE_KEY,
                        role=PropertyRole.IDENTITY,
                        reason=ConfirmationReason.NO_MATCH,
                        options=self._family_option_ids(text),
                        allow_not_important=False,
                    )
                ],
            )

        if resolution.grouping_prompt is not None:
            extracted: dict[str, Any] = {}
            if locked_series:
                extracted["series"] = locked_series
            if locked_line:
                extracted["line"] = locked_line
            if resolution.family is not None:
                extracted["brand"] = resolution.family.brand
                extracted["match_score"] = round(resolution.score, 3)
                extracted["match_shorthand"] = resolution.shorthand
            return NormalizedQuery(
                raw_text=text,
                extracted=extracted,
                candidate_family_id=resolution.family.id if resolution.family else None,
                needs_confirmation=True,
                pending_properties=[resolution.grouping_prompt],
            )

        family = resolution.family
        assert family is not None

        category = self.catalog.get_category(family.category_id)
        if category is None:
            raise ValueError(f"Unknown category for family {family.id}")

        extracted = {
            "brand": family.brand,
            "family_id": family.id,
            "family_name": family.family_name,
            "category_id": family.category_id,
            "match_score": round(resolution.score, 3),
            "match_shorthand": resolution.shorthand,
        }
        if family.series:
            extracted["series"] = family.series
        if family.line:
            extracted["line"] = family.line

        if resolution.shorthand and locked_family_id is None:
            return NormalizedQuery(
                raw_text=text,
                extracted=extracted,
                candidate_family_id=family.id,
                needs_confirmation=True,
                pending_properties=[
                    ConfirmationPrompt(
                        property_key=FAMILY_CHOICE_KEY,
                        role=PropertyRole.IDENTITY,
                        reason=ConfirmationReason.SHORTHAND,
                        options=self._family_option_ids(text, family.id),
                        allow_not_important=False,
                    )
                ],
            )

        variants = self.catalog.list_variants(family.id)
        constraints: dict[str, Any] = {}
        pending: list[ConfirmationPrompt] = []
        parsed_by_key = self._parse_attributes(text, family)
        candidate_variants = variants

        for key in category.identity_keys:
            prompt = self._resolve_property(
                key=key,
                role=PropertyRole.IDENTITY,
                parsed_value=parsed_by_key.get(key),
                valid_options=family.valid_options.get(key, []),
                variants=candidate_variants,
                constraints=constraints,
            )
            if prompt is not None:
                pending.append(prompt)
            elif key in constraints:
                candidate_variants = filter_variants(candidate_variants, {key: constraints[key]})

        for key in category.optional_keys:
            prompt = self._resolve_property(
                key=key,
                role=PropertyRole.OPTIONAL,
                parsed_value=parsed_by_key.get(key),
                valid_options=family.valid_options.get(key, []),
                variants=candidate_variants,
                constraints=constraints,
                allow_not_important=True,
            )
            if prompt is not None:
                pending.append(prompt)
            elif key in constraints:
                candidate_variants = filter_variants(candidate_variants, {key: constraints[key]})

        extracted.update(constraints)
        candidate_variants = filter_variants(variants, constraints)

        if variants and not candidate_variants:
            pending, candidate_variants = self._closest_build_prompt(
                variants, constraints, pending
            )

        return NormalizedQuery(
            raw_text=text,
            extracted=extracted,
            candidate_family_id=family.id,
            candidate_variant_ids=[v.id for v in candidate_variants],
            needs_confirmation=bool(pending),
            pending_properties=pending,
        )

    def _parse_attributes(self, text: str, family: ProductFamily) -> dict[str, Any]:
        return parse_listing_attributes(text, family.valid_options)

    def _closest_build_prompt(
        self,
        variants: list[ProductVariant],
        constraints: dict[str, Any],
        pending: list[ConfirmationPrompt],
    ) -> tuple[list[ConfirmationPrompt], list[ProductVariant]]:
        """
        The family is right but nothing in the catalog is built that way.

        Offer the nearest builds instead of silently widening the search, and drop
        the per-attribute prompts — they were computed against an empty candidate set.
        """
        suggestions = rank_closest_variants(variants, constraints, limit=CLOSEST_VARIANT_LIMIT)
        kept = [prompt for prompt in pending if prompt.property_key == FAMILY_CHOICE_KEY]
        kept.append(
            ConfirmationPrompt(
                property_key=VARIANT_CHOICE_KEY,
                role=PropertyRole.IDENTITY,
                reason=ConfirmationReason.NO_EXACT_VARIANT,
                options=[variant.id for variant in suggestions],
                allow_not_important=False,
            )
        )
        return kept, suggestions

    def _resolve_family(
        self,
        text: str,
        *,
        locked_series: str | None,
        locked_line: str | None,
        locked_family_id: str | None,
    ) -> _FamilyResolution:
        if locked_family_id:
            family = self.catalog.get_family(locked_family_id)
            if family is None:
                return _FamilyResolution(None, 0.0, False, None)
            scored = score_query_against_labels(text, _family_labels(family), self.vocabulary)
            return _FamilyResolution(family, scored.score, False, None)

        catalog_families = self.catalog.list_families()
        candidates = catalog_families
        if locked_series:
            candidates = [family for family in candidates if family.series == locked_series]
        if locked_line:
            candidates = [family for family in candidates if family.line == locked_line]
        if not candidates:
            return _FamilyResolution(None, 0.0, False, None)

        scored = self._score_families(text, candidates)
        if not scored or scored[0][1].score < FAMILY_MATCH_THRESHOLD:
            return _FamilyResolution(None, 0.0, False, None)
        if self._unknown_generation_token(text, catalog_families):
            return _FamilyResolution(None, 0.0, False, None)

        top_family, top_result = scored[0]
        if not locked_series and not locked_line:
            narrowed = self._narrow_by_brand(text, top_family, catalog_families)
            if isinstance(narrowed, ConfirmationPrompt):
                return _FamilyResolution(top_family, top_result.score, top_result.shorthand, narrowed)
            candidates = narrowed

        if not locked_line:
            narrowed = self._narrow_by_line(text, candidates)
            if isinstance(narrowed, ConfirmationPrompt):
                return _FamilyResolution(top_family, top_result.score, top_result.shorthand, narrowed)
            candidates = narrowed

        matching = self._filter_by_splitting_tokens(text, candidates)
        if not matching:
            matching = list(candidates)
        if len(matching) > 1:
            ranked = self._score_families(text, matching)
            top_match, top_match_score = ranked[0]
            return _FamilyResolution(
                top_match,
                top_match_score.score,
                top_match_score.shorthand,
                ConfirmationPrompt(
                    property_key=FAMILY_CHOICE_KEY,
                    role=PropertyRole.IDENTITY,
                    reason=ConfirmationReason.AMBIGUOUS,
                    options=[family.id for family in _family_prompt_order(matching)],
                    allow_not_important=False,
                ),
            )
        if len(matching) == 1:
            family = matching[0]
            family_score = score_query_against_labels(
                text, _family_labels(family), self.vocabulary
            )
            return _FamilyResolution(family, family_score.score, family_score.shorthand, None)

        return _FamilyResolution(top_family, top_result.score, top_result.shorthand, None)

    def _narrow_by_brand(
        self,
        text: str,
        top_family: ProductFamily,
        catalog_families: list[ProductFamily],
    ) -> list[ProductFamily] | ConfirmationPrompt:
        brand_mates = [family for family in catalog_families if family.brand == top_family.brand]
        series_names = _unique_in_order(
            [family.series for family in brand_mates if family.series]
        )
        if len(series_names) <= 1:
            if top_family.series:
                return [family for family in brand_mates if family.series == top_family.series]
            return brand_mates

        matching = self._filter_by_splitting_tokens(text, brand_mates)
        matching_series = _unique_in_order(
            [family.series for family in matching if family.series]
        )
        if len(matching_series) == 1:
            return [family for family in brand_mates if family.series == matching_series[0]]
        options = matching_series if len(matching_series) > 1 else series_names
        return ConfirmationPrompt(
            property_key=SERIES_CHOICE_KEY,
            role=PropertyRole.IDENTITY,
            reason=ConfirmationReason.AMBIGUOUS,
            options=sorted(options),
            allow_not_important=False,
        )

    def _narrow_by_line(
        self, text: str, candidates: list[ProductFamily]
    ) -> list[ProductFamily] | ConfirmationPrompt:
        line_names = _unique_in_order([family.line for family in candidates if family.line])
        if len(line_names) <= 1:
            if len(line_names) == 1:
                return [family for family in candidates if family.line == line_names[0]]
            return candidates

        matching = self._filter_by_splitting_tokens(text, candidates)
        matching_lines = _unique_in_order([family.line for family in matching if family.line])
        if len(matching_lines) == 1:
            return [family for family in candidates if family.line == matching_lines[0]]
        options = matching_lines if len(matching_lines) > 1 else line_names
        return ConfirmationPrompt(
            property_key=LINE_CHOICE_KEY,
            role=PropertyRole.IDENTITY,
            reason=ConfirmationReason.AMBIGUOUS,
            options=sorted(options),
            allow_not_important=False,
        )

    def _unknown_generation_token(self, text: str, families: list[ProductFamily]) -> bool:
        """True when the query names a generation the catalog does not stock."""
        query_generations = [
            token
            for token in tokenize(strip_spec_tokens(text))
            if GENERATION_TOKEN_PATTERN.fullmatch(token)
        ]
        if not query_generations:
            return False
        catalog_tokens: set[str] = set()
        for family in families:
            catalog_tokens.update(_family_tokens(family))
        return any(token not in catalog_tokens for token in query_generations)

    def _filter_by_splitting_tokens(
        self, text: str, families: list[ProductFamily]
    ) -> list[ProductFamily]:
        """Keep families that contain every query token that splits this cluster."""
        if len(families) <= 1:
            return list(families)

        token_sets = [_family_tokens(family) for family in families]
        shared = set.intersection(*token_sets) if token_sets else set()
        query_tokens = set(tokenize(strip_spec_tokens(text)))
        splitting = set()
        for tokens in token_sets:
            splitting.update(tokens - shared)
        query_split = query_tokens & splitting
        if not query_split:
            return list(families)
        return [family for family, tokens in zip(families, token_sets) if query_split <= tokens]

    def _score_families(
        self,
        text: str,
        families: list[ProductFamily] | None = None,
    ) -> list[tuple[ProductFamily, FamilyMatchScore]]:
        pool = families if families is not None else self.catalog.list_families()
        scored = [
            (family, score_query_against_labels(text, _family_labels(family), self.vocabulary))
            for family in pool
        ]
        scored.sort(key=lambda item: item[1].score, reverse=True)
        return scored

    def _family_option_ids(self, text: str, always_include: str | None = None) -> list[str]:
        """
        Families worth offering in the popup, best first.

        A near miss has to score well *and* share a naming token with the query —
        the picker must never hand the user a product from an unrelated family.
        ``always_include`` keeps the matched family on the list when the popup is
        only there to disambiguate it.
        """
        options = [
            family.id
            for family, result in self._score_families(text)
            if result.score >= FAMILY_SUGGESTION_THRESHOLD
            and (
                family.id == always_include
                or shares_distinctive_token(text, _family_labels(family), self.vocabulary)
            )
        ][:FAMILY_OPTION_LIMIT]

        if always_include and always_include not in options:
            options.insert(0, always_include)
        return options

    def _resolve_property(
        self,
        key: str,
        role: PropertyRole,
        parsed_value: Any,
        valid_options: list[Any],
        variants: list[ProductVariant],
        constraints: dict[str, Any],
        allow_not_important: bool = False,
    ) -> ConfirmationPrompt | None:
        """Return a confirmation prompt, auto-fill constraint, or None if resolved."""
        if parsed_value is not None:
            if valid_options and parsed_value not in valid_options:
                return ConfirmationPrompt(
                    property_key=key,
                    role=role,
                    reason=ConfirmationReason.INVALID,
                    options=valid_options,
                    allow_not_important=False,
                )
            constraints[key] = parsed_value
            return None

        distinct = distinct_values(variants, key)
        if len(distinct) == 1:
            constraints[key] = distinct[0]
            return None

        if not distinct and valid_options:
            distinct = valid_options

        if not distinct:
            return None

        if len(distinct) == 1:
            constraints[key] = distinct[0]
            return None

        return ConfirmationPrompt(
            property_key=key,
            role=role,
            reason=ConfirmationReason.MISSING,
            options=distinct,
            allow_not_important=allow_not_important and role == PropertyRole.OPTIONAL,
        )
