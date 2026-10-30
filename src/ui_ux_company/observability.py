from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .langfuse_trace import LangfuseTracer


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except Exception:
            pass
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)


def _trim(value: Any, limit: int = 2400) -> Any:
    value = _jsonable(value)
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class ObservabilityService:
    """Read-only Langfuse projections for the company control room.

    The browser never receives the Langfuse secret. FastAPI queries the
    Langfuse API and returns compact, cached summaries plus deep links.
    """

    def __init__(self, tracer: LangfuseTracer, *, ttl_seconds: int = 8):
        self.tracer = tracer
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[float, Any]] = {}

    @property
    def client(self):
        return self.tracer.client

    def _cached(self, key: str, loader: Callable[[], Any], ttl: int | None = None) -> Any:
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and now - cached[0] < (ttl or self.ttl_seconds):
            return cached[1]
        value = loader()
        self._cache[key] = (now, value)
        return value

    def _unavailable(self, reason: str | None = None) -> dict[str, Any]:
        return {
            "enabled": False,
            "status": "unavailable",
            "reason": reason or "Langfuse is disabled or unavailable.",
            "project_id": self.tracer.project_id,
            "base_url": self.tracer.base_url,
            "dataset": self.tracer.dataset,
        }

    def health(self) -> dict[str, Any]:
        return self.tracer.health()

    def overview(self) -> dict[str, Any]:
        if self.tracer.client is None:
            return self._unavailable()
        return self._cached("overview", self._overview, ttl=6)

    def _overview(self) -> dict[str, Any]:
        client = self.client
        if client is None:
            return self._unavailable()
        try:
            observations = client.api.observations.get_many(limit=100)
            scores = client.api.scores.get_many(limit=100, fields="subject")
            datasets = client.api.datasets.list(limit=20)
            experiments = client.api.experiments.list(
                from_start_time=datetime.now(timezone.utc) - timedelta(days=90),
                limit=20,
            )
        except Exception as exc:
            return self._unavailable(f"{type(exc).__name__}: {exc}")
        obs = [_observation(item) for item in (observations.data or [])]
        score_rows = [_score(item) for item in (scores.data or [])]
        trace_ids = {item.get("traceId") for item in obs if item.get("traceId")}
        session_ids = {item.get("sessionId") for item in obs if item.get("sessionId")}
        return {
            "enabled": True,
            "status": "connected",
            "project_id": self.tracer.project_id,
            "base_url": self.tracer.base_url,
            "dataset": self.tracer.dataset,
            "trace_count": len(trace_ids),
            "session_count": len(session_ids),
            "observation_count": len(obs),
            "score_count": len(score_rows),
            "dataset_count": len(datasets.data or []),
            "experiment_count": len(experiments.data or []),
            "recent_observations": obs[:100],
            "recent_scores": score_rows[:100],
            "datasets": [_dataset(item) for item in (datasets.data or [])],
            "experiments": [_experiment(item) for item in (experiments.data or [])],
        }

    def observations(
        self,
        *,
        session_id: str | None = None,
        trace_id: str | None = None,
        name: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        if self.tracer.client is None:
            return {"observations": [], "enabled": False}
        key = f"obs:{session_id}:{trace_id}:{name}:{limit}"
        return self._cached(key, lambda: self._observations(session_id, trace_id, name, limit), ttl=5)

    def _observations(
        self,
        session_id: str | None,
        trace_id: str | None,
        name: str | None,
        limit: int,
    ) -> dict[str, Any]:
        client = self.client
        if client is None:
            return {"observations": [], "enabled": False}
        try:
            rows = client.api.observations.get_many(
                session_id=session_id,
                trace_id=trace_id,
                name=name,
                limit=max(1, min(limit, 200)),
            )
        except Exception as exc:
            return {
                "observations": [],
                "enabled": False,
                "reason": f"{type(exc).__name__}: {exc}",
            }
        return {
            "enabled": True,
            "observations": [_observation(item) for item in (rows.data or [])],
        }

    def scores(
        self,
        *,
        trace_id: str | None = None,
        session_id: str | None = None,
        name: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        if self.tracer.client is None:
            return {"scores": [], "enabled": False}
        key = f"scores:{trace_id}:{session_id}:{name}:{limit}"
        return self._cached(key, lambda: self._scores(trace_id, session_id, name, limit), ttl=5)

    def _scores(
        self,
        trace_id: str | None,
        session_id: str | None,
        name: str | None,
        limit: int,
    ) -> dict[str, Any]:
        client = self.client
        if client is None:
            return {"scores": [], "enabled": False}
        try:
            rows = client.api.scores.get_many(
                trace_id=trace_id,
                session_id=session_id,
                name=name,
                limit=max(1, min(limit, 500)),
                fields="subject",
            )
        except Exception as exc:
            return {
                "scores": [],
                "enabled": False,
                "reason": f"{type(exc).__name__}: {exc}",
            }
        return {"enabled": True, "scores": [_score(item) for item in (rows.data or [])]}

    def datasets(self, limit: int = 50) -> dict[str, Any]:
        if self.tracer.client is None:
            return {"datasets": [], "enabled": False}
        return self._cached(
            f"datasets:{limit}",
            lambda: self._datasets(limit),
            ttl=10,
        )

    def _datasets(self, limit: int) -> dict[str, Any]:
        client = self.client
        if client is None:
            return {"datasets": [], "enabled": False}
        try:
            rows = client.api.datasets.list(limit=max(1, min(limit, 100)))
        except Exception as exc:
            return {
                "datasets": [],
                "enabled": False,
                "reason": f"{type(exc).__name__}: {exc}",
            }
        return {"enabled": True, "datasets": [_dataset(item) for item in (rows.data or [])]}

    def dataset(self, name: str) -> dict[str, Any]:
        if self.tracer.client is None:
            return {"items": [], "enabled": False}
        key = f"dataset:{name}"
        return self._cached(key, lambda: self._dataset(name), ttl=10)

    def _dataset(self, name: str) -> dict[str, Any]:
        client = self.client
        if client is None:
            return {"items": [], "enabled": False}
        try:
            dataset = client.get_dataset(name)
        except Exception as exc:
            return {
                "items": [],
                "enabled": False,
                "reason": f"{type(exc).__name__}: {exc}",
            }
        return {
            "enabled": True,
            "dataset": _dataset(dataset),
            "items": [_dataset_item(item) for item in (dataset.items or [])],
        }

    def experiments(self, limit: int = 50, days: int = 90) -> dict[str, Any]:
        if self.tracer.client is None:
            return {"experiments": [], "enabled": False}
        return self._cached(
            f"experiments:{limit}:{days}",
            lambda: self._experiments(limit, days),
            ttl=10,
        )

    def _experiments(self, limit: int, days: int) -> dict[str, Any]:
        client = self.client
        if client is None:
            return {"experiments": [], "enabled": False}
        try:
            rows = client.api.experiments.list(
                from_start_time=datetime.now(timezone.utc) - timedelta(days=max(1, days)),
                limit=max(1, min(limit, 100)),
            )
        except Exception as exc:
            return {
                "experiments": [],
                "enabled": False,
                "reason": f"{type(exc).__name__}: {exc}",
            }
        return {
            "enabled": True,
            "experiments": [_experiment(item) for item in (rows.data or [])],
        }

    def experiment_items(self, experiment_id: str, limit: int = 100) -> dict[str, Any]:
        if self.tracer.client is None:
            return {"items": [], "enabled": False}
        key = f"experiment-items:{experiment_id}:{limit}"
        return self._cached(
            key,
            lambda: self._experiment_items(experiment_id, limit),
            ttl=10,
        )

    def _experiment_items(self, experiment_id: str, limit: int) -> dict[str, Any]:
        client = self.client
        if client is None:
            return {"items": [], "enabled": False}
        try:
            rows = client.api.experiments.list_items(
                experiment_id=experiment_id,
                from_start_time=datetime.now(timezone.utc) - timedelta(days=365),
                limit=max(1, min(limit, 200)),
            )
        except Exception as exc:
            return {
                "items": [],
                "enabled": False,
                "reason": f"{type(exc).__name__}: {exc}",
            }
        return {
            "enabled": True,
            "items": [_jsonable(item) for item in (rows.data or [])],
        }

    def trace_url(self, trace_id: str | None) -> str | None:
        return self.tracer.trace_url(trace_id)


def _observation(item: Any) -> dict[str, Any]:
    return {
        "id": getattr(item, "id", None),
        "traceId": getattr(item, "trace_id", None),
        "parentObservationId": getattr(item, "parent_observation_id", None),
        "name": getattr(item, "name", None),
        "type": getattr(item, "type", None),
        "startTime": _iso(getattr(item, "start_time", None)),
        "endTime": _iso(getattr(item, "end_time", None)),
        "latency": getattr(item, "latency", None),
        "sessionId": getattr(item, "session_id", None),
        "userId": getattr(item, "user_id", None),
        "traceName": getattr(item, "trace_name", None),
        "model": getattr(item, "model", None),
        "level": str(getattr(item, "level", "") or ""),
        "environment": getattr(item, "environment", None),
        "input": _trim(getattr(item, "input", None)),
        "output": _trim(getattr(item, "output", None)),
        "usageDetails": _jsonable(getattr(item, "usage_details", None)),
        "costDetails": _jsonable(getattr(item, "cost_details", None)),
        "totalCost": getattr(item, "total_cost", None),
        "metadata": _trim(getattr(item, "metadata", None), 1200),
    }


def _score(item: Any) -> dict[str, Any]:
    subject = _jsonable(getattr(item, "subject", None))
    trace_id = None
    if isinstance(subject, dict) and subject.get("kind") == "trace":
        trace_id = subject.get("id")
    return {
        "id": getattr(item, "id", None),
        "name": getattr(item, "name", None),
        "value": _jsonable(getattr(item, "value", None)),
        "dataType": str(getattr(item, "data_type", "") or ""),
        "source": str(getattr(item, "source", "") or ""),
        "timestamp": _iso(getattr(item, "timestamp", None)),
        "environment": getattr(item, "environment", None),
        "traceId": trace_id,
        "subject": subject,
    }


def _dataset(item: Any) -> dict[str, Any]:
    return {
        "id": getattr(item, "id", None),
        "name": getattr(item, "name", None),
        "description": getattr(item, "description", None),
        "metadata": _jsonable(getattr(item, "metadata", None)),
        "createdAt": _iso(getattr(item, "created_at", None)),
        "updatedAt": _iso(getattr(item, "updated_at", None)),
    }


def _dataset_item(item: Any) -> dict[str, Any]:
    return {
        "id": getattr(item, "id", None),
        "input": _trim(getattr(item, "input", None), 1800),
        "expectedOutput": _trim(getattr(item, "expected_output", None), 1200),
        "metadata": _jsonable(getattr(item, "metadata", None)),
        "createdAt": _iso(getattr(item, "created_at", None)),
    }


def _experiment(item: Any) -> dict[str, Any]:
    return {
        "id": getattr(item, "id", None),
        "name": getattr(item, "name", None),
        "description": getattr(item, "description", None),
        "startTime": _iso(getattr(item, "start_time", None)),
        "endTime": _iso(getattr(item, "end_time", None)),
        "itemCount": getattr(item, "item_count", None),
        "datasetId": getattr(item, "dataset_id", None),
    }
