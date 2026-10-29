from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .artifacts import ArtifactStore
from .config import Settings


class CodeWorker:
    """Runs a real OpenCode build agent against a target Android repository.

    Default mode is an isolated git worktree: the agent edits real project files,
    but the main checkout is untouched until a human/merge gate promotes the branch.
    """

    def __init__(self, settings: Settings, artifacts: ArtifactStore):
        self.settings = settings
        self.artifacts = artifacts

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, "code_edits_enabled", False))

    async def _run(
        self,
        cmd: list[str],
        cwd: Path,
        timeout: int,
        env_extra: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        env = dict(os.environ)
        # OpenCode resolves the project from $PWD, not only the OS cwd. Without this
        # a worker launched by the server edits the server's project instead of the
        # target worktree.
        env["PWD"] = str(cwd)
        if env_extra:
            env.update({str(key): str(value) for key, value in env_extra.items()})
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, "", f"timed out after {timeout}s"
        return (
            int(proc.returncode or 0),
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
        )

    async def _snapshot_commit(self, repo: Path, run_id: str) -> tuple[str, str | None]:
        """Create a temporary commit of the current working tree without touching it.

        Worktree mode must start from what the human is actually looking at, including
        tracked edits and untracked files. The snapshot exists only in the object DB;
        the target checkout, index, and current branch are not modified.
        """
        head_code, head_out, head_err = await self._run(["git", "rev-parse", "--verify", "HEAD"], repo, 60)
        if head_code != 0:
            return "HEAD", f"target repo has no commits: {head_err[:500]}"
        head = head_out.strip()
        base_tree_code, base_tree_out, _ = await self._run(["git", "rev-parse", f"{head}^{{tree}}"], repo, 60)
        if base_tree_code != 0:
            return "HEAD", "could not read the target repo tree"

        fd, index_path = tempfile.mkstemp(prefix="uiux-snapshot-index-", dir=str(self.settings.runtime_dir))
        os.close(fd)
        env = {"GIT_INDEX_FILE": index_path}
        try:
            read_code, _read_out, read_err = await self._run(["git", "read-tree", head], repo, 60, env)
            if read_code != 0:
                return "HEAD", f"could not snapshot the target repo: {read_err[:500]}"
            add_code, _add_out, add_err = await self._run(["git", "add", "-A"], repo, 60, env)
            if add_code != 0:
                return "HEAD", f"could not snapshot the target repo: {add_err[:500]}"
            tree_code, tree_out, tree_err = await self._run(["git", "write-tree"], repo, 60, env)
            if tree_code != 0:
                return "HEAD", f"could not snapshot the target repo: {tree_err[:500]}"
            tree = tree_out.strip()
            if tree == base_tree_out.strip():
                return "HEAD", None
            author = {
                "GIT_AUTHOR_NAME": "UIUX Agent Company",
                "GIT_AUTHOR_EMAIL": "uiux@local",
                "GIT_COMMITTER_NAME": "UIUX Agent Company",
                "GIT_COMMITTER_EMAIL": "uiux@local",
            }
            commit_code, commit_out, commit_err = await self._run(
                ["git", "commit-tree", tree, "-p", head, "-m", f"uiux: snapshot for {run_id}"],
                repo,
                60,
                {**env, **author},
            )
            if commit_code != 0:
                return "HEAD", f"could not snapshot the target repo: {commit_err[:500]}"
            return commit_out.strip(), None
        finally:
            Path(index_path).unlink(missing_ok=True)

    async def apply(
        self,
        run_id: str,
        repo_path: str | Path,
        goal: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled", "reason": "UIUX_CODE_EDITS is off"}
        repo = Path(repo_path).expanduser().resolve()
        if not (repo / ".git").exists():
            return {"status": "failed", "reason": f"target repo is not a git repository: {repo}"}
        mode = str(getattr(self.settings, "code_edit_mode", "worktree"))
        if mode == "direct":
            worktree = repo
            branch = self._git_branch(repo)
        else:
            base, snapshot_error = await self._snapshot_commit(repo, run_id)
            if snapshot_error:
                return {"status": "failed", "reason": snapshot_error}
            branch = f"uiux/{run_id}"
            worktree = self.settings.runtime_dir / "worktrees" / run_id
            await self._run(["git", "worktree", "remove", "--force", str(worktree)], repo, 60)
            await self._run(["git", "worktree", "prune"], repo, 60)
            if worktree.exists():
                shutil.rmtree(worktree, ignore_errors=True)
            worktree.parent.mkdir(parents=True, exist_ok=True)
            code, _out, err = await self._run(
                ["git", "worktree", "add", "-B", branch, str(worktree), base],
                repo,
                120,
            )
            if code != 0:
                return {"status": "failed", "reason": f"git worktree failed: {err[:1000]}"}

        model = getattr(self.settings, "code_worker_model", "") or getattr(self.settings, "model_override", "") or self.settings.opencode_model
        prompt = (
            "You are the Android implementation worker for the UI/UX Agent Company. "
            "Edit the project files directly to implement the approved design. "
            "Respect the repository AGENTS.md, design tokens, shared-file ownership, and the approved plan. "
            "Do not touch secrets or production infrastructure. Keep the change surgical. "
            "Run the narrowest useful verification command if the repository supports it. "
            "When finished, briefly summarize files changed and verification evidence.\n\n"
            f"RUN: {run_id}\nGOAL: {goal}\n\n"
            "APPROVED CONTEXT:\n"
            + json.dumps(context, ensure_ascii=False, indent=2, default=str)[:60000]
        )
        cmd = [
            self.settings.opencode_bin,
            "--standalone",
            "run",
            "--agent",
            getattr(self.settings, "code_worker_agent", "build"),
            "--model",
            model,
            "--auto",
            "--format",
            "json",
            prompt,
        ]
        code, stdout, stderr = await self._run(
            cmd, worktree, int(getattr(self.settings, "code_worker_timeout", 900))
        )
        events: list[dict[str, Any]] = []
        texts: list[str] = []
        tools: list[dict[str, Any]] = []
        errors: list[str] = []
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            events.append(event)
            if event.get("type") == "text":
                part = event.get("part") or {}
                if isinstance(part, dict) and part.get("text"):
                    texts.append(str(part["text"]))
            if event.get("type") == "tool_use":
                part = event.get("part") or {}
                if isinstance(part, dict):
                    tools.append({"tool": part.get("tool"), "state": (part.get("state") or {}).get("status")})
            if event.get("type") == "error":
                errors.append(str(event.get("error")))
        # Capture the diff. Worktree mode commits so the patch is durable and reviewable.
        _status_code, status_out, _ = await self._run(["git", "status", "--porcelain"], worktree, 60)
        changed_files = [line[3:] for line in status_out.splitlines() if line.strip()]
        diff = ""
        if mode != "direct" and changed_files:
            await self._run(["git", "add", "-A"], worktree, 60)
            commit_code, _commit_out, commit_err = await self._run(
                ["git", "-c", "user.name=UIUX Agent Company", "-c", "user.email=uiux@local", "commit", "-m", f"uiux: {run_id} implementation"],
                worktree,
                120,
            )
            if commit_code == 0:
                _, diff, _ = await self._run(["git", "diff", "HEAD~1", "HEAD", "--binary"], worktree, 120)
            else:
                errors.append(f"git commit failed: {commit_err[:1000]}")
                _, diff, _ = await self._run(["git", "diff", "--cached", "--binary", "HEAD"], worktree, 120)
        elif changed_files:
            # Direct mode must not stage into the user's real index. Use a temporary
            # index so untracked files are included in the reviewable patch.
            fd, index_path = tempfile.mkstemp(prefix="uiux-index-", dir=str(self.settings.runtime_dir))
            os.close(fd)
            try:
                env_extra = {"GIT_INDEX_FILE": index_path}
                read_code, _read_out, _read_err = await self._run(
                    ["git", "read-tree", "HEAD"], worktree, 60, env_extra
                )
                if read_code == 0:
                    await self._run(["git", "add", "-A"], worktree, 60, env_extra)
                    _, diff, _ = await self._run(
                        ["git", "diff", "--cached", "--binary", "HEAD"], worktree, 120, env_extra
                    )
                else:
                    _, diff, _ = await self._run(["git", "diff", "--binary"], worktree, 120)
            finally:
                Path(index_path).unlink(missing_ok=True)

        self.artifacts.write_text(run_id, "code_worker_transcript.txt", stdout + "\n" + stderr, created_by="code_worker")
        if diff:
            self.artifacts.write_text(run_id, "code_diff.patch", diff, "text/x-patch", created_by="code_worker")
        result = {
            "status": "edited" if changed_files else ("failed" if code != 0 else "no_changes"),
            "mode": mode,
            "worktree": str(worktree),
            "branch": branch,
            "changed_files": changed_files[:200],
            "tool_calls": tools,
            "errors": errors,
            "exit_code": code,
            "summary": "\n".join(texts)[-4000:],
            "diff_artifact": "code_diff.patch" if diff else None,
        }
        self.artifacts.write_json(run_id, "code_edit_result.json", result, created_by="code_worker")
        return result

    def _git_branch(self, repo: Path) -> str:
        import subprocess

        try:
            return subprocess.check_output(["git", "branch", "--show-current"], cwd=repo, text=True).strip() or "detached"
        except Exception:
            return "unknown"
