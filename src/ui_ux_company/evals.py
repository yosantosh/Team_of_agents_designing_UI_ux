from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from .codeworker import CodeWorker
from .config import Settings
from .langfuse_trace import LangfuseTracer
from .models import new_id
from .registry import ProjectProfile
from .storage import Storage


LANES = {
    "company_with_packet": "Company pipeline + intent packet + target images",
    "company_without_packet": "Company pipeline without intent packet",
    "direct_opencode": "Direct OpenCode build baseline",
}


def _output_dict(output: Any) -> dict[str, Any]:
    return output if isinstance(output, dict) else {}


def _numeric_evaluator(name: str, key: str, comment: str):
    def evaluator(*, output: Any, **_: Any):
        from langfuse import Evaluation

        value = _output_dict(output).get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return Evaluation(name=name, value=0.0, comment=f"{comment} (not reported)")
        return Evaluation(name=name, value=float(value), comment=comment)

    return evaluator


def _boolean_evaluator(name: str, key: str, expected: Any, comment: str):
    def evaluator(*, output: Any, **_: Any):
        from langfuse import Evaluation

        value = _output_dict(output).get(key)
        if value is None:
            return Evaluation(name=name, value=False, comment=f"{comment} (not reported)")
        return Evaluation(name=name, value=bool(value == expected), comment=comment)

    return evaluator


def _critic_evaluator(*, output: Any, **_: Any):
    from langfuse import Evaluation

    value = _output_dict(output).get("critic_score")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return Evaluation(name="critic_score", value=0.0, comment="Critic score not reported")
    return Evaluation(name="critic_score", value=float(value), comment="Independent design critic score")


def _critic_pass_evaluator(*, output: Any, **_: Any):
    from langfuse import Evaluation

    value = _output_dict(output).get("critique_gate")
    if value is None:
        return Evaluation(name="critique_pass", value=False, comment="Critique gate not reported")
    return Evaluation(name="critique_pass", value=value == "pass", comment=f"Critique gate: {value}")


def _code_status_evaluator(*, output: Any, **_: Any):
    from langfuse import Evaluation

    value = _output_dict(output).get("code_status")
    return Evaluation(name="code_status", value=str(value or "not_run"), data_type="CATEGORICAL")


def _run_aggregate_evaluator(*, item_results: list[Any], **_: Any):
    from langfuse import Evaluation

    outputs = [_output_dict(getattr(item, "output", None)) for item in item_results]
    total = len(outputs)
    passes = sum(1 for item in outputs if item.get("critique_gate") == "pass")
    scores = [float(item["critic_score"]) for item in outputs if isinstance(item.get("critic_score"), (int, float))]
    coverage = sum(int(item.get("intent_coverage_count") or 0) for item in outputs)
    tokens = sum(int(item.get("tokens") or 0) for item in outputs)
    return [
        Evaluation(name="experiment_pass_rate", value=round(passes / total, 4) if total else 0.0),
        Evaluation(name="experiment_avg_critic_score", value=round(sum(scores) / len(scores), 4) if scores else 0.0),
        Evaluation(name="experiment_intent_coverage", value=float(coverage)),
        Evaluation(name="experiment_tokens", value=float(tokens)),
    ]


def load_golden_tasks(settings: Settings) -> list[dict[str, Any]]:
    path = settings.org_dir / "golden_tasks.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tasks = data.get("tasks") or []
    return [task for task in tasks if isinstance(task, dict) and task.get("id")]


