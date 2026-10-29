from __future__ import annotations

from fastapi.testclient import TestClient

from ui_ux_company.api import create_app


def test_api_health_and_registry(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["roborezzi"] is False
        registry = client.get("/api/registry")
        assert registry.status_code == 200
        assert registry.json()["summary"]["agents"] >= 20
        project = client.get("/api/project")
        assert project.status_code == 200
        assert project.json()["project"]["package"] == "com.example.dhunchat"
        root = client.get("/")
        assert root.status_code == 200
