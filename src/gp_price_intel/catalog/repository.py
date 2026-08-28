"""Reference catalog loader (small seed JSON, not a price warehouse)."""

from __future__ import annotations

import json
from pathlib import Path

from gp_price_intel.config import get_settings
from gp_price_intel.domain.models import Category, ProductFamily, ProductVariant


class CatalogRepository:
    """Loads curated categories, families, and variants from data/catalog."""

    def __init__(self, catalog_dir: Path | None = None) -> None:
        settings = get_settings()
        self.catalog_dir = catalog_dir or (settings.data_dir / "catalog")
        self._categories: dict[str, Category] = {}
        self._families: dict[str, ProductFamily] = {}
        self._variants: dict[str, ProductVariant] = {}
        self.reload()

    def reload(self) -> None:
        self._categories = {
            c.id: c for c in self._load_list("categories.json", Category)
        }
        self._families = {
            f.id: f for f in self._load_list("families.json", ProductFamily)
        }
        self._variants = {
            v.id: v for v in self._load_list("variants.json", ProductVariant)
        }

    def _load_list(self, filename: str, model: type) -> list:
        path = self.catalog_dir / filename
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [model.model_validate(item) for item in raw]

    def list_categories(self) -> list[Category]:
        return list(self._categories.values())

    def get_category(self, category_id: str) -> Category | None:
        return self._categories.get(category_id)

    def list_families(self, category_id: str | None = None) -> list[ProductFamily]:
        families = list(self._families.values())
        if category_id is None:
            return families
        return [f for f in families if f.category_id == category_id]

    def get_family(self, family_id: str) -> ProductFamily | None:
        return self._families.get(family_id)

    def list_variants(self, family_id: str | None = None) -> list[ProductVariant]:
        variants = list(self._variants.values())
        if family_id is None:
            return variants
        return [v for v in variants if v.family_id == family_id]

    def get_variant(self, variant_id: str) -> ProductVariant | None:
        return self._variants.get(variant_id)
