"""Fixture adapter structural tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gp_price_intel.adapters.fixture import FixtureAdapter, FixtureDataError
from gp_price_intel.adapters.registry import load_sources
from gp_price_intel.config import Settings
from gp_price_intel.domain.models import SearchScope, StockStatus

FAMILY = "samsung-galaxy-s26-ultra"
VARIANT = "samsung-galaxy-s26-ultra-512-12-eu-black"


def _row(**overrides: object) -> dict:
    row = {
        "id": "x1",
        "source_id": "fixture-de",
        "family_id": FAMILY,
        "variant_id": VARIANT,
        "listing_title": "S26 Ultra",
        "listing_url": "https://example.com",
        "price": 1000,
        "currency": "EUR",
        "country": "DE",
    }
    row.update(overrides)
    return {key: value for key, value in row.items() if value is not _ABSENT}


class _Absent:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "<absent>"


_ABSENT = _Absent()


def _adapter(tmp_path: Path, rows: list[dict], source_ids: tuple[str, ...] = ("fixture-de",)):
    fixture = tmp_path / "offers.json"
    fixture.write_text(json.dumps({"offers": rows}), encoding="utf-8")
    return FixtureAdapter(
        settings=Settings(data_dir=tmp_path.parent),
        sources=[s for s in load_sources() if s.id in source_ids],
        fixture_path=fixture,
    )


def _scope() -> SearchScope:
    return SearchScope(family_id=FAMILY, variant_ids=[VARIANT])


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


# --- Nothing a row leaves out is invented ------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    ["id", "source_id", "family_id", "price", "currency", "listing_title", "listing_url"],
)
async def test_a_row_missing_a_required_field_is_reported_not_guessed(
    tmp_path: Path, field: str
) -> None:
    """
    Each of these was previously defaulted, and each default was a wrong answer.

    A missing currency read the price as EUR, a missing source invented a
    reliability, and a missing family dropped the row from every search in silence.
    """
    adapter = _adapter(tmp_path, [_row(**{field: _ABSENT})])

    with pytest.raises(FixtureDataError, match=field):
        await adapter.search(_scope(), "TR")


@pytest.mark.asyncio
async def test_an_unregistered_source_is_refused_rather_than_synthesized(
    tmp_path: Path,
) -> None:
    """
    A source used to be conjured from the row, with reliability 0.7.

    That is a ranking input, so a typo in `source_id` would have quietly produced a
    seller whose trustworthiness we had made up.
    """
    adapter = _adapter(tmp_path, [_row(source_id="fixture-de-typo")])

    with pytest.raises(FixtureDataError, match="source registry"):
        await adapter.search(_scope(), "TR")


@pytest.mark.asyncio
async def test_absent_stock_status_is_unknown_not_in_stock(tmp_path: Path) -> None:
    """Assuming in-stock guesses in the seller's favour and inflates confidence."""
    adapter = _adapter(tmp_path, [_row(stock_status=_ABSENT)])

    offers = await adapter.search(_scope(), "TR")

    assert [o.stock_status for o in offers] == [StockStatus.UNKNOWN]


@pytest.mark.asyncio
async def test_an_unrecognised_stock_status_is_reported(tmp_path: Path) -> None:
    """A typo is an authoring mistake, not a listing whose stock is unknowable."""
    adapter = _adapter(tmp_path, [_row(stock_status="instock")])

    with pytest.raises(FixtureDataError, match="stock_status"):
        await adapter.search(_scope(), "TR")


def test_an_adapter_with_no_sources_is_refused(tmp_path: Path) -> None:
    with pytest.raises(FixtureDataError, match="sources"):
        FixtureAdapter(settings=Settings(data_dir=tmp_path), sources=[])
