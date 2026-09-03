"""Similarity scoring invariants (relative + structural)."""

from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.normalize.query_normalizer import QueryNormalizer
from gp_price_intel.normalize.similarity import (
    score_query_against_labels,
    similarity,
    strip_spec_tokens,
    token_set_ratio,
)


def test_strip_spec_tokens_removes_storage_and_region() -> None:
    residue = strip_spec_tokens("Samsung Galaxy S26 Ultra 512 GB EU Black")
    assert "512" not in residue
    assert "eu" not in residue.casefold()
    assert "s26" in residue.casefold()
    assert "ultra" in residue.casefold()


def test_token_set_ratio_prefers_same_tokens_over_unrelated() -> None:
    related = token_set_ratio("ultra s26 samsung", "samsung galaxy s26 ultra")
    unrelated = token_set_ratio("ultra s26 samsung", "apple macbook air m3")
    assert related > unrelated


def test_similarity_with_specs_stripped_still_ranks_family_label_above_unrelated() -> None:
    query = strip_spec_tokens("Samsung Galaxy S26 Ultra 512 GB Black")
    family = similarity(query, "Samsung Galaxy S26 Ultra")
    unrelated = similarity(query, "Apple MacBook Air M3")
    assert family > unrelated


def test_typo_query_still_ranks_correct_label_above_unrelated() -> None:
    typo = "samsun galxy s26 ultra"
    assert similarity(typo, "Samsung Galaxy S26 Ultra") > similarity(typo, "Apple iPhone 16 Pro")
    assert similarity("aple iphone 16 pro", "Apple iPhone 16 Pro") > similarity(
        "aple iphone 16 pro", "Samsung Galaxy S26 Ultra"
    )


def test_similarity_does_not_confuse_unrelated_products() -> None:
    iphone = score_query_against_labels("MacBook Air M3 512GB", ["Apple iPhone 16 Pro"]).score
    macbook = score_query_against_labels("MacBook Air M3 512GB", ["Apple MacBook Air M3"]).score
    assert macbook > iphone


def test_compact_alias_marks_shorthand_and_beats_unrelated_label() -> None:
    result = score_query_against_labels("s26u", ["S26U", "Galaxy S26 Ultra"])
    unrelated = score_query_against_labels("s26u", ["Apple MacBook Air M3"]).score
    assert result.shorthand is True
    assert result.score > unrelated


def test_s26u_normalizer_matches_with_family_confirmation() -> None:
    normalizer = QueryNormalizer(CatalogRepository())
    result = normalizer.normalize("s26u")
    assert result.candidate_family_id == "samsung-galaxy-s26-ultra"
    assert result.extracted.get("match_shorthand") is True
    assert result.needs_confirmation is True
    family_prompt = next(p for p in result.pending_properties if p.property_key == "family_id")
    assert family_prompt.reason.value == "shorthand"
    assert "samsung-galaxy-s26-ultra" in family_prompt.options


def test_normalizer_matches_with_specs_and_reordered_words() -> None:
    normalizer = QueryNormalizer(CatalogRepository())
    result = normalizer.normalize("512gb black ultra s26 samsung galaxy")
    assert result.candidate_family_id == "samsung-galaxy-s26-ultra"
    assert result.extracted.get("storage_gb") == 512
    assert result.extracted.get("colour") == "Black"
