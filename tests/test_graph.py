from __future__ import annotations

import asyncio

from ui_ux_company.graph import CompanyGraph


def test_mock_design_run_completes(settings):
    graph = CompanyGraph(settings)
    try:
        result = asyncio.run(
            graph.run(
                "Redesign the profile screen",
                mode="design",
                demo=True,
                auto_approve=True,
                backend="mock",
            )
        )
        assert result["status"] == "completed"
        assert result["critique_gate"] == "pass"
        assert result["eval_gate"] == "pass"
        assert result["release"]["artifact_type"] == "design_package"
        artifacts = graph.storage.list_artifacts(result["run_id"])
        names = {a["name"] for a in artifacts}
        assert "company_brief.json" in names
        assert "ceo_directive.json" in names
        assert "product_brief.json" in names
        assert "screen_spec.json" in names
        assert "design_contract.json" in names
        assert "release_candidate.json" in names
        assert "RELEASE_NOTES.md" in names
        assert "code_edit_result.json" not in names, "design mode must not edit project files"
        assert graph.storage.list_agent_messages(result["run_id"]), "company feed should record handoffs"
        assert graph.storage.list_agent_outputs(result["run_id"]), "agent outputs should be saved"
        approvals = graph.storage.list_approvals(None, 100)
        design_approvals = [a for a in approvals if a["run_id"] == result["run_id"] and a["gate"] == "human_design_approval"]
        assert design_approvals
        assert design_approvals[0]["from_agent"] == "independent_design_critic"
        run = graph.storage.get_run(result["run_id"])
        assert run["status"] == "completed"
        recritique = asyncio.run(graph.recritique(result["run_id"]))
        assert recritique["critique_gate"] in {"pass", "needs_rework", "escalate"}
    finally:
        asyncio.run(graph.aclose())
        graph.storage.close()


class _RecordingCodeWorker:
    enabled = True

    def __init__(self):
        self.calls = []

    async def apply(self, run_id, repo_path, goal, context, intent_packet=None):
        self.calls.append(
            {
                "run_id": run_id,
                "repo_path": str(repo_path),
                "goal": goal,
                "has_packet": bool(intent_packet),
                "packet_ids": [
                    item.get("id") for item in (intent_packet or {}).get("non_negotiables", [])
                ][:3],
                "has_plan": bool(context.get("implementation_plan")),
            }
        )
        return {
            "status": "edited",
            "mode": "worktree",
            "worktree": "/tmp/fake-worktree",
            "branch": f"uiux/{run_id}",
            "changed_files": ["app/src/main/java/Example.kt"],
            "diff_artifact": "code_diff.patch",
        }


def test_code_worker_failure_routes_to_escalation(settings):
    graph = CompanyGraph(settings)
    try:
        assert graph._route_after_implement({"implementation": {"code_edits": {"status": "failed"}}}) == "escalate"
        assert graph._route_after_implement({"implementation": {"code_edits": {"status": "no_changes"}}}) == "escalate"
        assert graph._route_after_implement({"implementation": {"code_edits": {"status": "edited"}}}) == "qa"
        assert graph._route_after_implement({"implementation": {"status": "skipped"}}) == "qa"
    finally:
        asyncio.run(graph.aclose())
        graph.storage.close()


def test_build_run_hands_approved_design_to_code_worker(settings):
    worker = _RecordingCodeWorker()
    graph = CompanyGraph(settings, code_worker=worker)
    try:
        result = asyncio.run(
            graph.run(
                "Implement the approved profile screen",
                mode="build",
                demo=False,
                auto_approve=True,
                backend="mock",
            )
        )
        assert len(worker.calls) >= 1
        assert all(call["has_packet"] for call in worker.calls)
        assert all(call["has_plan"] for call in worker.calls)
        assert result["implementation"]["code_edits"]["status"] == "edited"
        artifacts = {a["name"] for a in graph.storage.list_artifacts(result["run_id"])}
        assert "implementation_plan.json" in artifacts
        assert "intent_packet.json" in artifacts
    finally:
        asyncio.run(graph.aclose())
        graph.storage.close()


def test_company_context_is_short_and_shared(settings):
    graph = CompanyGraph(settings)
    try:
        graph.storage.create_run("run_ctx", "Redesign the feed", "design")
        graph.storage.add_agent_message(
            "run_ctx", "chief_orchestrator", "visual_ui_designer", "handoff",
            did="Assigned Screen spec", doing="Designing the feed",
        )
        graph.storage.add_agent_message(
            "run_ctx", "visual_ui_designer", "design_lead", "artifact",
            did="Completed Screen spec", doing="Handing result back",
        )
        context = graph._company_context(
            {"run_id": "run_ctx", "prompt": "Redesign the feed", "mode": "design", "status": "design_system"}
        )
        assert context["recent_activity"]
        assert any("Visual UI Designer" in item for item in context["recent_activity"])
        assert "do not repeat" in context["note"]
        assert context["stage"] == "design_system"
    finally:
        asyncio.run(graph.aclose())
        graph.storage.close()


