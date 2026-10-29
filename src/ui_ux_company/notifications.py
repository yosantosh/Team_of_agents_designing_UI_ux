from __future__ import annotations

from typing import Any

import httpx

from .config import Settings


class Notifier:
    """Best-effort webhook notifier for decisions that need a human."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def notify(self, title: str, message: str, data: dict[str, Any] | None = None) -> bool:
        url = getattr(self.settings, "webhook_url", "") or ""
        if not url:
            return False
        payload = {"title": title, "message": message, "data": data or {}}
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.post(url, json=payload)
                return response.status_code < 400
        except Exception:
            return False
