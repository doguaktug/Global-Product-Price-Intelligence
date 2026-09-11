"""Similarity scoring invariants (relative + structural)."""

from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.normalize.query_normalizer import QueryNormalizer
from gp_price_intel.normalize.similarity import (
    build_distinctive_vocabulary,
    score_query_against_labels,
    shares_distinctive_token,
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


def test_distinctive_tokens_separate_ultra_from_base() -> None:
    ultra = score_query_against_labels(
        "Samsung Galaxy S26 Ultra",
        ["Samsung Galaxy S26 Ultra", "S26 Ultra"],
    ).score
    base = score_query_against_labels(
        "Samsung Galaxy S26 Ultra",
        ["Samsung Galaxy S26", "Galaxy S26"],
    ).score
    assert ultra > base


def test_distinctive_tokens_separate_iphone_pro_from_base() -> None:
    pro = score_query_against_labels("Apple iPhone 16 Pro", ["Apple iPhone 16 Pro"]).score
    base = score_query_against_labels("Apple iPhone 16 Pro", ["Apple iPhone 16"]).score
    assert pro > base


def test_vocabulary_is_learned_from_the_catalog_not_hardcoded() -> None:
    """A brand the code has never heard of still gets its model names separated."""
    vocabulary = build_distinctive_vocabulary(
        [
            ("Dell", ["Dell XPS 14", "XPS 14"]),
            ("Dell", ["Dell Inspiron 14", "Inspiron 14"]),
            ("Dell", ["Dell Latitude 14", "Latitude 14"]),
        ]
    )

    assert vocabulary.holds("xps")
    assert vocabulary.holds("inspiron")
    # Shared by every Dell family, so it separates nothing within the brand.
    assert not vocabulary.holds("dell")

    xps = score_query_against_labels("Dell XPS 14", ["Dell XPS 14"], vocabulary).score
    inspiron = score_query_against_labels(
        "Dell XPS 14", ["Dell Inspiron 14"], vocabulary
    ).score
    assert xps > inspiron


def test_shorthand_needs_a_short_query_not_just_a_compact_alias() -> None:
    typed_in_full = score_query_against_labels("iPad Air 11", ["iPadAir11", "iPad Air 11 M3"])
    abbreviated = score_query_against_labels("ipadair11", ["iPadAir11", "iPad Air 11 M3"])

    assert typed_in_full.shorthand is False
    assert abbreviated.shorthand is True


def test_generation_tokens_keep_screen_sizes_apart() -> None:
    fourteen = score_query_against_labels("MacBook Pro 14", ["Apple MacBook Pro 14 M4"]).score
    sixteen = score_query_against_labels("MacBook Pro 14", ["Apple MacBook Pro 16 M4"]).score
    assert fourteen > sixteen


def test_unrelated_query_shares_no_token_with_any_family() -> None:
    labels = ["Apple iPhone 15", "iPhone 15"]
    assert shares_distinctive_token("Apple iPhone 15 Pro", labels) is True
    assert shares_distinctive_token("Dyson V15 vacuum cleaner", labels) is False
