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

    async def apply(self, run_id, repo_path, goal, context):
        self.calls.append(
            {
                "run_id": run_id,
                "repo_path": str(repo_path),
                "goal": goal,
                "has_contract": bool(context.get("design_contract")),
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
        assert all(call["has_contract"] for call in worker.calls)
        assert result["implementation"]["code_edits"]["status"] == "edited"
        artifacts = {a["name"] for a in graph.storage.list_artifacts(result["run_id"])}
        assert "implementation_plan.json" in artifacts
    finally:
        asyncio.run(graph.aclose())
        graph.storage.close()