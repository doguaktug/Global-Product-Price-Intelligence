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
    assert 'id="include-used"' in response.text
    assert "include used / refurbished" in response.text
    assert response.headers.get("cache-control", "").startswith("no-store")
    assert "/ui/app.js?v=" in response.text
    assert "/ui/styles.css?v=" in response.text


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
    # `hidden` has to beat the class `display` rules, or the views and the ranked
    # list render on top of each other instead of staying closed.
    assert "[hidden] {\n  display: none !important;\n}" in css.text
    # Product names are the retailer link, not a separate control.
    assert "function listingLink(" in js.text
    assert 'link.className = "product-link"' in js.text
    assert "function highlightProductName(" in js.text
    assert "product.append(listingLink(productName, offer))" in js.text
    assert "highlight-empty" in js.text
    assert "heading.append(listingLink(offer.listing_title, offer))" in js.text
    assert "strong.append(listingLink(offer.listing_title, offer))" in js.text
    assert ".product-link {" in css.text
    assert ".highlight-empty {" in css.text
    assert "include_used: Boolean($(\"include-used\")?.checked)" in js.text
    assert "function appendCondition(" in js.text
    assert ".check-row:has(input:checked)" in css.text
    assert "Which model should we search for?" in js.text
    assert "Which series should we search for?" in js.text
    assert "Which line should we search for?" in js.text
    assert 'session.status === "needs_confirmation"' in js.text


def test_sidebar_has_no_placeholder_menu() -> None:
    html = TestClient(create_app()).get("/").text
    assert "rail-nav" not in html
    assert "rail-item" not in html
    assert "weight system" not in html
    assert "trust system" not in html


def test_decision_cards_put_reasons_under_the_price() -> None:
    client = TestClient(create_app())
    js = client.get("/ui/app.js").text
    css = client.get("/ui/styles.css").text
    assert "ARGUMENT_FACTORS" in js
    assert "function priceBlock(" in js
    assert "function explanationBlock(" in js
    assert "why-list" in js
    assert "card-notes" in js
    assert "card.append(priceBlock(offer))" in js
    assert "card.append(explanationBlock(best.explanation, whyLabel))" in js
    assert js.index("card.append(priceBlock(offer))") < js.index(
        "card.append(explanationBlock(best.explanation, whyLabel))"
    )
    assert "width: fit-content" in css
    assert "subgrid" in css
    assert ".price-block:hover .cost-details" in css
    assert ".offer-card,\n.alt-card {\n  display: flex" in css
    assert "function specChangeReasons(" in js
    assert "function formatCapacityGb(" in js
    assert "forAlternative: true" in js
    assert ".spec-change {" in css
    assert "whole / 1024" in js


def test_the_ranked_list_starts_hidden_behind_its_toggle() -> None:
    client = TestClient(create_app())
    html = client.get("/").text
    js = client.get("/ui/app.js").text
    assert 'id="full-list" hidden' in html
    assert "list all other options" in html
    # Re-hidden on every render, so a second search cannot inherit an open list.
    assert "list.hidden = true;" in js


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
