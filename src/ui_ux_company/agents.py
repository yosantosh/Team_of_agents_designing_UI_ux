from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .gateway import ModelGateway
from .models import AgentResult
from .registry import Registry


class AgentRunner:
    def __init__(self, gateway: ModelGateway, registry: Registry):
        self.gateway = gateway
        self.registry = registry

    def title(self, role: str) -> str:
        try:
            return str(self.registry.agent(role).get("title", role))
        except KeyError:
            return role

    async def run(
        self,
        role: str,
        goal: str,
        context: dict[str, Any],
        *,
        images: list[Path] | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        backend: str | None = None,
    ) -> AgentResult:
        result = await self.gateway.generate(
            role,
            goal,
            context,
            images=images,
            run_id=run_id,
            task_id=task_id,
            backend=backend,
        )
        return AgentResult(
            role=role,
            data=result.data or {"raw_text": result.text},
            text=result.text,
            model=result.model,
            usage={
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cached_tokens": result.cached_tokens,
                "reasoning_tokens": result.reasoning_tokens,
                "cost_usd": result.cost_usd,
                "duration_ms": result.duration_ms,
                "estimated": result.estimated,
            },
        )

    async def run_many(
        self,
        jobs: list[tuple[str, str, dict[str, Any], list[Path] | None]],
        *,
        run_id: str | None = None,
        backend: str | None = None,
    ) -> list[AgentResult]:
        tasks = [
            self.run(role, goal, context, images=images, run_id=run_id, task_id=role, backend=backend)
            for role, goal, context, images in jobs
        ]
        return list(await asyncio.gather(*tasks))
