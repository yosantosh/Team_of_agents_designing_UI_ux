from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


SKIP_DIRS = {".git", "node_modules", "build", ".gradle", ".idea", ".runtime", "artifacts", "__pycache__"}


def _read(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _first_match(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, re.MULTILINE)
    return match.group(1).strip() if match else None


def validate_android_project(path: str | Path) -> dict[str, Any]:
    root = Path(path).expanduser().resolve()
    result: dict[str, Any] = {
        "path": str(root),
        "exists": root.is_dir(),
        "valid": False,
        "score": 0,
        "package": None,
        "namespace": None,
        "min_sdk": None,
        "compile_sdk": None,
        "target_sdk": None,
        "ui_stack": None,
        "has_gradle": False,
        "has_manifest": False,
        "has_compose": False,
        "has_design_tokens": False,
        "has_current_ui": False,
        "has_target_ui": False,
        "warnings": [],
        "errors": [],
    }
    if not root.is_dir():
        result["errors"].append("path is not a directory")
        return result

    gradle_files: list[Path] = []
    settings_files: list[Path] = []
    manifests: list[Path] = []
    token_files: list[Path] = []
    has_current_ui = False
    has_target_ui = False
    max_depth = 12
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        try:
            depth = len(current_path.relative_to(root).parts)
        except ValueError:
            depth = 0
        if depth > max_depth:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        name = current_path.name.lower()
        if name in {"current_ui", "screenshots_of_current_ui", "current_screens"}:
            has_current_ui = True
        if name in {"target_ui", "target-ui", "reference_ui"}:
            has_target_ui = True
        for filename in files:
            path = current_path / filename
            if filename in {"build.gradle.kts", "build.gradle"}:
                gradle_files.append(path)
            elif filename in {"settings.gradle.kts", "settings.gradle"}:
                settings_files.append(path)
            elif filename == "AndroidManifest.xml":
                manifests.append(path)
            elif filename in {"DesignTokens.kt", "design_tokens.json", "Theme.kt", "Color.kt", "Type.kt"} or filename.endswith("DesignTokens.kt"):
                token_files.append(path)
    result["has_gradle"] = bool(gradle_files or settings_files)
    result["has_manifest"] = bool(manifests)
    if settings_files:
        result["score"] += 25
    if gradle_files:
        result["score"] += 25
    if manifests:
        result["score"] += 25

    text = "\n".join(_read(p) for p in gradle_files[:20])
    result["min_sdk"] = _first_match(text, r"minSdk\s*=\s*(\d+)")
    result["compile_sdk"] = _first_match(text, r"compileSdk\s*=\s*(\d+)")
    result["target_sdk"] = _first_match(text, r"targetSdk\s*=\s*(\d+)")
    result["namespace"] = _first_match(text, r"namespace\s*=\s*[\"']([^\"']+)[\"']")
    result["package"] = _first_match(text, r"applicationId\s*=\s*[\"']([^\"']+)[\"']") or result["namespace"]
    result["has_compose"] = "androidx.compose" in text or "compose" in text.lower()
    if result["has_compose"]:
        result["ui_stack"] = "Jetpack Compose"
        result["score"] += 15

    result["has_design_tokens"] = bool(token_files)
    if token_files:
        result["score"] += 10
        result["design_token_files"] = [str(p.relative_to(root)) for p in token_files[:10]]

    result["has_current_ui"] = has_current_ui
    result["has_target_ui"] = has_target_ui

    if not result["has_gradle"]:
        result["errors"].append("no Gradle files found")
    if not result["has_manifest"]:
        result["errors"].append("no AndroidManifest.xml found")
    if not result["has_compose"]:
        result["warnings"].append("no Compose dependency detected")
    if not result["min_sdk"]:
        result["warnings"].append("minSdk not found")
    if not result["has_design_tokens"]:
        result["warnings"].append("no design-token files detected")
    if not result["has_current_ui"]:
        result["warnings"].append("no current-UI screenshot folder detected")
    if not result["has_target_ui"]:
        result["warnings"].append("no Target_ui folder detected")

    result["valid"] = bool(result["has_gradle"] and result["has_manifest"])
    result["score"] = min(100, int(result["score"]))
    return result
