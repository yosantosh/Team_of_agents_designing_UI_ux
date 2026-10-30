from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

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


FILENAME_SCREEN_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("feed", "home", "timeline", "homescreen"), "feed"),
    (("profile", "account", "avatar", "followers", "following"), "profile"),
    (("message", "messages", "chat", "inbox", "conversation"), "messages"),
    (("music", "player", "queue", "nowplaying"), "player"),
    (("initial", "splash", "orbit", "onboarding", "welcome"), "splash"),
    (("notification", "notfication", "bell", "activity"), "notifications"),
    (("create", "compose", "upload", "post"), "create"),
    (("explore", "discover", "search"), "explore"),
    (("login", "signin", "signup", "auth"), "login"),
    (("room", "space"), "room"),
)


def guess_screen(filename: str) -> str:
    """Infer a screen only from the reference filename.

    The filename is intentionally the strongest contract signal. Vision
    analysis is optional and must never be required to identify a screen.
    """
    stem = Path(filename).stem.lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
    compact = normalized.replace("_", "")
    best: tuple[int, int, str] | None = None
    for order, (keys, screen) in enumerate(FILENAME_SCREEN_PATTERNS):
        for key in keys:
            position = compact.find(key)
            if position < 0:
                continue
            candidate = (position, order, screen)
            if best is None or candidate < best:
                best = candidate
    if best is not None:
        return best[2]
    return normalized or "unknown"


FILENAME_STATE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("permission", "permission_denied"),
    ("offline", "offline"),
    ("empty", "empty"),
    ("error", "error"),
    ("loading", "loading"),
    ("edit", "edit"),
    ("long_text", "long_text"),
    ("big_size", "large"),
    ("only_in_big", "large"),
    ("remove_volume", "simplified"),
)


def guess_state(filename: str) -> str:
    stem = Path(filename).stem.lower().replace("_", " ")
    for key, state in FILENAME_STATE_PATTERNS:
        if key.replace("_", " ") in stem or key in stem:
            return state
    return "default"


def filename_tokens(filename: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", Path(filename).stem.lower())
        if token and token not in {"the", "and", "for", "with", "should", "be", "replace", "remove"}
    }


def _resolve_manifest_path(settings: Settings, raw: str | None, default: str) -> Path:
    path = Path(str(raw or default)).expanduser()
    if path.is_absolute():
        return path
    raw_text = str(raw or default).replace("\\", "/")
    if raw_text.startswith("artifacts/"):
        return settings.artifacts_dir / raw_text[len("artifacts/"):]
    return settings.root / path


def _manifest_path(settings: Settings, profile: ProjectProfile) -> Path:
    return _resolve_manifest_path(
        settings,
        profile.target_ui.get("manifest"),
        "artifacts/target_ui/manifest.json",
    )


def _current_manifest_path(settings: Settings, profile: ProjectProfile) -> Path:
    return _resolve_manifest_path(
        settings,
        profile.data.get("current_ui", {}).get("manifest"),
        "artifacts/current_ui/manifest.json",
    )


def discover_current_ui_path(settings: Settings, profile: ProjectProfile) -> Path | None:
    config = profile.data.get("current_ui", {})
    candidates = [str(settings.current_ui_path)]
    if config.get("path"):
        candidates.append(str(config["path"]))
    candidates.extend(str(p) for p in config.get("candidate_paths", []))
    for raw in candidates:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = settings.root / path
        if path.exists() and path.is_dir():
            images = [p for p in path.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS]
            if images:
                return path
    return None


