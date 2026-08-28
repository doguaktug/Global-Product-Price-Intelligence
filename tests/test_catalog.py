"""Catalog repository smoke tests."""

from gp_price_intel.catalog.repository import CatalogRepository


def test_catalog_loads_seed_data() -> None:
    repo = CatalogRepository()
    categories = repo.list_categories()
    families = repo.list_families()
    variants = repo.list_variants()

    assert {c.id for c in categories} >= {"smartphone", "laptop", "tablet"}
    assert any(f.id == "samsung-galaxy-s26-ultra" for f in families)
    assert len(variants) >= 3


def test_family_valid_options_include_storage() -> None:
    repo = CatalogRepository()
    family = repo.get_family("samsung-galaxy-s26-ultra")
    assert family is not None
    assert 512 in family.valid_options["storage_gb"]
    assert "colour" in family.valid_options
