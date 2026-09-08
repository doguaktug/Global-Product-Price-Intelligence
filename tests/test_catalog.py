"""Catalog repository smoke tests."""

from gp_price_intel.catalog.repository import CatalogRepository


def test_catalog_loads_seed_data() -> None:
    repo = CatalogRepository()
    categories = repo.list_categories()
    families = repo.list_families()
    variants = repo.list_variants()

    assert {c.id for c in categories} >= {"smartphone", "laptop", "tablet"}
    family_ids = {f.id for f in families}
    assert "samsung-galaxy-s26-ultra" in family_ids
    assert "samsung-galaxy-s26" in family_ids
    assert "samsung-galaxy-s26-plus" in family_ids
    assert "samsung-galaxy-s25-ultra" in family_ids
    assert "samsung-galaxy-s25" in family_ids
    assert "apple-iphone-16-pro" in family_ids
    assert "apple-iphone-16" in family_ids
    assert "apple-iphone-16-pro-max" in family_ids
    assert "apple-iphone-16-plus" in family_ids
    assert "apple-macbook-air-m4" in family_ids
    assert "apple-macbook-pro-14-m4" in family_ids
    assert "asus-zenbook-14-oled" in family_ids
    assert "asus-rog-zephyrus-g14" in family_ids
    assert len(variants) >= 20


def test_family_valid_options_include_storage() -> None:
    repo = CatalogRepository()
    family = repo.get_family("samsung-galaxy-s26-ultra")
    assert family is not None
    assert 512 in family.valid_options["storage_gb"]
    assert "colour" in family.valid_options


def test_non_pro_iphone_and_asus_families_load() -> None:
    repo = CatalogRepository()
    iphone = repo.get_family("apple-iphone-16")
    assert iphone is not None
    assert iphone.brand == "Apple"
    assert 128 in iphone.valid_options["storage_gb"]

    zenbook = repo.get_family("asus-zenbook-14-oled")
    assert zenbook is not None
    assert zenbook.brand == "ASUS"
    variants = repo.list_variants("asus-rog-zephyrus-g14")
    assert variants
