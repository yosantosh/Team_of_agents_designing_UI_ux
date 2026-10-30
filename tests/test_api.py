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
        assert settings_response.json()["code_edits_enabled"] is True
        assert settings_response.json()["code_edit_mode"] == "worktree"
        bad_mode = client.put("/api/settings", json={"code_edit_mode": "unsafe-unknown"})
        assert bad_mode.status_code == 400
        fs = client.get("/api/fs/list", params={"path": str(settings.root)})
        assert fs.status_code == 200
        assert fs.json()["path"]
        approvals = client.get("/api/approvals", params={"status": "all"})
        assert approvals.status_code == 200
        history = client.get("/api/history")
        assert history.status_code == 200
        assert "history" in history.json()
        current = client.get("/api/current-ui")
        assert current.status_code == 200
        assert "images" in current.json()
        target = client.get("/api/target-ui").json()
        if target.get("images"):
            image_id = target["images"][0]["image_id"]
            annotated = client.post(
                "/api/target-ui/annotations",
                json={"image_id": image_id, "annotations": [{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2, "label": "test"}]},
            )
            assert annotated.status_code == 200
            assert annotated.json()["annotations"][0]["label"] == "test"
        search = client.get("/api/fs/search", params={"root": str(settings.root), "patterns": "target_ui,current_ui"})
        assert search.status_code == 200
        assert "results" in search.json()
        validation = client.get("/api/project/validate")
        assert validation.status_code == 200
        assert "valid" in validation.json()
        app_plan = client.get("/api/app-plan")
        assert app_plan.status_code == 200
        assert "content" in app_plan.json()
        eval_tasks = client.get("/api/evals/tasks")
        assert eval_tasks.status_code == 200
        assert len(eval_tasks.json()["tasks"]) >= 3
        eval_experiments = client.get("/api/evals/experiments")
        assert eval_experiments.status_code == 200
        assert "experiments" in eval_experiments.json()
        langfuse = client.get("/api/langfuse/health")
        assert langfuse.status_code == 200
        assert langfuse.json()["status"] == "disabled"
        observability = client.get("/api/observability/overview")
        assert observability.status_code == 200
        assert observability.json()["status"] == "unavailable"
        root = client.get("/")
        assert root.status_code == 200
        flow = client.get("/flow")
        assert flow.status_code == 200
        assert "Live orchestration flow" in flow.text


def test_api_delete_run_removes_history_entry(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.storage.create_run("run_api_delete", "delete me", "design", "completed")
        response = client.delete("/api/runs/run_api_delete")
        assert response.status_code == 200
        assert response.json()["deleted"]["runs"] == 1
        assert app.state.storage.get_run("run_api_delete") is None
        history = client.get("/api/history").json()["history"]
        assert all(item["id"] != "run_api_delete" for item in history)
