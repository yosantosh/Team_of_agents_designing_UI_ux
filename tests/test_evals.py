from __future__ import annotations

import asyncio

from ui_ux_company.evals import EvalHarness, load_golden_tasks
from ui_ux_company.graph import CompanyGraph


def test_golden_tasks_exist(settings):
    tasks = load_golden_tasks(settings)
    assert len(tasks) >= 3
    assert any(task["id"] == "homefeed_target_fidelity" for task in tasks)


def test_company_eval_lane_records_metrics(settings):
    graph = CompanyGraph(settings)
    harness = EvalHarness(settings, graph.storage, graph, graph.profile)
    try:
        result = asyncio.run(
            harness.run(
                "company_without_packet",
                ["homefeed_target_fidelity"],
                backend="mock",
            )
        )
        assert result["status"] == "completed"
        assert result["metrics"]["task_count"] == 1
        assert result["results"][0]["task_id"] == "homefeed_target_fidelity"
        assert result["results"][0]["critique_gate"] in {"pass", "needs_rework", "escalate"}
    finally:
        asyncio.run(graph.aclose())
        graph.storage.close()
