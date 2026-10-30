from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .artifacts import ArtifactStore
from .config import Settings
from .models import utcnow
from .storage import Storage


class RollbackError(RuntimeError):
    pass


class RollbackService:
    """Safely undo a run's code changes without resetting the shared checkout."""

    def __init__(self, settings: Settings, storage: Storage, artifacts: ArtifactStore):
        self.settings = settings
        self.storage = storage
        self.artifacts = artifacts

    def _run(self, args: list[str], cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout,
        )

    def code_result(self, run_id: str) -> dict[str, Any]:
        artifact = self.storage.get_artifact(run_id, "code_edit_result.json")
        if artifact and artifact.get("path"):
            try:
                return json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        run = self.storage.get_run(run_id)
        state = (run or {}).get("state") or {}
        implementation = state.get("implementation") if isinstance(state, dict) else {}
        if isinstance(implementation, dict):
            return dict(implementation.get("code_edits") or {})
        return {}

    def _repo(self, code: dict[str, Any]) -> Path:
        raw = code.get("repo_path") or self.settings.project_dir or self.settings.root
        repo = Path(str(raw)).expanduser().resolve()
        if not (repo / ".git").exists():
            raise RollbackError(f"target repo is not a git repository: {repo}")
        return repo

    def plan(self, run_id: str) -> dict[str, Any]:
        run = self.storage.get_run(run_id)
        if not run:
            raise RollbackError("run not found")
        if isinstance(run.get("state"), dict) and run["state"].get("rollback"):
            return {
                "run_id": run_id,
                "run_status": run.get("status"),
                "action": "none",
                "reason": "run already rolled back",
                "has_patch": False,
            }
        code = self.code_result(run_id)
        patch = self.storage.get_artifact(run_id, "code_diff.patch")
        worktree = Path(str(code.get("worktree"))) if code.get("worktree") else None
        mode = str(code.get("mode") or "")
        branch = str(code.get("branch") or "")
        branch_exists = False
        branch_merged = False
        repo = self._repo(code) if code else None
        if repo and branch:
            branch_exists = self._run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], repo).returncode == 0
            merged_proc = self._run(["git", "branch", "--merged"], repo)
            branch_merged = any(line.strip().lstrip("* ") == branch for line in merged_proc.stdout.splitlines())
        if mode == "worktree" and worktree and worktree.exists():
            action = "discard_worktree"
        elif mode == "worktree" and branch_exists and not branch_merged:
            action = "discard_worktree"
        elif mode == "worktree" and branch_exists and branch_merged and code.get("worker_commit"):
            action = "revert_commit"
        elif code.get("diff_artifact") and patch:
            action = "reverse_patch"
        elif code.get("worker_commit"):
            action = "revert_commit"
        else:
            action = "none"
        return {
            "run_id": run_id,
            "run_status": run.get("status"),
            "action": action,
            "mode": mode,
            "worktree": str(worktree) if worktree else None,
            "branch": code.get("branch"),
            "base_commit": code.get("base_commit"),
            "worker_commit": code.get("worker_commit"),
            "changed_files": code.get("changed_files") or [],
            "has_patch": bool(patch),
            "branch_exists": branch_exists,
            "branch_merged": branch_merged,
        }

    def execute(self, run_id: str, mode: str = "auto", *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise RollbackError("rollback requires explicit confirmation")
        run = self.storage.get_run(run_id)
        if not run:
            raise RollbackError("run not found")
        if run.get("status") in {"queued", "running", "awaiting_approval", "awaiting_design_approval"}:
            raise RollbackError("cannot rollback an active run")
        code = self.code_result(run_id)
        if not code:
            raise RollbackError("run has no code edit result")
        repo = self._repo(code)
        plan = self.plan(run_id)
        action = plan["action"] if mode == "auto" else mode
        if action == "none":
            raise RollbackError("run has no rollbackable code changes")
        if action == "discard_worktree":
            result = self._discard_worktree(repo, code)
        elif action == "reverse_patch":
            result = self._reverse_patch(repo, run_id)
        elif action == "revert_commit":
            result = self._revert_commit(repo, code)
        else:
            raise RollbackError(f"unknown rollback mode: {action}")

        record = {
            "run_id": run_id,
            "action": action,
            "mode": code.get("mode"),
            "branch": code.get("branch"),
            "worktree": code.get("worktree"),
            "base_commit": code.get("base_commit"),
            "worker_commit": code.get("worker_commit"),
            "result": result,
            "rolled_back_at": utcnow(),
        }
        self.artifacts.write_json(run_id, "rollback.json", record, created_by="rollback_service")
        state = dict(run.get("state") or {})
        state["rollback"] = record
        self.storage.update_run(run_id, state=state)
        return record

    def _discard_worktree(self, repo: Path, code: dict[str, Any]) -> dict[str, Any]:
        worktree = Path(str(code.get("worktree"))).expanduser()
        branch = str(code.get("branch") or "")
        removed = False
        if worktree.exists():
            proc = self._run(["git", "worktree", "remove", "--force", str(worktree)], repo)
            if proc.returncode != 0:
                shutil.rmtree(worktree, ignore_errors=True)
            removed = not worktree.exists()
        self._run(["git", "worktree", "prune"], repo)
        branch_deleted = False
        merged = False
        if branch:
            merged_proc = self._run(["git", "branch", "--merged"], repo)
            merged = any(line.strip().lstrip("* ") == branch for line in merged_proc.stdout.splitlines())
            if not merged:
                delete = self._run(["git", "branch", "-D", branch], repo)
                branch_deleted = delete.returncode == 0
        return {
            "worktree_removed": removed,
            "branch": branch,
            "branch_deleted": branch_deleted,
            "branch_merged": merged,
            "note": "Merged branch was left untouched; use revert_commit to undo merged code." if merged else "",
        }

    def _reverse_patch(self, repo: Path, run_id: str) -> dict[str, Any]:
        artifact = self.storage.get_artifact(run_id, "code_diff.patch")
        if not artifact or not artifact.get("path"):
            raise RollbackError("code_diff.patch is missing")
        patch_path = Path(artifact["path"])
        check = self._run(["git", "apply", "--check", "-R", str(patch_path)], repo)
        if check.returncode != 0:
            raise RollbackError(f"reverse patch does not apply cleanly: {check.stderr[:1000]}")
        apply = self._run(["git", "apply", "-R", str(patch_path)], repo)
        if apply.returncode != 0:
            raise RollbackError(f"reverse patch failed: {apply.stderr[:1000]}")
        return {"patch": str(patch_path), "applied": True}

    def _revert_commit(self, repo: Path, code: dict[str, Any]) -> dict[str, Any]:
        worker_commit = str(code.get("worker_commit") or "")
        if not worker_commit:
            raise RollbackError("worker commit is missing")
        ancestor = self._run(["git", "merge-base", "--is-ancestor", worker_commit, "HEAD"], repo)
        if ancestor.returncode != 0:
            raise RollbackError("worker commit is not on the current branch; cannot revert")
        revert = self._run(["git", "revert", "--no-edit", worker_commit], repo, timeout=180)
        if revert.returncode != 0:
            self._run(["git", "revert", "--abort"], repo)
            raise RollbackError(f"git revert failed: {revert.stderr[:1000]}")
        return {"reverted_commit": worker_commit, "output": revert.stdout.strip()[:500]}

    def delete_run(self, run_id: str, *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise RollbackError("delete requires explicit confirmation")
        run = self.storage.get_run(run_id)
        if not run:
            raise RollbackError("run not found")
        if run.get("status") in {"queued", "running", "awaiting_approval", "awaiting_design_approval"}:
            raise RollbackError("cannot delete an active run")
        code = self.code_result(run_id)
        repo = None
        worktree = None
        branch = None
        if code:
            try:
                repo = self._repo(code)
                worktree = Path(str(code.get("worktree"))).expanduser() if code.get("worktree") else None
                branch = code.get("branch")
            except RollbackError:
                repo = None
        if worktree and worktree.exists() and repo is not None:
            self._discard_worktree(repo, code)
        run_dir = self.settings.artifact_dir(run_id)
        if run_dir.exists():
            shutil.rmtree(run_dir, ignore_errors=True)
        deleted = self.storage.delete_run(run_id)
        return {
            "run_id": run_id,
            "deleted": deleted.get("deleted", {}),
            "worktree_removed": not worktree.exists() if worktree else False,
            "branch": branch,
            "artifacts_removed": str(run_dir),
        }
