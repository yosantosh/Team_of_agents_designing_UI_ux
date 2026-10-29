from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


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

    model_backend: str = "opencode"       # opencode | mock
    opencode_bin: str = "opencode"
    opencode_agent: str = "uiux-worker"
    opencode_model: str = "modal/deepseek-v4.1-flash-1017"
    opencode_timeout: int = 300
    opencode_cwd: Path = PROJECT_ROOT
    max_context_chars: int = 120000

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
