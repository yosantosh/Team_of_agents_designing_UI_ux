from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .mock_data import mock_response
from .models import ModelResult, estimate_tokens, extract_json
from .prompts import build_user_prompt, system_prompt
from .registry import Registry
from .storage import Storage


class ModelProviderError(RuntimeError):
    def __init__(self, message: str, *, provider_status: int | None = None, detail: str | None = None):
        super().__init__(message)
        self.provider_status = provider_status
        self.detail = detail


class ModelFormatError(ValueError):
    pass


class BudgetExceeded(RuntimeError):
    pass


@dataclass(slots=True)
class ModelConfig:
    alias: str
    provider: str
    model: str
    input_cost: float
    output_cost: float


class ModelGateway:
    """Provider-agnostic model gateway.

    Default backend is OpenCode's CLI, using a project-local headless agent with all
    tools denied. This lets the company reuse the models already configured in
    OpenCode without copying provider keys into this project. A direct-provider
    backend can be added later behind the same interface.
    """

    def __init__(self, settings: Settings, registry: Registry, storage: Storage):
        self.settings = settings
        self.registry = registry
        self.storage = storage

    def _agent_spec(self, role: str) -> dict[str, Any]:
        for agent in self.registry.agents:
            if agent.get("id") == role:
                return agent
        return {"id": role, "model": "reasoner"}

    def _model_config(self, role: str, model_override: str | None = None) -> ModelConfig:
        agent = self._agent_spec(role)
        alias = str(agent.get("model", "reasoner"))
        budget = self.storage.budget_summary(self.settings.monthly_token_budget)
        if budget["used_pct"] >= self.settings.token_warn_pct and role not in {"design_critic", "org_evolution"}:
            alias = "cheap"
        spec = self.registry.model_alias(alias)
        if model_override:
            return ModelConfig(
                alias="override",
                provider="opencode",
                model=model_override,
                input_cost=float(spec.get("cost_per_million_input", 0.0)),
                output_cost=float(spec.get("cost_per_million_output", 0.0)),
            )
        return ModelConfig(
            alias=alias,
            provider=str(spec.get("provider", "opencode")),
            model=str(spec.get("model", self.settings.opencode_model)),
            input_cost=float(spec.get("cost_per_million_input", 0.0)),
            output_cost=float(spec.get("cost_per_million_output", 0.0)),
        )

    async def generate(
        self,
        role: str,
        goal: str,
        context: dict[str, Any],
        *,
        images: list[Path] | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        backend: str | None = None,
        model_override: str | None = None,
    ) -> ModelResult:
        budget = self.storage.budget_summary(self.settings.monthly_token_budget)
        if budget["used_pct"] >= self.settings.token_hard_stop_pct:
            raise BudgetExceeded(
                f"Monthly token budget exhausted: {budget['used_tokens']} / {budget['monthly_total_tokens']} tokens. "
                "Raise the budget or wait for the next cycle."
            )

        config = self._model_config(role, model_override)
        agent_spec = self._agent_spec(role)
        prompt_key = str(agent_spec.get("prompt_key", role))
        prompt = build_user_prompt(
            prompt_key,
            goal,
            context,
            max_chars=self.settings.max_context_chars,
        )
        full_prompt = system_prompt(prompt_key) + "\n\n" + prompt
        images = images or []

        backend = (backend or self.settings.model_backend).strip().lower()
        if backend == "mock":
            result = await self._mock(config, role, prompt_key, context, full_prompt, images, run_id, task_id)
        elif backend == "opencode":
            result = await self._opencode(config, role, full_prompt, images, run_id, task_id)
        else:
            raise ModelProviderError(f"unknown model backend: {backend}")

        self.storage.record_usage(
            run_id=run_id,
            task_id=task_id,
            agent_id=role,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cached_tokens=result.cached_tokens,
            reasoning_tokens=result.reasoning_tokens,
            cost_usd=result.cost_usd,
        )
        return result

    async def _mock(
        self,
        config: ModelConfig,
        role: str,
        prompt_key: str,
        context: dict[str, Any],
        prompt: str,
        images: list[Path],
        run_id: str | None,
        task_id: str | None,
    ) -> ModelResult:
        started = time.monotonic()
        data = mock_response(prompt_key, context)
        text = json.dumps(data, ensure_ascii=False, indent=2)
        usage_in = estimate_tokens(prompt, len(images))
        usage_out = estimate_tokens(text)
        cost = (usage_in / 1_000_000) * config.input_cost + (usage_out / 1_000_000) * config.output_cost
        return ModelResult(
            text=text,
            data=data,
            model=f"mock/{role}",
            role=role,
            session_id=None,
            input_tokens=usage_in,
            output_tokens=usage_out,
            cost_usd=round(cost, 6),
            duration_ms=int((time.monotonic() - started) * 1000),
            raw_events=[],
            estimated=True,
        )

    async def _opencode(
        self,
        config: ModelConfig,
        role: str,
        prompt: str,
        images: list[Path],
        run_id: str | None,
        task_id: str | None,
    ) -> ModelResult:
        attempts = 2
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            cmd = [
                self.settings.opencode_bin,
                "run",
                "--agent",
                self.settings.opencode_agent,
                "--format",
                "json",
                "--model",
                config.model,
                "--title",
                f"uiux-{role}-{(run_id or 'run')[:40]}",
            ]
            for image in images:
                cmd.extend(["--file", str(image.resolve())])
            cmd.append(prompt)

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self.settings.opencode_cwd),
                )
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(
                        proc.communicate(), timeout=self.settings.opencode_timeout
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    raise ModelProviderError(
                        f"OpenCode worker timed out after {self.settings.opencode_timeout}s"
                    )
                except asyncio.CancelledError:
                    proc.kill()
                    await proc.wait()
                    raise
            except FileNotFoundError as exc:
                raise ModelProviderError(
                    f"OpenCode binary not found: {self.settings.opencode_bin}. "
                    "Install OpenCode or set UIUX_OPENCODE_BIN."
                ) from exc

            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")
            events: list[dict[str, Any]] = []
            texts: list[str] = []
            session_id: str | None = None
            provider_error: dict[str, Any] | None = None

            for line in stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                events.append(event)
                session_id = event.get("sessionID") or session_id
                if event.get("type") == "text":
                    part = event.get("part") or {}
                    if isinstance(part, dict) and part.get("text"):
                        texts.append(str(part["text"]))
                elif event.get("type") == "error":
                    provider_error = event.get("error") if isinstance(event.get("error"), dict) else {"message": str(event.get("error"))}

            if provider_error:
                message = str(provider_error.get("message") or provider_error)
                raise ModelProviderError(
                    f"OpenCode model error for {config.model}: {message}",
                    provider_status=provider_error.get("status") if isinstance(provider_error.get("status"), int) else None,
                    detail=json.dumps(provider_error, ensure_ascii=False)[:2000],
                )

            text = "\n".join(texts).strip()
            if not text:
                if proc.returncode != 0:
                    raise ModelProviderError(
                        f"OpenCode worker failed with exit code {proc.returncode}: {stderr[:1000]}"
                    )
                raise ModelFormatError(
                    f"OpenCode worker returned no text for {role}. stderr: {stderr[:1000]}"
                )

            try:
                data = extract_json(text)
            except Exception as exc:
                last_error = ModelFormatError(
                    f"Model returned non-JSON for {role}: {text[:500]}"
                )
                if attempt < attempts:
                    await asyncio.sleep(1.0)
                    continue
                raise last_error from exc

            usage_in = estimate_tokens(prompt, len(images))
            usage_out = estimate_tokens(text)
            cost = (usage_in / 1_000_000) * config.input_cost + (usage_out / 1_000_000) * config.output_cost
            return ModelResult(
                text=text,
                data=data,
                model=config.model,
                role=role,
                session_id=session_id,
                input_tokens=usage_in,
                output_tokens=usage_out,
                cost_usd=round(cost, 6),
                duration_ms=int((time.monotonic() - started) * 1000),
                raw_events=events,
                estimated=True,
            )

        raise last_error or ModelProviderError(f"model failed for {role}")

    async def test_model(self, model: str) -> dict[str, Any]:
        """Small live probe: ask the selected model who it is."""
        started = time.monotonic()
        cmd = [
            self.settings.opencode_bin,
            "run",
            "--agent",
            self.settings.opencode_agent,
            "--format",
            "json",
            "--model",
            model,
            "Hey which model you are? Reply in one short sentence.",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.settings.opencode_cwd),
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=min(120, self.settings.opencode_timeout)
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {"ok": False, "model": model, "error": "model test timed out"}
        except FileNotFoundError:
            return {"ok": False, "model": model, "error": "opencode binary not found"}
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        texts = []
        session_id = None
        error = None
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            session_id = event.get("sessionID") or session_id
            if event.get("type") == "text":
                part = event.get("part") or {}
                if isinstance(part, dict) and part.get("text"):
                    texts.append(str(part["text"]))
            if event.get("type") == "error":
                error = event.get("error")
        text = "\n".join(texts).strip()
        if error and not text:
            return {"ok": False, "model": model, "error": str(error), "stderr": stderr[-1000:]}
        return {
            "ok": bool(text),
            "model": model,
            "text": text or "No text returned.",
            "session_id": session_id,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "stderr": stderr[-1000:] if not text else "",
        }

    def list_models(self) -> dict[str, Any]:
        return {
            "backend": self.settings.model_backend,
            "default_model": self.settings.model_override or self.settings.opencode_model,
            "aliases": self.registry.models,
            "note": "Use `opencode models` to verify availability. The gateway reuses OpenCode's provider configuration.",
        }
