"""Used / refurbished / open-box listings are filtered; condition is indicated."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from gp_price_intel.adapters.fixture import FixtureAdapter
from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.domain.models import ItemCondition, SearchScope
from gp_price_intel.normalize.condition import (
    condition_label,
    is_non_new_condition,
    parse_item_condition,
)
from gp_price_intel.orchestrator.search import _empty_result_reason


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("New", ItemCondition.NEW),
        ("Brand New", ItemCondition.NEW),
        ("Used", ItemCondition.USED),
        ("Second hand", ItemCondition.USED),
        ("2nd hand", ItemCondition.USED),
        ("Gebraucht", ItemCondition.USED),
        ("ikinci el", ItemCondition.USED),
        ("中古", ItemCondition.USED),
        ("Manufacturer refurbished", ItemCondition.REFURBISHED),
        ("Open Box", ItemCondition.OPEN_BOX),
        ("", ItemCondition.UNKNOWN),
    ],
)
def test_parse_item_condition(text: str, expected: ItemCondition) -> None:
    assert parse_item_condition(text) == expected


def test_title_can_reveal_used_when_aspect_missing() -> None:
    assert (
        parse_item_condition(None, "Samsung Galaxy S26 Ultra 512GB ikinci el")
        == ItemCondition.USED
    )


def test_non_new_conditions_are_excluded() -> None:
    assert is_non_new_condition(ItemCondition.USED)
    assert is_non_new_condition(ItemCondition.REFURBISHED)
    assert is_non_new_condition(ItemCondition.OPEN_BOX)
    assert not is_non_new_condition(ItemCondition.NEW)
    assert not is_non_new_condition(ItemCondition.UNKNOWN)


def test_condition_labels_are_readable() -> None:
    assert "Used" in condition_label(ItemCondition.USED)
    assert condition_label(ItemCondition.NEW) == "New"


@pytest.mark.asyncio
async def test_fixture_adapter_drops_used_listings_by_default() -> None:
    catalog = CatalogRepository()
    offers = await FixtureAdapter(catalog=catalog).search(
        SearchScope(family_id="samsung-galaxy-s26-ultra"),
        destination_country="TR",
    )
    ids = {offer.id for offer in offers}
    assert "fixture-de-s26-512-black-used" not in ids


@pytest.mark.asyncio
async def test_fixture_adapter_keeps_used_when_include_used() -> None:
    catalog = CatalogRepository()
    offers = await FixtureAdapter(catalog=catalog).search(
        SearchScope(family_id="samsung-galaxy-s26-ultra"),
        destination_country="TR",
        include_used=True,
    )
    used = next(o for o in offers if o.id == "fixture-de-s26-512-black-used")
    assert used.condition == ItemCondition.USED


def test_empty_reason_mentions_used_filter() -> None:
    reason = _empty_result_reason(
        ref_currency="TRY",
        adapter_errors=[],
        fetched=3,
        out_of_stock=0,
        used_filtered=3,
        eligible=0,
        unmatched=0,
        conversion_failures=[],
        landed_failures=[],
        adapters_configured=True,
    )
    assert "used" in reason.lower()
