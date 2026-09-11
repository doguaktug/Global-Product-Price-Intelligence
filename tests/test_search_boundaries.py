"""A search only ever runs against the family the user actually asked for."""

from __future__ import annotations

import pytest

from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.domain.models import (
    ConfirmationReason,
    PropertyChoice,
    PropertyChoiceKind,
    PropertyRole,
    SessionStatus,
)
from gp_price_intel.normalize.confirmation import ConfirmationError, resolve_search_scope
from gp_price_intel.normalize.query_normalizer import QueryNormalizer
from gp_price_intel.orchestrator.search import SearchOrchestrator

UNKNOWN_QUERIES = ["Dyson V15 vacuum cleaner", "espresso machine 900", "Nikon Z6 III body"]


@pytest.fixture
def catalog() -> CatalogRepository:
    return CatalogRepository()


@pytest.fixture
def normalizer(catalog: CatalogRepository) -> QueryNormalizer:
    return QueryNormalizer(catalog)


@pytest.mark.parametrize("query", UNKNOWN_QUERIES)
def test_unknown_product_never_offers_another_family(
    normalizer: QueryNormalizer, query: str
) -> None:
    result = normalizer.normalize(query)

    assert result.candidate_family_id is None
    assert result.needs_confirmation is True
    assert result.candidate_variant_ids == []
    prompt = next(p for p in result.pending_properties if p.property_key == "family_id")
    assert prompt.reason == ConfirmationReason.NO_MATCH
    assert prompt.options == []


@pytest.mark.parametrize("query", UNKNOWN_QUERIES)
def test_unknown_product_cannot_start_a_search(
    catalog: CatalogRepository, normalizer: QueryNormalizer, query: str
) -> None:
    normalized = normalizer.normalize(query)

    with pytest.raises(ConfirmationError, match="No catalog family matched"):
        resolve_search_scope(catalog, normalized, [])


def test_unknown_product_cannot_be_forced_onto_a_catalog_family(
    catalog: CatalogRepository, normalizer: QueryNormalizer
) -> None:
    normalized = normalizer.normalize("Dyson V15 vacuum cleaner")

    with pytest.raises(ConfirmationError, match="not one of the suggested matches"):
        resolve_search_scope(
            catalog,
            normalized,
            [
                PropertyChoice(
                    property_key="family_id",
                    kind=PropertyChoiceKind.VALUE,
                    value="apple-iphone-15",
                )
            ],
        )


@pytest.mark.asyncio
async def test_orchestrator_refuses_to_run_an_unmatched_query() -> None:
    orch = SearchOrchestrator()
    session = orch.start_session("Dyson V15 vacuum cleaner")

    assert session.status == SessionStatus.NEEDS_CONFIRMATION
    with pytest.raises(ConfirmationError):
        await orch.run(session)


def test_near_miss_suggestions_stay_inside_the_product_line(
    normalizer: QueryNormalizer, catalog: CatalogRepository
) -> None:
    """An unreleased generation should suggest its own line, not every family."""
    result = normalizer.normalize("Galaxy S27 Ultra 512GB")

    prompt = next(p for p in result.pending_properties if p.property_key == "family_id")
    assert prompt.options
    for family_id in prompt.options:
        family = catalog.get_family(family_id)
        assert family is not None
        assert "Ultra" in family.family_name


def test_missing_build_suggests_the_closest_variants(normalizer: QueryNormalizer) -> None:
    # The M4 Air is sold as 256/16 or 1024/32 — never 256/32.
    result = normalizer.normalize("MacBook Air M4 256GB 32GB RAM")

    assert result.candidate_family_id == "apple-macbook-air-m4"
    assert result.needs_confirmation is True
    prompt = next(p for p in result.pending_properties if p.property_key == "variant_id")
    assert prompt.reason == ConfirmationReason.NO_EXACT_VARIANT
    assert prompt.role == PropertyRole.IDENTITY
    assert prompt.allow_not_important is False
    assert prompt.options
    assert all(option.startswith("apple-macbook-air-m4") for option in prompt.options)
    assert result.candidate_variant_ids == prompt.options


def test_closest_build_choice_collapses_the_scope(
    catalog: CatalogRepository, normalizer: QueryNormalizer
) -> None:
    normalized = normalizer.normalize("MacBook Air M4 256GB 32GB RAM")
    prompt = next(p for p in normalized.pending_properties if p.property_key == "variant_id")
    picked = prompt.options[0]

    scope, confirmed_id = resolve_search_scope(
        catalog,
        normalized,
        [PropertyChoice(property_key="variant_id", kind=PropertyChoiceKind.VALUE, value=picked)],
    )

    assert confirmed_id == picked
    assert scope.variant_ids == [picked]
    assert scope.family_id == "apple-macbook-air-m4"
    variant = catalog.get_variant(picked)
    assert variant is not None
    assert scope.constraints["storage_gb"] == variant.storage_gb
    assert scope.constraints["processor"] == variant.processor


def test_closest_build_requires_an_answer(
    catalog: CatalogRepository, normalizer: QueryNormalizer
) -> None:
    normalized = normalizer.normalize("MacBook Air M4 256GB 32GB RAM")

    with pytest.raises(ConfirmationError, match="closest builds"):
        resolve_search_scope(catalog, normalized, [])


def test_closest_build_choice_cannot_cross_families(
    catalog: CatalogRepository, normalizer: QueryNormalizer
) -> None:
    normalized = normalizer.normalize("MacBook Air M4 256GB 32GB RAM")

    with pytest.raises(ConfirmationError, match="not one of the suggestions"):
        resolve_search_scope(
            catalog,
            normalized,
            [
                PropertyChoice(
                    property_key="variant_id",
                    kind=PropertyChoiceKind.VALUE,
                    value="apple-iphone-16-128-8-eu-black",
                )
            ],
        )
