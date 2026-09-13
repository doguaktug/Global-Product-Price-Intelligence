"""Domain and orchestrator smoke tests."""

from decimal import Decimal

from gp_price_intel.config import Settings
from gp_price_intel.domain.models import Money, PreferenceOrigin, UserPreferences
from gp_price_intel.orchestrator.search import SearchOrchestrator

QUERY = "Samsung Galaxy S26 Ultra 512 GB"


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


def _configured(**overrides: str) -> SearchOrchestrator:
    return SearchOrchestrator(settings=Settings(**overrides))


def test_the_configured_defaults_are_what_ranking_uses() -> None:
    """
    `/health` advertises these settings, so a search has to honour the same ones.

    The literals on `UserPreferences` keep the domain model a leaf that needs no
    environment; they are not the deployment's defaults.
    """
    orch = _configured(
        default_destination_country="DE", default_reference_currency="EUR"
    )

    prefs = orch.start_session(QUERY, None).preferences

    assert prefs.destination_country == "DE"
    assert prefs.reference_currency == "EUR"


def test_moving_only_the_sliders_is_not_a_chosen_destination() -> None:
    """
    Weights are not a country.

    A caller that sent preferences without one has still not chosen where they are
    buying to, so the configured default applies and the page must be able to say
    it was assumed.
    """
    orch = _configured(
        default_destination_country="DE", default_reference_currency="EUR"
    )

    prefs = orch.start_session(QUERY, UserPreferences(weights={"price": 1.0})).preferences

    assert prefs.destination_country == "DE"
    assert prefs.reference_currency == "EUR"
    assert prefs.origin is PreferenceOrigin.DEFAULT


def test_an_explicit_choice_beats_the_configured_default() -> None:
    orch = _configured(
        default_destination_country="DE", default_reference_currency="EUR"
    )

    prefs = orch.start_session(QUERY, UserPreferences(reference_currency="GBP")).preferences

    assert prefs.reference_currency == "GBP"
    assert prefs.destination_country == "DE"
    assert prefs.origin is PreferenceOrigin.MANUAL


def test_a_choice_equal_to_the_default_still_counts_as_chosen() -> None:
    """Sending `TR` explicitly is a decision, even where it matches the default."""
    prefs = (
        _configured(default_destination_country="TR")
        .start_session(QUERY, UserPreferences(destination_country="TR"))
        .preferences
    )

    assert prefs.origin is PreferenceOrigin.MANUAL
