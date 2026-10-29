from __future__ import annotations

from typing import Any


def _screen_from_context(context: dict[str, Any]) -> str:
    request = context.get("change_request") or {}
    screens = request.get("target_screens") or []
    if screens:
        return str(screens[0])
    return "profile"


def mock_response(role: str, context: dict[str, Any]) -> dict[str, Any]:
    screen = _screen_from_context(context)
    prompt = str(context.get("prompt") or context.get("goal") or "Redesign the profile screen")
    common = {
        "assumptions": [
            "Mock backend used for deterministic local verification; no model call was made.",
            "Target_ui reference is treated as layout guidance, not a brand copy.",
        ]
    }

    if role == "product_analyst":
        return {
            "change_type": "redesign",
            "app_archetype": "hybrid_proximity_social",
            "target_surfaces": ["profile"],
            "target_screens": ["profile_header", "media_grid", "profile_actions"],
            "problem": "The profile screen must establish identity quickly, make media discovery effortless, and keep primary actions reachable with one hand.",
            "users": ["returning creator", "casual viewer", "new visitor"],
            "success_metrics": [
                "Profile primary action is found without hesitation",
                "Media category switch completes in one tap",
                "No clipped text at 200% font scale",
            ],
            "constraints": [
                "Pure OLED black canvas",
                "Purple is icon-only",
                "No gradients outside the music player",
                "All touch targets >= 48dp",
            ],
            "non_goals": ["New backend endpoints", "Recommendation ranking changes"],
            "risk": "medium",
            "acceptance_criteria": [
                "Default, loading, empty, error, offline, long-name, and 200% font states are specified",
                "All colors and spacing reference design tokens",
                "Visual QA has a target or baseline for every captured state",
            ],
            **common,
        }

    if role == "ux_researcher":
        return {
            "findings": [
                {"finding": "Users scan identity and media before reading bio copy.", "evidence": "Common social-profile eye-tracking pattern; verify with local user test.", "confidence": "medium"},
                {"finding": "Crowded top-bar icon clusters increase accidental taps on compact phones.", "evidence": "Thumb-zone and target-size heuristics; verify with 360dp usability test.", "confidence": "medium"},
            ],
            "competitor_patterns": [
                "Identity block above a dense media grid",
                "Sticky category tabs with a single active indicator",
                "Primary action as a high-contrast pill",
            ],
            "friction_points": ["Long usernames", "Empty media grid", "Offline media", "Ambiguous follow/message actions"],
            "unknowns": ["Real task-completion data", "TalkBack behavior on the current profile header"],
            "research_requests": ["5-person moderated usability test on 360dp device", "TalkBack pass on profile actions"],
            **common,
        }

    if role == "android_platform":
        return {
            "constraints": [
                {"constraint": "Use edge-to-edge with WindowInsets-aware padding.", "reason": "Modern Android target SDK behavior.", "risk_if_ignored": "Content under system bars."},
                {"constraint": "Support predictive back for nested profile/media destinations.", "reason": "Android navigation expectation.", "risk_if_ignored": "Broken back gesture and lost context."},
                {"constraint": "Respect 200% font scale and display size.", "reason": "Accessibility and Android configuration changes.", "risk_if_ignored": "Clipped names and controls."},
            ],
            "required_states": ["loading", "empty", "error", "offline", "partial_error", "long_text", "permission_denied"],
            "adaptive_risks": ["Foldable hinge", "Tablet two-pane profile", "Landscape media grid"],
            "ios_isms_to_avoid": ["Bottom sheet with no Android back behavior", "Non-standard back chevron", "Fixed 44pt touch targets"],
            "verification_commands": ["./gradlew :app:assembleDebug", "./gradlew :app:testDebugUnitTest"],
            **common,
        }

    if role == "ia_interaction":
        return {
            "navigation": ["Profile is a top-level destination", "Media category tabs stay local to profile", "Edit profile is a modal destination"],
            "flows": [
                {"name": "View profile", "steps": ["Open profile", "Scan identity", "Switch media category", "Open media"], "failure_paths": ["offline", "empty grid", "blocked content"]},
                {"name": "Edit profile", "steps": ["Tap edit", "Change field", "Save", "See confirmation"], "failure_paths": ["validation error", "network failure", "permission denied"]},
            ],
            "screen_inventory": ["profile_header", "media_grid", "profile_actions", "edit_profile", "followers_list"],
            "state_matrix": {
                "profile_header": ["default", "loading", "error", "offline", "long_text", "permission_denied"],
                "media_grid": ["default", "loading", "empty", "error", "offline", "partial_error", "deleted_content"],
            },
            "interaction_rules": ["One primary action", "Category switch preserves scroll position per category", "Destructive actions require confirmation"],
            **common,
        }

    if role == "target_ui_analyst":
        return {
            "screen": screen,
            "confidence": 0.86,
            "layout": {
                "type": "identity_header + segmented_media_grid",
                "regions": ["system bars", "top action row", "avatar and identity", "bio and metadata", "primary action row", "category tabs", "media grid"],
            },
            "components": ["avatar", "icon_button", "primary_pill", "segmented_tabs", "media_tile", "empty_state"],
            "visual_roles": {"background": "pure black", "surface": "near-black solid", "text_primary": "white", "accent": "purple icons only"},
            "typography": ["display name", "handle", "bio", "metadata", "tab label"],
            "spacing": ["16dp screen gutter", "12dp card gap", "24dp section gap"],
            "states": ["default", "loading", "empty", "offline", "long_text"],
            "conflicts": ["Reference uses a brand-specific icon set; replace with the project icon system."],
            "rights_note": "reference_only",
            **common,
        }

    if role == "design_system":
        return {
            "tokens": {
                "color": {
                    "bg_canvas": "#000000",
                    "surface_solid": "#0A0A0C",
                    "text_primary": "#FFFFFF",
                    "text_secondary": "#8E8E93",
                    "icon_accent": "#A855F7",
                    "indicator_red": "#FF3B30",
                },
                "type": {"display": "28sp/700", "title": "20sp/600", "body": "15sp/400", "meta": "13sp/400"},
                "spacing": {"xs": "4dp", "sm": "8dp", "md": "16dp", "lg": "24dp", "xl": "32dp"},
                "radius": {"card": "24dp", "pill": "999dp", "sheet": "28dp"},
                "elevation": {"floating": "16dp", "sheet": "24dp"},
                "motion": {"fast": "120ms", "standard": "220ms", "spring": "medium"},
            },
            "token_changes": [
                {"name": "profile.grid.gap", "from": "8dp", "to": "12dp", "reason": "Improves media tile separation without reducing density."}
            ],
            "forbidden": ["raw hex values", "ad-hoc spacing outside the 4dp grid", "purple button fills"],
            "version": "1.0.0",
            **common,
        }

    if role == "visual_designer":
        return {
            "screen": screen,
            "goal": "Identity-first profile with one unmistakable primary action and a calm, premium media grid.",
            "hierarchy": ["avatar and display name", "primary action", "media categories", "media grid", "bio and metadata"],
            "layout": {
                "regions": [
                    {"name": "top_bar", "contents": ["back or title", "share", "settings"], "spacing": "16dp", "height": "56dp"},
                    {"name": "identity", "contents": ["avatar", "display name", "handle", "bio"], "spacing": "16dp", "height": "wrap"},
                    {"name": "actions", "contents": ["primary pill", "secondary glass pill", "icon button"], "spacing": "8dp", "height": "48dp"},
                    {"name": "media_tabs", "contents": ["posts", "reels", "tagged"], "spacing": "0dp", "height": "44dp"},
                    {"name": "media_grid", "contents": ["3-column grid or empty state"], "spacing": "12dp", "height": "wrap"},
                ]
            },
            "components": ["avatar", "primary_pill", "glass_pill", "icon_button", "segmented_tabs", "media_tile", "empty_state"],
            "token_usage": ["color.bg_canvas", "color.surface_solid", "color.text_primary", "color.text_secondary", "color.icon_accent", "spacing.md", "radius.card"],
            "states": {
                "default": "Identity, actions, tabs, and media grid populated.",
                "loading": "Avatar and media tiles use skeleton shapes; no layout shift.",
                "empty": "Media grid replaced by a quiet empty state with one action.",
                "error": "Inline retry row; identity remains visible.",
                "offline": "Cached identity and media with a clear offline banner.",
                "long_text": "Display name wraps to two lines; handle ellipsizes; actions stay visible.",
                "permission_denied": "Media picker rationale and settings shortcut.",
            },
            "target_fidelity": {"reference_used": True, "confidence": 0.86, "deliberate_deviations": ["Replaced reference brand icons with project icon system.", "Used project token values for color and spacing."]},
            "accessibility_notes": ["48dp minimum targets", "Display name is a heading for TalkBack", "Media tiles expose content description and index"],
            **common,
        }

    if role == "design_contract":
        return {
            "screen": screen,
            "authoritative_decisions": {
                "primary_action": "One filled white pill: Follow for a stranger, Message for a connection, Share profile for self.",
                "offline_behavior": "Show cached identity and media with a single inline offline banner; one retry control.",
                "empty_behavior": "Replace only the grid region with a quiet empty state and one action; identity remains visible.",
                "error_behavior": "Keep identity visible; one inline retry row above the grid; no duplicate stale badges.",
                "component_inventory": ["avatar", "primary_pill", "glass_pill", "icon_button", "segmented_tabs", "media_tile", "empty_state", "inline_status"],
                "state_matrix": {
                    "profile_header": ["default", "loading", "error", "offline", "long_text", "permission_denied"],
                    "media_grid": ["default", "loading", "empty", "error", "offline", "partial_error", "deleted_content"],
                },
                "token_bindings": {
                    "background": "color.bg_canvas",
                    "surface": "color.surface_solid",
                    "text": "color.text_primary",
                    "secondary_text": "color.text_secondary",
                    "accent": "color.icon_accent",
                    "gutter": "spacing.md",
                    "card_radius": "radius.card",
                },
            },
            "resolved_conflicts": [
                {"issue": "Synthesis listed an optional decorative empty-state mark; the screen spec omitted it.", "decision": "Do not ship a decorative mark in v1; use the project icon system if one is added later.", "source": "screen_spec + design tokens", "rationale": "Reduce visual noise and avoid an unowned asset."},
                {"issue": "Offline retry was described in multiple places.", "decision": "One inline status control owns retry, stale text, and timestamp.", "source": "design system + Android constraints", "rationale": "Avoid duplicate TalkBack announcements."},
            ],
            "conflicts_remaining": [],
            "open_questions": ["Confirm the own-profile primary action with product (share vs edit)."],
            "assumptions": ["Mock contract; real run requires human calibration of these resolutions."],
        }

    if role == "design_critic":
        return {
            "verdict": "PASS",
            "score": 0.91,
            "checklist": {
                "primary_task": "pass",
                "hierarchy": "pass",
                "tokens": "pass",
                "states": "pass",
                "small_phone_360dp": "pass",
                "font_scale_200": "pass",
                "touch_targets_48dp": "pass",
                "dictator_laws": "pass",
                "target_fidelity": "pass",
            },
            "issues": [
                {"severity": "minor", "area": "media_grid", "problem": "Empty state copy is not specified in the screen spec.", "fix": "Add content-designer copy before implementation.", "evidence": "screen_spec.states.empty"},
            ],
            "rework_instructions": ["Add empty-state copy and a TalkBack label for the media category tabs."],
            "escalation": None,
            "assumptions": ["Mock critique; independent human/model calibration still required."],
        }

    if role == "compose_implementer":
        return {
            "strategy": "Implement a stateless ProfileScreen with a ProfileUiState, split header/grid/tabs into small composables, keep shared navigation untouched.",
            "files": [
                {"path": "OrphicApp/app/src/main/java/com/example/dhunchat/ui/profile/ProfileScreen.kt", "change": "Compose screen and state rendering", "reason": "Feature-local UI"},
                {"path": "OrphicApp/app/src/main/java/com/example/dhunchat/ui/profile/ProfileViewModel.kt", "change": "UiState and event handling", "reason": "Unidirectional state"},
            ],
            "composables": [
                {"name": "ProfileScreen", "signature": "ProfileScreen(state, onEvent)", "states": ["default", "loading", "empty", "error", "offline"]},
                {"name": "ProfileHeader", "signature": "ProfileHeader(identity, onAction)", "states": ["default", "long_text"]},
                {"name": "MediaGrid", "signature": "MediaGrid(items, onOpen)", "states": ["default", "empty", "loading", "error"]},
            ],
            "state_model": "sealed interface ProfileUiState { Loading; Content(identity, tabs, media); Empty; Error(message) }",
            "previews": ["ProfileScreenPreview_Default", "ProfileScreenPreview_Empty", "ProfileScreenPreview_LongName", "ProfileScreenPreview_FontScale200"],
            "tests": ["ProfileScreenTest_Default", "ProfileScreenTest_Empty", "ProfileScreenTest_LongName", "ProfileScreenTest_Offline"],
            "verification_commands": ["./gradlew :app:assembleDebug", "./gradlew :app:testDebugUnitTest"],
            "risks": ["Shared navigation must be changed by the tech lead", "Liquid glass must not leak into profile cards"],
            "simulated": True,
            **common,
        }

    if role == "visual_qa":
        return {
            "verdict": "PASS",
            "score": 0.90,
            "screenshots_checked": 4,
            "issues": [],
            "device_coverage": ["compact_360dp", "large_412dp", "large_font_200"],
            "simulated": True,
            "assumptions": ["Mock QA; no real screenshots were captured. Production requires emulator/ADB or device-farm evidence."],
        }

    if role == "functional_a11y_qa":
        return {
            "verdict": "PASS",
            "flows_checked": ["view_profile", "switch_media_tab", "open_media", "edit_profile"],
            "a11y_checks": [
                {"check": "touch_targets_48dp", "status": "pass", "evidence": "spec"},
                {"check": "talkback_labels", "status": "pass", "evidence": "spec"},
                {"check": "font_scale_200", "status": "pass", "evidence": "spec"},
                {"check": "offline_retry", "status": "pass", "evidence": "spec"},
            ],
            "issues": [],
            "simulated": True,
            "assumptions": ["Mock QA; no emulator run. Production requires UIAutomator/AccessibilityChecks evidence."],
        }

    if role == "ceo":
        return {
            "directive": "Proceed with a design-led profile improvement. Keep the current product scope and protect the pure OLED black identity.",
            "priorities": ["Clarity of the primary action", "Target_ui fidelity", "Release evidence"],
            "constraints": ["No mock evidence", "Human remains CEO at L2", "Accessibility overrides target imagery"],
            "escalate_to_dictator": False,
            "escalation_reason": "",
            "budget_note": "Within the monthly token budget.",
            "company_health": "green",
            "assumptions": ["Mock CEO review; real runs call the selected OpenCode model."],
        }

    if role == "manager":
        return {
            "verdict": "PASS",
            "score": 0.90,
            "summary": "Design critique and simulated QA passed. The run is design-ready; production release still requires real capture and functional evidence.",
            "gate_results": [
                {"gate": "design_critique", "status": "pass", "notes": "Score 0.91"},
                {"gate": "human_design_approval", "status": "pass", "notes": "Auto-approved in demo mode"},
                {"gate": "visual_qa", "status": "pass", "notes": "Simulated"},
                {"gate": "functional_a11y", "status": "pass", "notes": "Simulated"},
            ],
            "blocking_issues": [],
            "evidence": ["design_critique.json", "visual_qa.json", "functional_qa.json"],
            "next_action": "release",
            "assumptions": ["Simulated QA is labeled and must not be treated as production evidence."],
        }

    if role == "release_manager":
        return {
            "release_candidate": "rc_profile_001",
            "gates": [
                {"gate": "design_critique", "status": "pass"},
                {"gate": "human_design_approval", "status": "pass"},
                {"gate": "visual_qa", "status": "pass"},
                {"gate": "functional_a11y", "status": "pass"},
            ],
            "rollout": {"stages": [1, 5, 25, 100], "feature_flags": ["profile_redesign_v1"], "monitoring": ["crash_free_rate", "anr_rate", "startup_time", "jank", "play_vitals"]},
            "rollback_plan": "Disable profile_redesign_v1 and restore the previous profile composable from the release tag.",
            "release_notes": ["Identity-first profile header", "Calmer media grid", "Explicit empty/offline/error states"],
            "verdict": "READY",
            "assumptions": ["Demo release candidate; real release requires device evidence and Dictator approval."],
        }

    if role == "design_ops":
        return {
            "memory_entry": "Profile redesign established an identity-first hierarchy and a 12dp media grid gap. Keep the single-primary-action rule for future surfaces.",
            "decisions": ["Target_ui reference wins layout/composition; tokens win color/type.", "Purple remains icon-only."],
            "design_debt": ["Empty-state copy needs content design.", "Add TalkBack labels to media tabs."],
            "artifact_index": ["product_brief.json", "screen_spec.json", "design_critique.json", "release_candidate.json"],
            "follow_ups": ["Run a 5-person usability test", "Capture emulator screenshots on 360dp and 200% font"],
            **common,
        }

    if role == "org_evolution":
        return {
            "proposal_id": "evo_quality_001",
            "title": "Add a dedicated Content Design gate before critique",
            "problem": "The current run passes critique with an unowned empty-state copy gap. Content design is disabled, so copy quality is not independently verified.",
            "evidence": [
                "design_critique.issues contains a minor empty-state copy finding",
                "content_designer is disabled in the registry",
                "No string catalog artifact exists in the run",
            ],
            "changes": [
                {
                    "type": "enable_agent",
                    "target": "content_designer",
                    "from": False,
                    "to": True,
                    "rationale": "Make microcopy an explicit, reviewed artifact before design critique.",
                }
            ],
            "expected_impact": {
                "quality": "Fewer copy defects and fewer late-stage rewrites",
                "speed": "Slightly longer discovery, shorter rework",
                "cost": "+3-5% tokens per run",
                "risk": "Low; content designer is advisory and does not edit code",
            },
            "risks": ["Adds one handoff", "May duplicate copy work if the visual designer already writes microcopy"],
            "eval_plan": ["Run 5 golden profile/feed tasks with and without the content gate", "Compare critique copy findings and rework loops"],
            "rollback_plan": "Set content_designer.enabled=false in the registry and restore the previous version from org/registry_history/.",
            "status": "proposed",
        }

    return {
        "result": "mock response",
        "role": role,
        "note": prompt[:200],
        **common,
    }
