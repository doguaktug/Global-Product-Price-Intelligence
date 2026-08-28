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
    parse_colour,
    parse_memory_gb,
    parse_region_version,
    parse_storage_gb,
)
from gp_price_intel.normalize.similarity import FamilyMatchScore, score_query_against_labels

FAMILY_MATCH_THRESHOLD = 0.45
FAMILY_AMBIGUITY_GAP = 0.06


class QueryNormalizer:
    """Parse user text → structured attributes + confirmation prompts."""

    def __init__(self, catalog: CatalogRepository | None = None) -> None:
        self.catalog = catalog or CatalogRepository()

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
                        reason=ConfirmationReason.AMBIGUOUS,
                        options=[f.id for f in self.catalog.list_families()],
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
                    options=self._family_option_ids(text),
                    allow_not_important=False,
                )
            )
        elif family_shorthand:
            pending.append(
                ConfirmationPrompt(
                    property_key="family_id",
                    role=PropertyRole.IDENTITY,
                    reason=ConfirmationReason.SHORTHAND,
                    options=self._family_option_ids(text),
                    allow_not_important=False,
                )
            )

        # Parse explicit attributes from the query text.
        parsed_storage = parse_storage_gb(text)
        parsed_memory = parse_memory_gb(text)
        parsed_region = parse_region_version(text)
        valid_colours = family.valid_options.get("colour", [])
        parsed_colour = parse_colour(text, valid_colours)

        parsed_by_key = {
            "storage_gb": parsed_storage,
            "memory_gb": parsed_memory,
            "region_version": parsed_region,
            "colour": parsed_colour,
        }

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
                candidate_variants = self._filter(candidate_variants, key, constraints[key])

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
                candidate_variants = self._filter(candidate_variants, key, constraints[key])

        extracted.update(constraints)
        candidate_variant_ids = [v.id for v in candidate_variants]

        needs_confirmation = bool(pending) or family_ambiguous or family_shorthand

        return NormalizedQuery(
            raw_text=text,
            extracted=extracted,
            candidate_family_id=family.id,
            candidate_variant_ids=candidate_variant_ids,
            needs_confirmation=needs_confirmation,
            pending_properties=pending,
        )

    def _match_family(
        self, text: str
    ) -> tuple[ProductFamily | None, float, bool, bool]:
        scored: list[tuple[ProductFamily, FamilyMatchScore]] = []
        for family in self.catalog.list_families():
            labels = [
                f"{family.brand} {family.family_name}",
                family.family_name,
                *family.aliases,
            ]
            result = score_query_against_labels(text, labels)
            scored.append((family, result))

        scored.sort(key=lambda item: item[1].score, reverse=True)
        if not scored or scored[0][1].score < FAMILY_MATCH_THRESHOLD:
            return None, 0.0, False, False

        top_family, top_result = scored[0]
        ambiguous = len(scored) > 1 and (top_result.score - scored[1][1].score) < FAMILY_AMBIGUITY_GAP
        shorthand = top_result.shorthand and not ambiguous
        return top_family, top_result.score, ambiguous, shorthand

    def _family_option_ids(self, text: str) -> list[str]:
        """Rank families by similarity for family confirmation picker."""
        ranked = [
            (
                family.id,
                score_query_against_labels(
                    text, [f"{family.brand} {family.family_name}", *family.aliases]
                ).score,
            )
            for family in self.catalog.list_families()
        ]
        ranked.sort(key=lambda item: item[1], reverse=True)
        return [family_id for family_id, _ in ranked]

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

        distinct = self._distinct_values(variants, key)
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

    @staticmethod
    def _distinct_values(variants: list[ProductVariant], key: str) -> list[Any]:
        values: list[Any] = []
        for variant in variants:
            value = getattr(variant, key, None)
            if value is not None and value not in values:
                values.append(value)
        return values

    @staticmethod
    def _filter(
        variants: list[ProductVariant],
        key: str,
        value: Any,
    ) -> list[ProductVariant]:
        return [v for v in variants if getattr(v, key, None) == value]
