"""Query normalization against the reference catalog."""

from __future__ import annotations

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
from gp_price_intel.normalize.attribute_parser import (
    parse_capacities,
    parse_colour,
    parse_connectivity,
    parse_processor,
    parse_region_version,
)
from gp_price_intel.normalize.confirmation import (
    VARIANT_CHOICE_KEY,
    distinct_values,
    filter_variants,
    rank_closest_variants,
)
from gp_price_intel.normalize.similarity import (
    FamilyMatchScore,
    build_distinctive_vocabulary,
    score_query_against_labels,
    shares_distinctive_token,
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


class QueryNormalizer:
    """Parse user text → structured attributes + confirmation prompts."""

    def __init__(self, catalog: CatalogRepository | None = None) -> None:
        self.catalog = catalog or CatalogRepository()
        self.vocabulary = build_distinctive_vocabulary(
            (family.brand, _family_labels(family)) for family in self.catalog.list_families()
        )

    def normalize(self, raw_text: str) -> NormalizedQuery:
        text = raw_text.strip()
        if not text:
            return NormalizedQuery(raw_text=text, needs_confirmation=True)

        family, family_score, family_ambiguous, family_shorthand = self._match_family(text)
        if family is None:
            return NormalizedQuery(
                raw_text=text,
                extracted={},
                needs_confirmation=True,
                pending_properties=[
                    ConfirmationPrompt(
                        property_key="family_id",
                        role=PropertyRole.IDENTITY,
                        reason=ConfirmationReason.NO_MATCH,
                        options=self._family_option_ids(text),
                        allow_not_important=False,
                    )
                ],
            )

        category = self.catalog.get_category(family.category_id)
        if category is None:
            raise ValueError(f"Unknown category for family {family.id}")

        extracted: dict[str, Any] = {
            "brand": family.brand,
            "family_id": family.id,
            "family_name": family.family_name,
            "category_id": family.category_id,
            "match_score": round(family_score, 3),
            "match_shorthand": family_shorthand,
        }

        variants = self.catalog.list_variants(family.id)
        constraints: dict[str, Any] = {}
        pending: list[ConfirmationPrompt] = []

        if family_ambiguous:
            pending.append(
                ConfirmationPrompt(
                    property_key="family_id",
                    role=PropertyRole.IDENTITY,
                    reason=ConfirmationReason.AMBIGUOUS,
                    options=self._family_option_ids(text, family.id),
                    allow_not_important=False,
                )
            )
        elif family_shorthand:
            pending.append(
                ConfirmationPrompt(
                    property_key="family_id",
                    role=PropertyRole.IDENTITY,
                    reason=ConfirmationReason.SHORTHAND,
                    options=self._family_option_ids(text, family.id),
                    allow_not_important=False,
                )
            )

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
            needs_confirmation=bool(pending) or family_ambiguous or family_shorthand,
            pending_properties=pending,
        )

    def _parse_attributes(self, text: str, family: ProductFamily) -> dict[str, Any]:
        options = family.valid_options
        storage, memory = parse_capacities(
            text,
            options.get("storage_gb", []),
            options.get("memory_gb", []),
        )
        return {
            "storage_gb": storage,
            "memory_gb": memory,
            "region_version": parse_region_version(text),
            "colour": parse_colour(text, options.get("colour", [])),
            "processor": parse_processor(text, options.get("processor", [])),
            "connectivity": parse_connectivity(text, options.get("connectivity", [])),
        }

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
        kept = [prompt for prompt in pending if prompt.property_key == "family_id"]
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

    def _match_family(self, text: str) -> tuple[ProductFamily | None, float, bool, bool]:
        scored = self._score_families(text)
        if not scored or scored[0][1].score < FAMILY_MATCH_THRESHOLD:
            return None, 0.0, False, False

        top_family, top_result = scored[0]
        runner_up = scored[1][1].score if len(scored) > 1 else 0.0
        ambiguous = (top_result.score - runner_up) < FAMILY_AMBIGUITY_GAP
        shorthand = top_result.shorthand and not ambiguous
        return top_family, top_result.score, ambiguous, shorthand

    def _score_families(self, text: str) -> list[tuple[ProductFamily, FamilyMatchScore]]:
        scored = [
            (family, score_query_against_labels(text, _family_labels(family), self.vocabulary))
            for family in self.catalog.list_families()
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
