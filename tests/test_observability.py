from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from ui_ux_company.observability import ObservabilityService


class _FakeApi:
    def __init__(self):
        self.observations = SimpleNamespace(get_many=self._observations)
        self.scores = SimpleNamespace(get_many=self._scores)
        self.datasets = SimpleNamespace(list=self._datasets)
        self.experiments = SimpleNamespace(list=self._experiments)

    def _observations(self, **kwargs):
        now = datetime.now(timezone.utc)
        return SimpleNamespace(
            data=[
                SimpleNamespace(
                    id="obs-1",
                    trace_id="trace-1",
                    parent_observation_id=None,
                    name="agent:designer",
                    type="AGENT",
                    start_time=now,
                    end_time=now,
                    latency=0.2,
                    session_id="run-1",
                    user_id=None,
                    trace_name="uiux-design-run-1",
                    model="mock/model",
                    level="DEFAULT",
                    environment="local",
                    input='{"goal": "x"}',
                    output='{"ok": true}',
                    usage_details={"input": 1, "output": 2},
                    cost_details={},
                    total_cost=0.001,
                    metadata={},
                )
            ]
        )

    def _scores(self, **kwargs):
        return SimpleNamespace(
            data=[
                SimpleNamespace(
                    id="score-1",
                    name="critic_score",
                    value=0.91,
                    data_type="NUMERIC",
                    source="API",
                    timestamp=datetime.now(timezone.utc),
                    environment="local",
                    subject={"kind": "trace", "id": "trace-1"},
                )
            ]
        )

    def _datasets(self, **kwargs):
        return SimpleNamespace(
            data=[
                SimpleNamespace(
                    id="dataset-1",
                    name="uiux-golden-tasks",
                    description="Golden tasks",
                    metadata={},
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            ]
        )

    def _experiments(self, **kwargs):
        return SimpleNamespace(
            data=[
                SimpleNamespace(
                    id="experiment-1",
                    name="company_with_packet",
                    description="Eval",
                    start_time=datetime.now(timezone.utc),
                    end_time=datetime.now(timezone.utc),
                    item_count=1,
                    dataset_id="dataset-1",
                )
            ]
        )


class _FakeClient:
    def __init__(self):
        self.api = _FakeApi()


class _FakeTracer:
    project_id = "project-1"
    base_url = "http://langfuse:3000"
    dataset = "uiux-golden-tasks"

    def __init__(self):
        self.client = _FakeClient()

    def trace_url(self, trace_id):
        return f"{self.base_url}/project/{self.project_id}/traces/{trace_id}"

    def health(self):
        return {"connected": True, "status": "connected"}


def test_observability_overview_summarizes_langfuse():
    service = ObservabilityService(_FakeTracer(), ttl_seconds=0)
    overview = service.overview()
    assert overview["enabled"] is True
    assert overview["trace_count"] == 1
    assert overview["session_count"] == 1
    assert overview["score_count"] == 1
    assert overview["dataset_count"] == 1
    assert overview["experiment_count"] == 1
    assert overview["recent_observations"][0]["traceId"] == "trace-1"
    assert overview["recent_scores"][0]["traceId"] == "trace-1"
