from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


class FlexModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class ChangeRequest(FlexModel):
    run_id: str
    prompt: str
    change_type: str = "redesign"
    app_archetype: str = "hybrid_proximity_social"
    target_surfaces: list[str] = Field(default_factory=list)
    target_screens: list[str] = Field(default_factory=list)
    success_metrics: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    risk: Literal["low", "medium", "high", "critical"] = "medium"
    autonomy_level: str = "L2"
    acceptance_criteria: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class TaskContract(FlexModel):
    task_id: str
    run_id: str | None = None
    issued_by: str = "chief_orchestrator"
    assigned_role: str
    model_binding: str | None = None
    worker_runtime: str = "opencode"
    goal: str
    inputs: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    output_artifacts: list[str] = Field(default_factory=list)
    risk: str = "medium"
    budget_tokens: int | None = None
    rework_count: int = 0
    max_retries: int = 2
    status: str = "pending"
    evidence: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utcnow)


class GateResult(FlexModel):
    gate: str
    status: Literal["pass", "needs_rework", "escalate", "not_run", "skipped", "simulated"] = "not_run"
    score: float | None = None
    issues: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    reviewer: str | None = None
    simulated: bool = False
    notes: str | None = None


class Artifact(FlexModel):
    artifact_id: str
    run_id: str
    name: str
    path: str
    content_type: str = "application/json"
    sha256: str
    created_by: str = "system"
    created_at: str = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TargetImage(FlexModel):
    image_id: str
    file: str
    sha256: str
    width: int | None = None
    height: int | None = None
    screen: str | None = None
    state: str = "default"
    theme: str = "dark"
    status: Literal["unanalyzed", "analyzed", "low_confidence", "conflict", "approved"] = "unanalyzed"
    confidence: float = 0.0
    extracted: dict[str, Any] = Field(default_factory=dict)
    annotations: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    rights: str = "reference_only"


class TargetManifest(FlexModel):
    version: str = "1.0"
    generated_at: str = Field(default_factory=utcnow)
    source_path: str | None = None
    precedence: str = "tiered"
    confidence_threshold: float = 0.8
    images: list[TargetImage] = Field(default_factory=list)
    status: str = "unknown"
    notes: list[str] = Field(default_factory=list)


class EvolutionProposal(FlexModel):
    proposal_id: str
    title: str
    problem: str
    evidence: list[str] = Field(default_factory=list)
    changes: list[dict[str, Any]] = Field(default_factory=list)
    expected_impact: dict[str, Any] = Field(default_factory=dict)
    risks: list[str] = Field(default_factory=list)
    eval_plan: list[str] = Field(default_factory=list)
    rollback_plan: str = "Restore the previous registry version from org/registry_history/."
    status: str = "proposed"
    created_at: str = Field(default_factory=utcnow)
    metrics: dict[str, Any] = Field(default_factory=dict)


class RunRequest(FlexModel):
    prompt: str = Field(min_length=3)
    mode: Literal["explore", "design", "build", "ship", "audit", "fix"] = "design"
    target_ui_enabled: bool | None = None
    auto_approve: bool | None = None
    demo: bool = False
    backend: Literal["opencode", "mock"] | None = None
    model: str | None = None
    autonomy_level: str | None = None


class ApprovalDecision(FlexModel):
    decision: Literal["approve", "reject", "override"]
    option_id: str | None = None
    notes: str | None = None


class EvolutionRequest(FlexModel):
    focus: str = "quality"
    auto_approve: bool | None = None


class ModelResult(FlexModel):
    text: str
    data: dict[str, Any] | None = None
    model: str
    role: str
    session_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    raw_events: list[dict[str, Any]] = Field(default_factory=list)
    estimated: bool = True


class AgentResult(FlexModel):
    role: str
    data: dict[str, Any]
    text: str
    model: str
    usage: dict[str, Any] = Field(default_factory=dict)
    artifact_path: str | None = None


def json_dumps(data: Any, indent: int | None = 2) -> str:
    return json.dumps(data, ensure_ascii=False, indent=indent, default=str)


def extract_json(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction from a model response."""
    raw = text.strip()
    if not raw:
        raise ValueError("empty model response")
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        candidate = raw[start : end + 1]
        value = json.loads(candidate)
        if isinstance(value, dict):
            return value
    raise ValueError(f"model did not return a JSON object: {raw[:300]}")


def estimate_tokens(text: str, images: int = 0) -> int:
    return max(1, len(text) // 4) + images * 1200


def write_json_file(path: Path, data: Any) -> Artifact:
    payload = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload + "\n", encoding="utf-8")
    return Artifact(
        artifact_id=new_id("art"),
        run_id=path.parent.name,
        name=path.name,
        path=str(path),
        content_type="application/json",
        sha256=sha256_text(payload),
    )
