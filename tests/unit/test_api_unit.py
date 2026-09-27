"""API behaviour that needs no database."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.main import app, get_database_url


def test_database_down_returns_503():
    app.dependency_overrides[get_database_url] = lambda: (
        "postgresql://nobody:nothing@127.0.0.1:1/none"
    )
    try:
        r = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503 and r.json()["detail"] == "database unavailable"


def test_openapi_lists_endpoints():
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    for p in ("/health", "/detections", "/fires", "/fires/top", "/stats/daily", "/ingest-runs"):
        assert p in paths
