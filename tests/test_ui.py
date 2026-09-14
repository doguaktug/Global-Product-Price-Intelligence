"""UI shell smoke tests."""

from fastapi.testclient import TestClient

from gp_price_intel.api.main import create_app
from gp_price_intel.domain.models import DecisionHighlight, Explanation, HighlightKind
from gp_price_intel.ranking.highlights import collapse_highlights


def test_index_serves_the_search_page() -> None:
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "what are we looking for today?" in response.text
    assert "personalize weights" in response.text
    assert "change country/currency" in response.text


def test_ui_assets_are_served() -> None:
    client = TestClient(create_app())
    css = client.get("/ui/styles.css")
    js = client.get("/ui/app.js")
    assert css.status_code == 200
    assert js.status_code == 200
    assert "collapseHighlights" in js.text
    assert "originalSearchName" in js.text
    assert "searched-name" in css.text
    assert "scroll down for the best alternatives" in client.get("/").text
    assert ".view[hidden]" in css.text


def _hl(kind: HighlightKind, offer_id: str) -> DecisionHighlight:
    return DecisionHighlight(
        kind=kind,
        offer_id=offer_id,
        explanation=Explanation(headline=offer_id),
    )


def test_collapse_caps_unique_cards_at_five() -> None:
    highlights = [
        _hl(HighlightKind.BEST_OVERALL, "a"),
        _hl(HighlightKind.LOWEST_LIST_PRICE, "a"),
        _hl(HighlightKind.LOWEST_TOTAL_COST, "b"),
        _hl(HighlightKind.BEST_SELLER, "c"),
        _hl(HighlightKind.BEST_WARRANTY, "d"),
    ]
    groups = collapse_highlights(highlights)
    assert [group[0].offer_id for group in groups] == ["a", "b", "c", "d"]
    assert {item.kind for item in groups[0]} == {
        HighlightKind.BEST_OVERALL,
        HighlightKind.LOWEST_LIST_PRICE,
    }
