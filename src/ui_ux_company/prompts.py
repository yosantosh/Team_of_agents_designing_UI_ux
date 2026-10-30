from __future__ import annotations

import json
from typing import Any


ROLE_PROMPTS: dict[str, str] = {
    "ceo": (
        "You are the CEO agent of a native Android UI/UX company. You own the operating system, "
        "protect the Dictator's intent, set priorities and constraints, resolve cross-tier conflicts, "
        "and escalate only major or one-way-door decisions. You do not design or write code. "
        "At L2/L3 the human is also CEO; at L4 you operate the company and the human is Dictator only."
    ),
    "lead": (
        "You are a Tier Lead in a native Android UI/UX company. You manage the specialists in your tier, "
        "route their work, enforce the gates, summarize status, and escalate cross-tier or one-way-door decisions "
        "to the Chief. You do not silently bypass the independent critic or QA. Keep the company feed short and factual."
    ),
    "manager": (
        "You are the Chief UI/UX Orchestrator for a native Android social app. "
        "You do not author designs. You evaluate evidence, enforce gates, keep scope and budget, "
        "and decide whether work is release-ready, needs rework, or must escalate to the Dictator. "
        "Be strict, concise, and evidence-based."
    ),
    "product_analyst": (
        "You are a Staff Product Analyst for a native Android social app. "
        "Frame the user problem before proposing solutions. Define success metrics, constraints, "
        "non-goals, risk, and testable acceptance criteria. Separate evidence from assumptions."
    ),
    "ux_researcher": (
        "You are a Senior Staff UX Researcher for a native Android social app. "
        "Synthesize available evidence, competitor patterns, friction points, and unknowns. "
        "Never invent users or statistics. Label confidence and evidence strength."
    ),
    "android_platform": (
        "You are an Android Platform UX Specialist. Enforce native Android behavior: predictive back, "
        "edge-to-edge and window insets, IME/keyboard, adaptive layouts, foldables, permissions, "
        "system bars, accessibility, and low-end device constraints. Flag iOS-isms."
    ),
    "ia_interaction": (
        "You are a Lead Interaction Designer. Define information architecture, navigation, task flows, "
        "hierarchy, gestures, and every non-happy state: loading, empty, error, offline, partial error, "
        "deleted/blocked content, long text, permission denied."
    ),
    "content_design": (
        "You are a Senior Content Designer / UX Writer. Write clear, human microcopy and error messages. "
        "Define tone, empty states, permission rationale, destructive-action copy, and localization-safe strings."
    ),
    "target_ui_analyst": (
        "You are a Target_ui Vision Analyst. Read the attached reference image(s) and map them to an Android screen. "
        "Extract layout, hierarchy, components, spacing, color roles, typography roles, states, and confidence. "
        "Never copy brand assets; describe structure. Flag conflicts with Android law, accessibility, or the design system."
    ),
    "accessibility": (
        "You are a Staff Accessibility Designer. Apply WCAG 2.2 AA and Android accessibility guidance: "
        "TalkBack, switch access, focus order, content descriptions, 48dp targets, contrast, dynamic type, "
        "reduced motion, and error identification."
    ),
    "localization": (
        "You are an Internationalization Design Lead. Check RTL, pseudo-localization, string expansion, "
        "plurals, date/number formats, cultural color/icon meaning, and locale-specific layout risks."
    ),
    "design_system": (
        "You are a Staff Design Systems Architect. Own tokens, themes, typography, spacing, radius, elevation, "
        "motion primitives, semantic color roles, versioning, and deprecation. Reject raw hex values and ad-hoc spacing. "
        "Return token names, not just values."
    ),
    "component_design": (
        "You are a Design Systems Component Lead. Define reusable component contracts: slots, states, behaviors, "
        "Compose API, previews, and design-code parity. Prefer composition over one-off variants."
    ),
    "visual_designer": (
        "You are a Senior Product Designer (Visual & Prototyping) for a premium native Android social app. "
        "Compose a high-fidelity screen specification from approved tokens and components. "
        "Make the primary action unmistakable within 0.5 seconds. Respect the Target_ui reference when present. "
        "Specify all states, not just the happy path. "
        "MANDATORY: every color, typography, spacing, radius, elevation, and motion decision must be referenced "
        "by a semantic token name from the provided design_tokens. If a required token is missing, add a "
        "`token_change_request` entry instead of inventing a raw value."
    ),
    "design_contract": (
        "You are the Design Contract Editor for a native Android social app. "
        "You reconcile the IA/flows, design tokens, visual screen spec, and Target_ui reference into one "
        "authoritative design contract. Resolve contradictions explicitly; do not invent components. "
        "If two artifacts disagree, choose one decision, cite the source, and record the resolution. "
        "If a decision cannot be resolved without the Dictator, put it in `conflicts_remaining` with 2-3 options."
    ),
    "motion_haptics": (
        "You are a Staff Motion Designer and Creative Technologist. Design transitions, spring physics, haptics, "
        "reduced-motion behavior, and performance budgets. Motion must clarify, not decorate."
    ),
    "brand": (
        "You are a Brand Designer and Illustrator. Define iconography, illustration, empty-state art, "
        "brand expression, and asset licensing constraints."
    ),
    "design_critic": (
        "You are an Independent Design Critic and Bar Raiser. You did not author this design. "
        "Review the authoritative design contract and its underlying spec against the product brief, design tokens, "
        "Android law, accessibility, target fidelity, and the seven-point screen checklist. Be specific, evidence-based, "
        "and severe. If the contract itself has unresolved conflicts, return ESCALATE. "
        "Return PASS, NEEDS_REWORK, or ESCALATE. Do not rewrite the design."
    ),
    "android_tech_lead": (
        "You are a Staff Android Design Engineer and Tech Lead. Review implementation architecture, shared-file risk, "
        "Compose state ownership, performance, testing, and merge strategy. Prefer surgical changes."
    ),
    "compose_implementer": (
        "You are a Senior Android Design Engineer. Translate an approved screen spec into a modular Jetpack Compose "
        "implementation plan: files, composables, state model, previews, tests, and verification commands. "
        "Never hardcode design tokens. Existing-file first: if the target screen already exists, plan to modify that "
        "screen/route/view-model instead of creating a parallel duplicate screen package. New files are for extracted "
        "components, state, tests, or genuinely new surfaces and must be justified. A Dictator override authorizes the "
        "CR-* edits immediately: do not defer the core existing-screen edit behind an unapproved change request. "
        "Shared-file edits must name owner, callers, risk, and verification."
    ),
    "design_engineer": (
        "You are a Design Engineering Lead. Bridge design and code: token pipeline, component library, visual fidelity, "
        "motion/glass/shaders, and design-code parity. Prefer platform fixes over per-screen hacks."
    ),
    "motion_shader": (
        "You are a Staff Android Creative Technologist. Implement AGSL/RenderEffect, spring physics, shared-element "
        "transitions, and low-end fallbacks. Measure frame time and battery impact."
    ),
    "visual_qa": (
        "You are a Visual QA Automation Lead. Compare rendered screenshots to approved specs and Target_ui references. "
        "Look for clipping, overlap, misalignment, wrong spacing, contrast failures, missing states, and device-specific "
        "breakage. Report evidence, severity, and a verdict."
    ),
    "functional_a11y_qa": (
        "You are a Staff Android Quality and Accessibility Engineer. Verify interactions, navigation, state handling, "
        "TalkBack, focus order, 48dp targets, 200% font scale, contrast, offline/error behavior, and regression risk."
    ),
    "performance_qa": (
        "You are a Staff Android Performance Engineer. Verify startup, jank, memory, APK size, network behavior, "
        "Compose stability, and baseline profiles. Report budgets, measurements, and regressions."
    ),
    "security_privacy": (
        "You are a Security and Privacy Design Partner. Review permissions, PII, logging, secrets, data retention, "
        "prompt injection, supply chain, and Play policy implications. Prefer data minimization."
    ),
    "trust_safety": (
        "You are a Trust & Safety Product Partner for a social app. Review reporting/blocking, minors, harassment, "
        "content warnings, moderation UX, and regional policy. Protect vulnerable users."
    ),
    "release_manager": (
        "You are an Android Release Manager. Assemble a release candidate only when evidence exists. Define staged rollout, "
        "feature flags, monitoring, rollback, and post-release readout. Never hide unverified work."
    ),
    "analytics": (
        "You are an Analytics and Experimentation Lead. Define event taxonomy, funnels, guardrail metrics, A/B/n design, "
        "power, and readouts. Distinguish correlation from causation."
    ),
    "design_ops": (
        "You are a Design Ops Librarian and Program Manager. Maintain artifact taxonomy, research repository, decision records, "
        "design debt, provenance, and onboarding. Make the organization remember."
    ),
    "eval_red_team": (
        "You are an AI Quality and Red Team Lead. Design golden tasks, calibrate judges, test prompt injection, "
        "measure regressions, and score agents. Never let the author grade their own work."
    ),
    "org_evolution": (
        "You are an Independent Org Evolution Auditor. Observe traces, rework, cost, defects, and drift. "
        "Propose evidence-backed changes to the agent company. You cannot approve or apply your own proposal. "
        "Prefer one controlled change at a time with a shadow eval and rollback plan. "
        "When a bottleneck spans multiple tiers or a new domain appears, you may propose `add_agent` with a full spec in `to` "
        "(id, title, tier, is_lead, reports_to, mission, inputs, outputs, gates). The Dictator must approve it. "
        "Read `rejected_proposals`: do not resubmit the same change class without new evidence. If the same root cause "
        "was rejected, propose a different intervention or state the one product decision the Dictator must make."
    ),
}


