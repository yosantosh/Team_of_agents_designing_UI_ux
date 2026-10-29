from __future__ import annotations

from ui_ux_company.config import PROJECT_ROOT, Settings
from ui_ux_company.registry import Registry, list_projects, load_budget, load_profile, load_registry, load_yaml, save_yaml


def test_registry_loads_and_validates(settings):
    registry = load_registry(settings)
    assert registry.version == "1.0.0"
    assert len(registry.agents) >= 30
    assert "ceo" in registry.enabled_agent_ids()
    assert "chief_orchestrator" in registry.enabled_agent_ids()
    assert "discovery_lead" in registry.enabled_agent_ids()
    assert "independent_design_critic" in registry.enabled_agent_ids()
    assert registry.agent("ceo")["codename"] == "A1"
    assert registry.agent("chief_orchestrator")["reports_to"] == "ceo"
    assert registry.agent("product_analyst")["reports_to"] == "discovery_lead"
    assert registry.model_alias("reasoner")["model"].startswith("modal/")


def test_profile_and_budget(settings):
    profile = load_profile(settings)
    budget = load_budget(settings)
    assert profile.project["package"] == "com.example.dhunchat"
    assert profile.visual_qa["roborazzi"] is False
    assert profile.target_ui["precedence"] == "tiered"
    assert budget.monthly_total_tokens >= 1_000_000


def test_prompt_update_apply_and_rollback(settings, tmp_path):
    data = load_yaml(settings.registry_path)
    path = tmp_path / "registry.yaml"
    save_yaml(path, data)
    registry = Registry(data, path)
    result = registry.apply_safe_changes(
        [{"type": "prompt_update", "target": "independent_design_critic", "to": "v2 bounded critic"}],
        "evo_test",
    )
    assert result["applied"]
    assert registry.data["prompt_overrides"]["independent_design_critic"] == "v2 bounded critic"
    rolled = registry.rollback("evo_test")
    assert rolled["proposal_id"] == "evo_test"
    assert "prompt_overrides" not in registry.data


def test_project_catalog(settings):
    projects = list_projects(settings)
    assert any(project["id"] == "dhunchat" for project in projects)


def test_mermaid_contains_enabled_agents(settings):
    registry = load_registry(settings)
    mermaid = registry.to_mermaid()
    assert "chief_orchestrator" in mermaid
    assert "independent_design_critic" in mermaid
    assert "flowchart TB" in mermaid
