"""Apply user confirmation choices and build SearchScope."""

from __future__ import annotations

from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.domain.models import (
    ConfirmationPrompt,
    NormalizedQuery,
    ProductVariant,
    PropertyChoice,
    PropertyChoiceKind,
    PropertyRole,
    SearchScope,
)


class ConfirmationError(ValueError):
    """Raised when popup answers are incomplete or invalid."""


VARIANT_ATTRIBUTE_KEYS = frozenset(
    {
        "model_name",
        "model_number",
        "gtin",
        "storage_gb",
        "memory_gb",
        "region_version",
        "colour",
    }
)


def variant_constraints(constraints: dict) -> dict:
    """Only keys that exist on ProductVariant are used to filter catalog rows."""
    return {key: value for key, value in constraints.items() if key in VARIANT_ATTRIBUTE_KEYS}


def variant_matches_constraints(variant: ProductVariant, constraints: dict) -> bool:
    for key, value in variant_constraints(constraints).items():
        if getattr(variant, key, None) != value:
            return False
    return True


def filter_variants(
    variants: list[ProductVariant],
    constraints: dict,
) -> list[ProductVariant]:
    return [v for v in variants if variant_matches_constraints(v, constraints)]


def distinct_values(variants: list[ProductVariant], key: str) -> list:
    seen: list = []
    for variant in variants:
        value = getattr(variant, key, None)
        if value is not None and value not in seen:
            seen.append(value)
    return seen


def resolve_search_scope(
    catalog: CatalogRepository,
    normalized: NormalizedQuery,
    choices: list[PropertyChoice],
) -> tuple[SearchScope, str | None]:
    """
    Merge normalized extraction + popup choices into a SearchScope.

    Returns (scope, confirmed_variant_id) where confirmed_variant_id is set
    only when the scope collapses to exactly one catalog variant.
    """
    if not normalized.candidate_family_id:
        raise ConfirmationError("No catalog family matched the query.")

    choice_by_key = {choice.property_key: choice for choice in choices}
    family_id = normalized.candidate_family_id
    family_choice = choice_by_key.get("family_id")
    if family_choice is not None:
        if family_choice.kind != PropertyChoiceKind.VALUE or family_choice.value is None:
            raise ConfirmationError("Family confirmation requires a catalog family id.")
        family_id = str(family_choice.value)

    family = catalog.get_family(family_id)
    category = catalog.get_category(family.category_id) if family else None
    if family is None or category is None:
        raise ConfirmationError("Matched family is not in the catalog.")

    pending_by_key = {prompt.property_key: prompt for prompt in normalized.pending_properties}

    constraints = {**variant_constraints(normalized.extracted)}
    unconstrained_keys: list[str] = []

    for prompt in normalized.pending_properties:
        if prompt.property_key == "family_id":
            continue  # handled above

        choice = choice_by_key.get(prompt.property_key)
        if choice is None:
            if prompt.role == PropertyRole.IDENTITY:
                raise ConfirmationError(f"Missing required choice for {prompt.property_key}.")
            continue

        if choice.kind == PropertyChoiceKind.NOT_IMPORTANT:
            if prompt.role != PropertyRole.OPTIONAL or not prompt.allow_not_important:
                raise ConfirmationError(
                    f"'Not important' is not allowed for {prompt.property_key}."
                )
            unconstrained_keys.append(prompt.property_key)
            constraints.pop(prompt.property_key, None)
            continue

        if choice.value is None:
            raise ConfirmationError(f"Choice for {prompt.property_key} has no value.")

        if choice.value not in prompt.options:
            raise ConfirmationError(
                f"Invalid value {choice.value!r} for {prompt.property_key}."
            )
        constraints[prompt.property_key] = choice.value

    variants = filter_variants(catalog.list_variants(family.id), constraints)
    variant_ids = [v.id for v in variants]

    scope = SearchScope(
        family_id=family.id,
        constraints=constraints,
        unconstrained_keys=unconstrained_keys,
        variant_ids=variant_ids,
    )

    confirmed_variant_id = variant_ids[0] if len(variant_ids) == 1 else None
    return scope, confirmed_variant_id


def remaining_prompts_after_partial_choices(
    normalized: NormalizedQuery,
    choices: list[PropertyChoice],
) -> list[ConfirmationPrompt]:
    """Prompts still unanswered after a partial confirm submit."""
    answered = {c.property_key for c in choices}
    return [p for p in normalized.pending_properties if p.property_key not in answered]
