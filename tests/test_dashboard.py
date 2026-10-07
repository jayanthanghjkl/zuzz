"""Phase 9 dashboard tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from face.api.server import create_app
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_dashboard_endpoint_returns_payload(client: TestClient) -> None:
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    data = response.json()
    assert "brain" in data
    assert "resources" in data
    assert "activity" in data
    assert "health_score" in data


def test_dashboard_brain_regions_present(client: TestClient) -> None:
    data = client.get("/api/dashboard").json()
    regions = data.get("brain", {}).get("regions", [])
    assert len(regions) > 0


def test_dashboard_resource_gauges(client: TestClient) -> None:
    data = client.get("/api/dashboard").json()
    resources = data.get("resources", {})
    assert "ram_mb" in resources or "vram_used_mb" in resources


def test_dashboard_activity_list(client: TestClient) -> None:
    data = client.get("/api/dashboard").json()
    assert isinstance(data.get("activity", []), list)


def test_dashboard_health_score_range(client: TestClient) -> None:
    data = client.get("/api/dashboard").json()
    score = data.get("health_score", -1)
    assert 0 <= score <= 100


def test_dashboard_page_loads(client: TestClient) -> None:
    response = client.get("/pages/dashboard.html")
    assert response.status_code == 200


def test_settings_page_loads(client: TestClient) -> None:
    response = client.get("/pages/settings.html")
    assert response.status_code == 200


def test_plugins_page_loads(client: TestClient) -> None:
    response = client.get("/pages/plugins.html")
    assert response.status_code == 200


def test_activity_page_loads(client: TestClient) -> None:
    response = client.get("/pages/activity.html")
    assert response.status_code == 200


def test_brain_viz_page_loads(client: TestClient) -> None:
    response = client.get("/pages/brain_viz.html")
    assert response.status_code == 200


def test_pwa_manifest_loads(client: TestClient) -> None:
    response = client.get("/manifest.json")
    assert response.status_code == 200


def test_service_worker_loads(client: TestClient) -> None:
    response = client.get("/sw.js")
    assert response.status_code == 200
