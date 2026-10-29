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
        assert "product_brief.json" in names
        assert "screen_spec.json" in names
        assert "design_contract.json" in names
        assert "release_candidate.json" in names
        assert "RELEASE_NOTES.md" in names
        run = graph.storage.get_run(result["run_id"])
        assert run["status"] == "completed"
    finally:
        asyncio.run(graph.aclose())
        graph.storage.close()
