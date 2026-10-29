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
from .prompts import WORK_LABELS


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

    def __post_init__(self) -> None:
        self._enrich()

    def _enrich(self) -> None:
        """Assign stable codenames and a command chain if the registry omits them."""
        agents = list(self.data.get("agents", []))
        tier_leads = {int(k): str(v) for k, v in (self.data.get("tier_leads") or {}).items()}
        if not tier_leads:
            for lead in agents:
                if lead.get("is_lead") and int(lead.get("tier", 0)) > 0:
                    tier_leads[int(lead["tier"])] = str(lead["id"])
            self.data["tier_leads"] = {str(k): v for k, v in tier_leads.items()}
        used_codenames = {str(a.get("codename")) for a in agents if a.get("codename")}
        next_number = 1
        for agent in agents:
            agent_id = str(agent.get("id"))
            if not agent.get("codename"):
                while f"A{next_number}" in used_codenames:
                    next_number += 1
                agent["codename"] = f"A{next_number}"
                used_codenames.add(agent["codename"])
                next_number += 1
            if not agent.get("work_label"):
                agent["work_label"] = WORK_LABELS.get(agent_id, agent.get("title", agent_id))
            if "reports_to" not in agent:
                if agent_id == "ceo":
                    agent["reports_to"] = None
                elif agent_id == "chief_orchestrator":
                    agent["reports_to"] = "ceo"
                elif agent_id in tier_leads.values():
                    agent["reports_to"] = "chief_orchestrator"
                else:
                    tier = int(agent.get("tier", 0))
                    agent["reports_to"] = tier_leads.get(tier, "chief_orchestrator" if tier == 0 else None)
        reports: dict[str, list[str]] = {}
        for agent in agents:
            boss = agent.get("reports_to")
            if boss:
                reports.setdefault(str(boss), []).append(str(agent["id"]))
        for agent in agents:
            if agent.get("is_lead") or agent["id"] in {"ceo", "chief_orchestrator"}:
                existing = [str(x) for x in agent.get("manages", [])]
                if not existing:
                    agent["manages"] = reports.get(str(agent["id"]), [])
        self.data["agents"] = agents

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

    @property
    def tier_leads(self) -> dict[int, str]:
        return {int(k): str(v) for k, v in (self.data.get("tier_leads") or {}).items()}

    def codename(self, agent_id: str) -> str:
        try:
            return str(self.agent(agent_id).get("codename") or agent_id)
        except KeyError:
            return agent_id

    def agent_by_codename(self, codename: str) -> dict[str, Any] | None:
        for agent in self.agents:
            if str(agent.get("codename", "")).upper() == codename.upper():
                return agent
        return None

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
            "leads": sum(1 for a in self.agents if a.get("is_lead")),
            "tiers": len(self.data.get("tiers", [])),
            "pods": len(self.pods),
            "gates": len(self.gates),
            "default_autonomy": self.defaults.get("autonomy_level", "L2"),
        }

    def org_brief(self) -> dict[str, Any]:
        """Compact, non-prompt org knowledge injected into the Chief/CEO."""
        return {
            "version": self.version,
            "organization": self.data.get("organization"),
            "owner": self.data.get("owner"),
            "tier_leads": self.tier_leads,
            "agents": [
                {
                    "id": a.get("id"),
                    "codename": a.get("codename"),
                    "title": a.get("title"),
                    "tier": a.get("tier"),
                    "enabled": a.get("enabled"),
                    "is_lead": a.get("is_lead", False),
                    "reports_to": a.get("reports_to"),
                    "manages": a.get("manages", []),
                    "mission": a.get("mission"),
                    "work_label": a.get("work_label"),
                    "inputs": a.get("inputs", []),
                    "outputs": a.get("outputs", []),
                    "gates": a.get("gates", []),
                }
                for a in self.agents
            ],
            "gates": [
                {
                    "id": g.get("id"),
                    "owner": g.get("owner"),
                    "required": g.get("required", False),
                    "sla_minutes": g.get("sla_minutes"),
                    "pass_criteria": g.get("pass_criteria"),
                }
                for g in self.gates
            ],
            "playbooks": self.data.get("playbooks", {}),
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

    def rollback(self, proposal_id: str) -> dict[str, Any]:
        """Restore the registry version recorded as the 'from' version of an applied proposal."""
        history = self.data.get("evolution_history", [])
        entry = next((item for item in reversed(history) if item.get("proposal_id") == proposal_id), None)
        if not entry:
            raise ValueError("no applied registry change found for this proposal")
        from_version = str(entry.get("from_version") or "")
        backup = self.path.parent / "registry_history" / f"{from_version}.yaml"
        if not backup.exists():
            raise ValueError(f"registry backup not found: {backup}")
        backup_data = load_yaml(backup)
        current_version = self.version
        # Keep an audit trail of the rollback itself.
        history = list(self.data.get("evolution_history", []))
        history.append(
            {
                "proposal_id": proposal_id,
                "type": "rollback",
                "applied_at": utcnow(),
                "from_version": current_version,
                "to_version": from_version,
            }
        )
        backup_data["evolution_history"] = history
        self.data = backup_data
        self._enrich()
        save_yaml(self.path, self.data)
        return {"proposal_id": proposal_id, "rolled_back_to": from_version, "previous_version": current_version}

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
        skipped: list[dict[str, Any]] = []
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
            elif kind == "prompt_update":
                if target and to_value:
                    overrides = self.data.setdefault("prompt_overrides", {})
                    overrides[str(target)] = str(to_value)
                    applied.append(change)
            elif kind == "gate_threshold":
                gate = next((g for g in self.gates if str(g.get("id")) == str(target)), None)
                if gate is not None and isinstance(to_value, dict):
                    gate.update(to_value)
                    applied.append(change)
                elif str(target) in (self.data.get("defaults") or {}) and isinstance(to_value, (int, float, bool)):
                    self.data["defaults"][str(target)] = to_value
                    applied.append(change)
                else:
                    skipped.append(
                        {
                            **change,
                            "reason": "gate_threshold requires a gate id with a structured dict or an existing numeric default key",
                        }
                    )
            elif kind == "budget_cap":
                if str(target) in (self.data.get("defaults") or {}) and isinstance(to_value, (int, float)):
                    self.data["defaults"][str(target)] = to_value
                    applied.append(change)
                else:
                    skipped.append(
                        {
                            **change,
                            "reason": "budget_cap requires an existing numeric default key",
                        }
                    )
            elif kind == "add_agent":
                spec = to_value if isinstance(to_value, dict) else {}
                agent_id = str(spec.get("id") or "").strip()
                agents = self.data.setdefault("agents", [])
                if agent_id and not any(a.get("id") == agent_id for a in agents):
                    agent = {
                        "id": agent_id,
                        "title": str(spec.get("title") or agent_id.replace("_", " ").title()),
                        "tier": int(spec.get("tier", 1)),
                        "enabled": True,
                        "prompt_key": str(spec.get("prompt_key", "lead")),
                        "model": str(spec.get("model", "reasoner")),
                        "reports_to": spec.get("reports_to", "chief_orchestrator"),
                        "is_lead": bool(spec.get("is_lead", False)),
                        "mission": str(spec.get("mission", "Specialist added by an approved evolution proposal.")),
                        "inputs": spec.get("inputs", []),
                        "outputs": spec.get("outputs", []),
                        "tools": spec.get("tools", ["artifact_store"]),
                        "gates": spec.get("gates", []),
                        "kpis": spec.get("kpis", []),
                        "failure_modes": spec.get("failure_modes", []),
                    }
                    agents.append(agent)
                    self.data["agents"] = agents
                    applied.append(change)

        self._enrich()
        if not applied:
            return {
                "version": self.version,
                "applied": [],
                "skipped": skipped,
                "requested": changes,
                "backup": str(backup),
            }
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
            "skipped": skipped,
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


def list_projects(settings: Settings) -> list[dict[str, Any]]:
    directory = settings.org_dir / "projects"
    if not directory.exists():
        return []
    projects = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            data = load_yaml(path)
        except Exception:
            continue
        project = data.get("project", {})
        projects.append(
            {
                "id": path.stem,
                "name": project.get("name", path.stem),
                "repo": project.get("repo"),
                "package": project.get("package"),
                "path": str(path),
            }
        )
    return projects


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
