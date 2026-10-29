from __future__ import annotations

import asyncio
import json
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
from .models import EvolutionProposal, new_id, utcnow
from .prompts import work_label
from .registry import Registry, load_registry, save_yaml
from .storage import Storage


class EvolutionState(TypedDict, total=False):
    proposal_id: str
    focus: str
    auto_approve: bool
    backend: str
    model: str | None
    metrics: dict[str, Any]
    proposal: dict[str, Any]
    evaluation: dict[str, Any]
    approved: bool
    decision: dict[str, Any]
    apply_result: dict[str, Any]
    status: str


class EvolutionEngine:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        registry: Registry | None = None,
        storage: Storage | None = None,
        bus: EventBus | None = None,
        artifacts: ArtifactStore | None = None,
        gateway: ModelGateway | None = None,
        agents: AgentRunner | None = None,
    ):
        self.settings = settings or Settings.from_env()
        self.registry = registry or load_registry(self.settings)
        self.storage = storage or Storage(self.settings.storage_db)
        self.bus = bus or EventBus(self.storage)
        self.artifacts = artifacts or ArtifactStore(self.settings, self.storage)
        self.gateway = gateway or ModelGateway(self.settings, self.registry, self.storage)
        self.agents = agents or AgentRunner(self.gateway, self.registry)
        self._cm = None
        self.checkpointer = None
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

                self._cm = AsyncPostgresSaver.from_conn_string(self.settings.postgres_dsn)
                self.checkpointer = await self._cm.__aenter__()
            elif self.settings.durable_checkpoints:
                self._cm = AsyncSqliteSaver.from_conn_string(str(self.settings.checkpoint_db))
                self.checkpointer = await self._cm.__aenter__()
            else:
                self.checkpointer = MemorySaver()
            self.graph = self._build()
            self._init_lock = None

    def _build(self):
        builder = StateGraph(EvolutionState)
        builder.add_node("observe", self._observe)
        builder.add_node("diagnose", self._diagnose)
        builder.add_node("evaluate", self._evaluate)
        builder.add_node("approval", self._approval)
        builder.add_node("apply", self._apply)
        builder.add_node("reject", self._reject)
        builder.add_edge(START, "observe")
        builder.add_edge("observe", "diagnose")
        builder.add_edge("diagnose", "evaluate")
        builder.add_edge("evaluate", "approval")
        builder.add_conditional_edges(
            "approval",
            lambda state: "apply" if state.get("approved") else "reject",
            {"apply": "apply", "reject": "reject"},
        )
        builder.add_edge("apply", END)
        builder.add_edge("reject", END)
        return builder.compile(checkpointer=self.checkpointer)

    def _config(self, proposal_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": f"evo-{proposal_id}"}}

    async def _event(self, proposal_id: str, event_type: str, message: str, agent_id: str | None = None, data: dict[str, Any] | None = None):
        await self.bus.publish(proposal_id, event_type, message, agent_id, data)

    async def _observe(self, state: EvolutionState) -> dict[str, Any]:
        await self._event(state["proposal_id"], "evolution.started", "Evolution audit started", "org_evolution_auditor")
        runs = self.storage.list_runs(30)
        statuses: dict[str, int] = {}
        rework: dict[str, int] = {}
        for run in runs:
            statuses[run["status"]] = statuses.get(run["status"], 0) + 1
            values = run.get("state") or {}
            counts = values.get("rework_counts") if isinstance(values, dict) else None
            if isinstance(counts, dict):
                for key, value in counts.items():
                    rework[key] = rework.get(key, 0) + int(value)
        metrics = {
            "run_count": len(runs),
            "run_statuses": statuses,
            "rework_totals": rework,
            "budget": self.storage.budget_summary(self.settings.monthly_token_budget),
            "pending_approvals": len(self.storage.list_approvals("pending")),
            "registry": {
                "version": self.registry.version,
                "enabled_agents": self.registry.enabled_agent_ids(),
                "disabled_agents": [a["id"] for a in self.registry.agents if not a.get("enabled", False)],
                "pods": len(self.registry.pods),
            },
            "recent_runs": [
                {"run_id": r["run_id"], "status": r["status"], "prompt": r["prompt"][:160], "created_at": r["created_at"]}
                for r in runs[:10]
            ],
        }
        await self._event(state["proposal_id"], "evolution.observed", "Metrics collected", "org_evolution_auditor", metrics)
        return {"metrics": metrics, "status": "diagnose"}

    async def _diagnose(self, state: EvolutionState) -> dict[str, Any]:
        await self._event(
            state["proposal_id"],
            "agent.started",
            "Org evolution auditor diagnosing",
            "org_evolution_auditor",
            {
                "model": state.get("model") or self.settings.model_override or "registry default",
                "work_label": work_label("org_evolution_auditor"),
            },
        )
        result = await self.agents.run(
            "org_evolution_auditor",
            "Diagnose the biggest bottleneck in the agent company and propose one controlled, measurable evolution. "
            "You cannot approve or apply it. Prefer low-risk changes with a shadow eval and rollback plan.",
            {
                "focus": state.get("focus", "quality"),
                "metrics": state.get("metrics", {}),
                "registry": self.registry.summary(),
                "enabled_agents": self.registry.enabled_agent_ids(),
                "immutable": self.registry.data.get("evolution_policy", {}).get("immutable", []),
                "mutable": self.registry.data.get("evolution_policy", {}).get("mutable", []),
            },
            run_id=state["proposal_id"],
            task_id="org_evolution",
            backend=state.get("backend"),
            model=state.get("model"),
        )
        proposal = dict(result.data)
        proposal.setdefault("proposal_id", state["proposal_id"])
        proposal["proposal_id"] = state["proposal_id"]
        proposal.setdefault("status", "pending_approval")
        proposal.setdefault("created_at", utcnow())
        proposal["metrics"] = state.get("metrics", {})
        EvolutionProposal.model_validate(proposal)
        self.storage.create_evolution(proposal, focus=state.get("focus", "quality"))
        self.artifacts.write_json(state["proposal_id"], "evolution_proposal.json", proposal, created_by="org_evolution_auditor")
        await self._event(
            state["proposal_id"],
            "evolution.proposed",
            proposal.get("title", "Evolution proposal"),
            "org_evolution_auditor",
            {"proposal": proposal},
        )
        return {"proposal": proposal, "status": "evaluate"}

    async def _evaluate(self, state: EvolutionState) -> dict[str, Any]:
        proposal = state.get("proposal", {})
        changes = proposal.get("changes", [])
        immutable = set(self.registry.data.get("evolution_policy", {}).get("immutable", []))
        safe_types = {
            "enable_agent",
            "disable_agent",
            "model_swap",
            "prompt_update",
            "routing_change",
            "gate_threshold",
            "budget_cap",
            "pod_composition",
        }
        issues = []
        for change in changes:
            if change.get("type") not in safe_types:
                issues.append(f"Unsupported change type: {change.get('type')}")
            if str(change.get("target")) in immutable:
                issues.append(f"Attempted immutable change: {change.get('target')}")
        heuristic_pass = not issues and bool(changes)
        evaluation = {
            "verdict": "PASS" if heuristic_pass else "NEEDS_WORK",
            "shadow_eval": "heuristic_mvp",
            "simulated": True,
            "issues": issues,
            "checks": [
                {"check": "proposal_has_changes", "status": "pass" if changes else "fail"},
                {"check": "no_immutable_changes", "status": "pass" if not any(str(c.get('target')) in immutable for c in changes) else "fail"},
                {"check": "rollback_plan_present", "status": "pass" if proposal.get("rollback_plan") else "fail"},
                {"check": "eval_plan_present", "status": "pass" if proposal.get("eval_plan") else "fail"},
            ],
            "note": "MVP heuristic. Production must run the golden-task shadow eval with LangSmith experiments before approval.",
        }
        self.storage.update_evolution(state["proposal_id"], status="pending_approval", evaluation=evaluation, proposal={**proposal, "status": "pending_approval"})
        self.artifacts.write_json(state["proposal_id"], "evolution_evaluation.json", evaluation, created_by="eval_red_team")
        await self._event(state["proposal_id"], "evolution.evaluated", evaluation["verdict"], "eval_red_team", evaluation)
        return {"evaluation": evaluation, "status": "approval"}

    async def _approval(self, state: EvolutionState) -> dict[str, Any]:
        proposal = state.get("proposal", {})
        evaluation = state.get("evaluation", {})
        if state.get("auto_approve"):
            decision = {"decision": "approve", "notes": "Auto-approved.", "mode": "auto"}
            self.storage.update_evolution(state["proposal_id"], status="approved", decision=decision)
            return {"approved": True, "decision": decision, "status": "apply"}
        approval = self.storage.pending_approval_for_run(state["proposal_id"], "evolution_approval")
        if not approval:
            approval = self.storage.create_approval(
                state["proposal_id"],
                "evolution_approval",
                "Approve org evolution",
                f"{proposal.get('title', 'Evolution proposal')}\n\n{proposal.get('problem', '')}",
                [
                    {"option_id": "approve", "label": "Approve and apply safe changes", "impact": "Registry version bumps; structural changes still require a deploy."},
                    {"option_id": "reject", "label": "Reject", "impact": "No change."},
                ],
                from_agent="org_evolution_auditor",
                from_codename=self.registry.codename("org_evolution_auditor"),
                sla_minutes=1440,
            )
        await self._event(
            state["proposal_id"],
            "approval.required",
            "Evolution approval required",
            "dictator",
            {"approval_id": approval["approval_id"], "proposal": proposal, "evaluation": evaluation},
        )
        decision = interrupt(
            {
                "type": "evolution_approval",
                "proposal_id": state["proposal_id"],
                "approval_id": approval["approval_id"],
                "title": "Approve org evolution",
                "proposal": proposal,
                "evaluation": evaluation,
                "options": approval.get("options", []),
            }
        )
        if not isinstance(decision, dict):
            decision = {"decision": str(decision)}
        self.storage.decide_approval(approval["approval_id"], decision)
        approved = decision.get("decision") in {"approve", "override"}
        self.storage.update_evolution(state["proposal_id"], status="approved" if approved else "rejected", decision=decision)
        await self._event(
            state["proposal_id"],
            "evolution.decided",
            "Approved" if approved else "Rejected",
            "dictator",
            decision,
        )
        return {"approved": approved, "decision": decision, "status": "apply" if approved else "reject"}

    async def _apply(self, state: EvolutionState) -> dict[str, Any]:
        proposal = state.get("proposal", {})
        result = self.registry.apply_safe_changes(proposal.get("changes", []), state["proposal_id"])
        proposal["status"] = "applied"
        proposal["applied_version"] = result["version"]
        self.storage.update_evolution(
            state["proposal_id"],
            status="applied",
            proposal=proposal,
            decision=state.get("decision"),
        )
        self.artifacts.write_json(state["proposal_id"], "evolution_apply_result.json", result, created_by="architecture_registry_service")
        await self._event(state["proposal_id"], "evolution.applied", f"Registry now {result['version']}", "architecture_registry_service", result)
        return {"apply_result": result, "status": "applied"}

    async def _reject(self, state: EvolutionState) -> dict[str, Any]:
        self.storage.update_evolution(state["proposal_id"], status="rejected", decision=state.get("decision"))
        await self._event(state["proposal_id"], "evolution.rejected", "Proposal rejected", "dictator")
        return {"status": "rejected"}

    def _sanitize(self, result: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        for key, value in result.items():
            if key == "__interrupt__":
                clean[key] = [
                    {"id": getattr(item, "id", None), "value": getattr(item, "value", item)}
                    for item in value or []
                ]
            else:
                try:
                    json.dumps(value, default=str)
                    clean[key] = value
                except TypeError:
                    clean[key] = str(value)
        return clean

    async def start(
        self,
        focus: str = "quality",
        *,
        auto_approve: bool = False,
        backend: str | None = None,
        model: str | None = None,
        proposal_id: str | None = None,
    ) -> dict[str, Any]:
        await self._ensure_graph()
        proposal_id = proposal_id or new_id("evo")
        initial: EvolutionState = {
            "proposal_id": proposal_id,
            "focus": focus,
            "auto_approve": auto_approve,
            "backend": backend or self.settings.model_backend,
            "model": model or self.settings.model_override,
            "status": "observe",
        }
        result = await self.graph.ainvoke(initial, self._config(proposal_id))
        clean = self._sanitize(dict(result))
        self.artifacts.write_json(proposal_id, "evolution_state.json", clean, created_by="org_evolution_auditor")
        return clean

    async def canary(self, proposal_id: str, scope: str = "one_surface", runs: int = 5) -> dict[str, Any]:
        """Small-scope canary observation using recent production-run metrics."""
        existing = self.storage.get_evolution(proposal_id)
        if not existing:
            raise ValueError("proposal not found")
        if existing.get("status") not in {"applied", "approved", "canary", "promoted"}:
            raise ValueError("canary requires an applied proposal")
        recent = self.storage.list_runs(max(1, min(runs, 50)))
        total = len(recent)
        completed = sum(1 for run in recent if run.get("status") == "completed")
        escalated = sum(1 for run in recent if run.get("status") == "escalated")
        failed = sum(1 for run in recent if run.get("status") == "failed")
        pass_rate = (completed / total) if total else 0.0
        verdict = "pass" if total and pass_rate >= 0.5 and failed == 0 else "fail"
        canary = {
            "scope": scope,
            "runs_requested": runs,
            "runs_observed": total,
            "completed": completed,
            "escalated": escalated,
            "failed": failed,
            "pass_rate": round(pass_rate, 3),
            "verdict": verdict,
            "evaluated_at": utcnow(),
            "note": "Automated metric canary over recent production runs. Production should use LangSmith golden tasks for a full shadow eval.",
        }
        proposal = dict(existing.get("proposal") or {})
        proposal["canary"] = canary
        proposal["status"] = "promoted" if verdict == "pass" else "canary_failed"
        self.storage.update_evolution(
            proposal_id,
            status=str(proposal["status"]),
            proposal=proposal,
            decision={"decision": "canary", "notes": f"Canary {verdict}."},
        )
        self.artifacts.write_json(proposal_id, "evolution_canary.json", canary, created_by="eval_red_team")
        await self.bus.publish(
            proposal_id,
            "evolution.canary",
            f"Canary {verdict}: {completed}/{total} completed",
            "eval_red_team",
            canary,
        )
        return canary

    async def promote(self, proposal_id: str) -> dict[str, Any]:
        existing = self.storage.get_evolution(proposal_id)
        if not existing:
            raise ValueError("proposal not found")
        proposal = dict(existing.get("proposal") or {})
        proposal["status"] = "promoted"
        self.storage.update_evolution(
            proposal_id,
            status="promoted",
            proposal=proposal,
            decision={"decision": "promote", "notes": "Canary promoted."},
        )
        await self.bus.publish(proposal_id, "evolution.promoted", "Evolution promoted", "dictator")
        return {"proposal_id": proposal_id, "status": "promoted"}

    async def rollback(self, proposal_id: str) -> dict[str, Any]:
        existing = self.storage.get_evolution(proposal_id)
        if existing and existing.get("status") not in {"applied", "approved", "promoted", "canary_failed"}:
            raise ValueError("rollback requires an applied proposal")
        result = self.registry.rollback(proposal_id)
        self.storage.update_evolution(
            proposal_id,
            status="rolled_back",
            decision={"decision": "rollback", "notes": f"Restored registry {result['rolled_back_to']}."},
        )
        await self.bus.publish(
            proposal_id,
            "evolution.rolled_back",
            f"Registry rolled back to {result['rolled_back_to']}",
            "architecture_registry_service",
            result,
        )
        return result

    async def stop(self, proposal_id: str) -> dict[str, Any]:
        """Stop an evolution run, whether it is executing or paused at approval."""
        pending = self.storage.pending_approval_for_run(proposal_id, "evolution_approval")
        if pending:
            self.storage.decide_approval(
                pending["approval_id"],
                {"decision": "stop", "notes": "Stopped by Dictator."},
                status="stopped",
            )
        existing = self.storage.get_evolution(proposal_id)
        if existing:
            proposal = dict(existing.get("proposal") or {})
            if proposal.get("title") in {"Evolution audit running", "Evolution audit starting"}:
                proposal["title"] = "Evolution stopped before proposal"
                proposal["problem"] = "The Dictator stopped the audit before the independent auditor produced a proposal."
                proposal["status"] = "stopped"
            self.storage.update_evolution(
                proposal_id,
                status="stopped",
                decision={"decision": "stop", "notes": "Stopped by Dictator."},
                proposal=proposal,
            )
        else:
            self.storage.create_evolution(
                {
                    "proposal_id": proposal_id,
                    "title": "Evolution stopped before proposal",
                    "problem": "The Dictator stopped the evolution run before the auditor produced a proposal.",
                    "evidence": [],
                    "changes": [],
                    "expected_impact": {},
                    "risks": [],
                    "eval_plan": [],
                    "rollback_plan": "No change applied.",
                    "status": "stopped",
                },
                focus="stopped",
            )
        await self.bus.publish(
            proposal_id,
            "evolution.stopped",
            "Evolution stopped by Dictator",
            "dictator",
            {"proposal_id": proposal_id},
        )
        return {"proposal_id": proposal_id, "status": "stopped"}

    async def resume(self, proposal_id: str, decision: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_graph()
        result = await self.graph.ainvoke(Command(resume=decision), self._config(proposal_id))
        clean = self._sanitize(dict(result))
        self.artifacts.write_json(proposal_id, "evolution_state.json", clean, created_by="org_evolution_auditor")
        return clean

    async def get_state(self, proposal_id: str) -> dict[str, Any]:
        await self._ensure_graph()
        snapshot = await self.graph.aget_state(self._config(proposal_id))
        interrupts = [
            {"id": getattr(item, "id", None), "value": getattr(item, "value", item)}
            for item in (getattr(snapshot, "interrupts", None) or [])
        ]
        return {
            "values": self._sanitize(dict(snapshot.values)),
            "next": list(snapshot.next or []),
            "interrupts": interrupts,
        }

    async def aclose(self) -> None:
        if self._cm is not None:
            await self._cm.__aexit__(None, None, None)
            self._cm = None
            self.graph = None

    def close(self) -> None:
        """Best-effort synchronous close for short-lived CLI processes."""
        return None