def scan_current_ui(
    settings: Settings,
    profile: ProjectProfile,
    *,
    path: Path | None = None,
) -> TargetManifest:
    target_path = path or discover_current_ui_path(settings, profile)
    manifest = TargetManifest(
        source_path=str(target_path) if target_path else None,
        precedence="baseline",
        confidence_threshold=1.0,
        status="not_available",
    )
    if not target_path or not target_path.exists():
        manifest.notes.append("No current-UI screenshots found; visual regression needs a baseline.")
        _write_current_manifest(settings, profile, manifest)
        return manifest
    images = sorted(p for p in target_path.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)
    if not images:
        manifest.notes.append("Current-UI folder exists but contains no supported images.")
        _write_current_manifest(settings, profile, manifest)
        return manifest
    for image_path in images[:16]:
        width = height = None
        try:
            with Image.open(image_path) as img:
                width, height = img.size
        except Exception:
            pass
        manifest.images.append(
            TargetImage(
                image_id=f"current_{len(manifest.images) + 1:02d}",
                file=str(image_path),
                filename=image_path.name,
                sha256=_hash_file(image_path),
                width=width,
                height=height,
                screen=guess_screen(image_path.name),
                screen_hint=guess_screen(image_path.name),
                state_hint=guess_state(image_path.name),
                status="analyzed",
                confidence=1.0,
                analysis_is_advisory=True,
                rights="internal_baseline",
            )
        )
    manifest.status = "scanned"
    manifest.notes.append("Current UI baseline captured. Used for audit and visual regression, not as a design target.")
    _write_current_manifest(settings, profile, manifest)
    return manifest


def _write_current_manifest(settings: Settings, profile: ProjectProfile, manifest: TargetManifest) -> None:
    path = _current_manifest_path(settings, profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")


def load_current_manifest(settings: Settings, profile: ProjectProfile) -> TargetManifest:
    path = _current_manifest_path(settings, profile)
    if not path.exists():
        return TargetManifest(status="not_available")
    return TargetManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))


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
    try:
        previous = load_manifest(settings, profile)
    except Exception:
        previous = TargetManifest(status="not_available")
    previous_annotations = {
        Path(image.file).name: list(image.annotations or [])
        for image in previous.images
        if image.file
    }
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
        annotations = [
            {**annotation, "file": str(image_path), "filename": image_path.name}
            for annotation in previous_annotations.get(image_path.name, [])
        ]
        image = TargetImage(
            image_id=f"target_{len(manifest.images) + 1:02d}",
            file=str(image_path),
            filename=image_path.name,
            sha256=_hash_file(image_path),
            width=width,
            height=height,
            screen=guess_screen(image_path.name),
            screen_hint=guess_screen(image_path.name),
            state_hint=guess_state(image_path.name),
            status="unanalyzed",
            confidence=0.0,
            analysis_is_advisory=True,
            annotations=annotations,
            rights=str(profile.target_ui.get("rights_policy", "reference_only")),
        )
        manifest.images.append(image)

    # scan_target_ui is sync for CLI/API simplicity. analyze_manifest() performs
    # the async vision calls when the caller wants model extraction.
    manifest.status = "scanned"
    manifest.notes.append(
        "Images were hashed and mapped by filename. Filenames are authoritative for screen identity; vision extraction is optional and advisory."
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
    model: str | None = None,
    on_result: Callable[[str, Any, Any], None] | None = None,
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
                        "annotations": image.annotations,
                    },
                    "policy": "Layout and hierarchy reference only; do not copy brand assets. Use human annotations as authoritative region/component labels.",
                },
                images=[Path(image.file)],
                run_id=run_id,
                task_id=f"target_ui:{image.image_id}",
                backend=backend,
                model=model,
            )
            if on_result:
                try:
                    on_result("target_ui_analyst", result, image)
                except Exception:
                    pass
            data = result.data
            image.extracted = data
            confidence = data.get("confidence")
            image.confidence = float(confidence) if isinstance(confidence, (int, float)) else 0.0
            image.analysis_is_advisory = True
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
    manifest.notes = [note for note in manifest.notes if "analyze_manifest" not in note]
    manifest.notes.append("Vision analysis complete. Confidence and conflicts are recorded per image.")
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
