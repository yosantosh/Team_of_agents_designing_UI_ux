from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from .config import Settings
from .models import new_id, sha256_text, utcnow
from .storage import Storage


class ArtifactStore:
    def __init__(self, settings: Settings, storage: Storage):
        self.settings = settings
        self.storage = storage

    def run_dir(self, run_id: str) -> Path:
        return self.settings.artifact_dir(run_id)

    def _maybe_upload_s3(self, run_id: str, path: Path) -> str | None:
        if not getattr(self.settings, "s3_bucket", ""):
            return None
        try:
            import boto3

            client = boto3.client(
                "s3",
                endpoint_url=self.settings.s3_endpoint or None,
                aws_access_key_id=self.settings.s3_access_key or None,
                aws_secret_access_key=self.settings.s3_secret_key or None,
                region_name=self.settings.s3_region,
            )
            key = f"runs/{run_id}/{path.name}"
            client.upload_file(str(path), self.settings.s3_bucket, key)
            return f"s3://{self.settings.s3_bucket}/{key}"
        except Exception:
            return None

    def _safe_name(self, name: str) -> str:
        name = name.replace("\\", "/").split("/")[-1]
        safe = "".join(ch for ch in name if ch.isalnum() or ch in "._-")
        if not safe:
            safe = "artifact"
        return safe

    def write_json(
        self,
        run_id: str,
        name: str,
        data: Any,
        created_by: str = "system",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe = self._safe_name(name)
        if not safe.endswith(".json"):
            safe += ".json"
        payload = json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n"
        return self.write_text(run_id, safe, payload, "application/json", created_by, metadata)

    def write_text(
        self,
        run_id: str,
        name: str,
        text: str,
        content_type: str = "text/plain",
        created_by: str = "system",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe = self._safe_name(name)
        path = self.run_dir(run_id) / safe
        path.write_text(text, encoding="utf-8")
        s3_uri = self._maybe_upload_s3(run_id, path)
        metadata = dict(metadata or {})
        if s3_uri:
            metadata["s3_uri"] = s3_uri
        return self.storage.add_artifact(
            run_id=run_id,
            name=safe,
            path=str(path),
            content_type=content_type,
            sha256=sha256_text(text),
            created_by=created_by,
            metadata=metadata or {},
        )

    def write_bytes(
        self,
        run_id: str,
        name: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        created_by: str = "system",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe = self._safe_name(name)
        path = self.run_dir(run_id) / safe
        path.write_bytes(data)
        s3_uri = self._maybe_upload_s3(run_id, path)
        metadata = dict(metadata or {})
        if s3_uri:
            metadata["s3_uri"] = s3_uri
        return self.storage.add_artifact(
            run_id=run_id,
            name=safe,
            path=str(path),
            content_type=content_type,
            sha256=hashlib.sha256(data).hexdigest(),
            created_by=created_by,
            metadata=metadata or {},
        )

    def copy_file(
        self,
        run_id: str,
        source: Path,
        name: str | None = None,
        created_by: str = "system",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe = self._safe_name(name or source.name)
        target = self.run_dir(run_id) / safe
        shutil.copy2(source, target)
        content_type = "image/png" if target.suffix.lower() == ".png" else "application/octet-stream"
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        s3_uri = self._maybe_upload_s3(run_id, target)
        metadata = dict(metadata or {})
        if s3_uri:
            metadata["s3_uri"] = s3_uri
        return self.storage.add_artifact(
            run_id=run_id,
            name=safe,
            path=str(target),
            content_type=content_type,
            sha256=digest,
            created_by=created_by,
            metadata=metadata or {},
        )

    def path(self, run_id: str, name: str) -> Path:
        return self.run_dir(run_id) / self._safe_name(name)

    def list(self, run_id: str) -> list[dict[str, Any]]:
        return self.storage.list_artifacts(run_id)

    def screenshots_dir(self, run_id: str) -> Path:
        path = self.run_dir(run_id) / "screenshots"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def target_images_dir(self, run_id: str) -> Path:
        path = self.run_dir(run_id) / "target_images"
        path.mkdir(parents=True, exist_ok=True)
        return path


def file_response_headers(path: Path) -> dict[str, str]:
    return {
        "Content-Disposition": f'inline; filename="{path.name}"',
        "Cache-Control": "no-store",
        "X-Artifact-Mtime": utcnow(),
    }
