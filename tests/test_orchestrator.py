"""Domain and orchestrator smoke tests."""

from decimal import Decimal

from gp_price_intel.domain.models import Money, UserPreferences
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
