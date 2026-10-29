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
        settings_response = client.get("/api/settings")
        assert settings_response.status_code == 200
        assert settings_response.json()["backend"] == "mock"
        assert settings_response.json()["monthly_token_budget"] > 0
        fs = client.get("/api/fs/list", params={"path": str(settings.root)})
        assert fs.status_code == 200
        assert fs.json()["path"]
        approvals = client.get("/api/approvals", params={"status": "all"})
        assert approvals.status_code == 200
        current = client.get("/api/current-ui")
        assert current.status_code == 200
        assert "images" in current.json()
        search = client.get("/api/fs/search", params={"root": str(settings.root), "patterns": "target_ui,current_ui"})
        assert search.status_code == 200
        assert "results" in search.json()
        validation = client.get("/api/project/validate")
        assert validation.status_code == 200
        assert "valid" in validation.json()
        root = client.get("/")
        assert root.status_code == 200
