from __future__ import annotations

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
from .config import Settings
from .events import EventBus
from .gateway import ModelGateway
from .models import new_id, utcnow
from .registry import BudgetPolicy, ProjectProfile, Registry, load_budget, load_profile, load_registry
from .storage import Storage
from .target_ui import (
    analyze_manifest,
    load_manifest,
    match_screenshots,
    save_manifest,
    scan_target_ui,
    screenshots_for_run,
    visual_diff,
)


class CompanyState(TypedDict, total=False):
    run_id: str
    prompt: str
    mode: str
    backend: str
    demo: bool
    auto_approve: bool
    autonomy_level: str
    target_ui_enabled: bool
    target_ui_path: str
    change_request: dict[str, Any]
    target_manifest: dict[str, Any]
    research: dict[str, Any]
    synthesis: dict[str, Any]
    design_tokens: dict[str, Any]
    screen_spec: dict[str, Any]
    design_contract: dict[str, Any]
    critique: dict[str, Any]
    critique_gate: str
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
    ):
        self.settings = settings or Settings.from_env()
        self.registry = registry or load_registry(self.settings)
        self.profile = profile or load_profile(self.settings)
        self.budget = budget or load_budget(self.settings)
        self.storage = storage or Storage(self.settings.storage_db)
        self.bus = bus or EventBus(self.storage)
        self.artifacts = artifacts or ArtifactStore(self.settings, self.storage)
        self.gateway = gateway or ModelGateway(self.settings, self.registry, self.storage)
        self.agents = agents or AgentRunner(self.gateway, self.registry)
        self.checkpointer = None
        self._checkpointer_cm = None
        self.graph = None

    async def _ensure_graph(self) -> None:
        if self.graph is not None:
            return
        if self.settings.durable_checkpoints:
            self._checkpointer_cm = AsyncSqliteSaver.from_conn_string(str(self.settings.checkpoint_db))
            self.checkpointer = await self._checkpointer_cm.__aenter__()
        else:
            self.checkpointer = MemorySaver()
        self.graph = self._build()

    # ------------------------------------------------------------------ graph
    def _build(self):
        builder = StateGraph(CompanyState)
        builder.add_node("intake", self._intake)
        builder.add_node("target_ui", self._target_ui)
        builder.add_node("discovery", self._discovery)
        builder.add_node("synthesis", self._synthesis)
        builder.add_node("design_system", self._design_system)
        builder.add_node("visual_design", self._visual_design)
        builder.add_node("design_contract", self._design_contract)
        builder.add_node("critique", self._critique)
        builder.add_node("human_gate", self._human_gate)
        builder.add_node("implement", self._implement)
        builder.add_node("qa", self._qa)
        builder.add_node("manager_eval", self._manager_eval)
        builder.add_node("release", self._release)
        builder.add_node("learn", self._learn)
        builder.add_node("escalate", self._escalate)

        builder.add_edge(START, "intake")
        builder.add_edge("intake", "target_ui")
        builder.add_edge("target_ui", "discovery")
        builder.add_edge("discovery", "synthesis")
        builder.add_edge("synthesis", "design_system")
        builder.add_edge("design_system", "visual_design")
        builder.add_edge("visual_design", "design_contract")
        builder.add_edge("design_contract", "critique")
        builder.add_conditional_edges(
            "critique",
            self._route_after_critique,
            {"human_gate": "human_gate", "visual_design": "visual_design", "escalate": "escalate"},
        )
        builder.add_conditional_edges(
            "human_gate",
            self._route_after_human,
            {"implement": "implement", "escalate": "escalate"},
        )
        builder.add_edge("implement", "qa")
        builder.add_edge("qa", "manager_eval")
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

    def _route_after_manager(self, state: CompanyState) -> str:
        gate = state.get("eval_gate", "escalate")
        if gate == "pass":
            return "release"
        counts = state.get("rework_counts", {})
        if gate == "needs_rework" and int(counts.get("implementation", 0)) <= self.settings.max_rework:
            return "implement"
        return "escalate"

    # --------------------------------------------------------------- helpers
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

    async def _call(
        self,
        state: CompanyState,
        role: str,
        goal: str,
        context: dict[str, Any],
        images: list[Path] | None = None,
    ):
        await self._event(state, "agent.started", f"{role} started", role)
        started = time.monotonic()
        try:
            result = await self.agents.run(
                role,
                goal,
                context,
                images=images,
                run_id=state["run_id"],
                task_id=role,
                backend=state.get("backend"),
            )
        except Exception as exc:
            await self._event(
                state,
                "agent.failed",
                f"{role} failed: {exc}",
                role,
                {"error": str(exc)},
            )
            raise
        await self._event(
            state,
            "agent.completed",
            f"{role} completed in {int((time.monotonic() - started) * 1000)}ms",
            role,
            {"usage": result.usage, "model": result.model},
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
        await self._event(state, "stage.completed", "Product brief ready", "product_analyst")
        return {
            "change_request": change_request,
            "rework_counts": {"design": 0, "implementation": 0},
            "status": "discovery",
        }

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
        results = await self.agents.run_many(jobs, run_id=state["run_id"], backend=state.get("backend"))
        research = {
            "ux": results[0].data,
            "android": results[1].data,
            "target_ui": state.get("target_manifest", {}),
        }
        await self._write(state, "research_findings.json", research, "ux_researcher")
        await self._event(state, "stage.completed", "Discovery complete", "chief_orchestrator")
        return {"research": research, "status": "synthesis"}

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
            "Compose a high-fidelity screen specification from approved tokens and components. Specify all states and reconcile Target_ui.",
            {
                "design_tokens": state.get("design_tokens", {}),
                "target_manifest": state.get("target_manifest", {}),
                "change_request": state.get("change_request", {}),
                "synthesis": state.get("synthesis", {}),
                "previous_critique": critique,
                "rework_count": state.get("rework_counts", {}).get("design", 0),
                "design_system": self.profile.design_system,
            },
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
        return {"critique": critique, "critique_gate": gate, "rework_counts": counts, "status": "human_gate"}

    async def _human_gate(self, state: CompanyState) -> dict[str, Any]:
        self._set_status(state, "awaiting_design_approval")
        critique = state.get("critique", {})
        screen_spec = state.get("screen_spec", {})
        title = "Approve the design direction"
        message = (
            f"{screen_spec.get('goal', 'Design ready for approval.')} "
            f"Critic score: {critique.get('score', 'n/a')}. "
            "Approving allows implementation to start."
        )
        options = [
            {"option_id": "approve", "label": "Approve design", "impact": "Proceed to implementation."},
            {"option_id": "reject", "label": "Send back for rework", "impact": "The design team revises before implementation."},
            {"option_id": "override", "label": "Dictator override", "impact": "Proceed with a recorded override rationale."},
        ]
        if state.get("auto_approve"):
            approval = self.storage.create_approval(
                state["run_id"], "human_design_approval", title, message, options
            )
            decision = {"decision": "approve", "option_id": "approve", "notes": "Auto-approved in demo mode.", "mode": "auto"}
            self.storage.decide_approval(approval["approval_id"], decision)
            await self._write(state, "design_approval.json", decision, "dictator")
            await self._event(state, "gate.result", "Design auto-approved", "dictator", {"gate": "human_design_approval", "status": "pass"})
            return {"design_approved": True, "human_decision": decision, "status": "implement"}

        approval = self.storage.pending_approval_for_run(state["run_id"], "human_design_approval")
        if not approval:
            approval = self.storage.create_approval(
                state["run_id"], "human_design_approval", title, message, options
            )
        await self._event(
            state,
            "approval.required",
            "Design approval required",
            "dictator",
            {"approval_id": approval["approval_id"], "gate": "human_design_approval", "title": title, "message": message, "options": options},
        )
        decision = interrupt(
            {
                "type": "design_approval",
                "run_id": state["run_id"],
                "approval_id": approval["approval_id"],
                "title": title,
                "message": message,
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

    async def _implement(self, state: CompanyState) -> dict[str, Any]:
        if state.get("mode") == "design":
            data = {
                "status": "skipped",
                "reason": "design-only run; implementation is a separate, explicitly approved run.",
                "simulated": False,
            }
            await self._write(state, "implementation_plan.json", data, "compose_implementer")
            return {"implementation": data, "status": "qa"}
        self._set_status(state, "implementation")
        await self._event(state, "stage.started", "Compose implementation plan", "compose_implementer")
        result = await self._call(
            state,
            "compose_implementer",
            "Translate the approved screen spec into a surgical Compose implementation plan with files, state, previews, tests, and verification commands.",
            {
                "change_request": state.get("change_request", {}),
                "design_contract": state.get("design_contract", {}),
                "screen_spec": state.get("screen_spec", {}),
                "design_tokens": state.get("design_tokens", {}),
                "synthesis": state.get("synthesis", {}),
                "project": self.profile.project,
                "mode": state.get("mode"),
                "demo": state.get("demo", False),
            },
        )
        data = dict(result.data)
        if state.get("demo"):
            data["simulated"] = True
        await self._write(state, "implementation_plan.json", data, "compose_implementer")
        await self._event(state, "stage.completed", "Implementation plan ready", "compose_implementer")
        return {"implementation": data, "status": "qa"}

    async def _qa(self, state: CompanyState) -> dict[str, Any]:
        if state.get("mode") == "design" or state.get("implementation", {}).get("status") == "skipped":
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
        manifest_data = state.get("target_manifest", {})
        target_images = []
        if isinstance(manifest_data, dict):
            for image in manifest_data.get("images", []):
                if isinstance(image, dict) and image.get("file"):
                    target_images.append(Path(image["file"]))
        diffs = []
        if screenshots and target_images:
            from .models import TargetManifest

            manifest = TargetManifest.model_validate(manifest_data)
            for target, shot in match_screenshots(manifest, screenshots)[:4]:
                if target:
                    try:
                        diffs.append({"screen": target.screen, "screenshot": str(shot), **visual_diff(Path(target.file), shot)})
                    except Exception as exc:
                        diffs.append({"screen": target.screen, "screenshot": str(shot), "error": str(exc)})

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
                    "visual_diffs": diffs,
                    "device_matrix": self.profile.visual_qa.get("device_matrix", []),
                    "demo": state.get("demo", False),
                },
                images=screenshots[:4],
            )
            visual_result = dict(visual.data)
            if state.get("demo"):
                visual_result["simulated"] = True
            if diffs:
                visual_result["deterministic_diffs"] = diffs
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
        if mode == "design":
            deterministic = critique_gate == "pass" and design_approved
        else:
            deterministic = critique_gate == "pass" and design_approved and qa_gate == "pass"
        result = await self._call(
            state,
            "chief_orchestrator",
            "Evaluate the evidence and decide release, rework, or escalation. Do not rubber-stamp; enforce required gates.",
            {
                "change_request": state.get("change_request", {}),
                "critique": state.get("critique", {}),
                "qa": state.get("qa", {}),
                "implementation": state.get("implementation", {}),
                "mode": mode,
                "demo": simulated,
                "budget": self.storage.budget_summary(self.settings.monthly_token_budget),
            },
        )
        evaluation = dict(result.data)
        model_verdict = str(evaluation.get("verdict", "ESCALATE")).upper()
        if deterministic:
            gate = "pass"
            evaluation["verdict"] = "PASS"
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
        release["artifact_type"] = "design_package" if state.get("mode") == "design" else "release_candidate"
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
        critique = state.get("critique", {})
        qa = state.get("qa", {})
        reason = "Work did not pass the required gates within the rework limit, or the Dictator rejected the design."
        if critique.get("verdict") == "ESCALATE":
            reason = "The independent critic escalated a design decision."
        if qa.get("gate") == "not_run":
            reason = "QA evidence is missing; production release is blocked until real screenshots and test evidence exist."
        options = [
            {"option_id": "force", "label": "Dictator override and continue", "impact": "Proceed with a recorded override; safety and release risks remain owned by the Dictator."},
            {"option_id": "rework", "label": "Approve another rework cycle", "impact": "Reset the relevant rework counter and try again."},
            {"option_id": "cancel", "label": "Cancel this change", "impact": "Stop the run and keep the current design."},
        ]
        approval = self.storage.create_approval(
            state["run_id"],
            "dictator_escalation",
            "Dictator decision required",
            reason,
            options,
        )
        escalation = {
            "run_id": state["run_id"],
            "approval_id": approval["approval_id"],
            "reason": reason,
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
            {"approval_id": approval["approval_id"], "gate": "dictator_escalation", "reason": reason, "options": options},
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
        autonomy_level: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        await self._ensure_graph()
        run_id = run_id or new_id("run")
        self.storage.create_run(run_id, prompt, mode, "running")
        await self.bus.publish(run_id, "run.started", "Run started", "chief_orchestrator", {"prompt": prompt, "mode": mode})
        initial: CompanyState = {
            "run_id": run_id,
            "prompt": prompt,
            "mode": mode,
            "backend": backend or self.settings.model_backend,
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
