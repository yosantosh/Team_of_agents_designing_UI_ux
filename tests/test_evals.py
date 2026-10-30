from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ui_ux_company.evals import (
    EvalHarness,
    _critic_evaluator,
    _critic_pass_evaluator,
    _run_aggregate_evaluator,
    load_golden_tasks,
)
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
        assert result["metrics"]["langfuse"]["enabled"] is False
        assert result["metrics"]["langfuse"]["status"] == "disabled"
    finally:
        asyncio.run(graph.aclose())
        graph.storage.close()


class _FakeDataset:
    def __init__(self):
        self.id = "dataset-1"
        self.items = []


class _FakeLangfuse:
    def __init__(self):
        self.dataset = _FakeDataset()
        self.created_items = []
        self.experiment_kwargs = None

    def get_dataset(self, name):
        if not self.created_items:
            raise RuntimeError("not found")
        return self.dataset

    def create_dataset(self, **kwargs):
        return self.dataset

    def create_dataset_item(self, **kwargs):
        self.created_items.append(kwargs)
        self.dataset.items.append(SimpleNamespace(id=kwargs["id"], metadata=kwargs["metadata"]))

    def run_experiment(self, **kwargs):
        self.experiment_kwargs = kwargs
        return SimpleNamespace(
            experiment_id="exp-1",
            experiment_url="http://langfuse/project/p/experiments/exp-1",
            item_results=[SimpleNamespace(output={})],
            run_evaluations=[SimpleNamespace(name="pass_rate", value=1.0, comment=None)],
        )


class _FakeTracer:
    def __init__(self):
        self.enabled = True
        self.client = _FakeLangfuse()
        self.dataset = "uiux-golden-tasks"
        self.base_url = "http://langfuse"
        self.project_id = "p"


def test_langfuse_evaluators_and_export_use_dataset(settings):
    graph = CompanyGraph(settings)
    harness = EvalHarness(settings, graph.storage, graph, graph.profile, tracer=_FakeTracer())
    try:
        tasks = load_golden_tasks(settings)[:1]
        results = [
            {
                "task_id": tasks[0]["id"],
                "lane": "company_with_packet",
                "critic_score": 0.91,
                "critique_gate": "pass",
                "intent_coverage_count": 4,
                "visual_verdict": "PASS",
                "code_status": "edited",
                "changed_files": 2,
                "target_images": 3,
                "tokens": 100,
                "duration_ms": 500,
            }
        ]
        metrics = {"pass_rate": 1.0}
        exported = harness._sync_langfuse("eval-test", "company_with_packet", tasks, results, metrics)
        assert exported["enabled"] is True
        assert exported["experiment_id"] == "exp-1"
        assert exported["dataset"]["created"] == 1
        kwargs = harness.tracer.client.experiment_kwargs
        assert kwargs["data"], "dataset items should be used for the experiment"
        assert _critic_evaluator(output=results[0]).value == 0.91
        assert _critic_pass_evaluator(output=results[0]).value is True
        aggregates = _run_aggregate_evaluator(item_results=[SimpleNamespace(output=results[0])])
        assert {item.name for item in aggregates} == {
            "experiment_pass_rate",
            "experiment_avg_critic_score",
            "experiment_intent_coverage",
            "experiment_tokens",
        }
    finally:
        graph.storage.close()