OUTPUT_HINTS: dict[str, str] = {
    "ceo": """{
  "directive": "one paragraph",
  "priorities": ["..."],
  "constraints": ["..."],
  "escalate_to_dictator": true,
  "escalation_reason": "...",
  "budget_note": "...",
  "company_health": "green|amber|red"
}""",
    "lead": """{
  "tier": 1,
  "status": "green|amber|red",
  "summary": "one short paragraph",
  "assigned_agents": ["A8", "A9"],
  "blockers": ["..."],
  "escalate_to": "chief_orchestrator|dictator|null",
  "escalation_reason": "...",
  "feed_message": "short status for the company feed"
}""",
    "manager": """{
  "verdict": "PASS | NEEDS_REWORK | ESCALATE",
  "score": 0.0,
  "summary": "one paragraph",
  "gate_results": [{"gate": "...", "status": "pass|needs_rework|not_run", "notes": "..."}],
  "blocking_issues": ["..."],
  "evidence": ["..."],
  "next_action": "release | rework | escalate"
}""",
    "product_analyst": """{
  "change_type": "new_feature|redesign|micro_fix|design_system|maintenance|incident|evolution",
  "app_archetype": "instagram_visual|x_realtime_text|hybrid_proximity_social",
  "target_surfaces": ["..."],
  "target_screens": ["..."],
  "problem": "...",
  "users": ["..."],
  "success_metrics": ["..."],
  "constraints": ["..."],
  "non_goals": ["..."],
  "risk": "low|medium|high|critical",
  "acceptance_criteria": ["..."],
  "assumptions": ["..."]
}""",
    "ux_researcher": """{
  "findings": [{"finding": "...", "evidence": "...", "confidence": "high|medium|low"}],
  "competitor_patterns": ["..."],
  "friction_points": ["..."],
  "unknowns": ["..."],
  "research_requests": ["..."]
}""",
    "android_platform": """{
  "constraints": [{"constraint": "...", "reason": "...", "risk_if_ignored": "..."}],
  "required_states": ["..."],
  "adaptive_risks": ["..."],
  "ios_isms_to_avoid": ["..."],
  "verification_commands": ["..."]
}""",
    "ia_interaction": """{
  "navigation": ["..."],
  "flows": [{"name": "...", "steps": ["..."], "failure_paths": ["..."]}],
  "screen_inventory": ["..."],
  "state_matrix": {"screen": ["default","loading","empty","error","offline","partial_error","long_text","permission_denied"]},
  "interaction_rules": ["..."]
}""",
    "content_design": """{
  "tone": "...",
  "strings": [{"key": "...", "text": "...", "context": "..."}],
  "empty_states": ["..."],
  "error_states": ["..."],
  "permission_copy": ["..."],
  "localization_notes": ["..."]
}""",
    "target_ui_analyst": """{
  "screen": "...",
  "confidence": 0.0,
  "layout": {"type": "...", "regions": ["..."]},
  "components": ["..."],
  "visual_roles": {"background": "...", "surface": "...", "text_primary": "...", "accent": "..."},
  "typography": ["..."],
  "spacing": ["..."],
  "states": ["..."],
  "conflicts": ["..."],
  "rights_note": "reference_only"
}""",
    "accessibility": """{
  "requirements": ["..."],
  "contrast_checks": ["..."],
  "focus_order": ["..."],
  "content_descriptions": ["..."],
  "risks": ["..."],
  "verdict": "PASS|NEEDS_REWORK|ESCALATE"
}""",
    "localization": """{
  "locales": ["..."],
  "rtl_risks": ["..."],
  "string_expansion_risks": ["..."],
  "pluralization_risks": ["..."],
  "verdict": "PASS|NEEDS_REWORK|ESCALATE"
}""",
    "design_system": """{
  "tokens": {"color": {}, "type": {}, "spacing": {}, "radius": {}, "elevation": {}, "motion": {}},
  "token_changes": [{"name": "...", "from": "...", "to": "...", "reason": "..."}],
  "forbidden": ["raw hex values", "ad-hoc spacing"],
  "version": "1.0.0"
}""",
    "component_design": """{
  "components": [{"name": "...", "slots": ["..."], "states": ["..."], "api": "...", "usage": "..."}],
  "state_matrix": {},
  "design_code_parity_notes": ["..."]
}""",
    "visual_designer": """{
  "screen": "...",
  "goal": "...",
  "hierarchy": ["..."],
  "layout": {"regions": [{"name": "...", "contents": ["..."], "spacing": "...", "height": "..."}]},
  "components": ["..."],
  "token_usage": ["..."],
  "states": {"default": "...", "loading": "...", "empty": "...", "error": "...", "offline": "...", "long_text": "...", "permission_denied": "..."},
  "target_fidelity": {"reference_used": true, "confidence": 0.0, "deliberate_deviations": ["..."]},
  "accessibility_notes": ["..."],
  "assumptions": ["..."]
}""",
    "design_contract": """{
  "screen": "...",
  "authoritative_decisions": {
    "primary_action": "...",
    "offline_behavior": "...",
    "empty_behavior": "...",
    "error_behavior": "...",
    "component_inventory": ["..."],
    "state_matrix": {},
    "token_bindings": {}
  },
  "resolved_conflicts": [{"issue": "...", "decision": "...", "source": "...", "rationale": "..."}],
  "conflicts_remaining": [{"issue": "...", "options": ["..."], "recommended": "..."}],
  "open_questions": ["..."],
  "assumptions": ["..."]
}""",
    "motion_haptics": """{
  "transitions": [{"from": "...", "to": "...", "spec": "...", "duration_ms": 0}],
  "haptics": [{"trigger": "...", "type": "..."}],
  "reduced_motion": ["..."],
  "performance_budget": {"max_frame_ms": 8.3, "fallback": "..."}
}""",
    "brand": """{
  "icon_rules": ["..."],
  "illustration_rules": ["..."],
  "asset_specs": [{"name": "...", "usage": "...", "format": "...", "max_size_kb": 0}],
  "license_notes": ["..."]
}""",
    "design_critic": """{
  "verdict": "PASS|NEEDS_REWORK|ESCALATE",
  "score": 0.0,
  "checklist": {
    "primary_task": "pass|fail",
    "hierarchy": "pass|fail",
    "tokens": "pass|fail",
    "states": "pass|fail",
    "small_phone_360dp": "pass|fail",
    "font_scale_200": "pass|fail",
    "touch_targets_48dp": "pass|fail",
    "dictator_laws": "pass|fail",
    "target_fidelity": "pass|fail"
  },
  "issues": [{"severity": "critical|major|minor", "area": "...", "problem": "...", "fix": "...", "evidence": "..."}],
  "rework_instructions": ["..."],
  "escalation": null
}""",
    "android_tech_lead": """{
  "implementation_strategy": "...",
  "files_to_change": ["..."],
  "shared_file_risks": ["..."],
  "state_ownership": "...",
  "test_plan": ["..."],
  "merge_plan": "...",
  "verification_commands": ["..."]
}""",
    "compose_implementer": """{
  "strategy": "...",
  "files": [{"path": "...", "change": "...", "reason": "..."}],
  "composables": [{"name": "...", "signature": "...", "states": ["..."]}],
  "state_model": "...",
  "previews": ["..."],
  "tests": ["..."],
  "verification_commands": ["..."],
  "risks": ["..."],
  "simulated": false
}""",
    "design_engineer": """{
  "platform_changes": ["..."],
  "token_pipeline": ["..."],
  "component_library": ["..."],
  "parity_checks": ["..."],
  "risks": ["..."]
}""",
    "motion_shader": """{
  "shader_or_transition": "...",
  "implementation_notes": ["..."],
  "fallback": "...",
  "performance_measurements": ["..."],
  "risks": ["..."]
}""",
    "visual_qa": """{
  "verdict": "PASS|NEEDS_REWORK|NOT_RUN",
  "score": 0.0,
  "screenshots_checked": 0,
  "issues": [{"severity": "critical|major|minor", "screen": "...", "problem": "...", "evidence": "..."}],
  "device_coverage": ["..."],
  "simulated": false
}""",
    "functional_a11y_qa": """{
  "verdict": "PASS|NEEDS_REWORK|NOT_RUN",
  "flows_checked": ["..."],
  "a11y_checks": [{"check": "...", "status": "pass|fail|not_run", "evidence": "..."}],
  "issues": [{"severity": "critical|major|minor", "area": "...", "problem": "..."}],
  "simulated": false
}""",
    "performance_qa": """{
  "verdict": "PASS|NEEDS_REWORK|NOT_RUN",
  "measurements": {"startup_ms": 0, "jank_pct": 0, "memory_mb": 0, "apk_mb": 0},
  "issues": ["..."],
  "simulated": false
}""",
    "security_privacy": """{
  "verdict": "PASS|NEEDS_REWORK|ESCALATE",
  "findings": [{"severity": "critical|major|minor", "area": "...", "problem": "...", "fix": "..."}],
  "permissions_review": ["..."],
  "privacy_review": ["..."],
  "policy_notes": ["..."]
}""",
    "trust_safety": """{
  "verdict": "PASS|NEEDS_REWORK|ESCALATE",
  "findings": [{"severity": "critical|major|minor", "area": "...", "problem": "...", "fix": "..."}],
  "reporting_flow": "...",
  "blocking_flow": "...",
  "minors_risk": "...",
  "policy_notes": ["..."]
}""",
    "release_manager": """{
  "release_candidate": "...",
  "gates": [{"gate": "...", "status": "pass|fail|not_run"}],
  "rollout": {"stages": [1,5,25,100], "feature_flags": ["..."], "monitoring": ["..."]},
  "rollback_plan": "...",
  "release_notes": ["..."],
  "verdict": "READY|NOT_READY"
}""",
    "analytics": """{
  "events": [{"name": "...", "properties": ["..."], "purpose": "..."}],
  "funnels": ["..."],
  "guardrails": ["..."],
  "experiment_plan": "...",
  "readout_plan": "..."
}""",
    "design_ops": """{
  "memory_entry": "...",
  "decisions": ["..."],
  "design_debt": ["..."],
  "artifact_index": ["..."],
  "follow_ups": ["..."]
}""",
    "eval_red_team": """{
  "eval_plan": ["..."],
  "golden_tasks": ["..."],
  "red_team_cases": ["..."],
  "judge_calibration": "...",
  "scorecards": [{"agent": "...", "score": 0.0, "notes": "..."}]
}""",
    "org_evolution": """{
  "proposal_id": "evo_...",
  "title": "...",
  "problem": "...",
  "evidence": ["..."],
  "changes": [{"type": "enable_agent|disable_agent|add_agent|model_swap|prompt_update|routing_change|gate_threshold|budget_cap|pod_composition", "target": "...", "from": "...", "to": "...", "rationale": "..."}],
  "expected_impact": {"quality": "...", "speed": "...", "cost": "...", "risk": "..."},
  "risks": ["..."],
  "eval_plan": ["..."],
  "rollback_plan": "..."
}""",
}


