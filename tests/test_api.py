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
