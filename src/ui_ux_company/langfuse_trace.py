from __future__ import annotations

import contextlib
import logging
import sys
import threading
from dataclasses import dataclass
from typing import Any, Iterator

from .config import Settings

logger = logging.getLogger(__name__)

_CLIENTS: dict[str, Any] = {}
_CLIENTS_LOCK = threading.Lock()


def _as_number(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class TraceHandle:
    tracer: "LangfuseTracer"
    trace_id: str | None
    url: str | None
    span: Any = None

    def update(self, *, output: Any = None, metadata: dict[str, Any] | None = None) -> None:
        self.tracer.update_span(self.span, output=output, metadata=metadata)

    def score(
        self,
        name: str,
        value: Any,
        *,
        data_type: str | None = None,
        comment: str | None = None,
    ) -> None:
        if not self.trace_id:
            return
        self.tracer.score(self.trace_id, name, value, data_type=data_type, comment=comment)


class LangfuseTracer:
    """Thin, failure-tolerant Langfuse wrapper.

    A disabled or unavailable Langfuse instance is a no-op. Company runs and the
    local evaluation database never depend on the observability service.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: Any = None
        self._client_error: str | None = None
        self._client_checked = False
        self._project_id: str | None = None
        self._last_check: str | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------ configuration
    @property
    def base_url(self) -> str:
        return str(getattr(self.settings, "langfuse_base_url", "") or "http://127.0.0.1:3000").rstrip("/")

    @property
    def dataset(self) -> str:
        return str(getattr(self.settings, "langfuse_dataset", "") or "uiux-golden-tasks")

    @property
    def environment(self) -> str:
        return str(getattr(self.settings, "langfuse_environment", "") or "local")

    @property
    def configured(self) -> bool:
        return bool(
            getattr(self.settings, "langfuse_enabled", False)
            and getattr(self.settings, "langfuse_public_key", "")
            and getattr(self.settings, "langfuse_secret_key", "")
            and self.base_url
        )

    @property
    def enabled(self) -> bool:
        return self.configured and self._get_client() is not None

    @property
    def client(self) -> Any:
        return self._get_client()

    @property
    def public_key(self) -> str:
        return str(getattr(self.settings, "langfuse_public_key", ""))

    def _get_client(self) -> Any:
        if not self.configured:
            return None
        if self._client_checked:
            return self._client
        with self._lock:
            if self._client_checked:
                return self._client
            self._client_checked = True
            try:
                from langfuse import Langfuse

                with _CLIENTS_LOCK:
                    client = _CLIENTS.get(self.public_key)
                    if client is None:
                        client = Langfuse(
                            public_key=self.public_key,
                            secret_key=str(self.settings.langfuse_secret_key),
                            base_url=self.base_url,
                            timeout=int(getattr(self.settings, "langfuse_timeout", 5) or 5),
                            environment=self.environment,
                            tracing_enabled=True,
                        )
                        _CLIENTS[self.public_key] = client
                self._client = client
            except Exception as exc:  # pragma: no cover - defensive
                self._client_error = f"{type(exc).__name__}: {exc}"
                self._client = None
                logger.warning("Langfuse client unavailable: %s", self._client_error)
        return self._client

    # ----------------------------------------------------------- trace helpers
    def create_trace_id(self, seed: str | None = None) -> str | None:
        client = self._get_client()
        if client is None:
            return None
        try:
            return str(client.create_trace_id(seed=seed))
        except Exception:
            return None

    def langchain_callbacks(self, trace_id: str | None = None) -> list[Any]:
        if not self.enabled:
            return []
        try:
            from langfuse.langchain import CallbackHandler

            context = {"trace_id": trace_id} if trace_id else None
            return [CallbackHandler(public_key=self.public_key, trace_context=context)]
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Langfuse callback unavailable: %s", exc)
            return []

    @contextlib.contextmanager
    def run_trace(
        self,
        *,
        run_id: str,
        mode: str = "run",
        prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> Iterator[TraceHandle | None]:
        client = self._get_client()
        if client is None:
            yield None
            return
        trace_id = self.create_trace_id(seed=run_id)
        if not trace_id:
            yield None
            return
        trace_metadata = {
            "run_id": run_id,
            "mode": mode,
            "registry_version": str(getattr(self.settings, "registry_version", "") or ""),
            **(metadata or {}),
        }
        trace_tags = ["uiux", mode, f"project-{getattr(self.settings, 'project_id', '')}", *(tags or [])]
        handle = TraceHandle(self, trace_id, self.trace_url(trace_id))
        observation = None
        propagation = None
        try:
            observation = client.start_as_current_observation(
                name=f"uiux-run-{mode}",
                as_type="chain",
                trace_context={"trace_id": trace_id},
                input={"prompt": prompt} if prompt else None,
                metadata=trace_metadata,
            )
            handle.span = observation.__enter__()
        except Exception as exc:  # pragma: no cover - tracing must never fail a run
            logger.warning("Langfuse run trace could not start: %s", exc)
            observation = None
            handle.span = None
        try:
            try:
                propagation = self._propagate(
                    trace_name=f"uiux-{mode}-{run_id}",
                    session_id=run_id,
                    metadata=trace_metadata,
                    tags=trace_tags,
                )
                propagation.__enter__()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Langfuse attribute propagation failed: %s", exc)
                propagation = None
            yield handle
        finally:
            if propagation is not None:
                try:
                    propagation.__exit__(None, None, None)
                except Exception:
                    pass
            if observation is not None:
                try:
                    observation.__exit__(*sys.exc_info())
                except Exception:
                    pass
            if handle.url is None:
                handle.url = self.trace_url(trace_id)

    def _propagate(self, **kwargs: Any):
        try:
            from langfuse import propagate_attributes

            return propagate_attributes(environment=self.environment, **kwargs)
        except Exception:
            return contextlib.nullcontext()

    @contextlib.contextmanager
    def span(
        self,
        name: str,
        *,
        as_type: str = "span",
        input: Any = None,
        metadata: dict[str, Any] | None = None,
        model: str | None = None,
        trace_id: str | None = None,
        level: str | None = None,
    ) -> Iterator[Any]:
        client = self._get_client()
        if client is None:
            yield None
            return
        context = {"trace_id": trace_id} if trace_id else None
        observation = None
        try:
            observation = client.start_as_current_observation(
                name=name,
                as_type=as_type,
                trace_context=context,
                input=input,
                metadata=metadata,
                model=model,
                level=level,  # type: ignore[arg-type]
            )
            span = observation.__enter__()
        except Exception as exc:  # pragma: no cover - never break a run
            logger.warning("Langfuse span %s failed: %s", name, exc)
            yield None
            return
        try:
            yield span
        finally:
            try:
                observation.__exit__(*sys.exc_info())
            except Exception:
                pass

    def update_span(
        self,
        span: Any,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        usage_details: dict[str, int] | None = None,
        cost_details: dict[str, float] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        if span is None:
            return
        try:
            span.update(
                output=output,
                metadata=metadata,
                usage_details=usage_details,
                cost_details=cost_details,
                level=level,  # type: ignore[arg-type]
                status_message=status_message,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Langfuse span update failed: %s", exc)

    def span_now(
        self,
        name: str,
        *,
        as_type: str = "span",
        input: Any = None,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
        level: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        with self.span(
            name,
            as_type=as_type,
            input=input,
            metadata=metadata,
            trace_id=trace_id,
            level=level,
        ) as span:
            self.update_span(span, output=output)

    # ---------------------------------------------------------------- scoring
    def score(
        self,
        trace_id: str | None,
        name: str,
        value: Any,
        *,
        data_type: str | None = None,
        comment: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not trace_id or not self.enabled:
            return
        if isinstance(value, bool):
            resolved_type = data_type or "BOOLEAN"
        elif _as_number(value) is not None:
            resolved_type = data_type or "NUMERIC"
            value = _as_number(value)
        else:
            resolved_type = data_type or "CATEGORICAL"
        if value is None:
            return
        client = self._get_client()
        if client is None:
            return
        try:
            client.create_score(
                trace_id=trace_id,
                name=name,
                value=value,
                data_type=resolved_type,  # type: ignore[arg-type]
                comment=comment,
                metadata=metadata,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Langfuse score %s failed: %s", name, exc)

    def score_run(self, trace_id: str | None, values: dict[str, Any], *, duration_ms: int | None = None) -> None:
        if not trace_id or not self.enabled:
            return
        critique = values.get("critique") or {}
        qa = values.get("qa") or {}
        implementation = values.get("implementation") or {}
        code = implementation.get("code_edits") if isinstance(implementation, dict) else {}
        coverage = (code or {}).get("intent_coverage") or []
        rework = values.get("rework_counts") or {}
        critic_score = _as_number(critique.get("score"))
        if critic_score is not None:
            self.score(trace_id, "critic_score", critic_score, comment="Independent design critic score")
        if values.get("critique_gate"):
            self.score(
                trace_id,
                "critic_pass",
                str(values.get("critique_gate")) == "pass",
                comment=f"Design critique gate: {values.get('critique_gate')}",
            )
        if values.get("eval_gate"):
            self.score(
                trace_id,
                "release_pass",
                str(values.get("eval_gate")) == "pass",
                comment=f"Manager evaluation gate: {values.get('eval_gate')}",
            )
        if coverage:
            self.score(trace_id, "intent_coverage", float(len(coverage)), comment="Implemented intent-packet items")
        visual = (qa.get("visual") or {}).get("verdict")
        if visual:
            self.score(
                trace_id,
                "visual_qa_pass",
                str(visual).upper() == "PASS",
                comment=f"Visual QA verdict: {visual}",
            )
        if rework:
            self.score(
                trace_id,
                "rework_cycles",
                float(sum(int(value or 0) for value in rework.values())),
                comment="Total design + implementation rework cycles",
            )
        if duration_ms is not None:
            self.score(trace_id, "duration_ms", float(duration_ms))
        target_fidelity = _as_number(critique.get("target_fidelity"))
        if target_fidelity is not None:
            self.score(trace_id, "target_fidelity", target_fidelity, comment="Target_ui fidelity score")

    # ------------------------------------------------------------------ health
    def trace_url(self, trace_id: str | None) -> str | None:
        if not trace_id:
            return None
        project_id = self.project_id
        if not project_id:
            # Never make a network call on the run path. Health/status discovery
            # caches the project id; the UI can then build the deep link from the
            # stored trace id. CLI users can set LANGFUSE_PROJECT_ID explicitly.
            return None
        return f"{self.base_url}/project/{project_id}/traces/{trace_id}"

    @property
    def project_id(self) -> str | None:
        configured = str(getattr(self.settings, "langfuse_project_id", "") or "").strip()
        if configured:
            return configured
        return self._project_id

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.configured,
            "configured": self.configured,
            "base_url": self.base_url,
            "dataset": self.dataset,
            "environment": self.environment,
            "project_id": self.project_id,
            "last_error": self._client_error,
        }

    def health(self) -> dict[str, Any]:
        from .models import utcnow

        self._last_check = utcnow()
        summary = self.summary()
        summary["checked_at"] = self._last_check
        if not self.configured:
            summary.update(
                {
                    "status": "disabled",
                    "connected": False,
                    "reason": (
                        "Langfuse is disabled. Set LANGFUSE_ENABLED=true, "
                        "LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and LANGFUSE_BASE_URL."
                    ),
                }
            )
            return summary
        client = self._get_client()
        if client is None:
            summary.update(
                {
                    "status": "unavailable",
                    "connected": False,
                    "reason": self._client_error or "Langfuse SDK could not be initialized.",
                }
            )
            return summary
        try:
            connected = bool(client.auth_check())
        except Exception as exc:
            summary.update(
                {
                    "status": "unavailable",
                    "connected": False,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )
            return summary
        if connected:
            try:
                projects = client.api.projects.get()
                project = (projects.data or [None])[0]
                if project and getattr(project, "id", None):
                    self._project_id = str(project.id)
                    summary["project_id"] = self.project_id
            except Exception:
                pass
        summary.update(
            {
                "status": "connected" if connected else "unavailable",
                "connected": connected,
                "reason": None if connected else "Langfuse auth_check failed. Check the API keys and base URL.",
            }
        )
        return summary

    # ------------------------------------------------------------------ lifecycle
    def flush(self) -> None:
        client = self._get_client()
        if client is None:
            return
        try:
            client.flush()
        except Exception:
            pass

    def shutdown(self) -> None:
        client = self._get_client()
        if client is None:
            return
        try:
            client.shutdown()
        except Exception:
            pass
