from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .config import Settings
from .models import utcnow
from .registry import ProjectProfile


DEFAULT_NAMES = (
    "docs/dhunchat_android_liquid_glass_ui_ux_plan.md",
    "docs/ui_ux_refinment_plan.md",
    "docs/overall_app_plan.md",
    "docs/app_plan.md",
    "App_plan.md",
    "APP_PLAN.md",
    "org/app_plan.md",
)


def _repo_root(settings: Settings, profile: ProjectProfile) -> Path:
    raw = profile.project.get("repo") or settings.project_dir or settings.root
    return Path(str(raw)).expanduser().resolve()


def _resolve(raw: str | None, roots: list[Path]) -> Path | None:
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    if path.is_absolute():
        return path.resolve()
    for root in roots:
        candidate = (root / path).resolve()
        if candidate.exists():
            return candidate
    return (roots[0] / path).resolve() if roots else path.resolve()


def plan_candidates(settings: Settings, profile: ProjectProfile) -> list[Path]:
    repo = _repo_root(settings, profile)
    roots = [repo, settings.root]
    declared = [
        profile.project.get("app_plan_path"),
        profile.data.get("design_system", {}).get("spec_path"),
        profile.data.get("design_system", {}).get("baseline_path"),
    ]
    paths: list[Path] = []
    for raw in declared:
        resolved = _resolve(raw, roots)
        if resolved and resolved not in paths:
            paths.append(resolved)
    for name in DEFAULT_NAMES:
        resolved = _resolve(name, roots)
        if resolved and resolved not in paths:
            paths.append(resolved)
    return paths


def _choose(settings: Settings, profile: ProjectProfile, raw_path: str | None = None) -> Path:
    repo = _repo_root(settings, profile)
    roots = [repo, settings.root]
    if raw_path:
        return _resolve(raw_path, roots) or (repo / raw_path).resolve()
    for candidate in plan_candidates(settings, profile):
        if candidate.exists():
            return candidate
    return plan_candidates(settings, profile)[0]


def app_plan_path(settings: Settings, profile: ProjectProfile, raw_path: str | None = None) -> Path:
    return _choose(settings, profile, raw_path)


def read_app_plan(settings: Settings, profile: ProjectProfile, raw_path: str | None = None) -> dict[str, Any]:
    path = _choose(settings, profile, raw_path)
    candidates = [
        {"path": str(item), "exists": item.exists(), "relative": _relative(item, settings.root)}
        for item in plan_candidates(settings, profile)
    ]
    if not path.exists():
        return {
            "path": str(path),
            "relative_path": _relative(path, settings.root),
            "exists": False,
            "content": "",
            "sha256": "",
            "headings": [],
            "candidates": candidates,
            "updated_at": None,
        }
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "path": str(path),
        "relative_path": _relative(path, settings.root),
        "exists": True,
        "content": text,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "headings": re.findall(r"(?m)^#{1,4}\s+(.+)$", text)[:30],
        "candidates": candidates,
        "size": len(text),
        "updated_at": utcnow(),
    }


def write_app_plan(
    settings: Settings,
    profile: ProjectProfile,
    content: str,
    raw_path: str | None = None,
) -> dict[str, Any]:
    repo = _repo_root(settings, profile)
    path = _choose(settings, profile, raw_path)
    allowed = [repo, settings.root]
    if not any(_is_relative(path, root) for root in allowed):
        raise ValueError(f"app plan path is outside the project/company roots: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return read_app_plan(settings, profile, str(path))


def search_app_plans(
    settings: Settings,
    profile: ProjectProfile,
    query: str = "plan",
    limit: int = 50,
) -> list[dict[str, Any]]:
    repo = _repo_root(settings, profile)
    roots = [repo / "docs", repo, settings.org_dir]
    needle = (query or "plan").lower()
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        pattern = "**/*.md" if root != repo else "docs/**/*.md"
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            haystack = f"{path.name} {path.parent.name}".lower()
            if needle not in haystack and not any(word in haystack for word in ("plan", "spec", "design", "ux")):
                continue
            if path not in found:
                found.append(path)
            if len(found) >= limit:
                return [
                    {"path": str(item), "relative_path": _relative(item, settings.root), "name": item.name}
                    for item in found
                ]
    return [
        {"path": str(item), "relative_path": _relative(item, settings.root), "name": item.name}
        for item in found[:limit]
    ]


def plan_context(settings: Settings, profile: ProjectProfile, max_chars: int = 8000) -> dict[str, Any]:
    plan = read_app_plan(settings, profile)
    text = str(plan.get("content") or "")
    excerpt = text[:max_chars]
    return {
        "path": plan.get("relative_path") or plan.get("path"),
        "sha256": plan.get("sha256"),
        "exists": plan.get("exists"),
        "headings": plan.get("headings") or [],
        "excerpt": excerpt,
        "truncated": len(text) > max_chars,
    }


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _is_relative(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
