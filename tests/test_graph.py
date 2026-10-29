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
        assert graph.storage.list_agent_messages(result["run_id"]), "company feed should record handoffs"
        approvals = graph.storage.list_approvals(None, 100)
        design_approvals = [a for a in approvals if a["run_id"] == result["run_id"] and a["gate"] == "human_design_approval"]
        assert design_approvals
        assert design_approvals[0]["from_agent"] == "independent_design_critic"
        run = graph.storage.get_run(result["run_id"])
        assert run["status"] == "completed"
    finally:
        asyncio.run(graph.aclose())
        graph.storage.close()
