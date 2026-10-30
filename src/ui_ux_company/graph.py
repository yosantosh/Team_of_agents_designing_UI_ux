from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .agents import AgentRunner
from .artifacts import ArtifactStore
from .codeworker import CodeWorker
from .config import Settings
from .events import EventBus
from .gateway import ModelGateway
from .intent import build_intent_packet, select_target_images
from .models import new_id, utcnow
from .prompts import work_label
from .registry import BudgetPolicy, ProjectProfile, Registry, load_budget, load_profile, load_registry
from .storage import Storage
from .target_ui import (
    analyze_manifest,
    load_current_manifest,
    load_manifest,
    match_screenshots,
    save_manifest,
    scan_target_ui,
    screenshots_for_run,
    visual_diff,
)


class CompanyState(TypedDict, total=False):
    run_id: str
    project_id: str
    prompt: str
    mode: str
    backend: str
    model: str | None
    demo: bool
    auto_approve: bool
    autonomy_level: str
    target_ui_enabled: bool
    target_ui_path: str
    change_request: dict[str, Any]
    ceo_directive: dict[str, Any]
    target_manifest: dict[str, Any]
    research: dict[str, Any]
    synthesis: dict[str, Any]
    design_tokens: dict[str, Any]
    screen_spec: dict[str, Any]
    design_contract: dict[str, Any]
    critique: dict[str, Any]
    critique_gate: str
    tier_reviews: dict[str, Any]
    design_approved: bool
    human_decision: dict[str, Any]
    implementation: dict[str, Any]
    qa: dict[str, Any]
    qa_gate: str
    evaluation: dict[str, Any]
    eval_gate: str
    release: dict[str, Any]
    learnings: dict[str, Any]
    rework_counts: dict[str, int]
    errors: list[str]
    status: str
    approval_id: str | None





