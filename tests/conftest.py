from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from ui_ux_company.config import PROJECT_ROOT, Settings


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    base = Settings.from_env(root=PROJECT_ROOT)
    base.storage_db = tmp_path / "uiux.sqlite3"
    base.checkpoint_db = tmp_path / "checkpoints.sqlite3"
    base.runtime_dir = tmp_path
    base.artifacts_dir = tmp_path / "artifacts"
    base.target_ui_path = PROJECT_ROOT / "target_ui_example"
    base.model_backend = "mock"
    base.auto_approve = True
    base.durable_checkpoints = False
    base.ensure_dirs()
    return base