def test_escalation_has_easy_summary_and_expandable_details(settings):
    graph = CompanyGraph(settings)
    try:
        graph.storage.create_run("run_escalate", "Redesign the feed", "build")
        result = asyncio.run(
            graph._escalate(
                {
                    "run_id": "run_escalate",
                    "critique": {
                        "verdict": "ESCALATE",
                        "score": 0.42,
                        "issues": [
                            {
                                "severity": "critical",
                                "area": "tokens",
                                "problem": "Accent text contrast is below 4.5:1.",
                                "fix": "Use a text-safe accent.",
                            }
                        ],
                    },
                    "qa": {},
                    "implementation": {},
                }
            )
        )
        approval = graph.storage.get_approval(result["approval_id"])
        assert approval["summary"].startswith("The design was stopped before coding")
        assert "What happened" in approval["details"]
        assert "Accent text contrast" in approval["details"]
        assert "What you can do" in approval["details"]
        assert approval["message"] == approval["summary"]
    finally:
        asyncio.run(graph.aclose())
        graph.storage.close()

def test_failed_run_can_resume_from_checkpoint(settings):
    graph = CompanyGraph(settings)
    original_synthesis = graph._synthesis
    calls = {"count": 0}

    async def flaky_synthesis(state):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary synthesis failure")
        return await original_synthesis(state)

    graph._synthesis = flaky_synthesis
    try:
        result = asyncio.run(
            graph.run(
                "Resume the profile screen implementation",
                mode="build",
                demo=True,
                auto_approve=True,
                backend="mock",
            )
        )
        assert result["status"] == "failed"
        run_id = result["run_id"]
        snapshot = asyncio.run(graph.get_state(run_id))
        assert "synthesis" in snapshot["next"]
        resumed = asyncio.run(graph.resume_run(run_id))
        assert resumed["status"] == "completed"
        assert calls["count"] >= 2
        assert graph.storage.list_agent_outputs(run_id)
    finally:
        asyncio.run(graph.aclose())
        graph.storage.close()


def test_escalated_design_run_can_force_resume(settings):
    graph = CompanyGraph(settings)
    original_critique = graph._critique

    async def escalating_critique(state):
        return {
            "critique": {
                "verdict": "ESCALATE",
                "score": 0.4,
                "issues": [
                    {
                        "severity": "critical",
                        "area": "tokens",
                        "problem": "Blocking issue",
                        "fix": "Fix it",
                    }
                ],
            },
            "critique_gate": "escalate",
            "rework_counts": state.get("rework_counts", {"design": 0, "implementation": 0}),
            "status": "human_gate",
        }

    graph._critique = escalating_critique
    try:
        result = asyncio.run(
            graph.run(
                "Force-continue this design",
                mode="build",
                demo=True,
                auto_approve=True,
                backend="mock",
            )
        )
        assert result["status"] == "escalated"
        resumed = asyncio.run(graph.resume_escalated(result["run_id"], {"decision": "override", "notes": "test"}))
        assert resumed["status"] == "completed"
        assert resumed["human_decision"]["decision"] == "override"
    finally:
        asyncio.run(graph.aclose())
        graph.storage.close()


def test_decline_on_major_escalation_halts_without_dictate(settings):
    graph = CompanyGraph(settings)

    async def critical_critique(state):
        return {
            "critique": {
                "verdict": "ESCALATE",
                "score": 0.3,
                "issues": [
                    {"severity": "critical", "area": f"area {i}", "problem": "problem", "fix": "fix"}
                    for i in range(4)
                ],
            },
            "critique_gate": "escalate",
            "rework_counts": state.get("rework_counts", {"design": 0, "implementation": 0}),
            "status": "human_gate",
        }

    graph._critique = critical_critique
    try:
        result = asyncio.run(
            graph.run("Major decision", mode="build", demo=True, auto_approve=True, backend="mock")
        )
        assert result["status"] == "escalated"
        halted = asyncio.run(graph.resume_escalated(result["run_id"], {"decision": "reject", "notes": ""}))
        assert halted["status"] == "stopped"
        assert "halted" in halted["reason"]
    finally:
        asyncio.run(graph.aclose())
        graph.storage.close()


def test_decline_with_dictate_requests_rework_and_continues(settings):
    graph = CompanyGraph(settings)
    original_critique = graph._critique
    calls = {"count": 0}

    async def flaky_critique(state):
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "critique": {
                    "verdict": "ESCALATE",
                    "score": 0.4,
                    "issues": [
                        {"severity": "critical", "area": "layout", "problem": "problem", "fix": "fix"}
                    ],
                },
                "critique_gate": "escalate",
                "rework_counts": state.get("rework_counts", {"design": 0, "implementation": 0}),
                "status": "human_gate",
            }
        return await original_critique(state)

    graph._critique = flaky_critique
    try:
        result = asyncio.run(
            graph.run("Try a safer design", mode="build", demo=True, auto_approve=True, backend="mock")
        )
        assert result["status"] == "escalated"
        resumed = asyncio.run(
            graph.resume_escalated(
                result["run_id"],
                {"decision": "reject", "notes": "Use a simpler layout and keep the current top bar."},
            )
        )
        assert resumed["status"] == "completed"
        assert calls["count"] >= 2
        assert resumed["rework_counts"]["design"] >= 1
    finally:
        asyncio.run(graph.aclose())
        graph.storage.close()
