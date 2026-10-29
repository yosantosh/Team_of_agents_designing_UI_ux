from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import Settings


ROLE_ORDER = {"viewer": 0, "engineer": 1, "design_lead": 2, "dictator": 3}


def sha256_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class User:
    name: str
    role: str
    token_sha256: str


class AuthService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.users: list[User] = []
        self._load()

    def _load(self) -> None:
        users_file = self.settings.org_dir / "users.yaml"
        if users_file.exists():
            try:
                data = yaml.safe_load(users_file.read_text(encoding="utf-8")) or {}
                for item in data.get("users", []):
                    token_hash = str(item.get("token_sha256") or "")
                    if token_hash:
                        self.users.append(
                            User(
                                name=str(item.get("name", "User")),
                                role=str(item.get("role", "viewer")),
                                token_sha256=token_hash,
                            )
                        )
            except Exception:
                self.users = []
        env_token = getattr(self.settings, "auth_token", "") or ""
        if env_token and not self.users:
            self.users.append(
                User(
                    name="Local Dictator",
                    role=getattr(self.settings, "auth_role", "dictator"),
                    token_sha256=sha256_token(env_token),
                )
            )

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, "auth_enabled", False))

    def authenticate(self, token: str | None) -> User | None:
        if not self.enabled:
            return User(name="Local Dictator", role="dictator", token_sha256="")
        if not token:
            return None
        digest = sha256_token(token)
        for user in self.users:
            if user.token_sha256 == digest:
                return user
        return None

    def can_write(self, user: User | None, method: str, path: str) -> bool:
        if not self.enabled:
            return True
        if user is None:
            return False
        if method.upper() in {"GET", "HEAD", "OPTIONS"}:
            return True
        role = ROLE_ORDER.get(user.role, -1)
        if path.startswith("/api/admin/"):
            return user.role == "dictator"
        if path.startswith("/api/settings") or path.startswith("/api/projects/") and path.endswith("/activate"):
            return user.role == "dictator"
        if path.endswith("/decide") or path.endswith("/approve") or path.startswith("/api/evolve/"):
            return user.role == "dictator"
        if path.startswith("/api/approvals/") or path.startswith("/api/audit"):
            return user.role in {"dictator", "design_lead"}
        if path.startswith("/api/runs") and "/recheck-qa" in path:
            return role >= ROLE_ORDER["engineer"]
        if path.startswith("/api/runs") and "/agent-task" in path:
            return role >= ROLE_ORDER["engineer"]
        if path.startswith("/api/runs") and ("design-contract" in path or "recritique" in path):
            return role >= ROLE_ORDER["design_lead"]
        if path.startswith("/api/target-ui/") or path.startswith("/api/current-ui/"):
            return role >= ROLE_ORDER["design_lead"]
        if path.startswith("/api/runs"):
            return role >= ROLE_ORDER["engineer"]
        return role >= ROLE_ORDER["engineer"]
