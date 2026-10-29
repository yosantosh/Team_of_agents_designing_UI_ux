from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Small .env loader; does not override already-set environment variables."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _path(name: str, default: Path, root: Path) -> Path:
    value = os.getenv(name)
    if not value:
        return default
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


@dataclass(slots=True)
class Settings:
    root: Path = PROJECT_ROOT
    org_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "org")
    artifacts_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "artifacts")
    runtime_dir: Path = field(default_factory=lambda: PROJECT_ROOT / ".runtime")
    storage_db: Path = field(default_factory=lambda: PROJECT_ROOT / ".runtime" / "uiux.sqlite3")
    checkpoint_db: Path = field(default_factory=lambda: PROJECT_ROOT / ".runtime" / "checkpoints.sqlite3")
    registry_path: Path = field(default_factory=lambda: PROJECT_ROOT / "org" / "architecture_registry.yaml")
    profile_path: Path = field(default_factory=lambda: PROJECT_ROOT / "org" / "project_profile.yaml")
    budget_path: Path = field(default_factory=lambda: PROJECT_ROOT / "org" / "budget_policy.yaml")
    registry_schema_path: Path = field(default_factory=lambda: PROJECT_ROOT / "org" / "schemas" / "registry.schema.json")
    target_ui_path: Path = field(default_factory=lambda: PROJECT_ROOT / "target_ui_example")

    model_backend: str = "opencode"       # opencode | mock (mock is test-only)
    opencode_bin: str = "opencode"
    opencode_agent: str = "uiux-worker"
    opencode_model: str = "modal/deepseek-v4.1-flash-1017"
    model_override: str | None = None
    opencode_timeout: int = 300
    opencode_cwd: Path = PROJECT_ROOT
    max_context_chars: int = 120000
    project_dir: Path | None = None

    autonomy_level: str = "L2"
    auto_approve: bool = True
    max_rework: int = 2
    target_ui_enabled: bool = True
    target_ui_analyze: bool = True

    monthly_token_budget: int = 100_000_000
    token_warn_pct: int = 80
    token_hard_stop_pct: int = 100

    host: str = "127.0.0.1"
    port: int = 8765
    durable_checkpoints: bool = True

    @classmethod
    def from_env(cls, root: Path | None = None) -> "Settings":
        root = (root or PROJECT_ROOT).resolve()
        _load_dotenv(root / ".env")
        org_dir = _path("UIUX_ORG_DIR", root / "org", root)
        artifacts_dir = _path("UIUX_ARTIFACTS_DIR", root / "artifacts", root)
        runtime_dir = _path("UIUX_DATA_DIR", root / ".runtime", root)
        settings = cls(
            root=root,
            org_dir=org_dir,
            artifacts_dir=artifacts_dir,
            runtime_dir=runtime_dir,
            storage_db=_path("UIUX_STORAGE_DB", runtime_dir / "uiux.sqlite3", root),
            checkpoint_db=_path("UIUX_CHECKPOINT_DB", runtime_dir / "checkpoints.sqlite3", root),
            registry_path=_path("UIUX_REGISTRY", org_dir / "architecture_registry.yaml", root),
            profile_path=_path("UIUX_PROFILE", org_dir / "project_profile.yaml", root),
            budget_path=_path("UIUX_BUDGET", org_dir / "budget_policy.yaml", root),
            registry_schema_path=_path("UIUX_REGISTRY_SCHEMA", org_dir / "schemas" / "registry.schema.json", root),
            target_ui_path=_path("UIUX_TARGET_UI_PATH", root / "target_ui_example", root),
            model_backend=os.getenv("UIUX_MODEL_BACKEND", "opencode").strip().lower(),
            opencode_bin=os.getenv("UIUX_OPENCODE_BIN", "opencode"),
            opencode_agent=os.getenv("UIUX_OPENCODE_AGENT", "uiux-worker"),
            opencode_model=os.getenv("UIUX_OPENCODE_MODEL", "modal/deepseek-v4.1-flash-1017"),
            model_override=os.getenv("UIUX_MODEL_OVERRIDE") or None,
            opencode_timeout=_int("UIUX_OPENCODE_TIMEOUT", 300),
            opencode_cwd=_path("UIUX_OPENCODE_CWD", root, root),
            max_context_chars=_int("UIUX_MAX_CONTEXT_CHARS", 120000),
            autonomy_level=os.getenv("UIUX_AUTONOMY_LEVEL", "L2"),
            auto_approve=_bool("UIUX_AUTO_APPROVE", True),
            max_rework=_int("UIUX_MAX_REWORK", 2),
            target_ui_enabled=_bool("UIUX_TARGET_UI_ENABLED", True),
            target_ui_analyze=_bool("UIUX_TARGET_UI_ANALYZE", True),
            monthly_token_budget=_int("UIUX_MONTHLY_TOKEN_BUDGET", 100_000_000),
            token_warn_pct=_int("UIUX_TOKEN_WARN_PCT", 80),
            token_hard_stop_pct=_int("UIUX_TOKEN_HARD_STOP_PCT", 100),
            host=os.getenv("UIUX_HOST", "127.0.0.1"),
            port=_int("UIUX_PORT", 8765),
            durable_checkpoints=_bool("UIUX_DURABLE_CHECKPOINTS", True),
        )
        settings.ensure_dirs()
        load_runtime_settings(settings)
        return settings

    def ensure_dirs(self) -> None:
        for path in (
            self.root,
            self.org_dir,
            self.artifacts_dir,
            self.runtime_dir,
            self.artifacts_dir / "runs",
            self.artifacts_dir / "target_ui",
            self.runtime_dir / "runs",
        ):
            path.mkdir(parents=True, exist_ok=True)

    def artifact_dir(self, run_id: str) -> Path:
        path = self.artifacts_dir / "runs" / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def runtime_settings_path(self) -> Path:
        return self.runtime_dir / "settings.json"


def _resolve_path(raw: str | None, root: Path) -> Path | None:
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def load_runtime_settings(settings: Settings) -> dict[str, Any]:
    """Load dashboard-editable settings persisted under .runtime/settings.json."""
    path = settings.runtime_settings_path
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("model"):
        settings.model_override = str(data["model"])
        settings.opencode_model = str(data["model"])
    if data.get("monthly_token_budget"):
        try:
            settings.monthly_token_budget = int(data["monthly_token_budget"])
        except (TypeError, ValueError):
            pass
    if data.get("project_dir"):
        resolved = _resolve_path(str(data["project_dir"]), settings.root)
        if resolved:
            settings.project_dir = resolved
    if data.get("target_ui_dir"):
        resolved = _resolve_path(str(data["target_ui_dir"]), settings.root)
        if resolved:
            settings.target_ui_path = resolved
    if data.get("autonomy_level"):
        settings.autonomy_level = str(data["autonomy_level"])
    return data


def save_runtime_settings(settings: Settings, **updates: Any) -> dict[str, Any]:
    path = settings.runtime_settings_path
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (json.JSONDecodeError, OSError):
            data = {}
    for key, value in updates.items():
        if value is None:
            continue
        if key == "target_ui_dir":
            data["target_ui_dir"] = str(value)
        elif key == "project_dir":
            data["project_dir"] = str(value)
        elif key == "model":
            data["model"] = str(value)
            settings.model_override = str(value)
            settings.opencode_model = str(value)
        elif key == "monthly_token_budget":
            data["monthly_token_budget"] = int(value)
            settings.monthly_token_budget = int(value)
        elif key == "autonomy_level":
            data["autonomy_level"] = str(value)
            settings.autonomy_level = str(value)
        else:
            data[key] = value
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data