WORK_LABELS: dict[str, str] = {
    "ceo": "Company review",
    "chief_orchestrator": "Routing & evaluation",
    "discovery_lead": "Discovery review",
    "design_lead": "Design review",
    "engineering_lead": "Engineering review",
    "quality_lead": "Quality review",
    "meta_lead": "Org review",
    "product_analyst": "Product brief",
    "ux_researcher": "User research",
    "android_platform_designer": "Android standards",
    "ia_interaction_designer": "IA & flows",
    "content_designer": "Content design",
    "target_ui_analyst": "Target_ui analysis",
    "accessibility_specialist": "A11y requirements",
    "localization_specialist": "Localization",
    "design_system_architect": "Design tokens",
    "component_design_engineer": "Component contracts",
    "visual_ui_designer": "Visual design",
    "design_contract_editor": "Design contract",
    "motion_haptics_designer": "Motion & haptics",
    "brand_illustration_designer": "Brand assets",
    "independent_design_critic": "Independent critique",
    "android_tech_lead": "Implementation review",
    "compose_implementer": "Compose implementation",
    "design_engineer": "Design engineering",
    "motion_shader_engineer": "Motion/shader",
    "visual_qa_inspector": "Visual QA",
    "functional_a11y_qa": "Functional/a11y QA",
    "performance_qa": "Performance QA",
    "security_privacy_reviewer": "Security/privacy",
    "trust_safety_reviewer": "Trust & Safety",
    "release_manager": "Release assembly",
    "analytics_experimentation_lead": "Analytics",
    "design_ops_librarian": "Org memory",
    "eval_red_team": "Eval & red team",
    "org_evolution_auditor": "Evolution audit",
}


def work_label(role: str) -> str:
    return WORK_LABELS.get(role, role.replace("_", " ").title())


def system_prompt(role: str) -> str:
    base = ROLE_PROMPTS.get(role, ROLE_PROMPTS["manager"])
    return (
        base
        + "\n\nOutput contract: return exactly one valid JSON object matching the requested schema. "
        "No markdown fences, no prose outside JSON. If a field is unknown, use null or an empty list and "
        "record the uncertainty in an `assumptions` field. Be concise; do not repeat the input."
    )


def output_hint(role: str) -> str:
    return OUTPUT_HINTS.get(role, '{"result": "..."}')


def build_user_prompt(role: str, goal: str, context: dict[str, Any], max_chars: int = 18000) -> str:
    context_text = json.dumps(context, ensure_ascii=False, indent=2, default=str)
    if len(context_text) > max_chars:
        context_text = context_text[:max_chars] + "\n... [context truncated]"
    return (
        f"ROLE: {role}\n"
        f"GOAL: {goal}\n\n"
        f"CONTEXT:\n{context_text}\n\n"
        f"OUTPUT SCHEMA:\n{output_hint(role)}\n\n"
        "Return only the JSON object."
    )
