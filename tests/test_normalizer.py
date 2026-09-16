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


def test_base_s26_query_does_not_select_ultra(normalizer: QueryNormalizer) -> None:
    result = normalizer.normalize("Samsung Galaxy S26 256 GB Black")
    assert result.candidate_family_id == "samsung-galaxy-s26"
    assert result.candidate_family_id != "samsung-galaxy-s26-ultra"
    family_prompt = next(p for p in result.pending_properties if p.property_key == "family_id")
    assert family_prompt.options == [
        "samsung-galaxy-s26",
        "samsung-galaxy-s26-plus",
        "samsung-galaxy-s26-ultra",
    ]


def test_base_iphone_query_does_not_select_pro(normalizer: QueryNormalizer) -> None:
    result = normalizer.normalize("Apple iPhone 16 128 GB Black")
    assert result.candidate_family_id == "apple-iphone-16"
    family_prompt = next(p for p in result.pending_properties if p.property_key == "family_id")
    assert "apple-iphone-16" in family_prompt.options
    assert "apple-iphone-16-pro" in family_prompt.options
    pro = normalizer.normalize("Apple iPhone 16 Pro 256 GB")
    assert pro.candidate_family_id == "apple-iphone-16-pro"
    pro_prompt = next(p for p in pro.pending_properties if p.property_key == "family_id")
    assert pro_prompt.options == ["apple-iphone-16-pro", "apple-iphone-16-pro-max"]


def test_fuzzy_query_matches_family(normalizer: QueryNormalizer) -> None:
    result = normalizer.normalize("samsun s26 ultra 512gb")
    assert result.candidate_family_id == "samsung-galaxy-s26-ultra"
    assert result.extracted["storage_gb"] == 512


def test_missing_storage_prompts_identity_choice(normalizer: QueryNormalizer) -> None:
    result = normalizer.normalize("Samsung S26 Ultra")
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
    normalized = normalizer.normalize("Samsung S26 Ultra")
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


def test_asus_and_macbook_pro_queries_match_new_families(normalizer: QueryNormalizer) -> None:
    zenbook = normalizer.normalize("ASUS Zenbook 14 OLED 1TB")
    assert zenbook.candidate_family_id == "asus-zenbook-14-oled"
    mbp = normalizer.normalize("MacBook Pro 14 M4 1TB")
    assert mbp.candidate_family_id == "apple-macbook-pro-14-m4"
    air_m4 = normalizer.normalize("MacBook Air M4 512GB")
    assert air_m4.candidate_family_id == "apple-macbook-air-m4"


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
    normalized = normalizer.normalize("Samsung S26 Ultra")
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


def _prompt(result, key: str):
    return next(p for p in result.pending_properties if p.property_key == key)


def test_samsung_s25_asks_which_model_first(normalizer: QueryNormalizer) -> None:
    result = normalizer.normalize("samsung s25")

    assert result.needs_confirmation is True
    assert [p.property_key for p in result.pending_properties] == ["family_id"]
    prompt = _prompt(result, "family_id")
    assert prompt.reason == ConfirmationReason.AMBIGUOUS
    assert prompt.options == [
        "samsung-galaxy-s25",
        "samsung-galaxy-s25-plus",
        "samsung-galaxy-s25-ultra",
    ]
    assert not any(p.property_key == "storage_gb" for p in result.pending_properties)


def test_samsung_s25_plus_choice_then_asks_plus_specs() -> None:
    orch = SearchOrchestrator()
    session = orch.start_session("samsung s25")
    after_family = orch.apply_choices(
        session,
        [
            PropertyChoice(
                property_key="family_id",
                kind=PropertyChoiceKind.VALUE,
                value="samsung-galaxy-s25-plus",
            )
        ],
    )

    assert after_family.status == SessionStatus.NEEDS_CONFIRMATION
    assert after_family.normalized_query is not None
    assert after_family.normalized_query.candidate_family_id == "samsung-galaxy-s25-plus"
    keys = {p.property_key for p in after_family.normalized_query.pending_properties}
    assert "family_id" not in keys
    assert "storage_gb" in keys
    storage = _prompt(after_family.normalized_query, "storage_gb")
    assert set(storage.options) == {256, 512}


def test_asus_rog_asks_which_line_first(normalizer: QueryNormalizer) -> None:
    result = normalizer.normalize("asus rog")

    assert result.needs_confirmation is True
    assert [p.property_key for p in result.pending_properties] == ["line"]
    prompt = _prompt(result, "line")
    assert prompt.options == ["Flow", "Strix", "Zephyrus"]
    assert not any(p.property_key == "family_id" for p in result.pending_properties)


def test_asus_rog_zephyrus_then_asks_g14_or_g16() -> None:
    orch = SearchOrchestrator()
    session = orch.start_session("asus rog")
    after_line = orch.apply_choices(
        session,
        [PropertyChoice(property_key="line", kind=PropertyChoiceKind.VALUE, value="Zephyrus")],
    )

    assert after_line.status == SessionStatus.NEEDS_CONFIRMATION
    assert after_line.normalized_query is not None
    prompt = _prompt(after_line.normalized_query, "family_id")
    assert prompt.options == ["asus-rog-zephyrus-g14", "asus-rog-zephyrus-g16"]

    after_family = orch.apply_choices(
        after_line,
        [
            PropertyChoice(
                property_key="family_id",
                kind=PropertyChoiceKind.VALUE,
                value="asus-rog-zephyrus-g16",
            )
        ],
    )

    assert after_family.status == SessionStatus.NEEDS_CONFIRMATION
    assert after_family.normalized_query is not None
    assert after_family.normalized_query.candidate_family_id == "asus-rog-zephyrus-g16"
    keys = {p.property_key for p in after_family.normalized_query.pending_properties}
    assert "line" not in keys
    assert "family_id" not in keys
    assert "storage_gb" in keys
    storage = _prompt(after_family.normalized_query, "storage_gb")
    assert 2048 in storage.options


def test_named_zephyrus_g14_skips_line_and_model_prompts(normalizer: QueryNormalizer) -> None:
    result = normalizer.normalize("asus rog zephyrus g14")
    assert result.candidate_family_id == "asus-rog-zephyrus-g14"
    assert not any(p.property_key in {"line", "family_id"} for p in result.pending_properties)