class EvalHarness:
    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        company: Any,
        profile: ProjectProfile,
        code_worker: CodeWorker | None = None,
        tracer: LangfuseTracer | None = None,
    ):
        self.settings = settings
        self.storage = storage
        self.company = company
        self.profile = profile
        self.tracer = tracer or LangfuseTracer(settings)
        self.code_worker = code_worker or CodeWorker(settings, company.artifacts)

    async def run(
        self,
        lane: str,
        task_ids: list[str] | None = None,
        *,
        model: str | None = None,
        backend: str | None = None,
        experiment_id: str | None = None,
    ) -> dict[str, Any]:
        if lane not in LANES:
            raise ValueError(f"unknown eval lane: {lane}")
        tasks = load_golden_tasks(self.settings)
        if task_ids:
            wanted = set(task_ids)
            tasks = [task for task in tasks if task.get("id") in wanted]
        if not tasks:
            raise ValueError("no golden tasks selected")
        experiment_id = experiment_id or new_id("eval")
        self.storage.create_eval_experiment(experiment_id, lane, tasks)
        results: list[dict[str, Any]] = []
        started = time.monotonic()
        try:
            for task in tasks:
                result = await self._run_task(lane, task, model=model, backend=backend)
                results.append(result)
                self.storage.update_eval_experiment(experiment_id, results=results)
            metrics = self._aggregate(results)
            metrics["duration_ms"] = int((time.monotonic() - started) * 1000)
            try:
                metrics["langfuse"] = await asyncio.wait_for(
                    asyncio.to_thread(self._sync_langfuse, experiment_id, lane, tasks, results, metrics),
                    timeout=max(1, int(getattr(self.settings, "langfuse_eval_timeout", 30))),
                )
            except asyncio.TimeoutError:
                metrics["langfuse"] = {
                    "enabled": False,
                    "status": "failed",
                    "reason": "Langfuse experiment export timed out; local results were saved.",
                }
            except Exception as exc:
                metrics["langfuse"] = {
                    "enabled": False,
                    "status": "failed",
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            self.storage.update_eval_experiment(experiment_id, status="completed", results=results, metrics=metrics)
        except Exception as exc:
            metrics = self._aggregate(results)
            metrics["error"] = str(exc)
            self.storage.update_eval_experiment(experiment_id, status="failed", results=results, metrics=metrics)
        return self.storage.get_eval_experiment(experiment_id) or {}

    async def _run_task(
        self,
        lane: str,
        task: dict[str, Any],
        *,
        model: str | None,
        backend: str | None,
    ) -> dict[str, Any]:
        if lane == "direct_opencode":
            return await self._run_direct(task, model=model)
        return await self._run_company(task, lane, model=model, backend=backend)

    async def _run_company(
        self,
        task: dict[str, Any],
        lane: str,
        *,
        model: str | None,
        backend: str | None,
    ) -> dict[str, Any]:
        before = self.storage.budget_summary(self.settings.monthly_token_budget)
        started = time.monotonic()
        result = await self.company.run(
            str(task.get("prompt") or task.get("title") or task["id"]),
            mode=str(task.get("mode") or "design"),
            auto_approve=bool(task.get("auto_approve", True)),
            target_ui_enabled=bool(task.get("target_ui", True)),
            demo=False,
            backend=backend,
            model=model,
            intent_packet_enabled=(lane == "company_with_packet"),
        )
        after = self.storage.budget_summary(self.settings.monthly_token_budget)
        values = result if isinstance(result, dict) else {}
        critique = values.get("critique") or {}
        implementation = values.get("implementation") or {}
        code = implementation.get("code_edits") if isinstance(implementation, dict) else {}
        qa = values.get("qa") or {}
        target_manifest = values.get("target_manifest") or {}
        coverage = (code or {}).get("intent_coverage") or []
        return {
            "task_id": task["id"],
            "lane": lane,
            "run_id": values.get("run_id"),
            "status": values.get("status"),
            "critique_gate": values.get("critique_gate"),
            "eval_gate": values.get("eval_gate"),
            "critic_score": critique.get("score"),
            "critical_issues": len(
                [issue for issue in (critique.get("issues") or []) if issue.get("severity") == "critical"]
            ),
            "rework": values.get("rework_counts") or {},
            "visual_verdict": (qa.get("visual") or {}).get("verdict"),
            "artifact_count": len(self.storage.list_artifacts(str(values.get("run_id") or "")))
            if values.get("run_id") else 0,
            "target_images": len(target_manifest.get("images") or []),
            "changed_files": len((code or {}).get("changed_files") or []),
            "code_status": (code or {}).get("status"),
            "intent_coverage_count": len(coverage),
            "tokens": int(after.get("used_tokens", 0)) - int(before.get("used_tokens", 0)),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "langfuse_trace_id": values.get("langfuse_trace_id"),
            "langfuse_trace_url": values.get("langfuse_trace_url"),
        }

    async def _run_direct(self, task: dict[str, Any], *, model: str | None) -> dict[str, Any]:
        repo = Path(str(self.profile.project.get("repo") or self.settings.project_dir or self.settings.root))
        run_id = new_id("evaldirect")
        started = time.monotonic()
        with self.tracer.run_trace(
            run_id=run_id,
            mode="direct_eval",
            prompt=str(task.get("prompt") or task.get("title") or task["id"]),
            metadata={"run_id": run_id, "task_id": task["id"], "lane": "direct_opencode"},
            tags=["evaluation", "direct_opencode"],
        ) as trace:
            with self.tracer.span(
                "code_worker",
                as_type="tool",
                input={"task_id": task["id"], "repo": str(repo)},
                metadata={"run_id": run_id},
                trace_id=getattr(trace, "trace_id", None),
            ) as span:
                result = await self.code_worker.apply(
                    run_id,
                    repo,
                    str(task.get("prompt") or task.get("title") or task["id"]),
                    {"prompt": task.get("prompt"), "mode": "direct_baseline"},
                )
                self.tracer.update_span(
                    span,
                    output={
                        "status": result.get("status"),
                        "changed_files": result.get("changed_files", []),
                        "verification": result.get("verification"),
                    },
                    level="ERROR" if result.get("status") == "failed" else None,
                )
            duration = int((time.monotonic() - started) * 1000)
            diff = self.storage.get_artifact(run_id, "code_diff.patch")
            output = {
                "task_id": task["id"],
                "lane": "direct_opencode",
                "run_id": run_id,
                "status": result.get("status"),
                "code_status": result.get("status"),
                "changed_files": len(result.get("changed_files") or []),
                "diff_chars": Path(diff["path"]).stat().st_size if diff and diff.get("path") else 0,
                "duration_ms": duration,
                "langfuse_trace_id": getattr(trace, "trace_id", None),
            }
            if trace is not None:
                trace.update(output=output)
                if trace.trace_id:
                    self.tracer.score(trace.trace_id, "changed_files", float(output["changed_files"]))
                    self.tracer.score(trace.trace_id, "duration_ms", float(duration))
        self._cleanup_worktree(repo, result)
        return output

    def _cleanup_worktree(self, repo: Path, result: dict[str, Any]) -> None:
        if result.get("mode") != "worktree":
            return
        worktree = Path(str(result.get("worktree") or ""))
        branch = str(result.get("branch") or "")
        if worktree.exists():
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo, capture_output=True)
        if branch:
            subprocess.run(["git", "branch", "-D", branch], cwd=repo, capture_output=True)

    @staticmethod
    def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
        scores = [float(item["critic_score"]) for item in results if isinstance(item.get("critic_score"), (int, float))]
        passes = [item for item in results if item.get("critique_gate") == "pass"]
        coverage = [int(item.get("intent_coverage_count") or 0) for item in results]
        return {
            "task_count": len(results),
            "pass_count": len(passes),
            "pass_rate": round(len(passes) / len(results), 3) if results else 0,
            "avg_critic_score": round(sum(scores) / len(scores), 3) if scores else None,
            "total_tokens": sum(int(item.get("tokens") or 0) for item in results),
            "total_changed_files": sum(int(item.get("changed_files") or 0) for item in results),
            "intent_coverage_total": sum(coverage),
        }

    def _sync_langfuse(
        self,
        experiment_id: str,
        lane: str,
        tasks: list[dict[str, Any]],
        results: list[dict[str, Any]],
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        """Export a completed local experiment to Langfuse without blocking the run."""
        if not self.tracer.enabled:
            return {
                "enabled": False,
                "status": "disabled",
                "reason": "Langfuse is disabled or not configured; results were saved locally.",
            }
        client = self.tracer.client
        if client is None:
            return {"enabled": False, "status": "unavailable", "reason": "Langfuse client unavailable."}
        try:
            dataset, dataset_items = self._ensure_langfuse_dataset(client, tasks)
            precomputed = {str(item.get("task_id")): item for item in results}
            local_data = [
                {
                    "input": {
                        "task_id": task["id"],
                        "prompt": task.get("prompt"),
                        "mode": task.get("mode", "design"),
                        "title": task.get("title"),
                    },
                    "expected_output": {"criteria": task.get("criteria", [])},
                    "metadata": {
                        "task_id": task["id"],
                        "lane": lane,
                        "mode": task.get("mode", "design"),
                        "target_ui": bool(task.get("target_ui", True)),
                    },
                }
                for task in tasks
            ]

            def task_runner(*, item: Any, **_: Any):
                metadata = item.get("metadata") if isinstance(item, dict) else getattr(item, "metadata", {})
                task_id = str((metadata or {}).get("task_id") or "")
                if task_id not in precomputed:
                    raise ValueError(f"no local result for task {task_id}")
                return precomputed[task_id]

            result = client.run_experiment(
                name=f"uiux-eval-{lane}",
                run_name=f"{lane}-{experiment_id}",
                description="UI/UX Agent Company golden-task experiment",
                data=dataset_items or local_data,
                task=task_runner,
                evaluators=[
                    _critic_evaluator,
                    _critic_pass_evaluator,
                    _numeric_evaluator("intent_coverage", "intent_coverage_count", "Implemented intent-packet items"),
                    _numeric_evaluator("changed_files", "changed_files", "Files changed by the lane"),
                    _numeric_evaluator("tokens", "tokens", "Tokens consumed by the lane"),
                    _numeric_evaluator("duration_ms", "duration_ms", "Wall time for the task"),
                    _numeric_evaluator("target_images", "target_images", "Target_ui images supplied"),
                    _boolean_evaluator("visual_qa_pass", "visual_verdict", "PASS", "Visual QA verdict"),
                    _code_status_evaluator,
                ],
                run_evaluators=[_run_aggregate_evaluator],
                metadata={
                    "lane": lane,
                    "local_experiment_id": experiment_id,
                    "registry_version": str(self.company.registry.version),
                    "pass_rate": str(metrics.get("pass_rate", "")),
                },
            )
            experiment_url = getattr(result, "experiment_url", None) or self._experiment_url(
                getattr(result, "experiment_id", None)
            )
            return {
                "enabled": True,
                "status": "synced",
                "dataset": dataset,
                "experiment_id": getattr(result, "experiment_id", None),
                "experiment_url": experiment_url,
                "items": len(getattr(result, "item_results", []) or []),
                "run_evaluations": [
                    {"name": item.name, "value": item.value, "comment": item.comment}
                    for item in (getattr(result, "run_evaluations", []) or [])
                ],
            }
        except Exception as exc:
            return {
                "enabled": False,
                "status": "failed",
                "reason": f"{type(exc).__name__}: {exc}",
                "dataset": self.tracer.dataset,
            }

    def _ensure_langfuse_dataset(self, client: Any, tasks: list[dict[str, Any]]) -> tuple[dict[str, Any], list[Any]]:
        dataset_name = self.tracer.dataset
        existing: set[str] = set()
        dataset = None
        try:
            dataset = client.get_dataset(dataset_name)
            existing = {str(getattr(item, "id", "")) for item in getattr(dataset, "items", []) or []}
        except Exception:
            dataset = client.create_dataset(
                name=dataset_name,
                description="UI/UX Agent Company golden tasks (org/golden_tasks.yaml)",
                metadata={"source": "org/golden_tasks.yaml"},
            )
        created = 0
        wanted_ids = {f"gt-{task['id']}" for task in tasks}
        for task in tasks:
            item_id = f"gt-{task['id']}"
            if item_id in existing:
                continue
            client.create_dataset_item(
                dataset_name=dataset_name,
                id=item_id,
                input={
                    "task_id": task["id"],
                    "prompt": task.get("prompt"),
                    "mode": task.get("mode", "design"),
                    "title": task.get("title"),
                },
                expected_output={"criteria": task.get("criteria", [])},
                metadata={"task_id": task["id"], "target_ui": bool(task.get("target_ui", True))},
            )
            created += 1
        if created:
            try:
                dataset = client.get_dataset(dataset_name)
            except Exception:
                pass
        items = [
            item for item in (getattr(dataset, "items", []) or [])
            if str(getattr(item, "id", "")) in wanted_ids
        ]
        info = {
            "name": dataset_name,
            "dataset_id": getattr(dataset, "id", None) if dataset is not None else None,
            "items": len(tasks),
            "created": created,
            "linked_items": len(items),
        }
        return info, items

    def _experiment_url(self, experiment_id: str | None) -> str | None:
        if not experiment_id:
            return None
        project_id = self.tracer.project_id
        if not project_id:
            return None
        return f"{self.tracer.base_url}/project/{project_id}/experiments/{experiment_id}"
