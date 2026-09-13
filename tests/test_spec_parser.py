"""Unit normalization for specs that sources write in their own units and punctuation."""

from __future__ import annotations

import json
from pathlib import Path

from gp_price_intel.normalize.spec_parser import (
    parse_capacity_gb,
    parse_charge_mah,
    parse_length_inch,
    parse_number,
    parse_source_specs,
    parse_spec_value,
)

FIXTURE_PATH = Path("data/fixtures/offers.json")


def test_grouped_thousands_and_decimal_comma_are_told_apart() -> None:
    """'5,000' is five thousand; '6,9' is six point nine. Both reach us as source text."""
    assert parse_number("5,000") == 5000
    assert parse_number("5.000") == 5000
    assert parse_number("6,9") == 6.9
    assert parse_number("6.9") == 6.9
    assert parse_number("no digits here") is None


def test_the_assignment_battery_example_normalizes_to_one_value() -> None:
    """'5,000 mAh', '5000mAh' and '5 Ah' are the same battery."""
    assert parse_charge_mah("5,000 mAh") == 5000
    assert parse_charge_mah("5000mAh") == 5000
    assert parse_charge_mah("5.000 mAh") == 5000
    assert parse_charge_mah("5 Ah") == 5000
    assert parse_charge_mah("5.0 Ah") == 5000
    assert parse_charge_mah("4500") == 4500


def test_screen_size_normalizes_across_locales() -> None:
    assert parse_length_inch('6.9"') == 6.9
    assert parse_length_inch("6,9 inç") == 6.9
    assert parse_length_inch("6.9 inch") == 6.9
    assert parse_length_inch("6.9型") == 6.9
    assert parse_length_inch("6.9 Zoll") == 6.9
    # Metric listings have to compare against imperial ones.
    assert parse_length_inch("17,5 cm") == 6.9
    assert parse_length_inch("175 mm") == 6.9


def test_capacity_units_decide_the_scale() -> None:
    """A terabyte drive must not come back as the number 1."""
    assert parse_capacity_gb("1 TB") == 1024
    assert parse_capacity_gb("1TB") == 1024
    assert parse_capacity_gb("512 GB") == 512
    assert parse_capacity_gb("512GB") == 512
    assert parse_capacity_gb("12 GB RAM") == 12


def test_unreadable_spec_text_yields_nothing_rather_than_a_wrong_value() -> None:
    assert parse_charge_mah("see specifications") is None
    assert parse_length_inch(None) is None
    assert parse_capacity_gb("varies") is None
    assert parse_spec_value("battery_mah", "n/a") is None


def test_unknown_keys_pass_through_as_trimmed_text() -> None:
    assert parse_spec_value("colour", "  Sky Blue ") == "Sky Blue"
    assert parse_spec_value("colour", "   ") is None


def test_already_typed_values_are_left_alone() -> None:
    parsed = parse_source_specs({"storage_gb": 512, "display_inch": 6.9, "colour": None})
    assert parsed == {"storage_gb": 512, "display_inch": 6.9}


def test_every_fixture_offer_publishes_spec_text_that_normalizes() -> None:
    """The fixtures are the demo's data quality story — they must actually parse."""
    offers = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["offers"]
    assert offers

    for offer in offers:
        source_specs = offer.get("source_specs")
        assert source_specs, f"{offer['id']} publishes no source spec text"
        parsed = parse_source_specs(source_specs)
        for key in source_specs:
            assert key in parsed, f"{offer['id']} wrote {key} unreadably"


def test_fixture_spec_text_agrees_with_its_curated_columns() -> None:
    """
    Guards the parser against drift.

    Each fixture row carries both the messy string a retailer would publish and the
    curated typed column. They must resolve to the same number, or normalization is
    quietly changing what the catalog says the product is.
    """
    offers = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["offers"]
    checked = 0

    for offer in offers:
        parsed = parse_source_specs(offer["source_specs"])
        for key in ("storage_gb", "memory_gb"):
            if key in offer and key in parsed:
                assert parsed[key] == offer[key], (
                    f"{offer['id']}: {key} text {offer['source_specs'][key]!r} "
                    f"parsed to {parsed[key]} but the column says {offer[key]}"
                )
                checked += 1

    assert checked >= 20


def test_the_same_battery_written_five_ways_lands_on_one_number() -> None:
    offers = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["offers"]
    phones = [
        parse_source_specs(offer["source_specs"])["battery_mah"]
        for offer in offers
        if offer["family_id"] == "samsung-galaxy-s26-ultra"
    ]
    spellings = {
        offer["source_specs"]["battery_mah"]
        for offer in offers
        if offer["family_id"] == "samsung-galaxy-s26-ultra"
    }

    assert len(spellings) >= 4, "fixtures should exercise several spellings"
    assert set(phones) == {5000}