class CompanyGraph:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        registry: Registry | None = None,
        profile: ProjectProfile | None = None,
        budget: BudgetPolicy | None = None,
        storage: Storage | None = None,
        bus: EventBus | None = None,
        artifacts: ArtifactStore | None = None,
        gateway: ModelGateway | None = None,
        agents: AgentRunner | None = None,
        code_worker: CodeWorker | None = None,
    ):
        self.settings = settings or Settings.from_env()
        self.registry = registry or load_registry(self.settings)
        self.profile = profile or load_profile(self.settings)
        if self.settings.project_dir:
            self.profile.data.setdefault("project", {})["repo"] = str(self.settings.project_dir)
        self.budget = budget or load_budget(self.settings)
        self.storage = storage or Storage(self.settings.storage_db)
        self.bus = bus or EventBus(self.storage)
        self.artifacts = artifacts or ArtifactStore(self.settings, self.storage)
        self.gateway = gateway or ModelGateway(self.settings, self.registry, self.storage)
        self.agents = agents or AgentRunner(self.gateway, self.registry)
        self.code_worker = code_worker or CodeWorker(self.settings, self.artifacts)
        self.checkpointer = None
        self._checkpointer_cm = None
        self.graph = None
        self._init_lock: asyncio.Lock | None = None

    async def _ensure_graph(self) -> None:
        if self.graph is not None:
            return
        if self._init_lock is None:
            self._init_lock = asyncio.Lock()
        async with self._init_lock:
            if self.graph is not None:
                return
            if self.settings.durable_checkpoints and self.settings.checkpoint_backend == "postgres" and self.settings.postgres_dsn:
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

                self._checkpointer_cm = AsyncPostgresSaver.from_conn_string(self.settings.postgres_dsn)
                self.checkpointer = await self._checkpointer_cm.__aenter__()
            elif self.settings.durable_checkpoints:
                self._checkpointer_cm = AsyncSqliteSaver.from_conn_string(str(self.settings.checkpoint_db))
                self.checkpointer = await self._checkpointer_cm.__aenter__()
            else:
                self.checkpointer = MemorySaver()
            self.graph = self._build()
            self._init_lock = None

    # ------------------------------------------------------------------ graph
    def _build(self):
        builder = StateGraph(CompanyState)
        builder.add_node("intake", self._intake)
        builder.add_node("ceo_review", self._ceo_review)
        builder.add_node("target_ui", self._target_ui)
        builder.add_node("discovery", self._discovery)
        builder.add_node("synthesis", self._synthesis)
        builder.add_node("discovery_lead_review", self._discovery_lead_review)
        builder.add_node("design_system", self._design_system)
        builder.add_node("visual_design", self._visual_design)
        builder.add_node("design_contract", self._design_contract)
        builder.add_node("design_lead_review", self._design_lead_review)
        builder.add_node("critique", self._critique)
        builder.add_node("human_gate", self._human_gate)
        builder.add_node("engineering_lead_review", self._engineering_lead_review)
        builder.add_node("implement", self._implement)
        builder.add_node("qa", self._qa)
        builder.add_node("quality_lead_review", self._quality_lead_review)
        builder.add_node("manager_eval", self._manager_eval)
        builder.add_node("release", self._release)
        builder.add_node("learn", self._learn)
        builder.add_node("escalate", self._escalate)

        builder.add_edge(START, "intake")
        builder.add_edge("intake", "ceo_review")
        builder.add_edge("ceo_review", "target_ui")
        builder.add_edge("target_ui", "discovery")
        builder.add_edge("discovery", "synthesis")
        builder.add_edge("synthesis", "discovery_lead_review")
        builder.add_edge("discovery_lead_review", "design_system")
        builder.add_edge("design_system", "visual_design")
        builder.add_edge("visual_design", "design_contract")
        builder.add_edge("design_contract", "design_lead_review")
        builder.add_edge("design_lead_review", "critique")
        builder.add_conditional_edges(
            "critique",
            self._route_after_critique,
            {"human_gate": "human_gate", "visual_design": "visual_design", "escalate": "escalate"},
        )
        builder.add_conditional_edges(
            "human_gate",
            self._route_after_human,
            {"implement": "engineering_lead_review", "escalate": "escalate"},
        )
        builder.add_edge("engineering_lead_review", "implement")
        builder.add_conditional_edges(
            "implement",
            self._route_after_implement,
            {"qa": "qa", "escalate": "escalate"},
        )
        builder.add_edge("qa", "quality_lead_review")
        builder.add_edge("quality_lead_review", "manager_eval")
        builder.add_conditional_edges(
            "manager_eval",
            self._route_after_manager,
            {"release": "release", "implement": "implement", "escalate": "escalate"},
        )
        builder.add_edge("release", "learn")
        builder.add_edge("learn", END)
        builder.add_edge("escalate", END)
        return builder.compile(checkpointer=self.checkpointer)

    def _route_after_critique(self, state: CompanyState) -> str:
        gate = state.get("critique_gate", "escalate")
        if gate == "pass":
            return "human_gate"
        counts = state.get("rework_counts", {})
        if gate == "needs_rework" and int(counts.get("design", 0)) <= self.settings.max_rework:
            return "visual_design"
        return "escalate"

    def _route_after_human(self, state: CompanyState) -> str:
        return "implement" if state.get("design_approved") else "escalate"

    def _route_after_implement(self, state: CompanyState) -> str:
        implementation = state.get("implementation") or {}
        code_edits = implementation.get("code_edits")
        if code_edits and code_edits.get("status") not in {None, "edited"}:
            return "escalate"
        return "qa"

    def _route_after_manager(self, state: CompanyState) -> str:
        gate = state.get("eval_gate", "escalate")
        if gate == "pass":
            return "release"
        counts = state.get("rework_counts", {})
        if gate == "needs_rework" and int(counts.get("implementation", 0)) <= self.settings.max_rework:
            return "implement"
        return "escalate"

    # --------------------------------------------------------------- helpers
    def _gate_sla(self, gate_id: str, default: int = 1440) -> int:
        for gate in self.registry.gates:
            if gate.get("id") == gate_id and gate.get("sla_minutes"):
                return int(gate["sla_minutes"])
        return default

    def _dispatcher(self, role: str) -> str:
        try:
            return str(self.registry.agent(role).get("reports_to") or "chief_orchestrator")
        except KeyError:
            return "chief_orchestrator"

    async def _message(
        self,
        state: CompanyState,
        from_agent: str,
        to_agent: str | None,
        kind: str,
        did: str = "",
        doing: str = "",
        needs: str = "",
        artifact: str | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        message = self.storage.add_agent_message(
            state["run_id"],
            from_agent,
            to_agent,
            kind,
            did=did,
            doing=doing,
            needs=needs,
            artifact=artifact,
            confidence=confidence,
        )
        await self.bus.publish(
            state["run_id"],
            "agent.message",
            did or doing or kind,
            from_agent,
            {
                "message": message,
                "from_codename": self.registry.codename(from_agent) if from_agent else None,
                "to_codename": self.registry.codename(to_agent) if to_agent else None,
            },
        )
        return message

    async def _event(
        self,
        state: CompanyState,
        event_type: str,
        message: str,
        agent_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        run_id = state["run_id"]
        await self.bus.publish(run_id, event_type, message, agent_id, data)

    @staticmethod
    def _clip(value: Any, limit: int = 240) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[:limit].rsplit(" ", 1)[0] + "…"

    def _target_image_paths(self, state: CompanyState, limit: int = 3) -> list[Path]:
        manifest = state.get("target_manifest") or {}
        images = select_target_images(
            manifest,
            state.get("change_request") or {},
            state.get("screen_spec") or {},
            limit=limit,
        )
        paths: list[Path] = []
        for image in images:
            raw = image.get("file")
            if not raw:
                continue
            path = Path(str(raw)).expanduser()
            if path.exists() and path not in paths:
                paths.append(path)
        return paths

    def _record_agent_output(
        self,
        state: CompanyState,
        role: str,
        result: Any,
        goal: str,
        *,
        task_id: str | None = None,
    ) -> None:
        try:
            self.storage.add_agent_output(
                state["run_id"],
                role,
                task_id=task_id or role,
                stage=state.get("status"),
                model=getattr(result, "model", None),
                goal=goal,
                output=getattr(result, "data", None),
                text=getattr(result, "text", "") or "",
                usage=getattr(result, "usage", {}) or {},
            )
        except Exception:
            # Output capture is an audit aid; it must never break a run.
            pass

    def _agent_brief(self, agent_id: str | None) -> str:
        if not agent_id:
            return "System"
        if agent_id == "dictator":
            return "Dictator"
        try:
            agent = self.registry.agent(agent_id)
        except KeyError:
            return str(agent_id)
        title = str(agent.get("title") or agent_id)
        codename = str(agent.get("codename") or "").strip()
        return f"{codename} {title}".strip()

    def _company_context(self, state: CompanyState) -> dict[str, Any]:
        """Small shared picture of the company for every agent.

        It is deliberately awareness-only: agents should not repeat it or do another
        agent's job. It gives each specialist the same one-sentence company feed so
        they do not work from a stale or isolated view.
        """
        run_id = str(state.get("run_id") or "")
        messages = self.storage.list_agent_messages(run_id, limit=80) if run_id else []
        events = self.storage.list_events(run_id, limit=200) if run_id else []
        artifacts = self.storage.list_artifacts(run_id) if run_id else []
        statuses: dict[str, str] = {}
        for event in events:
            agent_id = event.get("agent_id")
            if not agent_id:
                continue
            if event.get("type") == "agent.started":
                statuses[str(agent_id)] = "running"
            elif event.get("type") == "agent.completed":
                statuses[str(agent_id)] = "completed"
            elif event.get("type") == "agent.failed":
                statuses[str(agent_id)] = "failed"
        active = [self._agent_brief(agent_id) for agent_id, status in statuses.items() if status == "running"]
        recent: list[str] = []
        for message in messages[-12:]:
            did = self._clip(message.get("did"), 150)
            doing = self._clip(message.get("doing"), 150)
            sentence = did or doing or str(message.get("kind") or "activity")
            if doing and doing != did:
                sentence = f"{sentence}; {doing}"
            recent.append(
                f"{self._agent_brief(message.get('from_agent'))} -> "
                f"{self._agent_brief(message.get('to_agent'))}: {sentence}"
            )
        pending = self.storage.pending_approval_for_run(run_id) if run_id else None
        return {
            "note": (
                "Shared live company context. Use it only for awareness and alignment; "
                "do not repeat it, and do not take over another agent's work."
            ),
            "stage": state.get("status"),
            "mode": state.get("mode"),
            "dictator_decree": self._clip(state.get("prompt"), 500),
            "active_agents": active,
            "recent_activity": recent,
            "artifacts_ready": [str(a.get("name")) for a in artifacts[-10:]],
            "pending_decision": self._clip((pending or {}).get("summary") or (pending or {}).get("title"), 200),
        }

    @staticmethod
    def _critique_detail(critique: dict[str, Any]) -> str:
        lines = [
            f"Verdict: {critique.get('verdict', 'n/a')}",
            f"Score: {critique.get('score', 'n/a')}",
        ]
        issues = critique.get("issues") or []
        if issues:
            lines.append("")
            lines.append("Issues:")
            for issue in issues[:8]:
                lines.append(
                    f"- [{issue.get('severity', 'issue')}] {issue.get('area', 'design')}: "
                    f"{issue.get('problem', '')} Fix: {issue.get('fix', '')}"
                )
        conflicts = critique.get("conflicts_remaining") or []
        if conflicts:
            lines.append("")
            lines.append("Open conflicts: " + "; ".join(str(item) for item in conflicts[:5]))
        return "\n".join(lines)

    async def _call(
        self,
        state: CompanyState,
        role: str,
        goal: str,
        context: dict[str, Any],
        images: list[Path] | None = None,
    ):
        dispatcher = self._dispatcher(role)
        await self._message(
            state,
            dispatcher,
            role,
            "handoff",
            did=f"Assigned {work_label(role)}",
            doing=f"{work_label(role)} in progress",
        )
        await self._event(
            state,
            "agent.started",
            f"{role} started",
            role,
            {
                "model": state.get("model") or self.settings.model_override or "registry default",
                "work_label": work_label(role),
                "task_id": role,
                "images": len(images or []),
            },
        )
        started = time.monotonic()
        shared_context = self._company_context(state)
        result = None
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                result = await self.agents.run(
                    role,
                    goal,
                    {**context, "company_context": shared_context},
                    images=images,
                    run_id=state["run_id"],
                    task_id=role,
                    backend=state.get("backend"),
                    model=state.get("model"),
                )
                break
            except Exception as exc:
                last_error = exc
                if attempt < 3:
                    await self._event(
                        state,
                        "agent.retry",
                        f"{role} attempt {attempt} failed; retrying",
                        role,
                        {"error": str(exc), "attempt": attempt},
                    )
                    await asyncio.sleep(1.5 * attempt)
        if result is None:
            await self._event(
                state,
                "agent.failed",
                f"{role} failed after retries: {last_error}",
                role,
                {"error": str(last_error)},
            )
            raise last_error or RuntimeError(f"{role} failed")
        self._record_agent_output(state, role, result, goal)
        await self._event(
            state,
            "agent.completed",
            f"{role} completed in {int((time.monotonic() - started) * 1000)}ms",
            role,
            {"usage": result.usage, "model": result.model, "work_label": work_label(role)},
        )
        await self._message(
            state,
            role,
            dispatcher,
            "artifact",
            did=f"Completed {work_label(role)}",
            doing="Handing result back",
        )
        return result

    async def _write(
        self,
        state: CompanyState,
        name: str,
        data: Any,
        created_by: str = "system",
        *,
        as_text: bool = False,
    ) -> dict[str, Any]:
        run_id = state["run_id"]
        if as_text:
            artifact = self.artifacts.write_text(
                run_id, name, str(data), "text/markdown", created_by=created_by
            )
        else:
            artifact = self.artifacts.write_json(run_id, name, data, created_by=created_by)
        await self._event(
            state,
            "artifact.created",
            name,
            created_by,
            {"path": artifact["path"], "sha256": artifact["sha256"], "content_type": artifact["content_type"]},
        )
        return artifact

    def _set_status(self, state: CompanyState, status: str) -> None:
        self.storage.update_run(state["run_id"], status=status)

    # ----------------------------------------------------------------- nodes
    async def _intake(self, state: CompanyState) -> dict[str, Any]:
        self._set_status(state, "intake")
        await self._event(state, "stage.started", "Intake & product brief", "product_analyst")
        result = await self._call(
            state,
            "product_analyst",
            "Turn the Dictator decree into a product brief with scope, metrics, constraints, risk, and acceptance criteria.",
            {
                "prompt": state["prompt"],
                "project": self.profile.project,
                "autonomy": self.profile.autonomy,
                "governance": self.profile.autonomy.get("governance", {}),
                "company_brief": self.registry.org_brief(),
                "playbooks": self.registry.data.get("playbooks", {}),
                "target_ui": self.profile.target_ui,
            },
        )
        change_request = dict(result.data)
        change_request["run_id"] = state["run_id"]
        change_request["prompt"] = state["prompt"]
        change_request.setdefault("change_type", "redesign")
        change_request.setdefault("target_screens", [])
        change_request.setdefault("acceptance_criteria", [])
        await self._write(state, "product_brief.json", change_request, "product_analyst")
        await self._write(state, "company_brief.json", self.registry.org_brief(), "ceo")
        await self._event(state, "stage.completed", "Product brief ready", "product_analyst")
        return {
            "change_request": change_request,
            "rework_counts": {"design": 0, "implementation": 0},
            "status": "discovery",
        }

    async def _ceo_review(self, state: CompanyState) -> dict[str, Any]:
        self._set_status(state, "ceo_review")
        await self._event(state, "stage.started", "CEO review", "ceo")
        result = await self._call(
            state,
            "ceo",
            "Review the decree and product brief. Set priorities and constraints, confirm the governance mode, "
            "and escalate only major or one-way-door decisions to the Dictator.",
            {
                "change_request": state.get("change_request", {}),
                "company_brief": self.registry.org_brief(),
                "governance": self.profile.autonomy.get("governance", {}),
                "autonomy_level": state.get("autonomy_level"),
                "budget": self.storage.budget_summary(self.settings.monthly_token_budget),
            },
        )
        directive = dict(result.data)
        await self._write(state, "ceo_directive.json", directive, "ceo")
        mode = str(state.get("mode", "design"))
        playbook = self.registry.data.get("playbooks", {}).get(mode, {})
        routing_plan = {
            "mode": mode,
            "playbook": playbook,
            "tier_leads": self.registry.tier_leads,
            "selected_agents": self.registry.enabled_agent_ids(),
            "gates": [g.get("id") for g in self.registry.gates if g.get("required")],
            "budget": self.storage.budget_summary(self.settings.monthly_token_budget),
            "ceo_directive": directive,
            "change_request": state.get("change_request", {}),
        }
        await self._write(state, "routing_plan.json", routing_plan, "chief_orchestrator")
        await self._message(
            state,
            "ceo",
            "chief_orchestrator",
            "broadcast",
            did="CEO review complete",
            doing="Handing priorities and constraints to the Chief",
            needs=str(directive.get("escalation_reason") or ""),
            artifact="ceo_directive.json",
        )
        if directive.get("escalate_to_dictator"):
            await self._event(
                state,
                "ceo.escalation",
                "CEO flagged a major decision for the Dictator",
                "ceo",
                {"reason": directive.get("escalation_reason")},
            )
        return {"ceo_directive": directive, "status": "target_ui"}

    async def _target_ui(self, state: CompanyState) -> dict[str, Any]:
        if not state.get("target_ui_enabled", True):
            manifest = {"status": "disabled", "images": [], "notes": ["Target_ui disabled for this run."]}
            await self._write(state, "target_manifest.json", manifest, "target_ui_analyst")
            return {"target_manifest": manifest, "status": "discovery"}

        self._set_status(state, "target_ui")
        await self._event(state, "stage.started", "Target_ui ingestion", "target_ui_analyst")
        manifest = scan_target_ui(
            self.settings,
            self.profile,
            path=Path(state["target_ui_path"]) if state.get("target_ui_path") else None,
            analyze=False,
            artifact_store=self.artifacts,
            run_id=state["run_id"],
        )
        if manifest.images and self.settings.target_ui_analyze:
            await self._event(
                state,
                "target_ui.analyzing",
                f"Analyzing {min(len(manifest.images), int(self.profile.target_ui.get('max_images_per_run', 8)))} reference image(s)",
                "target_ui_analyst",
            )
            manifest = await analyze_manifest(
                manifest,
                agent_runner=self.agents,
                run_id=state["run_id"],
                max_images=int(self.profile.target_ui.get("max_images_per_run", 8)),
                backend=state.get("backend"),
                model=state.get("model"),
                on_result=lambda role, result, image: self._record_agent_output(
                    state,
                    role,
                    result,
                    f"Analyze Target_ui image {image.image_id}",
                    task_id=f"target_ui:{image.image_id}",
                ),
            )
            save_manifest(self.settings, self.profile, manifest)
        data = manifest.model_dump()
        await self._write(state, "target_manifest.json", data, "target_ui_analyst")
        if manifest.images:
            await self._event(
                state,
                "target_ui.ready",
                f"{len(manifest.images)} reference image(s) available",
                "target_ui_analyst",
                {"screens": [img.screen for img in manifest.images]},
            )
        else:
            await self._event(
                state,
                "target_ui.unavailable",
                "No Target_ui images found; using research-led design path",
                "target_ui_analyst",
            )
            await self._message(
                state,
                "target_ui_analyst",
                "design_lead",
                "instruction",
                did="No Target_ui images found",
                doing="Proceeding research-led",
                needs="Add target images to the Target_ui folder for higher visual fidelity",
            )
            await self._event(
                state,
                "agent.instruction",
                "Target_ui is empty: add reference images for exact visual fidelity",
                "target_ui_analyst",
            )
        return {"target_manifest": data, "status": "discovery"}

    async def _discovery(self, state: CompanyState) -> dict[str, Any]:
        self._set_status(state, "discovery")
        await self._event(state, "stage.started", "Parallel discovery", "chief_orchestrator")
        base_context = {
            "prompt": state["prompt"],
            "change_request": state.get("change_request", {}),
            "project": self.profile.project,
            "target_manifest": state.get("target_manifest", {}),
            "android_quality": self.profile.android_quality,
            "company_context": self._company_context(state),
        }
        jobs = [
            (
                "ux_researcher",
                "Synthesize user evidence, competitor patterns, friction points, and unknowns for this change.",
                base_context,
                None,
            ),
            (
                "android_platform_designer",
                "Extract native Android platform constraints, required states, adaptive risks, and iOS-isms to avoid.",
                base_context,
                None,
            ),
        ]
        for role, _goal, _context, _images in jobs:
            await self._message(
                state,
                self._dispatcher(role),
                role,
                "handoff",
                did=f"Assigned {work_label(role)}",
                doing=f"{work_label(role)} in progress",
            )
            await self._event(
                state,
                "agent.started",
                f"{role} started",
                role,
                {
                    "model": state.get("model") or self.settings.model_override or "registry default",
                    "work_label": work_label(role),
                },
            )
        results = await self.agents.run_many(
            jobs,
            run_id=state["run_id"],
            backend=state.get("backend"),
            model=state.get("model"),
        )
        for (role, _goal, _context, _images), result in zip(jobs, results):
            self._record_agent_output(state, role, result, _goal)
            await self._event(
                state,
                "agent.completed",
                f"{role} completed",
                role,
                {"usage": result.usage, "model": result.model, "work_label": work_label(role)},
            )
            await self._message(
                state,
                role,
                self._dispatcher(role),
                "artifact",
                did=f"Completed {work_label(role)}",
                doing="Handing result to discovery lead",
            )
        research = {
            "ux": results[0].data,
            "android": results[1].data,
            "target_ui": state.get("target_manifest", {}),
        }
        await self._write(state, "research_findings.json", research, "ux_researcher")
        await self._event(state, "stage.completed", "Discovery complete", "chief_orchestrator")
        return {"research": research, "status": "synthesis"}

    async def _lead_review(
        self,
        state: CompanyState,
        role: str,
        tier: int,
        goal: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        await self._event(state, "stage.started", f"Tier {tier} lead review", role)
        result = await self._call(
            state,
            role,
            goal,
            {
                **context,
                "company_brief": self.registry.org_brief(),
                "change_request": state.get("change_request", {}),
                "routing_mode": state.get("mode"),
            },
        )
        data = dict(result.data)
        data.setdefault("tier", tier)
        data.setdefault("status", "green")
        await self._write(state, f"tier_{tier}_review.json", data, role)
        await self._message(
            state,
            role,
            "chief_orchestrator",
            "review",
            did=f"Tier {tier} review: {str(data.get('status', 'green')).upper()}",
            doing=str(data.get("summary", "Tier review complete")),
            needs=str(data.get("escalation_reason") or ""),
            artifact=f"tier_{tier}_review.json",
        )
        await self._event(
            state,
            "gate.result",
            f"Tier {tier} review: {data.get('status', 'green')}",
            role,
            {"gate": f"tier_{tier}_review", "status": data.get("status"), "review": data},
        )
        return data

    async def _discovery_lead_review(self, state: CompanyState) -> dict[str, Any]:
        data = await self._lead_review(
            state,
            self.registry.tier_leads.get(1, "discovery_lead"),
            1,
            "Review the Tier 1 discovery and IA outputs. Confirm the brief, evidence, Android constraints, flows, and state matrix are testable before design.",
            {
                "product_brief": state.get("change_request", {}),
                "research": state.get("research", {}),
                "synthesis": state.get("synthesis", {}),
                "target_manifest": state.get("target_manifest", {}),
            },
        )
        reviews = dict(state.get("tier_reviews", {}))
        reviews["1"] = data
        return {"tier_reviews": reviews, "status": "design_system"}

    async def _design_lead_review(self, state: CompanyState) -> dict[str, Any]:
        data = await self._lead_review(
            state,
            self.registry.tier_leads.get(2, "design_lead"),
            2,
            "Review the Tier 2 design system, visual spec, and design contract. Confirm target fidelity, token discipline, state coverage, and critique readiness.",
            {
                "design_tokens": state.get("design_tokens", {}),
                "screen_spec": state.get("screen_spec", {}),
                "design_contract": state.get("design_contract", {}),
                "target_manifest": state.get("target_manifest", {}),
            },
        )
        reviews = dict(state.get("tier_reviews", {}))
        reviews["2"] = data
        return {"tier_reviews": reviews, "status": "critique"}

    async def _engineering_lead_review(self, state: CompanyState) -> dict[str, Any]:
        data = await self._lead_review(
            state,
            self.registry.tier_leads.get(3, "engineering_lead"),
            3,
            "Review the implementation plan before code work. Confirm shared-file ownership, state model, tests, merge strategy, and verification commands.",
            {
                "design_contract": state.get("design_contract", {}),
                "screen_spec": state.get("screen_spec", {}),
                "implementation": state.get("implementation", {}),
                "project": self.profile.project,
                "repo_path": self.profile.project.get("repo") or str(self.settings.project_dir or ""),
                "repo_tool_policy": "read-only inspection is allowed; do not edit or run shell commands",
            },
        )
        reviews = dict(state.get("tier_reviews", {}))
        reviews["3"] = data
        return {"tier_reviews": reviews, "status": "implement"}

    async def _quality_lead_review(self, state: CompanyState) -> dict[str, Any]:
        data = await self._lead_review(
            state,
            self.registry.tier_leads.get(4, "quality_lead"),
            4,
            "Review the QA evidence before manager evaluation. Confirm visual, functional, accessibility, and performance evidence is real and sufficient.",
            {
                "qa": state.get("qa", {}),
                "screen_spec": state.get("screen_spec", {}),
                "current_ui_manifest": load_current_manifest(self.settings, self.profile).model_dump(),
                "target_manifest": state.get("target_manifest", {}),
            },
        )
        reviews = dict(state.get("tier_reviews", {}))
        reviews["4"] = data
        return {"tier_reviews": reviews, "status": "evaluation"}

    async def _synthesis(self, state: CompanyState) -> dict[str, Any]:
        self._set_status(state, "synthesis")
        await self._event(state, "stage.started", "Information architecture & flows", "ia_interaction_designer")
        result = await self._call(
            state,
            "ia_interaction_designer",
            "Define navigation, flows, screen inventory, state matrix, and interaction rules for the change.",
            {
                "change_request": state.get("change_request", {}),
                "research": state.get("research", {}),
                "target_manifest": state.get("target_manifest", {}),
                "project": self.profile.project,
            },
        )
        await self._write(state, "user_flows.json", result.data, "ia_interaction_designer")
        await self._event(state, "stage.completed", "IA and flows ready", "ia_interaction_designer")
        return {"synthesis": result.data, "status": "design_system"}

    async def _design_system(self, state: CompanyState) -> dict[str, Any]:
        self._set_status(state, "design_system")
        await self._event(state, "stage.started", "Design system & token delta", "design_system_architect")
        result = await self._call(
            state,
            "design_system_architect",
            "Produce the token delta and theme rules for this screen. Reject raw hex or ad-hoc spacing. Respect the project design law.",
            {
                "change_request": state.get("change_request", {}),
                "synthesis": state.get("synthesis", {}),
                "target_manifest": state.get("target_manifest", {}),
                "design_system": self.profile.design_system,
                "project": self.profile.project,
            },
        )
        await self._write(state, "design_tokens.json", result.data, "design_system_architect")
        await self._event(state, "stage.completed", "Design tokens ready", "design_system_architect")
        return {"design_tokens": result.data, "status": "visual_design"}

    async def _visual_design(self, state: CompanyState) -> dict[str, Any]:
        self._set_status(state, "visual_design")
        await self._event(state, "stage.started", "Visual screen specification", "visual_ui_designer")
        critique = state.get("critique") or {}
        result = await self._call(
            state,
            "visual_ui_designer",
            "Compose a high-fidelity screen specification from approved tokens and components. Specify all states and reconcile Target_ui. "
            "When Target_ui images exist, match their layout, composition, and visual hierarchy as exactly as possible; "
            "only deviate for accessibility or Android platform law, and record the reason.",
            {
                "design_tokens": state.get("design_tokens", {}),
                "target_manifest": state.get("target_manifest", {}),
                "target_fidelity": {
                    "precedence": self.profile.target_ui.get("precedence", "tiered"),
                    "fidelity": self.profile.target_ui.get("fidelity", "exact"),
                },
                "change_request": state.get("change_request", {}),
                "synthesis": state.get("synthesis", {}),
                "previous_critique": critique,
                "rework_count": state.get("rework_counts", {}).get("design", 0),
                "design_system": self.profile.design_system,
            },
            images=self._target_image_paths(state),
        )
        await self._write(state, "screen_spec.json", result.data, "visual_ui_designer")
        await self._event(state, "stage.completed", "Screen spec ready", "visual_ui_designer")
        return {"screen_spec": result.data, "status": "design_contract"}

    async def _design_contract(self, state: CompanyState) -> dict[str, Any]:
        self._set_status(state, "design_contract")
        await self._event(state, "stage.started", "Reconcile the design contract", "design_contract_editor")
        result = await self._call(
            state,
            "design_contract_editor",
            "Reconcile synthesis, tokens, screen spec, and Target_ui into one authoritative design contract. "
            "Resolve contradictions explicitly; list unresolved conflicts for the Dictator. Do not invent components.",
            {
                "synthesis": state.get("synthesis", {}),
                "design_tokens": state.get("design_tokens", {}),
                "screen_spec": state.get("screen_spec", {}),
                "target_manifest": state.get("target_manifest", {}),
                "change_request": state.get("change_request", {}),
                "android_quality": self.profile.android_quality,
            },
        )
        contract = dict(result.data)
        await self._write(state, "design_contract.json", contract, "design_contract_editor")
        remaining = contract.get("conflicts_remaining") or []
        await self._event(
            state,
            "stage.completed",
            f"Design contract ready ({len(remaining)} unresolved conflict(s))",
            "design_contract_editor",
            {"conflicts_remaining": remaining},
        )
        return {"design_contract": contract, "status": "critique"}

    async def _critique(self, state: CompanyState) -> dict[str, Any]:
        self._set_status(state, "critique")
        await self._event(state, "stage.started", "Independent design critique", "independent_design_critic")
        result = await self._call(
            state,
            "independent_design_critic",
            "Review the authoritative design contract and its underlying spec against the product brief, tokens, Android law, accessibility, target fidelity, and the seven-point checklist.",
            {
                "design_contract": state.get("design_contract", {}),
                "design_tokens": state.get("design_tokens", {}),
                "target_manifest": state.get("target_manifest", {}),
                "screen_spec": state.get("screen_spec", {}),
                "change_request": state.get("change_request", {}),
                "synthesis": state.get("synthesis", {}),
                "android_quality": self.profile.android_quality,
            },
            images=self._target_image_paths(state),
        )
        critique = dict(result.data)
        verdict = str(critique.get("verdict", "ESCALATE")).upper()
        score = float(critique.get("score", 0) or 0)
        contract_conflicts = (state.get("design_contract") or {}).get("conflicts_remaining") or []
        if contract_conflicts:
            critique.setdefault("issues", []).append(
                {
                    "severity": "critical",
                    "area": "design_contract",
                    "problem": "The design contract has unresolved conflicts that require the Dictator.",
                    "fix": "Resolve the listed conflicts before implementation.",
                    "evidence": contract_conflicts,
                }
            )
            verdict = "ESCALATE"
        gate = "pass" if verdict == "PASS" and score >= 0.80 else ("needs_rework" if verdict in {"PASS", "NEEDS_REWORK"} else "escalate")
        counts = dict(state.get("rework_counts", {}))
        if gate == "needs_rework":
            counts["design"] = int(counts.get("design", 0)) + 1
            if counts["design"] > self.settings.max_rework:
                gate = "escalate"
        critique["_gate"] = gate
        critique["_rework_count"] = counts.get("design", 0)
        await self._write(state, "design_critique.json", critique, "independent_design_critic")
        await self._event(
            state,
            "gate.result",
            f"Design critique: {verdict} ({score:.2f})",
            "independent_design_critic",
            {"gate": "design_critique", "status": gate, "score": score, "issues": critique.get("issues", [])},
        )
        await self._message(
            state,
            "independent_design_critic",
            "visual_ui_designer" if gate == "needs_rework" else "design_lead",
            "feedback" if gate == "needs_rework" else "review",
            did=f"Critique complete: {verdict} ({score:.2f})",
            doing="Sending rework instructions" if gate == "needs_rework" else "Handing approved design to the design lead",
            needs="Resolve the listed critique issues" if gate == "needs_rework" else "",
            artifact="design_critique.json",
        )
        return {"critique": critique, "critique_gate": gate, "rework_counts": counts, "status": "human_gate"}

    async def _human_gate(self, state: CompanyState) -> dict[str, Any]:
        self._set_status(state, "awaiting_design_approval")
        critique = state.get("critique", {})
        screen_spec = state.get("screen_spec", {})
        title = "Approve the design direction"
        score = critique.get("score", "n/a")
        summary = "The design review passed. Approving lets the team start implementation."
        details = "\n".join(
            [
                "What happened:",
                "- The design package is ready.",
                "- The independent critic found no blocking issues.",
                "",
                "What you can do:",
                "- Approve: start implementation.",
                "- Reject: send the design back for another pass.",
                "- Override: proceed with your own recorded instruction.",
                "",
                f"Reviewer score: {score}/1 (reference only).",
            ]
        )
        message = summary
        options = [
            {"option_id": "approve", "label": "Approve design", "impact": "Proceed to implementation."},
            {"option_id": "reject", "label": "Send back for rework", "impact": "The design team revises before implementation."},
            {"option_id": "override", "label": "Dictator override", "impact": "Proceed with a recorded override rationale."},
        ]
        if state.get("auto_approve"):
            approval = self.storage.create_approval(
                state["run_id"],
                "human_design_approval",
                title,
                message,
                options,
                from_agent="independent_design_critic",
                from_codename=self.registry.codename("independent_design_critic"),
                sla_minutes=self._gate_sla("human_design_approval"),
                summary=summary,
                details=details,
            )
            decision = {"decision": "approve", "option_id": "approve", "notes": "Auto-approved in demo mode.", "mode": "auto"}
            self.storage.decide_approval(approval["approval_id"], decision)
            await self._write(state, "design_approval.json", decision, "dictator")
            await self._event(state, "gate.result", "Design auto-approved", "dictator", {"gate": "human_design_approval", "status": "pass"})
            return {"design_approved": True, "human_decision": decision, "status": "implement"}

        approval = self.storage.pending_approval_for_run(state["run_id"], "human_design_approval")
        if not approval:
            approval = self.storage.create_approval(
                state["run_id"],
                "human_design_approval",
                title,
                message,
                options,
                from_agent="independent_design_critic",
                from_codename=self.registry.codename("independent_design_critic"),
                sla_minutes=self._gate_sla("human_design_approval"),
                summary=summary,
                details=details,
            )
        await self._event(
            state,
            "approval.required",
            "Design approval required",
            "dictator",
            {
                "approval_id": approval["approval_id"],
                "gate": "human_design_approval",
                "title": title,
                "summary": summary,
                "details": details,
                "options": options,
            },
        )
        await self._message(
            state,
            "independent_design_critic",
            "dictator",
            "escalation",
            did="Design passed critique; approval requested",
            doing="Waiting for the Dictator",
            needs=summary,
            artifact="design_critique.json",
        )
        decision = interrupt(
            {
                "type": "design_approval",
                "run_id": state["run_id"],
                "approval_id": approval["approval_id"],
                "title": title,
                "message": message,
                "summary": summary,
                "details": details,
                "options": options,
            }
        )
        if not isinstance(decision, dict):
            decision = {"decision": str(decision)}
        self.storage.decide_approval(approval["approval_id"], decision)
        approved = decision.get("decision") in {"approve", "override"}
        await self._write(state, "design_approval.json", decision, "dictator")
        await self._event(
            state,
            "gate.result",
            f"Design {'approved' if approved else 'rejected'} by Dictator",
            "dictator",
            {"gate": "human_design_approval", "status": "pass" if approved else "needs_rework", "decision": decision},
        )
        return {
            "design_approved": approved,
            "human_decision": decision,
            "approval_id": approval["approval_id"],
            "status": "implement" if approved else "escalate",
        }

    @staticmethod
    def _design_only(mode: str | None) -> bool:
        return mode in {"explore", "design", "audit"}

    async def _implement(self, state: CompanyState) -> dict[str, Any]:
        if self._design_only(state.get("mode")):
            data = {
                "status": "skipped",
                "reason": "design-only run; implementation is a separate, explicitly approved run.",
                "simulated": False,
            }
            await self._write(state, "implementation_plan.json", data, "compose_implementer")
            return {"implementation": data, "status": "qa"}
        self._set_status(state, "implementation")
        await self._event(state, "stage.started", "Compose implementation plan", "compose_implementer")
        intent_packet: dict[str, Any] | None = None
        plan_context: dict[str, Any] = {
            "change_request": state.get("change_request", {}),
            "design_contract": state.get("design_contract", {}),
            "screen_spec": state.get("screen_spec", {}),
            "design_tokens": state.get("design_tokens", {}),
            "synthesis": state.get("synthesis", {}),
            "project": self.profile.project,
            "mode": state.get("mode"),
            "demo": state.get("demo", False),
        }
        if state.get("mode") in {"build", "ship", "fix"}:
            try:
                intent_packet = build_intent_packet(
                    state,
                    max_chars=int(getattr(self.settings, "intent_packet_max_chars", 160000)),
                    repo_path=self.profile.project.get("repo") or self.settings.project_dir,
                )
                await self._write(state, "intent_packet.json", intent_packet, "chief_orchestrator")
                await self._event(
                    state,
                    "intent.packet.ready",
                    f"Intent packet ready ({intent_packet.get('meta', {}).get('chars', 0):,} chars)",
                    "chief_orchestrator",
                    {
                        "artifact": "intent_packet.json",
                        "version": intent_packet.get("version"),
                        "sources": intent_packet.get("sources", []),
                    },
                )
                plan_context = {
                    "intent_packet": intent_packet,
                    "project": self.profile.project,
                    "repo_path": self.profile.project.get("repo") or str(self.settings.project_dir or ""),
                    "repo_tool_policy": "read-only inspection is allowed; do not edit or run shell commands",
                    "mode": state.get("mode"),
                    "human_decision": state.get("human_decision", {}),
                }
            except Exception as exc:
                await self._event(
                    state,
                    "intent.packet.failed",
                    f"Intent packet could not be built: {exc}",
                    "chief_orchestrator",
                    {"error": str(exc)},
                )
        result = await self._call(
            state,
            "compose_implementer",
            "Translate the approved intent packet into a surgical Compose implementation plan with files, state, previews, tests, and verification commands. "
            "The intent_packet is authoritative; preserve exact tokens, decisions, target fidelity, and must-fix constraints. Do not average or reinterpret it. "
            "If implementation_policy.existing_screen_files is non-empty, you must plan to modify at least one of those existing screen files; "
            "do not create a parallel screen package and do not defer the core screen work to an unapproved change request. A Dictator override authorizes the listed CR-* edits now.",
            plan_context,
            images=self._target_image_paths(state),
        )
        data = dict(result.data)
        if state.get("demo"):
            data["simulated"] = True
        if intent_packet:
            data["intent_packet"] = {
                "artifact": "intent_packet.json",
                "version": intent_packet.get("version"),
                "sources": intent_packet.get("sources", []),
                "within_budget": (intent_packet.get("meta") or {}).get("within_budget"),
            }
        if (
            not state.get("demo")
            and self.code_worker.enabled
            and state.get("mode") in {"build", "ship", "fix"}
        ):
            await self._event(state, "code.editing", "Implementation worker editing project files", "compose_implementer")
            await self._event(
                state,
                "agent.started",
                "compose_implementer started code edits",
                "compose_implementer",
                {
                    "model": state.get("model") or self.settings.model_override or "registry default",
                    "work_label": "Code implementation",
                },
            )
            await self._message(
                state,
                "compose_implementer",
                "engineering_lead",
                "handoff",
                did="Starting code edits",
                doing="Applying the approved plan to an isolated worktree",
            )
            repo_path = self.profile.project.get("repo") or str(self.settings.project_dir or self.settings.root)
            packet_conflicts = (intent_packet or {}).get("decisions", {}).get("conflicts_remaining") or []
            human_decision = state.get("human_decision") or {}
            if packet_conflicts and human_decision.get("decision") != "override":
                code_result = {
                    "status": "failed",
                    "reason": "intent packet has unresolved design conflicts; resolve them before code edits",
                    "conflicts_remaining": packet_conflicts,
                }
            else:
                code_context = {
                    "prompt": state.get("prompt"),
                    "implementation_plan": data,
                    "project": self.profile.project,
                    "human_decision": human_decision,
                    "mode": state.get("mode"),
                }
                if not intent_packet:
                    # Fallback for a failed packet build: keep the legacy artifacts
                    # rather than sending the worker an under-specified prompt.
                    code_context.update(
                        {
                            "change_request": state.get("change_request", {}),
                            "design_contract": state.get("design_contract", {}),
                            "screen_spec": state.get("screen_spec", {}),
                        }
                    )
                try:
                    code_result = await self.code_worker.apply(
                        state["run_id"],
                        str(repo_path),
                        str(state.get("change_request", {}).get("problem") or state.get("prompt", "Implement the approved design")),
                        code_context,
                        intent_packet=intent_packet,
                        images=self._target_image_paths(state),
                    )
                except Exception as exc:
                    code_result = {"status": "failed", "reason": str(exc)}
            data["code_edits"] = code_result
            data["status"] = code_result.get("status", "unknown")
            await self._event(
                state,
                "code.edited" if code_result.get("status") == "edited" else "code.edit_result",
                f"Code worker: {code_result.get('status')}",
                "compose_implementer",
                {
                    "worktree": code_result.get("worktree"),
                    "branch": code_result.get("branch"),
                    "changed_files": code_result.get("changed_files", []),
                },
            )
            await self._event(
                state,
                "agent.completed",
                "compose_implementer completed code edits",
                "compose_implementer",
                {"work_label": "Code implementation"},
            )
            await self._message(
                state,
                "compose_implementer",
                "engineering_lead",
                "artifact",
                did=f"Code worker: {code_result.get('status')}",
                doing="Reviewing the implementation diff" if code_result.get("status") == "edited" else "Reporting the code-worker result",
                artifact=code_result.get("diff_artifact"),
            )
        await self._write(state, "implementation_plan.json", data, "compose_implementer")
        await self._event(state, "stage.completed", "Implementation plan ready", "compose_implementer")
        return {"implementation": data, "status": "qa"}

    async def _qa(self, state: CompanyState) -> dict[str, Any]:
        if self._design_only(state.get("mode")) or state.get("implementation", {}).get("status") == "skipped":
            qa = {
                "visual": {"verdict": "SKIPPED", "reason": "design-only run", "simulated": False},
                "functional": {"verdict": "SKIPPED", "reason": "design-only run", "simulated": False},
                "gate": "skipped",
            }
            await self._write(state, "qa_report.json", qa, "visual_qa_inspector")
            return {"qa": qa, "qa_gate": "skipped", "status": "evaluation"}

        self._set_status(state, "qa")
        await self._event(state, "stage.started", "Visual + functional/a11y QA", "visual_qa_inspector")
        screenshots = screenshots_for_run(self.artifacts, state["run_id"])
        current_manifest = load_current_manifest(self.settings, self.profile)
        if current_manifest.status == "not_available":
            await self._message(
                state,
                "visual_qa_inspector",
                "quality_lead",
                "instruction",
                did="No current-UI baseline found",
                doing="QA will be limited to target/spec evidence",
                needs="Add current-UI screenshots for visual regression and audit",
            )
            await self._event(
                state,
                "agent.instruction",
                "Current UI is empty: capture current screenshots for regression evidence",
                "visual_qa_inspector",
            )
        if not screenshots:
            await self._message(
                state,
                "visual_qa_inspector",
                "quality_lead",
                "instruction",
                did="No rendered screenshots supplied",
                doing="Visual QA cannot produce release evidence",
                needs="Run the emulator/ADB capture harness or upload screenshots",
            )
        manifest_data = state.get("target_manifest", {})
        target_images = []
        if isinstance(manifest_data, dict):
            for image in manifest_data.get("images", []):
                if isinstance(image, dict) and image.get("file"):
                    target_images.append(Path(image["file"]))
        diffs = []
        baseline_diffs = []
        if screenshots and target_images:
            from .models import TargetManifest

            manifest = TargetManifest.model_validate(manifest_data)
            for target, shot in match_screenshots(manifest, screenshots)[:4]:
                if target:
                    try:
                        diffs.append({"screen": target.screen, "screenshot": str(shot), **visual_diff(Path(target.file), shot)})
                    except Exception as exc:
                        diffs.append({"screen": target.screen, "screenshot": str(shot), "error": str(exc)})
        if screenshots and current_manifest.images:
            for baseline, shot in match_screenshots(current_manifest, screenshots)[:4]:
                if baseline:
                    try:
                        baseline_diffs.append({"screen": baseline.screen, "screenshot": str(shot), **visual_diff(Path(baseline.file), shot)})
                    except Exception as exc:
                        baseline_diffs.append({"screen": baseline.screen, "screenshot": str(shot), "error": str(exc)})

        if not screenshots:
            visual_result = {
                "verdict": "NOT_RUN",
                "score": 0.0,
                "issues": [{"severity": "critical", "screen": "all", "problem": "No screenshots supplied.", "evidence": "artifacts/runs/<run_id>/screenshots/ is empty"}],
                "device_coverage": [],
                "simulated": bool(state.get("demo")),
                "note": "Use the emulator/ADB capture harness or upload screenshots, then rerun QA.",
            }
        else:
            visual = await self._call(
                state,
                "visual_qa_inspector",
                "Compare the supplied screenshots to the approved spec and Target_ui references. Report defects, severity, and verdict.",
                {
                    "screen_spec": state.get("screen_spec", {}),
                    "target_manifest": manifest_data,
                    "current_ui_manifest": current_manifest.model_dump(),
                    "visual_diffs": diffs,
                    "baseline_diffs": baseline_diffs,
                    "device_matrix": self.profile.visual_qa.get("device_matrix", []),
                    "demo": state.get("demo", False),
                },
                images=(screenshots[:4] + self._target_image_paths(state, limit=4))[:8],
            )
            visual_result = dict(visual.data)
            if state.get("demo"):
                visual_result["simulated"] = True
            if diffs:
                visual_result["deterministic_diffs"] = diffs
            if baseline_diffs:
                visual_result["baseline_diffs"] = baseline_diffs
            await self._write(state, "visual_qa.json", visual_result, "visual_qa_inspector")

        if state.get("demo") and not screenshots:
            visual_result = {
                "verdict": "PASS",
                "score": 0.90,
                "issues": [],
                "device_coverage": ["compact_360dp", "large_412dp", "large_font_200"],
                "simulated": True,
                "note": "DEMO ONLY: no real screenshots. Production requires capture evidence.",
            }

        functional = await self._call(
            state,
            "functional_a11y_qa",
            "Verify critical flows, interaction states, TalkBack, focus, contrast, 48dp targets, 200% font, offline/error behavior, and regression risk.",
            {
                "screen_spec": state.get("screen_spec", {}),
                "synthesis": state.get("synthesis", {}),
                "android_quality": self.profile.android_quality,
                "demo": state.get("demo", False),
                "test_report": "not supplied",
            },
        )
        functional_result = dict(functional.data)
        if state.get("demo"):
            functional_result["simulated"] = True
        await self._write(state, "functional_qa.json", functional_result, "functional_a11y_qa")

        def verdict_of(data: dict[str, Any]) -> str:
            return str(data.get("verdict", "NOT_RUN")).upper()

        v = verdict_of(visual_result)
        f = verdict_of(functional_result)
        if v == "PASS" and f == "PASS":
            gate = "pass"
        elif "NEEDS_REWORK" in {v, f}:
            gate = "needs_rework"
        elif v == "NOT_RUN" or f == "NOT_RUN":
            gate = "not_run"
        else:
            gate = "needs_rework"

        qa = {"visual": visual_result, "functional": functional_result, "gate": gate, "simulated": bool(state.get("demo"))}
        await self._write(state, "qa_report.json", qa, "visual_qa_inspector")
        await self._event(
            state,
            "gate.result",
            f"QA: visual={v}, functional/a11y={f}",
            "visual_qa_inspector",
            {"gate": "visual_functional_qa", "status": gate, "visual": v, "functional": f, "simulated": bool(state.get("demo"))},
        )
        return {"qa": qa, "qa_gate": gate, "status": "evaluation"}

    async def _manager_eval(self, state: CompanyState) -> dict[str, Any]:
        self._set_status(state, "evaluation")
        await self._event(state, "stage.started", "Manager evaluation", "chief_orchestrator")
        critique_gate = state.get("critique_gate", "escalate")
        qa_gate = state.get("qa_gate", "not_run")
        mode = state.get("mode", "design")
        design_approved = bool(state.get("design_approved"))
        simulated = bool(state.get("demo"))
        tier_reviews = state.get("tier_reviews", {})
        lead_red = any(str(review.get("status", "green")).lower() == "red" for review in tier_reviews.values())
        if self._design_only(mode):
            deterministic = critique_gate == "pass" and design_approved
        else:
            deterministic = critique_gate == "pass" and design_approved and qa_gate == "pass"
        deterministic = deterministic and not lead_red
        result = await self._call(
            state,
            "chief_orchestrator",
            "Evaluate the evidence and decide release, rework, or escalation. Do not rubber-stamp; enforce required gates.",
            {
                "change_request": state.get("change_request", {}),
                "critique": state.get("critique", {}),
                "qa": state.get("qa", {}),
                "implementation": state.get("implementation", {}),
                "tier_reviews": tier_reviews,
                "mode": mode,
                "demo": simulated,
                "budget": self.storage.budget_summary(self.settings.monthly_token_budget),
            },
        )
        evaluation = dict(result.data)
        model_verdict = str(evaluation.get("verdict", "ESCALATE")).upper()
        if deterministic and not lead_red:
            gate = "pass"
            evaluation["verdict"] = "PASS"
        elif deterministic and lead_red:
            gate = "escalate"
            evaluation["verdict"] = "ESCALATE"
            evaluation["lead_veto"] = {
                "reason": "One or more tier leads marked the work red.",
                "reviews": tier_reviews,
            }
        else:
            counts = dict(state.get("rework_counts", {}))
            if counts.get("implementation", 0) < self.settings.max_rework and mode != "design":
                counts["implementation"] = int(counts.get("implementation", 0)) + 1
                gate = "needs_rework"
                evaluation["verdict"] = "NEEDS_REWORK"
            else:
                gate = "escalate"
                if model_verdict != "PASS":
                    evaluation["verdict"] = "ESCALATE"
            evaluation["rework_counts"] = counts
            state = {**state, "rework_counts": counts}
        evaluation["_deterministic_pass"] = deterministic
        evaluation["_simulated"] = simulated
        evaluation["_gate"] = gate
        evaluation["_model_verdict"] = model_verdict
        await self._write(state, "evaluation.json", evaluation, "chief_orchestrator")
        await self._event(
            state,
            "gate.result",
            f"Manager evaluation: {evaluation.get('verdict', gate)}",
            "chief_orchestrator",
            {"gate": "manager_evaluation", "status": gate, "deterministic": deterministic, "simulated": simulated},
        )
        return {"evaluation": evaluation, "eval_gate": gate, "rework_counts": state.get("rework_counts", {}), "status": "release" if gate == "pass" else "rework"}

    async def _release(self, state: CompanyState) -> dict[str, Any]:
        self._set_status(state, "release")
        await self._event(state, "stage.started", "Release candidate assembly", "release_manager")
        result = await self._call(
            state,
            "release_manager",
            "Assemble the release candidate. Verify gates, define staged rollout, monitoring, rollback, and release notes.",
            {
                "change_request": state.get("change_request", {}),
                "critique": state.get("critique", {}),
                "qa": state.get("qa", {}),
                "evaluation": state.get("evaluation", {}),
                "project": self.profile.project,
                "mode": state.get("mode", "design"),
                "demo": state.get("demo", False),
            },
        )
        release = dict(result.data)
        release["run_id"] = state["run_id"]
        release["generated_at"] = utcnow()
        release["mode"] = state.get("mode", "design")
        release["simulated"] = bool(state.get("demo"))
        release["artifact_type"] = "design_package" if self._design_only(state.get("mode")) else "release_candidate"
        release["gates"] = {
            "design_critique": state.get("critique_gate"),
            "human_design_approval": "pass" if state.get("design_approved") else "not_run",
            "visual_qa": (state.get("qa", {}) or {}).get("visual", {}).get("verdict"),
            "functional_a11y": (state.get("qa", {}) or {}).get("functional", {}).get("verdict"),
        }
        await self._write(state, "release_candidate.json", release, "release_manager")
        notes = release.get("release_notes", [])
        md = "# Release notes\n\n" + "\n".join(f"- {n}" for n in notes) + "\n\n"
        md += f"Mode: {release['mode']}\n\nSimulated: {release['simulated']}\n"
        await self._write(state, "RELEASE_NOTES.md", md, "release_manager", as_text=True)
        await self._event(
            state,
            "stage.completed",
            "Release candidate assembled",
            "release_manager",
            {"artifact_type": release["artifact_type"], "simulated": release["simulated"]},
        )
        return {"release": release, "status": "completed"}

    async def _learn(self, state: CompanyState) -> dict[str, Any]:
        await self._event(state, "stage.started", "Org memory & learnings", "design_ops_librarian")
        try:
            result = await self._call(
                state,
                "design_ops_librarian",
                "Write the durable learnings, decisions, design debt, and follow-ups from this run. Keep it short and reusable.",
                {
                    "change_request": state.get("change_request", {}),
                    "critique": state.get("critique", {}),
                    "release": state.get("release", {}),
                    "qa": state.get("qa", {}),
                    "prompt": state["prompt"],
                },
            )
            learnings = dict(result.data)
        except Exception as exc:
            learnings = {
                "memory_entry": "Run completed; design-ops agent unavailable.",
                "error": str(exc),
                "follow_ups": ["Review artifacts manually."],
            }
        await self._write(state, "learnings.json", learnings, "design_ops_librarian")
        await self._event(state, "stage.completed", "Memory updated", "design_ops_librarian")
        return {"learnings": learnings, "status": "completed"}

    async def _escalate(self, state: CompanyState) -> dict[str, Any]:
        self._set_status(state, "escalated")
        critique = state.get("critique") or {}
        qa = state.get("qa") or {}
        code_edits = (state.get("implementation") or {}).get("code_edits") or {}
        critical = [
            issue for issue in (critique.get("issues") or [])
            if str(issue.get("severity") or "").lower() == "critical"
        ]
        conflicts = (state.get("design_contract") or {}).get("conflicts_remaining") or []
        one_way_door = bool(
            (critique.get("escalation") or {}).get("one_way_door")
            or len(critical) >= 4
            or len(conflicts) >= 3
        )
        happened: list[str] = []
        why: list[str] = []
        reason = "Work did not pass the required gates within the rework limit, or the Dictator rejected the design."
        if critique.get("verdict") == "ESCALATE":
            reason = "The independent critic escalated a design decision."
            happened.append("The design review stopped the run before any code was changed.")
            why.append(f"{len(critical)} serious design problem(s) still need a decision.")
            for index, issue in enumerate(critical[:6], 1):
                area = str(issue.get("area") or "Design").replace("_", " ")
                problem = self._clip(issue.get("problem"), 260)
                fix = self._clip(issue.get("fix"), 220)
                why.append(f"{index}. {area}: {problem} Fix: {fix}")
            summary = (
                f"The design was stopped before coding: {len(critical)} serious problem(s) remain. "
                "You can approve anyway, ask for another design pass, or stop."
            )
        elif code_edits.get("status") in {"failed", "no_changes", "disabled"}:
            reason = (
                f"Code worker {code_edits.get('status')}: "
                f"{code_edits.get('reason') or 'no reviewable code patch was produced'}"
            )
            happened.append("The code worker could not produce a safe, reviewable code change.")
            why.append(self._clip(code_edits.get("reason") or "No reviewable patch was produced.", 500))
            summary = (
                "Coding stopped before QA because the code worker could not make a safe change. "
                "Open details for the reason and your options."
            )
        elif qa.get("gate") == "not_run":
            reason = "QA evidence is missing; production release is blocked until real screenshots and test evidence exist."
            happened.append("QA has no real screenshots or test evidence.")
            why.append("The release cannot be verified without real device evidence.")
            summary = (
                "QA is missing real screenshots or tests, so release is blocked. "
                "Open details to see what evidence is missing."
            )
        else:
            happened.append("The company stopped at a required gate.")
            why.append(self._clip(reason, 500))

        details_lines = ["What happened:", *[f"- {item}" for item in happened], "", "Why:"]
        details_lines.extend(f"- {item}" for item in why)
        details_lines.extend(
            [
                "",
                "What you can do:",
                "- Approve anyway: continue with a recorded override.",
                "- Ask for another design pass: the team fixes the issues and returns.",
                "- Stop this change: keep the current app unchanged.",
            ]
        )
        if qa.get("gate") == "not_run":
            details_lines.insert(-3, "- Upload screenshots, then re-run QA.")
        if one_way_door:
            details_lines.extend(
                [
                    "",
                    "This is a major one-way-door decision. If you decline without instructions, the run will halt.",
                ]
            )
        details = "\n".join(details_lines)
        options = [
            {"option_id": "force", "label": "Dictator override and continue", "impact": "Proceed with a recorded override; safety and release risks remain owned by the Dictator."},
            {"option_id": "rework", "label": "Approve another rework cycle", "impact": "Reset the relevant rework counter and try again."},
            {"option_id": "cancel", "label": "Cancel this change", "impact": "Stop the run and keep the current design."},
        ]
        approval = self.storage.create_approval(
            state["run_id"],
            "dictator_escalation",
            "Dictator decision required",
            summary,
            options,
            from_agent="chief_orchestrator",
            from_codename=self.registry.codename("chief_orchestrator"),
            sla_minutes=240,
            summary=summary,
            details=details,
        )
        escalation = {
            "run_id": state["run_id"],
            "approval_id": approval["approval_id"],
            "reason": reason,
            "summary": summary,
            "details": details,
            "halt_on_decline": one_way_door,
            "critique": critique,
            "qa_gate": qa.get("gate"),
            "options": options,
            "created_at": utcnow(),
        }
        await self._write(state, "escalation.json", escalation, "chief_orchestrator")
        await self._event(
            state,
            "approval.required",
            "Escalation requires a Dictator decision",
            "chief_orchestrator",
            {
                "approval_id": approval["approval_id"],
                "gate": "dictator_escalation",
                "summary": summary,
                "details": details,
                "halt_on_decline": one_way_door,
                "reason": reason,
                "options": options,
            },
        )
        await self._message(
            state,
            "chief_orchestrator",
            "dictator",
            "escalation",
            did="Escalating a one-way-door decision" if one_way_door else "Escalating a design decision",
            doing="Waiting for the Dictator",
            needs=summary,
            artifact="escalation.json",
        )
        return {"status": "escalated", "approval_id": approval["approval_id"]}

    # ------------------------------------------------------------- public API
    def _config(
        self,
        run_id: str,
        *,
        mode: str | None = None,
        backend: str | None = None,
    ) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": run_id},
            "run_name": f"uiux-{run_id}",
            "tags": ["uiux-agent-company", f"registry-{self.registry.version}"],
            "metadata": {
                "run_id": run_id,
                "mode": mode,
                "backend": backend,
                "registry_version": self.registry.version,
                "project": self.profile.project.get("name"),
                "autonomy_level": self.settings.autonomy_level,
            },
        }

    def _sanitize(self, result: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        for key, value in result.items():
            if key == "__interrupt__":
                interrupts = []
                for item in value or []:
                    interrupts.append(
                        {
                            "id": getattr(item, "id", None),
                            "value": getattr(item, "value", item),
                        }
                    )
                clean[key] = interrupts
            else:
                try:
                    json.dumps(value, default=str)
                    clean[key] = value
                except TypeError:
                    clean[key] = str(value)
        return clean

    async def run(
        self,
        prompt: str,
        *,
        mode: str = "design",
        auto_approve: bool | None = None,
        target_ui_enabled: bool | None = None,
        demo: bool = False,
        backend: str | None = None,
        model: str | None = None,
        autonomy_level: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        await self._ensure_graph()
        run_id = run_id or new_id("run")
        self.storage.create_run(run_id, prompt, mode, "running")
        self.storage.add_agent_message(run_id, "dictator", "ceo", "handoff", did="Issued a new decree", doing="CEO intake and priority check")
        self.storage.add_agent_message(run_id, "ceo", "chief_orchestrator", "handoff", did="Accepted the decree", doing="Routing the company")
        await self.bus.publish(run_id, "run.started", "Run started", "chief_orchestrator", {"prompt": prompt, "mode": mode})
        initial: CompanyState = {
            "run_id": run_id,
            "project_id": self.settings.project_id,
            "prompt": prompt,
            "mode": mode,
            "backend": backend or self.settings.model_backend,
            "model": model or self.settings.model_override,
            "demo": demo,
            "auto_approve": self.settings.auto_approve if auto_approve is None else auto_approve,
            "autonomy_level": autonomy_level or self.settings.autonomy_level,
            "target_ui_enabled": self.settings.target_ui_enabled if target_ui_enabled is None else target_ui_enabled,
            "target_ui_path": str(self.settings.target_ui_path),
            "rework_counts": {"design": 0, "implementation": 0},
            "errors": [],
            "status": "intake",
        }
        try:
            result = await self.graph.ainvoke(
                initial,
                self._config(run_id, mode=mode, backend=initial.get("backend")),
            )
            clean = self._sanitize(dict(result))
            interrupted = bool(clean.get("__interrupt__"))
            status = "awaiting_approval" if interrupted else str(clean.get("status", "completed"))
            self.storage.update_run(run_id, status=status, state=clean)
            if interrupted:
                await self.bus.publish(
                    run_id,
                    "run.paused",
                    "Run paused for Dictator approval",
                    "chief_orchestrator",
                    {"interrupts": clean.get("__interrupt__")},
                )
            else:
                self.storage.add_agent_message(
                    run_id,
                    "chief_orchestrator",
                    "dictator",
                    "broadcast",
                    did=f"Run {status}",
                    doing="Reporting the outcome",
                    artifact="release_candidate.json" if clean.get("release") else None,
                )
                await self.bus.publish(
                    run_id,
                    "run.completed",
                    f"Run {status}",
                    "chief_orchestrator",
                    {"status": status, "simulated": bool(clean.get("demo"))},
                )
            return clean
        except Exception as exc:
            error = {"message": str(exc), "type": type(exc).__name__, "at": utcnow()}
            self.artifacts.write_json(run_id, "error.json", error, created_by="chief_orchestrator")
            self.storage.update_run(run_id, status="failed", state={**initial, "status": "failed", "error": error})
            await self.bus.publish(run_id, "run.failed", str(exc), "chief_orchestrator", error)
            return {**initial, "status": "failed", "error": error}

    async def run_agent_task(
        self,
        run_id: str,
        agent_id: str,
        goal: str,
        context: dict[str, Any] | None = None,
        artifact_name: str | None = None,
    ) -> dict[str, Any]:
        """Generic routing node: dispatch a task to any enabled agent in the registry."""
        await self._ensure_graph()
        agent = self.registry.agent(agent_id)
        if not agent.get("enabled", False):
            raise ValueError(f"agent {agent_id} is not enabled")
        run = self.storage.get_run(run_id)
        if not run:
            raise ValueError("run not found")
        state: CompanyState = dict(self.storage.get_run(run_id).get("state") or {})
        if not state:
            try:
                snapshot = await self.graph.aget_state(self._config(run_id))
                state = dict(snapshot.values or {})
            except Exception:
                state = {}
        state["run_id"] = run_id
        result = await self._call(
            state,
            agent_id,
            goal,
            context or {},
        )
        name = artifact_name or f"agent_task_{agent_id}.json"
        await self._write(state, name, result.data, agent_id)
        return {"agent": agent_id, "codename": agent.get("codename"), "data": result.data, "artifact": name}

    async def recritique(self, run_id: str, contract: dict[str, Any] | None = None) -> dict[str, Any]:
        """Re-run the independent critic against an edited design contract."""
        await self._ensure_graph()
        try:
            snapshot = await self.graph.aget_state(self._config(run_id))
            state = dict(snapshot.values or {})
        except Exception:
            state = {}
        if not state:
            run = self.storage.get_run(run_id)
            if not run:
                raise ValueError("run not found")
            state = dict(run.get("state") or {})
        if not state:
            raise ValueError("run has no checkpoint state")
        state["run_id"] = run_id
        if contract is not None:
            state["design_contract"] = contract
        critique_update = await self._critique(state)
        state.update(critique_update)
        self.storage.update_run(run_id, status=str(state.get("status", "critique")), state=self._sanitize(state))
        await self.bus.publish(
            run_id,
            "run.recritiqued",
            f"Re-critique: {state.get('critique_gate')}",
            "independent_design_critic",
            {"critique_gate": state.get("critique_gate")},
        )
        return self._sanitize(state)

    async def recheck_qa(self, run_id: str) -> dict[str, Any]:
        """Re-run visual/functional QA for an existing run after new evidence is uploaded."""
        await self._ensure_graph()
        try:
            snapshot = await self.graph.aget_state(self._config(run_id))
            state = dict(snapshot.values or {})
        except Exception:
            state = {}
        if not state:
            run = self.storage.get_run(run_id)
            if not run:
                raise ValueError("run not found")
            state = dict(run.get("state") or {})
        if not state:
            raise ValueError("run has no checkpoint state to re-check")
        state["run_id"] = run_id
        state["mode"] = "ship"
        state["demo"] = False
        implementation = dict(state.get("implementation") or {})
        if implementation.get("status") == "skipped" or not implementation:
            implementation["status"] = "provided"
        state["implementation"] = implementation
        qa_update = await self._qa(state)
        state.update(qa_update)
        eval_update = await self._manager_eval(state)
        state.update(eval_update)
        if eval_update.get("eval_gate") == "pass":
            state.update(await self._release(state))
        else:
            state.update(await self._escalate(state))
        self.storage.update_run(run_id, status=str(state.get("status", "completed")), state=self._sanitize(state))
        await self.bus.publish(
            run_id,
            "run.rechecked",
            f"QA re-check complete: {state.get('eval_gate')}",
            "chief_orchestrator",
            {"eval_gate": state.get("eval_gate"), "qa_gate": state.get("qa_gate")},
        )
        return self._sanitize(state)

    async def stop(self, run_id: str, reason: str = "Stopped by Dictator.") -> dict[str, Any]:
        pending = self.storage.pending_approval_for_run(run_id)
        if pending:
            self.storage.decide_approval(
                pending["approval_id"],
                {"decision": "stop", "notes": reason},
                status="stopped",
            )
        run = self.storage.get_run(run_id)
        if run:
            self.storage.update_run(
                run_id,
                status="stopped",
                state={**(run.get("state") or {}), "status": "stopped", "stopped_reason": reason},
            )
        await self.bus.publish(run_id, "run.stopped", reason, "dictator", {"reason": reason})
        return {"run_id": run_id, "status": "stopped", "reason": reason}

    async def resume(self, run_id: str, decision: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_graph()
        await self.bus.publish(run_id, "run.resumed", "Run resumed by Dictator", "chief_orchestrator", {"decision": decision})
        try:
            result = await self.graph.ainvoke(
                Command(resume=decision),
                self._config(run_id),
            )
            clean = self._sanitize(dict(result))
            status = "awaiting_approval" if clean.get("__interrupt__") else str(clean.get("status", "completed"))
            self.storage.update_run(run_id, status=status, state=clean)
            await self.bus.publish(run_id, "run.completed" if not clean.get("__interrupt__") else "run.paused", f"Run {status}", "chief_orchestrator", {"status": status})
            return clean
        except Exception as exc:
            error = {"message": str(exc), "type": type(exc).__name__, "at": utcnow()}
            self.storage.update_run(run_id, status="failed", state={"status": "failed", "error": error})
            await self.bus.publish(run_id, "run.failed", str(exc), "chief_orchestrator", error)
            return {"status": "failed", "error": error}

    async def resume_run(self, run_id: str, decision: dict[str, Any] | None = None) -> dict[str, Any]:
        """Resume a failed or stopped run from its last durable checkpoint."""
        await self._ensure_graph()
        run = self.storage.get_run(run_id)
        if not run:
            raise ValueError("run not found")
        if run.get("status") not in {"failed", "stopped"}:
            raise ValueError(f"run is not resumable from status {run.get('status')}")
        snapshot = await self.graph.aget_state(self._config(run_id))
        next_nodes = list(snapshot.next or [])
        interrupts = list(getattr(snapshot, "interrupts", None) or [])
        if not next_nodes and not interrupts:
            raise ValueError("run has no resumable checkpoint")
        if interrupts and decision is None:
            raise ValueError("run is waiting for a decision; use the approval path")
        from_stage = ", ".join(next_nodes) or "approval"
        resumed_state = dict(snapshot.values or {})
        resumed_state.update({"status": "running", "resumed_from": from_stage})
        self.storage.update_run(run_id, status="running", state=resumed_state)
        self.artifacts.write_json(
            run_id,
            "resume.json",
            {"run_id": run_id, "from_stage": from_stage, "next": next_nodes, "resumed_at": utcnow()},
            created_by="chief_orchestrator",
        )
        await self.bus.publish(
            run_id,
            "run.resumed",
            f"Resuming from {from_stage}",
            "chief_orchestrator",
            {"from_stage": from_stage, "next": next_nodes},
        )
        try:
            result = await self.graph.ainvoke(
                Command(resume=decision) if interrupts and decision is not None else None,
                self._config(run_id),
            )
            clean = self._sanitize(dict(result))
            interrupted = bool(clean.get("__interrupt__"))
            status = "awaiting_approval" if interrupted else str(clean.get("status", "completed"))
            self.storage.update_run(run_id, status=status, state=clean)
            await self.bus.publish(
                run_id,
                "run.paused" if interrupted else "run.completed",
                f"Run {status}",
                "chief_orchestrator",
                {"status": status, "resumed": True},
            )
            return clean
        except Exception as exc:
            error = {"message": str(exc), "type": type(exc).__name__, "at": utcnow()}
            self.artifacts.write_json(run_id, "error.json", error, created_by="chief_orchestrator")
            self.storage.update_run(
                run_id,
                status="failed",
                state={**resumed_state, "status": "failed", "error": error},
            )
            await self.bus.publish(run_id, "run.failed", str(exc), "chief_orchestrator", error)
            return {"status": "failed", "error": error}

    def _is_crisis_escalation(self, values: dict[str, Any]) -> bool:
        critique = values.get("critique") or {}
        critical = [
            issue for issue in (critique.get("issues") or [])
            if str(issue.get("severity") or "").lower() == "critical"
        ]
        conflicts = (values.get("design_contract") or {}).get("conflicts_remaining") or []
        return bool(
            (critique.get("escalation") or {}).get("one_way_door")
            or len(critical) >= 4
            or len(conflicts) >= 3
        )

    async def resume_escalated(self, run_id: str, decision: dict[str, Any] | None = None) -> dict[str, Any]:
        """Continue an escalated design run according to the Dictator's decision.

        Approve/override/dictate -> continue with a recorded override.
        Decline with a rework instruction -> run another design pass.
        Decline without instructions on a very major decision -> halt.
        """
        await self._ensure_graph()
        run = self.storage.get_run(run_id)
        if not run:
            raise ValueError("run not found")
        if run.get("status") != "escalated":
            raise ValueError(f"run is not an escalated run: {run.get('status')}")
        payload = decision or {"decision": "override"}
        decision_kind = str(payload.get("decision") or "")
        if decision_kind not in {"approve", "override", "force", "reject"}:
            raise ValueError("escalation requires approve, override, force, or reject")
        snapshot = await self.graph.aget_state(self._config(run_id))
        values = dict(snapshot.values or {})
        design_stage = (
            values.get("critique_gate") == "escalate"
            or values.get("status") in {"critique", "human_gate", "awaiting_design_approval"}
        ) and not values.get("implementation")
        if not design_stage:
            raise ValueError("only design-stage escalations can be continued safely")
        notes = str(payload.get("notes") or "").strip()

        if decision_kind == "reject":
            if self._is_crisis_escalation(values) and not notes:
                reason = (
                    "The Dictator declined a major one-way-door decision without a dictate. "
                    "The run was halted to avoid continuing with unresolved intent."
                )
                values.update(
                    {
                        "status": "stopped",
                        "stopped_reason": reason,
                        "human_decision": {
                            "decision": "reject",
                            "notes": notes or "Declined without a dictate.",
                            "source": "dictator_escalation",
                            "approval_id": payload.get("approval_id"),
                        },
                    }
                )
                self.storage.update_run(run_id, status="stopped", state=values)
                self.artifacts.write_json(run_id, "escalation_decision.json", values["human_decision"], created_by="dictator")
                await self.bus.publish(run_id, "run.stopped", reason, "dictator", {"reason": reason, "halt_on_decline": True})
                return {"status": "stopped", "reason": reason}
            critique = dict(values.get("critique") or {})
            instructions = list(critique.get("rework_instructions") or [])
            instructions.append(notes or "Dictator declined this design; run another design pass with safer constraints.")
            critique["rework_instructions"] = instructions
            counts = dict(values.get("rework_counts") or {})
            counts["design"] = int(counts.get("design", 0)) + 1
            values.update(
                {
                    "critique": critique,
                    "critique_gate": "needs_rework",
                    "design_approved": False,
                    "human_decision": {
                        "decision": "reject",
                        "notes": notes or "Declined; rework requested.",
                        "source": "dictator_escalation",
                        "approval_id": payload.get("approval_id"),
                    },
                    "rework_counts": counts,
                    "status": "critique",
                }
            )
            as_node = "critique"
            message = "Escalation declined; another design pass requested"
        else:
            human_decision = {
                "decision": "override",
                "notes": notes or "Dictator approved the escalated design gate.",
                "source": "dictator_escalation",
                "approval_id": payload.get("approval_id"),
            }
            values.update(
                {
                    "critique_gate": "pass",
                    "design_approved": True,
                    "human_decision": human_decision,
                    "auto_approve": True,
                    "status": "human_gate",
                }
            )
            as_node = "human_gate"
            message = "Escalation approved; continuing with a recorded Dictator override"

        await self.graph.aupdate_state(self._config(run_id), values, as_node=as_node)
        self.storage.update_run(run_id, status="running", state={**values, "status": "running"})
        self.artifacts.write_json(
            run_id,
            "resume.json",
            {"run_id": run_id, "from_stage": "escalation", "decision": values.get("human_decision"), "resumed_at": utcnow()},
            created_by="chief_orchestrator",
        )
        await self.bus.publish(
            run_id,
            "run.resumed",
            message,
            "chief_orchestrator",
            {"from_stage": "escalation", "decision": values.get("human_decision")},
        )
        try:
            result = await self.graph.ainvoke(None, self._config(run_id))
            clean = self._sanitize(dict(result))
            interrupted = bool(clean.get("__interrupt__"))
            status = "awaiting_approval" if interrupted else str(clean.get("status", "completed"))
            self.storage.update_run(run_id, status=status, state=clean)
            await self.bus.publish(
                run_id,
                "run.paused" if interrupted else "run.completed",
                f"Run {status}",
                "chief_orchestrator",
                {"status": status, "resumed_from_escalation": True},
            )
            return clean
        except Exception as exc:
            error = {"message": str(exc), "type": type(exc).__name__, "at": utcnow()}
            self.artifacts.write_json(run_id, "error.json", error, created_by="chief_orchestrator")
            self.storage.update_run(run_id, status="failed", state={**values, "status": "failed", "error": error})
            await self.bus.publish(run_id, "run.failed", str(exc), "chief_orchestrator", error)
            return {"status": "failed", "error": error}

    async def delete_thread(self, run_id: str) -> None:
        """Best-effort removal of the LangGraph checkpoint thread for a deleted run."""
        try:
            await self._ensure_graph()
            checkpointer = getattr(self.graph, "checkpointer", None)
            if checkpointer and hasattr(checkpointer, "adelete_thread"):
                await checkpointer.adelete_thread(run_id)
        except Exception:
            pass

    async def get_state(self, run_id: str) -> dict[str, Any]:
        try:
            await self._ensure_graph()
            snapshot = await self.graph.aget_state(self._config(run_id))
            values = self._sanitize(dict(snapshot.values))
            interrupts = getattr(snapshot, "interrupts", None)
            serialized_interrupts = []
            for item in interrupts or []:
                serialized_interrupts.append({"id": getattr(item, "id", None), "value": getattr(item, "value", item)})
            return {
                "values": values,
                "next": list(snapshot.next or []),
                "interrupts": serialized_interrupts,
                "metadata": getattr(snapshot, "metadata", None),
            }
        except Exception:
            run = self.storage.get_run(run_id)
            return {"values": (run or {}).get("state") or {}, "next": [], "interrupts": []}

    async def aclose(self) -> None:
        if self._checkpointer_cm is not None:
            await self._checkpointer_cm.__aexit__(None, None, None)
            self._checkpointer_cm = None
            self.graph = None

    def close(self) -> None:
        """Best-effort synchronous close for short-lived CLI processes."""
        return None
