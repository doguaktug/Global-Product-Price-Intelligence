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
