from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from .codeworker import CodeWorker
from .config import Settings
from .models import new_id
from .registry import ProjectProfile
from .storage import Storage


LANES = {
    "company_with_packet": "Company pipeline + intent packet + target images",
    "company_without_packet": "Company pipeline without intent packet",
    "direct_opencode": "Direct OpenCode build baseline",
}


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
    ):
        self.settings = settings
        self.storage = storage
        self.company = company
        self.profile = profile
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
            metrics["langsmith"] = self._sync_langsmith(lane, tasks, results, metrics)
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
        }

    async def _run_direct(self, task: dict[str, Any], *, model: str | None) -> dict[str, Any]:
        repo = Path(str(self.profile.project.get("repo") or self.settings.project_dir or self.settings.root))
        run_id = new_id("evaldirect")
        started = time.monotonic()
        result = await self.code_worker.apply(
            run_id,
            repo,
            str(task.get("prompt") or task.get("title") or task["id"]),
            {"prompt": task.get("prompt"), "mode": "direct_baseline"},
        )
        duration = int((time.monotonic() - started) * 1000)
        diff = self.storage.get_artifact(run_id, "code_diff.patch")
        self._cleanup_worktree(repo, result)
        return {
            "task_id": task["id"],
            "lane": "direct_opencode",
            "run_id": run_id,
            "status": result.get("status"),
            "code_status": result.get("status"),
            "changed_files": len(result.get("changed_files") or []),
            "diff_chars": Path(diff["path"]).stat().st_size if diff and diff.get("path") else 0,
            "duration_ms": duration,
        }

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

    def _sync_langsmith(
        self,
        lane: str,
        tasks: list[dict[str, Any]],
        results: list[dict[str, Any]],
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        if not os.getenv("LANGSMITH_API_KEY"):
            return {"enabled": False, "reason": "LANGSMITH_API_KEY is not set"}
        try:
            from langsmith import Client
        except Exception as exc:
            return {"enabled": False, "reason": f"langsmith is not installed: {exc}"}
        try:
            client = Client()
            dataset_name = os.getenv("LANGSMITH_DATASET", "uiux-golden-tasks")
            if not client.has_dataset(dataset_name=dataset_name):
                client.create_dataset(dataset_name, description="UI/UX Agent Company golden tasks")
            for task in tasks:
                client.create_example(
                    dataset_name=dataset_name,
                    inputs={"task_id": task["id"], "prompt": task.get("prompt"), "mode": task.get("mode", "design")},
                    outputs={"criteria": task.get("criteria", [])},
                    metadata={"task_id": task["id"]},
                )
            project = os.getenv("LANGSMITH_PROJECT", "uiux-agent-company")
            for item in results:
                client.create_run(
                    name=f"uiux-eval-{lane}-{item['task_id']}",
                    inputs={"task_id": item["task_id"], "lane": lane},
                    run_type="chain",
                    project_name=project,
                    outputs=item,
                    extra={"lane": lane, "metrics": metrics, "task_id": item["task_id"]},
                )
            return {"enabled": True, "dataset": dataset_name, "project": project, "logged": len(results)}
        except Exception as exc:
            return {"enabled": False, "reason": str(exc)}
