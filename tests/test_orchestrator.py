"""Domain and orchestrator smoke tests."""

from decimal import Decimal

from gp_price_intel.domain.models import Money, PreferenceOrigin, UserPreferences
from gp_price_intel.orchestrator.search import SearchOrchestrator


def test_money_is_immutable_value_object() -> None:
    price = Money(amount=Decimal("1399.00"), currency="EUR")
    assert price.currency == "EUR"
    assert price.amount == Decimal("1399.00")


def test_start_session_creates_received_or_confirm_status() -> None:
    orch = SearchOrchestrator()
    session = orch.start_session(
        "Samsung Galaxy S26 Ultra 512 GB",
        UserPreferences(),
    )
    assert session.id
    assert session.raw_query.startswith("Samsung")
    assert session.normalized_query is not None
    assert session.preferences.destination_country == "TR"
    assert session.preferences.reference_currency == "TRY"
    assert session.preferences.weights["warranty"] == 0.15
    assert session.preferences.weights["price"] == 0.40


def test_supplied_preferences_are_recorded_as_manual() -> None:
    session = SearchOrchestrator().start_session(
        "Samsung Galaxy S26 Ultra 512 GB", UserPreferences(destination_country="DE")
    )

    assert session.preferences.origin is PreferenceOrigin.MANUAL


def test_absent_preferences_stay_default() -> None:
    """The page has to be able to say "we assumed TR" rather than imply a choice."""
    session = SearchOrchestrator().start_session("Samsung Galaxy S26 Ultra 512 GB", None)

    assert session.preferences.origin is PreferenceOrigin.DEFAULT
    assert session.preferences.destination_country == "TR"


def test_an_explicit_origin_is_not_overwritten() -> None:
    """Leaves room for the proposed geolocation step to set its own origin later."""
    session = SearchOrchestrator().start_session(
        "Samsung Galaxy S26 Ultra 512 GB",
        UserPreferences(destination_country="DE", origin=PreferenceOrigin.GEOLOCATION),
    )

    assert session.preferences.origin is PreferenceOrigin.GEOLOCATION
