"""Similarity scoring tests."""

from gp_price_intel.normalize.query_normalizer import QueryNormalizer
from gp_price_intel.normalize.similarity import (
    score_query_against_labels,
    similarity,
    strip_spec_tokens,
    token_set_ratio,
)
from gp_price_intel.catalog.repository import CatalogRepository


def test_strip_spec_tokens_removes_storage_and_region() -> None:
    residue = strip_spec_tokens("Samsung Galaxy S26 Ultra 512 GB EU Black")
    assert "512" not in residue
    assert "eu" not in residue.casefold()
    assert "s26" in residue.casefold()
    assert "ultra" in residue.casefold()


def test_token_set_ratio_handles_word_order() -> None:
    score = token_set_ratio("ultra s26 samsung", "samsung galaxy s26 ultra")
    assert score > 0.75


def test_similarity_with_specs_in_query_still_matches_label() -> None:
    query = "Samsung Galaxy S26 Ultra 512 GB Black"
    label = "Samsung Galaxy S26 Ultra"
    assert similarity(strip_spec_tokens(query), label) > 0.75


def test_similarity_handles_typos() -> None:
    assert similarity("samsun galxy s26 ultra", "Samsung Galaxy S26 Ultra") > 0.55
    assert similarity("aple iphone 16 pro", "Apple iPhone 16 Pro") > 0.55


def test_similarity_does_not_confuse_unrelated_products() -> None:
    iphone = score_query_against_labels("MacBook Air M3 512GB", ["Apple iPhone 16 Pro"]).score
    macbook = score_query_against_labels("MacBook Air M3 512GB", ["Apple MacBook Air M3"]).score
    assert macbook > iphone


def test_s26u_compact_alias_is_shorthand() -> None:
    result = score_query_against_labels("s26u", ["S26U", "Galaxy S26 Ultra"])
    assert result.score >= 0.85
    assert result.shorthand is True


def test_s26u_normalizer_matches_with_family_confirmation() -> None:
    normalizer = QueryNormalizer(CatalogRepository())
    result = normalizer.normalize("s26u")
    assert result.candidate_family_id == "samsung-galaxy-s26-ultra"
    assert result.extracted.get("match_shorthand") is True
    assert result.needs_confirmation is True
    family_prompt = next(p for p in result.pending_properties if p.property_key == "family_id")
    assert family_prompt.reason.value == "shorthand"
    assert family_prompt.options[0] == "samsung-galaxy-s26-ultra"

def test_normalizer_matches_with_specs_and_reordered_words() -> None:
    normalizer = QueryNormalizer(CatalogRepository())
    result = normalizer.normalize("512gb black ultra s26 samsung galaxy")
    assert result.candidate_family_id == "samsung-galaxy-s26-ultra"
    assert result.extracted.get("storage_gb") == 512
    assert result.extracted.get("colour") == "Black"
