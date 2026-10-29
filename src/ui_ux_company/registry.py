from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .config import Settings
from .models import utcnow


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=110),
        encoding="utf-8",
    )


@dataclass(slots=True)
class Registry:
    data: dict[str, Any]
    path: Path

    @property
    def version(self) -> str:
        return str(self.data.get("version", "0.0.0"))

    @property
    def agents(self) -> list[dict[str, Any]]:
        return list(self.data.get("agents", []))

    @property
    def enabled_agents(self) -> list[dict[str, Any]]:
        return [agent for agent in self.agents if agent.get("enabled", False)]

    @property
    def models(self) -> dict[str, Any]:
        return dict(self.data.get("models", {}))

    @property
    def defaults(self) -> dict[str, Any]:
        return dict(self.data.get("defaults", {}))

    @property
    def pods(self) -> list[dict[str, Any]]:
        return list(self.data.get("pods", []))

    @property
    def gates(self) -> list[dict[str, Any]]:
        return list(self.data.get("gates", []))

    def agent(self, agent_id: str) -> dict[str, Any]:
        for agent in self.agents:
            if agent.get("id") == agent_id:
                return agent
        raise KeyError(f"unknown agent: {agent_id}")

    def model_alias(self, alias: str) -> dict[str, Any]:
        try:
            return dict(self.models[alias])
        except KeyError as exc:
            raise KeyError(f"unknown model alias: {alias}") from exc

    def enabled_agent_ids(self) -> list[str]:
        return [str(a["id"]) for a in self.enabled_agents]

    def tier_agents(self) -> dict[int, list[dict[str, Any]]]:
        tiers: dict[int, list[dict[str, Any]]] = {}
        for agent in self.agents:
            tiers.setdefault(int(agent.get("tier", 99)), []).append(agent)
        return dict(sorted(tiers.items()))

    def validate(self, schema_path: Path) -> None:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.validate(self.data, schema)

    def summary(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "organization": self.data.get("organization"),
            "owner": self.data.get("owner"),
            "agents": len(self.agents),
            "enabled_agents": len(self.enabled_agents),
            "tiers": len(self.data.get("tiers", [])),
            "pods": len(self.pods),
            "gates": len(self.gates),
            "default_autonomy": self.defaults.get("autonomy_level", "L2"),
        }

    def to_mermaid(self) -> str:
        lines = ["flowchart TB"]
        tiers = self.tier_agents()
        for tier, agents in tiers.items():
            lines.append(f'  subgraph T{tier}["Tier {tier}"]')
            for agent in agents:
                if not agent.get("enabled", False):
                    continue
                label = str(agent.get("title", agent["id"])).replace('"', "'")
                lines.append(f'    {agent["id"]}["{label}"]')
            lines.append("  end")
        lines.append('  Dictator["Dictator (Human Product Owner)"] --> chief_orchestrator')
        for tier, agents in tiers.items():
            if tier == 0:
                continue
            for agent in agents:
                if agent.get("enabled", False):
                    lines.append(f"  chief_orchestrator --> {agent['id']}")
        enabled = [a["id"] for tier, agents in tiers.items() if tier != 0 for a in agents if a.get("enabled")]
        for index, agent_id in enumerate(enabled):
            if index % 4 == 0 and index:
                continue
            lines.append(f"  {agent_id} --> artifacts[(\"Versioned artifacts\")]")
        lines.append('  artifacts --> Dictator')
        return "\n".join(lines)

    def apply_safe_changes(self, changes: list[dict[str, Any]], proposal_id: str) -> dict[str, Any]:
        """Apply only low-risk registry mutations; leave the live graph rebuild to deployment.

        Supported: enable_agent, disable_agent, model_swap (alias target), gate_threshold
        stored in defaults, budget_cap stored in defaults. Other change types are recorded
        in evolution_history but require a human/code change.
        """
        backup_dir = self.path.parent / "registry_history"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{self.version}.yaml"
        if not backup.exists():
            shutil.copy2(self.path, backup)

        applied: list[dict[str, Any]] = []
        for change in changes:
            kind = change.get("type")
            target = change.get("target")
            to_value = change.get("to")
            if kind == "enable_agent":
                for agent in self.agents:
                    if agent.get("id") == target:
                        agent["enabled"] = True
                        applied.append(change)
            elif kind == "disable_agent":
                for agent in self.agents:
                    if agent.get("id") == target:
                        agent["enabled"] = False
                        applied.append(change)
            elif kind == "model_swap":
                if target in self.models and isinstance(to_value, str):
                    self.models[target]["model"] = to_value
                    self.data["models"] = self.models
                    applied.append(change)
            elif kind == "gate_threshold":
                self.data.setdefault("defaults", {})[str(target)] = to_value
                applied.append(change)
            elif kind == "budget_cap":
                self.data.setdefault("defaults", {})[str(target)] = to_value
                applied.append(change)

        parts = self.version.split(".")
        while len(parts) < 3:
            parts.append("0")
        try:
            parts[-1] = str(int(parts[-1]) + 1)
        except ValueError:
            parts.append("1")
        new_version = ".".join(parts)
        self.data["version"] = new_version
        history = self.data.setdefault("evolution_history", [])
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "proposal_id": proposal_id,
                "applied_at": utcnow(),
                "from_version": backup.stem,
                "to_version": new_version,
                "changes_requested": changes,
                "changes_applied": applied,
                "note": "Safe mutations only. Graph rebuild/redeploy is required for structural changes.",
            }
        )
        self.data["evolution_history"] = history
        save_yaml(self.path, self.data)
        return {
            "version": new_version,
            "applied": applied,
            "requested": changes,
            "backup": str(backup),
        }


