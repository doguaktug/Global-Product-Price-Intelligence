"""Fixture adapter structural tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gp_price_intel.adapters.fixture import FixtureAdapter
from gp_price_intel.adapters.registry import load_sources
from gp_price_intel.config import Settings
from gp_price_intel.domain.models import SearchScope


@pytest.mark.asyncio
async def test_fixture_adapter_returns_scoped_offers_with_source_metadata(tmp_path: Path) -> None:
    fixture = tmp_path / "offers.json"
    fixture.write_text(
        json.dumps(
            {
                "offers": [
                    {
                        "id": "x1",
                        "source_id": "fixture-de",
                        "family_id": "samsung-galaxy-s26-ultra",
                        "variant_id": "samsung-galaxy-s26-ultra-512-12-eu-black",
                        "listing_title": "S26 Ultra",
                        "listing_url": "https://example.com",
                        "price": 1000,
                        "currency": "EUR",
                        "country": "DE",
                    },
                    {
                        "id": "x-out",
                        "source_id": "fixture-de",
                        "family_id": "other-family",
                        "variant_id": "other",
                        "listing_title": "Other",
                        "listing_url": "https://example.com/other",
                        "price": 10,
                        "currency": "EUR",
                        "country": "DE",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    sources = [s for s in load_sources() if s.id == "fixture-de"]
    adapter = FixtureAdapter(
        settings=Settings(data_dir=tmp_path.parent),
        sources=sources,
        fixture_path=fixture,
    )
    scope = SearchScope(
        family_id="samsung-galaxy-s26-ultra",
        variant_ids=["samsung-galaxy-s26-ultra-512-12-eu-black"],
    )

    offers = await adapter.search(scope, "TR")

    assert len(offers) == 1
    assert offers[0].id == "x1"
    assert offers[0].source_id == "fixture-de"
    assert offers[0].country == "DE"
    assert offers[0].list_price.currency == "EUR"
    assert 0.0 <= offers[0].data_confidence <= 1.0


_USED = {"used", "refurbished", "open_box"}


def test_every_catalog_variant_has_at_least_two_new_fixture_offers() -> None:
    """Catalog searches must still produce offers when live eBay is down."""
    repo_root = Path(__file__).resolve().parents[1]
    variants = json.loads((repo_root / "data" / "catalog" / "variants.json").read_text())
    payload = json.loads((repo_root / "data" / "fixtures" / "offers.json").read_text())

    counts: dict[str, int] = {}
    for row in payload["offers"]:
        if str(row.get("condition") or "").lower() in _USED:
            continue
        if row.get("stock_status") == "out_of_stock":
            continue
        variant_id = row.get("variant_id")
        if variant_id:
            counts[variant_id] = counts.get(variant_id, 0) + 1

    short = [
        f"{variant['id']} ({counts.get(variant['id'], 0)})"
        for variant in variants
        if counts.get(variant["id"], 0) < 2
    ]
    assert not short, "variants with fewer than 2 new fixture offers: " + ", ".join(short)
