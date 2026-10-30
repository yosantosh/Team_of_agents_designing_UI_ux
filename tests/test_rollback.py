from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ui_ux_company.artifacts import ArtifactStore
from ui_ux_company.rollback import RollbackError, RollbackService
from ui_ux_company.storage import Storage


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _git_raw(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True)


def _repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "T")
    (path / "seed.txt").write_text("base\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "seed")
    return path


def _service(settings, storage: Storage, artifacts: ArtifactStore) -> RollbackService:
    settings.project_dir = settings.root
    return RollbackService(settings, storage, artifacts)


def test_rollback_discards_isolated_worktree_and_branch(settings, tmp_path):
    repo = _repo(tmp_path / "repo")
    worktree = tmp_path / "worktree"
    _git(repo, "worktree", "add", "-q", "-b", "uiux/run_discard", str(worktree), "HEAD")
    (worktree / "worker.txt").write_text("worker\n", encoding="utf-8")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-qm", "worker")
    storage = Storage(settings.storage_db)
    artifacts = ArtifactStore(settings, storage)
    code = {
        "status": "edited",
        "mode": "worktree",
        "repo_path": str(repo),
        "worktree": str(worktree),
        "branch": "uiux/run_discard",
        "changed_files": ["seed.txt"],
        "diff_artifact": "code_diff.patch",
    }
    try:
        storage.create_run("run_discard", "discard", "build", "completed")
        artifacts.write_json("run_discard", "code_edit_result.json", code, created_by="code_worker")
        result = _service(settings, storage, artifacts).execute("run_discard", confirm=True)
        assert result["action"] == "discard_worktree"
        assert not worktree.exists()
        assert "uiux/run_discard" not in _git(repo, "branch", "--format=%(refname:short)")
    finally:
        storage.close()


def test_rollback_reverses_direct_patch_and_removes_new_files(settings, tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "seed.txt").write_text("edited\n", encoding="utf-8")
    (repo / "new.txt").write_text("new\n", encoding="utf-8")
    _git(repo, "add", "-N", "new.txt")
    patch = _git_raw(repo, "diff", "--binary")
    storage = Storage(settings.storage_db)
    artifacts = ArtifactStore(settings, storage)
    artifacts.write_text("run_patch", "code_diff.patch", patch, "text/x-patch", created_by="code_worker")
    code = {
        "status": "edited",
        "mode": "direct",
        "repo_path": str(repo),
        "worktree": str(repo),
        "branch": _git(repo, "branch", "--show-current"),
        "changed_files": ["seed.txt", "new.txt"],
        "diff_artifact": "code_diff.patch",
    }
    try:
        storage.create_run("run_patch", "patch", "build", "completed")
        artifacts.write_json("run_patch", "code_edit_result.json", code, created_by="code_worker")
        result = _service(settings, storage, artifacts).execute("run_patch", confirm=True)
        assert result["action"] == "reverse_patch"
        assert (repo / "seed.txt").read_text(encoding="utf-8") == "base\n"
        assert not (repo / "new.txt").exists()
    finally:
        storage.close()


def test_delete_run_removes_data_and_artifacts(settings):
    storage = Storage(settings.storage_db)
    artifacts = ArtifactStore(settings, storage)
    try:
        storage.create_run("run_delete", "delete", "design", "completed")
        artifacts.write_json("run_delete", "keep.json", {"x": 1}, created_by="test")
        service = _service(settings, storage, artifacts)
        result = service.delete_run("run_delete", confirm=True)
        assert result["deleted"]["runs"] == 1
        assert storage.get_run("run_delete") is None
        assert storage.get_artifact("run_delete", "keep.json") is None
        assert not (settings.artifacts_dir / "runs" / "run_delete").exists()
    finally:
        storage.close()


def test_rollback_requires_confirmation(settings):
    storage = Storage(settings.storage_db)
    artifacts = ArtifactStore(settings, storage)
    try:
        storage.create_run("run_confirm", "confirm", "build", "completed")
        with pytest.raises(RollbackError):
            _service(settings, storage, artifacts).execute("run_confirm", confirm=False)
    finally:
        storage.close()
