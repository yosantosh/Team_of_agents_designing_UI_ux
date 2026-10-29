from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from ui_ux_company.artifacts import ArtifactStore
from ui_ux_company.codeworker import CodeWorker
from ui_ux_company.storage import Storage


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "seed")
    return path


def _fake_opencode(path: Path, *, filename: str = "probe.txt") -> Path:
    script = path / "fake-opencode"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        "from pathlib import Path\n"
        f"Path({filename!r}).write_text('EDITED', encoding='utf-8')\n"
        "Path('cwd_probe.txt').write_text(os.getcwd(), encoding='utf-8')\n"
        "Path('pwd_probe.txt').write_text(os.environ.get('PWD', ''), encoding='utf-8')\n"
        "print(json.dumps({'type': 'text', 'part': {'text': 'edited ' + "
        f"{filename!r}"
        "}}))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _worker(settings, monkeypatch, script: Path) -> tuple[CodeWorker, Storage]:
    settings.code_edits_enabled = True
    settings.code_edit_mode = "worktree"
    settings.opencode_bin = str(script)
    settings.code_worker_timeout = 30
    settings.model_backend = "mock"
    storage = Storage(settings.storage_db)
    artifacts = ArtifactStore(settings, storage)
    return CodeWorker(settings, artifacts), storage


def test_worktree_worker_edits_worktree_and_records_reviewable_patch(settings, tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "target")
    worker, storage = _worker(settings, monkeypatch, _fake_opencode(tmp_path))
    try:
        result = asyncio.run(worker.apply("run_cw", repo, "Implement the approved design", {}))
        worktree = Path(result["worktree"])

        assert result["status"] == "edited"
        assert result["mode"] == "worktree"
        assert result["branch"] == "uiux/run_cw"
        assert "probe.txt" in result["changed_files"]
        assert (worktree / "probe.txt").read_text(encoding="utf-8") == "EDITED"
        assert (worktree / "cwd_probe.txt").read_text(encoding="utf-8") == str(worktree)
        assert (worktree / "pwd_probe.txt").read_text(encoding="utf-8") == str(worktree)
        assert not (repo / "probe.txt").exists(), "the target checkout must stay untouched"
        assert _git(repo, "status", "--short") == ""
        assert result["diff_artifact"] == "code_diff.patch"
        patch = (settings.artifact_dir("run_cw") / "code_diff.patch").read_text(encoding="utf-8")
        assert "probe.txt" in patch
        assert "cwd_probe.txt" in patch
        assert storage.get_artifact("run_cw", "code_edit_result.json") is not None
    finally:
        storage.close()


def test_direct_mode_includes_untracked_files_in_patch_without_staging_real_index(settings, tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "target")
    worker, storage = _worker(settings, monkeypatch, _fake_opencode(tmp_path))
    settings.code_edit_mode = "direct"
    try:
        result = asyncio.run(worker.apply("run_direct", repo, "Implement the approved design", {}))
        assert result["status"] == "edited"
        assert result["worktree"] == str(repo.resolve())
        assert (repo / "probe.txt").exists()
        patch = (settings.artifact_dir("run_direct") / "code_diff.patch").read_text(encoding="utf-8")
        assert "probe.txt" in patch, "untracked files must be reviewable in direct mode"
        assert _git(repo, "diff", "--cached", "--name-only") == ""
    finally:
        storage.close()


def test_worktree_worker_snapshots_uncommitted_target_changes(settings, tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "target")
    (repo / "seed.txt").write_text("dirty tracked change\n", encoding="utf-8")
    (repo / "untracked_note.txt").write_text("local note\n", encoding="utf-8")
    worker, storage = _worker(settings, monkeypatch, _fake_opencode(tmp_path))
    try:
        result = asyncio.run(worker.apply("run_dirty", repo, "Implement", {}))
        worktree = Path(result["worktree"])
        assert result["status"] == "edited"
        assert (worktree / "seed.txt").read_text(encoding="utf-8") == "dirty tracked change\n"
        assert (worktree / "untracked_note.txt").read_text(encoding="utf-8") == "local note\n"
        assert (worktree / "probe.txt").read_text(encoding="utf-8") == "EDITED"
        patch = (settings.artifact_dir("run_dirty") / "code_diff.patch").read_text(encoding="utf-8")
        assert "probe.txt" in patch
        assert "untracked_note.txt" not in patch, "the patch should contain only the worker's changes"
        assert (repo / "seed.txt").read_text(encoding="utf-8") == "dirty tracked change\n"
        assert (repo / "untracked_note.txt").exists()
        assert not (repo / "probe.txt").exists(), "the target checkout must stay untouched"
    finally:
        storage.close()


def test_worker_refuses_non_git_target(settings, tmp_path, monkeypatch):
    target = tmp_path / "not-a-repo"
    target.mkdir()
    worker, storage = _worker(settings, monkeypatch, _fake_opencode(tmp_path))
    try:
        result = asyncio.run(worker.apply("run_missing", target, "Implement", {}))
        assert result["status"] == "failed"
        assert "not a git repository" in result["reason"]
    finally:
        storage.close()


def test_worker_can_be_disabled(settings, tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "target")
    worker, storage = _worker(settings, monkeypatch, _fake_opencode(tmp_path))
    settings.code_edits_enabled = False
    try:
        result = asyncio.run(worker.apply("run_off", repo, "Implement", {}))
        assert result["status"] == "disabled"
        assert not (repo / "probe.txt").exists()
    finally:
        storage.close()
