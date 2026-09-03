"""Catalog matching and confirmation popup tests."""

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
from gp_price_intel.normalize.similarity import similarity
from gp_price_intel.orchestrator.search import SearchOrchestrator


@pytest.fixture
def normalizer() -> QueryNormalizer:
    return QueryNormalizer(CatalogRepository())


def test_similarity_handles_typos() -> None:
    assert similarity("samsun galxy s26", "Samsung Galaxy S26 Ultra") > similarity(
        "samsun galxy s26", "Apple MacBook Air M3"
    )
    assert similarity("aple iphone", "Apple iPhone 16 Pro") > similarity(
        "aple iphone", "Samsung Galaxy S26 Ultra"
    )


def test_exact_query_skips_confirmation(normalizer: QueryNormalizer) -> None:
    result = normalizer.normalize("Samsung Galaxy S26 Ultra 512 GB Black")
    assert result.candidate_family_id == "samsung-galaxy-s26-ultra"
    assert result.extracted["storage_gb"] == 512
    assert result.extracted["colour"] == "Black"
    assert result.needs_confirmation is False
    assert "samsung-galaxy-s26-ultra-512-12-eu-black" in result.candidate_variant_ids
    assert len(result.candidate_variant_ids) == 1


def test_fuzzy_query_matches_family(normalizer: QueryNormalizer) -> None:
    result = normalizer.normalize("samsun s26 ultra 512gb")
    assert result.candidate_family_id == "samsung-galaxy-s26-ultra"
    assert result.extracted["storage_gb"] == 512


def test_missing_storage_prompts_identity_choice(normalizer: QueryNormalizer) -> None:
    result = normalizer.normalize("Samsung S26")
    assert result.needs_confirmation is True
    storage_prompt = next(p for p in result.pending_properties if p.property_key == "storage_gb")
    assert storage_prompt.role == PropertyRole.IDENTITY
    assert storage_prompt.reason == ConfirmationReason.MISSING
    assert storage_prompt.allow_not_important is False
    assert 512 in storage_prompt.options


def test_invalid_storage_prompts_correction(normalizer: QueryNormalizer) -> None:
    result = normalizer.normalize("Samsung S26 Ultra 600 GB")
    assert result.needs_confirmation is True
    storage_prompt = next(p for p in result.pending_properties if p.property_key == "storage_gb")
    assert storage_prompt.reason == ConfirmationReason.INVALID
    assert {256, 512, 1024}.issubset(set(storage_prompt.options))


def test_missing_colour_allows_not_important(normalizer: QueryNormalizer) -> None:
    result = normalizer.normalize("Samsung Galaxy S26 Ultra 512 GB")
    colour_prompt = next(p for p in result.pending_properties if p.property_key == "colour")
    assert colour_prompt.role == PropertyRole.OPTIONAL
    assert colour_prompt.allow_not_important is True
    assert "Black" in colour_prompt.options


def test_confirm_missing_storage_builds_scope(normalizer: QueryNormalizer) -> None:
    normalized = normalizer.normalize("Samsung S26")
    scope, confirmed_id = resolve_search_scope(
        CatalogRepository(),
        normalized,
        [PropertyChoice(property_key="storage_gb", kind=PropertyChoiceKind.VALUE, value=512)],
    )
    assert scope.family_id == "samsung-galaxy-s26-ultra"
    assert scope.constraints["storage_gb"] == 512
    assert confirmed_id is None  # colour still open in catalog variants
    assert len(scope.variant_ids) >= 2
    assert all("512" in variant_id for variant_id in scope.variant_ids)


def test_confirm_colour_not_important_searches_all_colours(normalizer: QueryNormalizer) -> None:
    catalog = CatalogRepository()
    normalized = normalizer.normalize("Samsung Galaxy S26 Ultra 512 GB")
    scope, _ = resolve_search_scope(
        catalog,
        normalized,
        [
            PropertyChoice(
                property_key="colour",
                kind=PropertyChoiceKind.NOT_IMPORTANT,
            )
        ],
    )
    assert "colour" in scope.unconstrained_keys
    assert len(scope.variant_ids) >= 2
    colours = {
        catalog.get_variant(variant_id).colour  # type: ignore[union-attr]
        for variant_id in scope.variant_ids
    }
    assert None not in colours
    assert len(colours) >= 2


def test_confirm_single_variant_sets_confirmed_id(normalizer: QueryNormalizer) -> None:
    normalized = normalizer.normalize("Samsung Galaxy S26 Ultra 512 GB")
    scope, confirmed_id = resolve_search_scope(
        CatalogRepository(),
        normalized,
        [
            PropertyChoice(
                property_key="colour",
                kind=PropertyChoiceKind.VALUE,
                value="Silver",
            )
        ],
    )
    assert confirmed_id == "samsung-galaxy-s26-ultra-512-12-eu-silver"
    assert len(scope.variant_ids) == 1


def test_orchestrator_session_needs_confirmation_for_incomplete_query() -> None:
    orch = SearchOrchestrator()
    session = orch.start_session("Samsung S26")
    assert session.status == SessionStatus.NEEDS_CONFIRMATION
    assert session.normalized_query is not None
    assert session.normalized_query.needs_confirmation is True


def test_orchestrator_apply_choices_clears_confirmation() -> None:
    orch = SearchOrchestrator()
    session = orch.start_session("Samsung Galaxy S26 Ultra 512 GB")
    updated = orch.apply_choices(
        session,
        [
            PropertyChoice(
                property_key="colour",
                kind=PropertyChoiceKind.NOT_IMPORTANT,
            )
        ],
    )
    assert updated.status == SessionStatus.RECEIVED
    assert updated.search_scope is not None
    assert updated.search_scope.unconstrained_keys == ["colour"]


def test_identity_not_important_rejected(normalizer: QueryNormalizer) -> None:
    normalized = normalizer.normalize("Samsung S26")
    with pytest.raises(ConfirmationError):
        resolve_search_scope(
            CatalogRepository(),
            normalized,
            [
                PropertyChoice(
                    property_key="storage_gb",
                    kind=PropertyChoiceKind.NOT_IMPORTANT,
                )
            ],
        )
