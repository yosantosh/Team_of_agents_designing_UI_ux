from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat

from .agents import AgentRunner
from .artifacts import ArtifactStore
from .config import Settings
from .models import TargetImage, TargetManifest, utcnow
from .registry import ProjectProfile, discover_target_ui_path


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def guess_screen(filename: str) -> str:
    stem = Path(filename).stem.lower()
    patterns = {
        "profile": "profile",
        "home": "feed",
        "feed": "feed",
        "explore": "explore",
        "search": "explore",
        "notification": "notifications",
        "activity": "notifications",
        "message": "messages",
        "chat": "messages",
        "upload": "create",
        "create": "create",
        "login": "login",
        "splash": "splash",
        "player": "player",
        "room": "room",
        "queue": "player_queue",
    }
    for key, screen in patterns.items():
        if key in stem:
            return screen
    cleaned = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
    return cleaned or "unknown"


def _manifest_path(settings: Settings, profile: ProjectProfile) -> Path:
    raw = profile.target_ui.get("manifest", "artifacts/target_ui/manifest.json")
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = settings.root / path
    return path


def scan_target_ui(
    settings: Settings,
    profile: ProjectProfile,
    *,
    path: Path | None = None,
    analyze: bool = False,
    agent_runner: AgentRunner | None = None,
    run_id: str | None = None,
    artifact_store: ArtifactStore | None = None,
) -> TargetManifest:
    target_path = path or discover_target_ui_path(settings, profile)
    manifest = TargetManifest(
        source_path=str(target_path) if target_path else None,
        precedence=str(profile.target_ui.get("precedence", "tiered")),
        confidence_threshold=float(profile.target_ui.get("confidence_threshold", 0.8)),
        status="not_available",
    )
    if not target_path or not target_path.exists():
        manifest.notes.append("No Target_ui images found; using the no-target design path.")
        _write_manifest(settings, profile, manifest, run_id, artifact_store)
        return manifest

    images = sorted(
        p for p in target_path.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )
    if not images:
        manifest.notes.append("Target_ui folder exists but contains no supported images.")
        _write_manifest(settings, profile, manifest, run_id, artifact_store)
        return manifest

    max_images = int(profile.target_ui.get("max_images_per_run", 8))
    for image_path in images[:max_images]:
        width = height = None
        try:
            with Image.open(image_path) as img:
                width, height = img.size
        except Exception:
            pass
        image = TargetImage(
            image_id=f"target_{len(manifest.images) + 1:02d}",
            file=str(image_path),
            sha256=_hash_file(image_path),
            width=width,
            height=height,
            screen=guess_screen(image_path.name),
            status="unanalyzed",
            confidence=0.0,
            rights=str(profile.target_ui.get("rights_policy", "reference_only")),
        )
        manifest.images.append(image)

    # scan_target_ui is sync for CLI/API simplicity. analyze_manifest() performs
    # the async vision calls when the caller wants model extraction.
    manifest.status = "scanned"
    manifest.notes.append(
        "Images were hashed and mapped by filename. Run analyze_manifest() for vision extraction."
    )
    _write_manifest(settings, profile, manifest, run_id, artifact_store)
    return manifest


async def analyze_manifest(
    manifest: TargetManifest,
    *,
    agent_runner: AgentRunner,
    run_id: str | None = None,
    max_images: int | None = None,
    backend: str | None = None,
) -> TargetManifest:
    if not manifest.images:
        manifest.status = "not_available"
        return manifest
    limit = max_images or len(manifest.images)
    for image in manifest.images[:limit]:
        try:
            result = await agent_runner.run(
                "target_ui_analyst",
                f"Analyze the attached Target_ui reference for screen '{image.screen}'.",
                {
                    "image": {
                        "file": image.file,
                        "width": image.width,
                        "height": image.height,
                        "screen_guess": image.screen,
                        "precedence": manifest.precedence,
                    },
                    "policy": "Layout and hierarchy reference only; do not copy brand assets.",
                },
                images=[Path(image.file)],
                run_id=run_id,
                task_id=f"target_ui:{image.image_id}",
                backend=backend,
            )
            data = result.data
            image.extracted = data
            confidence = data.get("confidence")
            image.confidence = float(confidence) if isinstance(confidence, (int, float)) else 0.0
            if data.get("screen") and image.screen == "unknown":
                image.screen = str(data["screen"])
            image.conflicts = [str(c) for c in data.get("conflicts", [])]
            image.status = (
                "analyzed"
                if image.confidence >= manifest.confidence_threshold
                else "low_confidence"
            )
            if image.conflicts:
                image.status = "conflict"
        except Exception as exc:
            image.status = "low_confidence"
            image.conflicts.append(f"analysis_failed: {exc}")
    manifest.status = "analyzed"
    return manifest


def _write_manifest(
    settings: Settings,
    profile: ProjectProfile,
    manifest: TargetManifest,
    run_id: str | None,
    artifact_store: ArtifactStore | None,
) -> None:
    path = _manifest_path(settings, profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = manifest.model_dump()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if run_id and artifact_store:
        target_dir = artifact_store.target_images_dir(run_id)
        for image in manifest.images:
            source = Path(image.file)
            if source.exists():
                dest = target_dir / source.name
                if not dest.exists():
                    dest.write_bytes(source.read_bytes())


def save_manifest(settings: Settings, profile: ProjectProfile, manifest: TargetManifest) -> Path:
    path = _manifest_path(settings, profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_manifest(settings: Settings, profile: ProjectProfile) -> TargetManifest:
    path = _manifest_path(settings, profile)
    if not path.exists():
        return TargetManifest(status="not_available")
    data = json.loads(path.read_text(encoding="utf-8"))
    return TargetManifest.model_validate(data)


def visual_diff(target: Path, actual: Path) -> dict[str, Any]:
    with Image.open(target) as target_img, Image.open(actual) as actual_img:
        target_rgb = target_img.convert("RGB")
        actual_rgb = actual_img.convert("RGB")
        if target_rgb.size != actual_rgb.size:
            target_rgb = target_rgb.resize(actual_rgb.size)
        diff = ImageChops.difference(target_rgb, actual_rgb)
        stat = ImageStat.Stat(diff)
        mean_diff = sum(stat.mean) / 3.0
        score = max(0.0, 1.0 - (mean_diff / 255.0))
        histogram = diff.convert("L").histogram()
        total_pixels = max(1, sum(histogram))
        over = sum(count for value, count in enumerate(histogram) if value > 24)
        return {
            "score": round(score, 4),
            "mean_abs_diff": round(mean_diff, 3),
            "pct_pixels_over_24": round((over / total_pixels) * 100, 2),
            "target_size": list(target_img.size),
            "actual_size": list(actual_img.size),
        }


def screenshots_for_run(artifact_store: ArtifactStore, run_id: str) -> list[Path]:
    directory = artifact_store.screenshots_dir(run_id)
    return sorted(p for p in directory.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS)


def match_screenshots(
    manifest: TargetManifest, screenshots: list[Path]
) -> list[tuple[TargetImage | None, Path]]:
    matches: list[tuple[TargetImage | None, Path]] = []
    used: set[str] = set()
    for shot in screenshots:
        screen = guess_screen(shot.name)
        matched = None
        for image in manifest.images:
            if image.image_id not in used and (image.screen == screen or screen in image.file):
                matched = image
                used.add(image.image_id)
                break
        matches.append((matched, shot))
    return matches