@dataclass(slots=True)
class ProjectProfile:
    data: dict[str, Any]
    path: Path

    @property
    def project(self) -> dict[str, Any]:
        return dict(self.data.get("project", {}))

    @property
    def target_ui(self) -> dict[str, Any]:
        return dict(self.data.get("target_ui", {}))

    @property
    def autonomy(self) -> dict[str, Any]:
        return dict(self.data.get("autonomy", {}))

    @property
    def budget(self) -> dict[str, Any]:
        return dict(self.data.get("budget", {}))

    @property
    def design_system(self) -> dict[str, Any]:
        return dict(self.data.get("design_system", {}))

    @property
    def visual_qa(self) -> dict[str, Any]:
        return dict(self.data.get("visual_qa", {}))

    @property
    def android_quality(self) -> dict[str, Any]:
        return dict(self.data.get("android_quality", {}))


@dataclass(slots=True)
class BudgetPolicy:
    data: dict[str, Any]
    path: Path

    @property
    def monthly_total_tokens(self) -> int:
        return int(self.data.get("monthly_total_tokens", 100_000_000))

    @property
    def per_run_tokens(self) -> int:
        return int(self.data.get("caps", {}).get("per_run_tokens", 2_000_000))

    @property
    def at_100_pct(self) -> str:
        return str(self.data.get("thresholds", {}).get("at_100_pct", "hard_stop_noncritical"))


def load_registry(settings: Settings, validate: bool = True) -> Registry:
    registry = Registry(load_yaml(settings.registry_path), settings.registry_path)
    if validate:
        registry.validate(settings.registry_schema_path)
    return registry


def load_profile(settings: Settings) -> ProjectProfile:
    return ProjectProfile(load_yaml(settings.profile_path), settings.profile_path)


def load_budget(settings: Settings) -> BudgetPolicy:
    return BudgetPolicy(load_yaml(settings.budget_path), settings.budget_path)


def discover_target_ui_path(settings: Settings, profile: ProjectProfile) -> Path | None:
    """Resolve Target_ui path from profile candidates; first existing non-empty folder wins."""
    config = profile.target_ui
    candidates: list[str] = []
    if config.get("path"):
        candidates.append(str(config["path"]))
    candidates.extend(str(p) for p in config.get("candidate_paths", []))
    for raw in candidates:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = settings.root / path
        if path.exists() and path.is_dir():
            images = [
                p
                for p in path.iterdir()
                if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            ]
            if images:
                return path
    return None
