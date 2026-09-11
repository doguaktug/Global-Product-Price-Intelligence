"""API health smoke test."""

from fastapi.testclient import TestClient

from gp_price_intel.api.main import create_app


def test_health() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["default_country"] == "TR"
    assert body["default_currency"] == "TRY"


def test_catalog_categories_endpoint() -> None:
    client = TestClient(create_app())
    response = client.get("/api/catalog/categories")
    assert response.status_code == 200
    ids = {row["id"] for row in response.json()}
    assert "smartphone" in ids


def test_normalize_endpoint_flags_missing_storage() -> None:
    client = TestClient(create_app())
    response = client.post("/api/search/normalize", json={"query": "Samsung S26"})
    assert response.status_code == 200
    body = response.json()
    assert body["needs_confirmation"] is True
    keys = {p["property_key"] for p in body["pending_properties"]}
    assert "storage_gb" in keys


def test_normalize_endpoint_reports_an_unmatched_query_with_no_options() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/search/normalize", json={"query": "Dyson V15 vacuum cleaner"}
    )
    assert response.status_code == 200
    body = response.json()

    assert body["candidate_family_id"] is None
    assert body["needs_confirmation"] is True
    prompt = next(p for p in body["pending_properties"] if p["property_key"] == "family_id")
    assert prompt["reason"] == "no_match"
    assert prompt["options"] == []


def test_normalize_endpoint_offers_the_closest_build_as_a_popup() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/search/normalize", json={"query": "MacBook Air M4 256GB 32GB RAM"}
    )
    assert response.status_code == 200
    body = response.json()

    assert body["candidate_family_id"] == "apple-macbook-air-m4"
    prompt = next(p for p in body["pending_properties"] if p["property_key"] == "variant_id")
    assert prompt["reason"] == "no_exact_variant"
    assert prompt["allow_not_important"] is False
    assert prompt["options"]


def test_confirm_endpoint_rejects_an_unmatched_query() -> None:
    client = TestClient(create_app())
    started = client.post("/api/search/start", json={"query": "Dyson V15 vacuum cleaner"})
    assert started.status_code == 200
    session = started.json()
    assert session["status"] == "needs_confirmation"

    confirmed = client.post(
        "/api/search/confirm", json={"session": session, "choices": []}
    )
    assert confirmed.status_code == 422
    assert "No catalog family matched" in confirmed.json()["detail"]


def test_tablet_category_is_browsable() -> None:
    client = TestClient(create_app())
    response = client.get("/api/catalog/families", params={"category_id": "tablet"})
    assert response.status_code == 200
    ids = {row["id"] for row in response.json()}
    assert "apple-ipad-air-11-m3" in ids
