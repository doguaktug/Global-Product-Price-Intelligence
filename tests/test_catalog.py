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


def test_every_category_is_actually_stocked() -> None:
    repo = CatalogRepository()
    by_category: dict[str, list[str]] = {c.id: [] for c in repo.list_categories()}
    for family in repo.list_families():
        by_category[family.category_id].append(family.id)

    for category_id, family_ids in by_category.items():
        assert family_ids, f"{category_id} has no families"
        for family_id in family_ids:
            variants = repo.list_variants(family_id)
            # One variant per family disables the confirmation gate and leaves the
            # alternatives scout with nothing to compare against.
            assert len(variants) >= 2, f"{family_id} has {len(variants)} variant(s)"


def test_categories_key_off_different_identity_specs() -> None:
    repo = CatalogRepository()
    laptop = repo.get_category("laptop")
    tablet = repo.get_category("tablet")
    phone = repo.get_category("smartphone")
    assert laptop is not None and tablet is not None and phone is not None

    assert "processor" in laptop.identity_keys
    assert "connectivity" in tablet.identity_keys
    assert "processor" not in phone.identity_keys
    assert "connectivity" not in phone.identity_keys


def test_laptop_and_tablet_variants_carry_their_identity_specs() -> None:
    repo = CatalogRepository()
    for family in repo.list_families("laptop"):
        for variant in repo.list_variants(family.id):
            assert variant.processor, variant.id
    for family in repo.list_families("tablet"):
        for variant in repo.list_variants(family.id):
            assert variant.connectivity, variant.id


def test_variant_attribute_reads_canonical_specs() -> None:
    repo = CatalogRepository()
    variant = repo.get_variant("apple-macbook-pro-16-m4-1024-24-us-silver")
    assert variant is not None

    assert variant.attribute("processor") == "M4 Pro"  # declared field
    assert variant.attribute("display_inch") == 16.2  # canonical_specs only
    assert variant.attribute("battery_mah") is None


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


def test_tablet_families_load_with_connectivity_options() -> None:
    repo = CatalogRepository()
    family_ids = {f.id for f in repo.list_families("tablet")}
    assert {
        "apple-ipad-air-11-m3",
        "apple-ipad-pro-13-m4",
        "samsung-galaxy-tab-s10-ultra",
    } <= family_ids

    ipad_air = repo.get_family("apple-ipad-air-11-m3")
    assert ipad_air is not None
    assert "Wi-Fi + Cellular" in ipad_air.valid_options["connectivity"]
