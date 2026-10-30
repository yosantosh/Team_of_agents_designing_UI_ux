from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import utcnow


PACKET_VERSION = "1.0"
DEFAULT_MAX_CHARS = 160_000


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _clip(value: Any, limit: int = 500) -> str:
    text = value if isinstance(value, str) else _compact(value)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _take(value: Any, limit: int) -> Any:
    if value is None:
        return []
    if isinstance(value, list):
        return value[:limit]
    return value


def _hash(value: Any) -> str:
    return hashlib.sha256(_compact(value).encode("utf-8")).hexdigest()[:16]


def _relevant_images(manifest: dict[str, Any], product_brief: dict[str, Any], screen_spec: dict[str, Any]) -> list[dict[str, Any]]:
    images = list(manifest.get("images") or [])
    targets = " ".join(
        [str(item) for item in (product_brief.get("target_screens") or [])]
        + [str(screen_spec.get("screen") or "")]
    ).lower()
    selected = []
    for image in images:
        screen = str(image.get("screen") or "").lower()
        if screen and screen in targets:
            selected.append(image)
    return (selected or images)[:4]


def _compact_image(image: dict[str, Any]) -> dict[str, Any]:
    extracted = image.get("extracted") or {}
    return {
        "id": image.get("image_id"),
        "screen": image.get("screen"),
        "state": image.get("state"),
        "confidence": image.get("confidence"),
        "status": image.get("status"),
        "annotations": image.get("annotations") or [],
        "conflicts": image.get("conflicts") or [],
        "layout": extracted.get("layout") or {},
        "components": _take(extracted.get("components"), 12),
        "visual_roles": extracted.get("visual_roles") or {},
        "states": _take(extracted.get("states"), 12),
    }


