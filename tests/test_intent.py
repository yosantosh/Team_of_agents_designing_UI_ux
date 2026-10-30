from __future__ import annotations

import json

from ui_ux_company.intent import build_intent_packet, packet_json


def _state() -> dict:
    return {
        "run_id": "run_intent",
        "mode": "build",
        "prompt": "Redesign HomeFeed with one primary action.",
        "change_request": {
            "prompt": "Redesign HomeFeed with one primary action.",
            "change_type": "redesign",
            "app_archetype": "hybrid_proximity_social",
            "target_surfaces": ["Home/Feed"],
            "target_screens": ["HomeFeed - default", "HomeFeed - empty"],
            "problem": "The primary action is unclear.",
            "users": ["New user", "Daily user"],
            "success_metrics": ["Primary action found within 5 seconds"],
            "constraints": ["Pure OLED black canvas", "48dp touch targets"],
            "non_goals": ["No backend changes"],
            "acceptance_criteria": ["One accent-filled primary action"],
            "risk": "medium",
        },
        "ceo_directive": {
            "directive": "Keep the change surgical.",
            "priorities": ["One primary action"],
            "constraints": ["No raw hex values"],
            "escalation_reason": "",
        },
        "research": {
            "android": {
                "constraints": [{"constraint": "Enable edge-to-edge"}],
                "required_states": ["loading", "empty", "offline"],
                "adaptive_risks": ["200% font scale"],
                "verification_commands": ["./gradlew lintDebug"],
            }
        },
        "synthesis": {
            "navigation": ["HomeFeed is the start destination"],
            "flows": [{"name": "Create", "steps": ["Tap Create"]}],
            "screen_inventory": ["HomeFeed"],
            "state_matrix": {"HomeFeed": ["default", "empty", "offline"]},
            "interaction_rules": ["Only one primary action"],
        },
        "target_manifest": {
            "images": [
                {
                    "image_id": "target_01",
                    "screen": "feed",
                    "state": "default",
                    "confidence": 0.78,
                    "status": "conflict",
                    "annotations": [{"label": "bell", "x": 0.9, "y": 0.1}],
                    "conflicts": ["music icon should be a bell"],
                    "extracted": {
                        "layout": {"type": "single-column feed", "regions": ["app bar", "feed"]},
                        "components": ["top app bar", "feed item"],
                        "visual_roles": {"background": "OLED black"},
                        "states": ["default"],
                    },
                },
                {
                    "image_id": "target_06",
                    "screen": "profile",
                    "state": "default",
                    "confidence": 0.9,
                    "status": "analyzed",
                    "annotations": [],
                    "conflicts": [],
                    "extracted": {"layout": {"type": "profile"}, "components": []},
                },
            ]
        },
        "design_tokens": {
            "tokens": {"color": {"canvas": "#000000"}, "space": {"md": "12dp"}},
            "forbidden": ["raw hex values"],
        },
        "screen_spec": {
            "screen": "HomeFeed",
            "goal": "Create is the only primary action.",
            "hierarchy": ["1 — Create FAB", "2 — Feed"],
            "layout": {"regions": [{"name": "app bar"}, {"name": "feed"}]},
            "components": ["HomeTopAppBar", "CreateFab"],
            "token_usage": ["Background: color.canvas"],
            "states": {"empty": "Show one create path"},
            "target_fidelity": {"reference_used": True, "confidence": 0.78},
            "accessibility_notes": ["48dp targets"],
        },
        "design_contract": {
            "authoritative_decisions": {"primary_action": "Docked Create FAB"},
            "conflicts_remaining": [],
            "open_questions": ["Is the bell ratified?"],
        },
        "critique": {
            "verdict": "PASS",
            "score": 0.86,
            "issues": [
                {
                    "severity": "critical",
                    "area": "tokens",
                    "problem": "Accent text contrast is too low.",
                    "fix": "Use text-safe accent.",
                    "evidence": "contrast 3.69:1",
                },
                {
                    "severity": "minor",
                    "area": "copy",
                    "problem": "Empty copy could be quieter.",
                    "fix": "Shorten it.",
                    "evidence": "",
                },
            ],
            "rework_instructions": ["Fix contrast"],
        },
        "tier_reviews": {
            "2": {"status": "amber", "summary": "Design is coherent.", "blockers": ["fidelity"], "escalation_reason": ""},
            "3": {"status": "green", "summary": "Plan is buildable.", "blockers": [], "escalation_reason": ""},
        },
        "human_decision": {"decision": "approve", "notes": "Proceed."},
    }


def test_intent_packet_is_selective_and_has_ids():
    packet = build_intent_packet(_state(), max_chars=160000)
    assert packet["meta"]["within_budget"] is True
    assert packet["non_negotiables"][0]["id"] == "NN-1"
    assert packet["must_fix"]["issues"][0]["id"] == "C-1"
    assert packet["deferred"][0]["id"] == "M-1"
    assert packet["visual_truth"]["target_images"][0]["id"] == "target_01"
    assert all(image["id"] != "target_06" for image in packet["visual_truth"]["target_images"])
    assert packet["decisions"]["authoritative"]["primary_action"] == "Docked Create FAB"
    assert packet["human_decisions"]["decision"] == "approve"
    assert packet["sources"]
    assert len(packet_json(packet)) < 160000


def test_intent_packet_drops_optional_sections_when_over_budget():
    packet = build_intent_packet(_state(), max_chars=1000)
    assert packet["omitted"]
    assert "reviews" in packet["omitted"]


def test_intent_packet_is_json_serializable():
    packet = build_intent_packet(_state())
    json.dumps(packet, ensure_ascii=False, default=str)
