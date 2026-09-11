"""Apply user confirmation choices and build SearchScope."""

from __future__ import annotations

from typing import Any

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
        "processor",
        "connectivity",
        "display_inch",
        "battery_mah",
    }
)

VARIANT_CHOICE_KEY = "variant_id"


def variant_constraints(constraints: dict) -> dict:
    """Only keys that exist on ProductVariant are used to filter catalog rows."""
    return {key: value for key, value in constraints.items() if key in VARIANT_ATTRIBUTE_KEYS}


def variant_matches_constraints(variant: ProductVariant, constraints: dict) -> bool:
    for key, value in variant_constraints(constraints).items():
        if variant.attribute(key) != value:
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
        value = variant.attribute(key)
        if value is not None and value not in seen:
            seen.append(value)
    return seen


def _distance(wanted: Any, actual: Any) -> float:
    """0 for a match, 1 for a miss, in between for a near numeric build."""
    if actual is None:
        return 0.75
    if actual == wanted:
        return 0.0
    if isinstance(wanted, (int, float)) and isinstance(actual, (int, float)) and wanted:
        return min(1.0, abs(float(actual) - float(wanted)) / abs(float(wanted)))
    return 1.0


def rank_closest_variants(
    variants: list[ProductVariant],
    constraints: dict,
    limit: int = 3,
) -> list[ProductVariant]:
    """Order catalog builds by how far they sit from what the user asked for."""
    wanted = variant_constraints(constraints)
    if not wanted:
        return variants[:limit]

    def total_distance(variant: ProductVariant) -> tuple[float, str]:
        distance = sum(_distance(value, variant.attribute(key)) for key, value in wanted.items())
        return distance, variant.id

    return sorted(variants, key=total_distance)[:limit]


def _chosen_family_id(
    normalized: NormalizedQuery,
    choice: PropertyChoice | None,
    prompt: ConfirmationPrompt | None,
) -> str | None:
    if choice is None:
        return normalized.candidate_family_id
    if choice.kind != PropertyChoiceKind.VALUE or choice.value is None:
        raise ConfirmationError("Family confirmation requires a catalog family id.")
    family_id = str(choice.value)
    # Never let a choice widen the search past the families the popup offered.
    if prompt is None:
        if family_id != normalized.candidate_family_id:
            raise ConfirmationError("Family was not open for confirmation on this query.")
    elif family_id not in prompt.options:
        raise ConfirmationError(f"Family {family_id!r} was not one of the suggested matches.")
    return family_id


def _scope_from_variant(
    variant: ProductVariant,
    category_keys: list[str],
) -> SearchScope:
    constraints = {
        key: variant.attribute(key)
        for key in category_keys
        if variant.attribute(key) is not None
    }
    return SearchScope(
        family_id=variant.family_id,
        constraints=constraints,
        variant_ids=[variant.id],
    )


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
    choice_by_key = {choice.property_key: choice for choice in choices}
    pending_by_key = {prompt.property_key: prompt for prompt in normalized.pending_properties}

    family_id = _chosen_family_id(
        normalized,
        choice_by_key.get("family_id"),
        pending_by_key.get("family_id"),
    )
    if not family_id:
        raise ConfirmationError(
            "No catalog family matched the query — refine the search. "
            "Offers are never taken from another product family."
        )

    family = catalog.get_family(family_id)
    category = catalog.get_category(family.category_id) if family else None
    if family is None or category is None:
        raise ConfirmationError("Matched family is not in the catalog.")

    category_keys = list(category.identity_keys) + list(category.optional_keys)

    # "Closest build" popup: the family is right, the exact build is not stocked.
    variant_prompt = pending_by_key.get(VARIANT_CHOICE_KEY)
    if variant_prompt is not None:
        choice = choice_by_key.get(VARIANT_CHOICE_KEY)
        if choice is None or choice.kind != PropertyChoiceKind.VALUE or choice.value is None:
            raise ConfirmationError("Pick one of the suggested closest builds to search for.")
        variant_id = str(choice.value)
        if variant_id not in variant_prompt.options:
            raise ConfirmationError(f"Variant {variant_id!r} was not one of the suggestions.")
        variant = catalog.get_variant(variant_id)
        if variant is None or variant.family_id != family.id:
            raise ConfirmationError(f"Variant {variant_id!r} does not belong to {family.id}.")
        return _scope_from_variant(variant, category_keys), variant.id

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
