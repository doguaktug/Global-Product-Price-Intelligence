"""Build source adapters from data/sources registry + environment."""

from __future__ import annotations

import json
from pathlib import Path

from gp_price_intel.adapters.base import SourceAdapter
from gp_price_intel.adapters.ebay import EbayAdapter
from gp_price_intel.adapters.fixture import FixtureAdapter
from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.config import Settings, get_settings
from gp_price_intel.domain.models import AcquisitionMethod, Source


def load_sources(data_dir: Path | None = None) -> list[Source]:
    settings = get_settings()
    path = (data_dir or settings.data_dir) / "sources" / "sources.json"
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Source.model_validate(item) for item in raw]


def build_adapters(
    catalog: CatalogRepository | None = None,
    settings: Settings | None = None,
) -> list[SourceAdapter]:
    catalog = catalog or CatalogRepository()
    settings = settings or get_settings()
    sources = {source.id: source for source in load_sources(settings.data_dir)}
    adapters: list[SourceAdapter] = []

    if settings.ebay_app_id and settings.ebay_cert_id:
        adapters.append(
            EbayAdapter(
                source=sources.get("ebay"),
                catalog=catalog,
                settings=settings,
            )
        )

    fixture_sources = [
        source
        for source in sources.values()
        if source.acquisition_method == AcquisitionMethod.FIXTURE
    ]
    adapters.append(FixtureAdapter(catalog=catalog, settings=settings, sources=fixture_sources))

    return adapters