def build_intent_packet(
    state: dict[str, Any],
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    """Build a compact, authoritative residual context packet for implementation.

    The packet is deliberately selective. It carries exact intent and visual truth
    from the important upstream tiers, not the full company transcript.
    """
    product_brief = state.get("change_request") or {}
    ceo = state.get("ceo_directive") or {}
    research = state.get("research") or {}
    android = research.get("android") or {}
    synthesis = state.get("synthesis") or {}
    manifest = state.get("target_manifest") or {}
    tokens = state.get("design_tokens") or {}
    screen_spec = state.get("screen_spec") or {}
    contract = state.get("design_contract") or {}
    critique = state.get("critique") or {}
    tier_reviews = state.get("tier_reviews") or {}

    constraints: list[dict[str, Any]] = []
    for source, items in (
        ("product_brief.constraints", product_brief.get("constraints") or []),
        ("ceo_directive.constraints", ceo.get("constraints") or []),
    ):
        for item in items[:10]:
            constraints.append(
                {
                    "id": f"NN-{len(constraints) + 1}",
                    "text": _clip(item, 420),
                    "source": source,
                }
            )

    issues = []
    deferred = []
    for issue in critique.get("issues") or []:
        severity = str(issue.get("severity") or "").lower()
        is_blocking = severity in {"critical", "major"}
        payload = {
            "id": f"C-{len(issues) + 1}" if is_blocking else f"M-{len(deferred) + 1}",
            "severity": severity or "issue",
            "area": issue.get("area"),
            "problem": _clip(issue.get("problem"), 650),
            "fix": _clip(issue.get("fix"), 650),
            "evidence": _clip(issue.get("evidence"), 350),
        }
        if is_blocking:
            issues.append(payload)
        else:
            deferred.append(payload)

    review_packet: dict[str, Any] = {}
    for tier, review in sorted(tier_reviews.items(), key=lambda item: str(item[0])):
        if not isinstance(review, dict):
            continue
        review_packet[f"tier_{tier}"] = {
            "status": review.get("status"),
            "summary": _clip(review.get("summary"), 700),
            "blockers": _take(review.get("blockers"), 6),
            "escalation_reason": _clip(review.get("escalation_reason"), 500),
            "feed_message": _clip(review.get("feed_message"), 300),
        }

    packet: dict[str, Any] = {
        "version": PACKET_VERSION,
        "run_id": state.get("run_id"),
        "mode": state.get("mode"),
        "generated_at": utcnow(),
        "precedence": [
            "dictator_decree",
            "human_decisions",
            "design_contract.authoritative_decisions",
            "target_fidelity_and_annotations",
            "android_platform_law_and_accessibility",
            "screen_spec",
            "implementation_plan",
        ],
        "north_star": {
            "decree": _clip(product_brief.get("prompt") or state.get("prompt"), 700),
            "change_type": product_brief.get("change_type"),
            "app_archetype": product_brief.get("app_archetype"),
            "target_surfaces": [_clip(item, 300) for item in _take(product_brief.get("target_surfaces"), 8)],
            "target_screens": [_clip(item, 300) for item in _take(product_brief.get("target_screens"), 10)],
            "problem": _clip(product_brief.get("problem"), 1_000),
            "users": [_clip(item, 350) for item in _take(product_brief.get("users"), 6)],
            "success_metrics": [_clip(item, 400) for item in _take(product_brief.get("success_metrics"), 8)],
            "non_goals": [_clip(item, 350) for item in _take(product_brief.get("non_goals"), 8)],
            "acceptance_criteria": [_clip(item, 450) for item in _take(product_brief.get("acceptance_criteria"), 10)],
            "risk": product_brief.get("risk"),
        },
        "non_negotiables": constraints,
        "ceo_directive": {
            "directive": _clip(ceo.get("directive"), 1_400),
            "priorities": [_clip(item, 380) for item in _take(ceo.get("priorities"), 8)],
            "constraints": [_clip(item, 380) for item in _take(ceo.get("constraints"), 8)],
            "escalation_reason": _clip(ceo.get("escalation_reason"), 700),
        },
        "ux_contract": {
            "navigation": [_clip(item, 350) for item in _take(synthesis.get("navigation"), 6)],
            "flows": _take(synthesis.get("flows"), 4),
            "screen_inventory": [_clip(item, 350) for item in _take(synthesis.get("screen_inventory"), 6)],
            "state_matrix": synthesis.get("state_matrix") or {},
            "interaction_rules": [_clip(item, 380) for item in _take(synthesis.get("interaction_rules"), 10)],
        },
        "platform_constraints": {
            "constraints": _take(android.get("constraints"), 10),
            "required_states": [_clip(item, 350) for item in _take(android.get("required_states"), 10)],
            "adaptive_risks": [_clip(item, 350) for item in _take(android.get("adaptive_risks"), 8)],
            "verification_commands": [_clip(item, 300) for item in _take(android.get("verification_commands"), 10)],
        },
        "visual_truth": {
            "screen": screen_spec.get("screen"),
            "goal": _clip(screen_spec.get("goal"), 700),
            "hierarchy": [_clip(item, 350) for item in _take(screen_spec.get("hierarchy"), 10)],
            "layout": screen_spec.get("layout") or {},
            "components": [_clip(item, 350) for item in _take(screen_spec.get("components"), 15)],
            "token_usage": [_clip(item, 350) for item in _take(screen_spec.get("token_usage"), 15)],
            "states": screen_spec.get("states") or {},
            "target_fidelity": screen_spec.get("target_fidelity") or {},
            "accessibility_notes": [_clip(item, 380) for item in _take(screen_spec.get("accessibility_notes"), 10)],
            "target_images": [_compact_image(image) for image in _relevant_images(manifest, product_brief, screen_spec)],
        },
        "tokens": {
            "tokens": tokens.get("tokens") or {},
            "forbidden": [_clip(item, 300) for item in _take(tokens.get("forbidden"), 15)],
        },
        "decisions": {
            "authoritative": contract.get("authoritative_decisions") or {},
            "conflicts_remaining": contract.get("conflicts_remaining") or [],
            "open_questions": [_clip(item, 450) for item in _take(contract.get("open_questions"), 10)],
        },
        "must_fix": {
            "verdict": critique.get("verdict"),
            "score": critique.get("score"),
            "issues": issues,
            "rework_instructions": [_clip(item, 400) for item in _take(critique.get("rework_instructions"), 10)],
        },
        "deferred": deferred,
        "reviews": review_packet,
        "human_decisions": state.get("human_decision") or {},
        "sources": [
            {"artifact": name, "sha256": _hash(value)}
            for name, value in (
                ("product_brief.json", product_brief),
                ("ceo_directive.json", ceo),
                ("user_flows.json", synthesis),
                ("research_findings.json", research),
                ("target_manifest.json", manifest),
                ("design_tokens.json", tokens),
                ("screen_spec.json", screen_spec),
                ("design_contract.json", contract),
                ("design_critique.json", critique),
                ("tier_reviews.json", tier_reviews),
            )
            if value
        ],
        "omitted": [],
    }

    packet["meta"] = {
        "packet_version": PACKET_VERSION,
        "max_chars": max_chars,
        "chars": len(_compact(packet)),
        "within_budget": True,
    }

    # Keep the packet selective if a future project produces much larger artifacts.
    # Drop optional context first; critical decisions and must-fix items are never dropped.
    for path, label in (
        (("reviews",), "reviews"),
        (("platform_constraints", "verification_commands"), "verification_commands"),
        (("ux_contract", "flows"), "flow_steps"),
        (("visual_truth", "accessibility_notes"), "accessibility_notes"),
        (("deferred",), "deferred_issues"),
    ):
        if len(_compact(packet)) <= max_chars:
            break
        target = packet
        for key in path[:-1]:
            target = target.get(key) or {}
        if isinstance(target, dict):
            target.pop(path[-1], None)
        packet["omitted"].append(label)

    packet["meta"]["chars"] = len(_compact(packet))
    packet["meta"]["within_budget"] = len(_compact(packet)) <= max_chars
    return packet


def packet_json(packet: dict[str, Any]) -> str:
    return _compact(packet)
