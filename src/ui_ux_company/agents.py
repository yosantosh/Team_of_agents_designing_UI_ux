from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .gateway import ModelGateway
from .langfuse_trace import LangfuseTracer
from .models import AgentResult
from .registry import Registry


class AgentRunner:
    def __init__(
        self,
        gateway: ModelGateway,
        registry: Registry,
        *,
        tracer: LangfuseTracer | None = None,
    ):
        self.gateway = gateway
        self.registry = registry
        self.tracer = tracer

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
        model: str | None = None,
    ) -> AgentResult:
        span_input = {
            "goal": goal,
            "context_keys": sorted(context.keys()),
            "images": len(images or []),
        }
        if self.tracer is None:
            result = await self.gateway.generate(
                role,
                goal,
                context,
                images=images,
                run_id=run_id,
                task_id=task_id,
                backend=backend,
                model_override=model,
            )
        else:
            with self.tracer.span(
                f"agent:{role}",
                as_type="agent",
                input=span_input,
                metadata={"role": role, "run_id": run_id, "task_id": task_id},
                model=model,
            ) as span:
                result = await self.gateway.generate(
                    role,
                    goal,
                    context,
                    images=images,
                    run_id=run_id,
                    task_id=task_id,
                    backend=backend,
                    model_override=model,
                )
                usage_details = {
                    "input": int(result.input_tokens or 0),
                    "output": int(result.output_tokens or 0),
                }
                total = int(result.input_tokens or 0) + int(result.output_tokens or 0)
                if total:
                    usage_details["total"] = total
                cost_details = {"total": float(result.cost_usd)} if result.cost_usd else None
                self.tracer.update_span(
                    span,
                    output={
                        "text": (result.text or "")[:4000],
                        "data_keys": sorted((result.data or {}).keys()),
                    },
                    usage_details=usage_details,
                    cost_details=cost_details,
                    metadata={
                        "model": result.model,
                        "duration_ms": result.duration_ms,
                        "estimated": result.estimated,
                        "cached_tokens": result.cached_tokens,
                        "reasoning_tokens": result.reasoning_tokens,
                    },
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
        model: str | None = None,
    ) -> list[AgentResult]:
        tasks = [
            self.run(role, goal, context, images=images, run_id=run_id, task_id=role, backend=backend, model=model)
            for role, goal, context, images in jobs
        ]
        return list(await asyncio.gather(*tasks))
