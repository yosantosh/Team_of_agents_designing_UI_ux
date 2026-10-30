from __future__ import annotations

from pathlib import Path

from ui_ux_company.app_plan import read_app_plan, search_app_plans, write_app_plan
from ui_ux_company.registry import load_profile


def test_app_plan_browse_save_and_search(settings, tmp_path):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    settings.root = tmp_path
    settings.org_dir = tmp_path / "org"
    settings.org_dir.mkdir()
    settings.project_dir = repo
    profile = load_profile(settings)
    profile.data.setdefault("project", {})["repo"] = str(repo)
    profile.data["project"]["app_plan_path"] = "docs/app_plan.md"

    saved = write_app_plan(settings, profile, "# App Plan\n\n- Build the feed\n- Keep OLED black\n", "docs/app_plan.md")
    assert saved["exists"] is True
    assert "Build the feed" in saved["content"]
    assert "App Plan" in saved["headings"]

    loaded = read_app_plan(settings, profile)
    assert loaded["sha256"] == saved["sha256"]

    found = search_app_plans(settings, profile, "plan")
    assert any(item["name"] == "app_plan.md" for item in found)
