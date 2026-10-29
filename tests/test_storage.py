from __future__ import annotations

from ui_ux_company.storage import Storage


def test_purge_mock_data_keeps_real_runs(settings):
    storage = Storage(settings.storage_db)
    try:
        storage.create_run("run_mock", "mock run", "design", "completed")
        storage.update_run("run_mock", state={"backend": "mock", "demo": True})
        storage.record_usage("run_mock", "task", "agent", "mock/agent", 100, 50)
        storage.create_run("run_real", "real run", "design", "completed")
        storage.update_run("run_real", state={"backend": "opencode", "demo": False})
        storage.record_usage("run_real", "task", "agent", "modal/deepseek", 200, 80)

        result = storage.purge_mock_data()

        assert result["runs"] == 1
        assert "run_mock" in result["purged_run_ids"]
        assert storage.get_run("run_mock") is None
        assert storage.get_run("run_real") is not None
        budget = storage.budget_summary(100_000_000)
        assert "mock/agent" not in budget["by_model"]
        assert "modal/deepseek" in budget["by_model"]
    finally:
        storage.close()
