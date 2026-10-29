# UI/UX Agent Company

A **LangGraph + LangSmith + OpenCode** agent company for native Android social-app UI/UX design, implementation, QA, maintenance, and organizational evolution.

The Dictator (you) issues a decree. The company classifies it, runs discovery, designs, critiques itself, pauses for your approval, plans the Compose implementation, runs QA, and assembles a release candidate. A separate evolution graph proposes improvements to the company itself — with a shadow evaluation, your approval, and rollback.

**Read the constitution:** [`ui_ux-agent_company.md`](ui_ux-agent_company.md)

---

## Quick start

```bash
cd /home/pluto/main_projects/Team_of_agents_designing_UI_ux

python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# 1. Deterministic, zero-token demo
.venv/bin/uiux demo

# 2. Control room
.venv/bin/uiux serve --host 127.0.0.1 --port 8765
# open http://127.0.0.1:8765

# 3. Real model run through OpenCode (design-only)
.venv/bin/uiux run "Redesign the profile screen: identity-first, one primary action, calm media grid, all states" \
  --mode design --auto-approve --demo

# 4. Tests
.venv/bin/pytest -q
```

The default model is `modal/deepseek-v4.1-flash-1017` through the project-local OpenCode agent `uiux-worker`. Check what your OpenCode install can reach:

```bash
opencode models
```

---

## What is in the box

```
org/
  architecture_registry.yaml     # agents, tiers, models, pods, gates, metrics
  project_profile.yaml            # Dhunchat / Orphic binding, Target_ui, device matrix
  budget_policy.yaml               # token budget + thresholds
  schemas/                         # JSON Schema contracts
src/ui_ux_company/
  graph.py                         # LangGraph design company
  evolution.py                     # separate evolution graph
  gateway.py                       # OpenCode model gateway (text + images)
  target_ui.py                     # scan, manifest, vision analysis, diff
  agents.py                        # role runner
  prompts.py                       # role prompts + JSON output contracts
  api.py                           # FastAPI control room + SSE
  cli.py                           # uiux demo/run/serve/evolve...
  storage.py                       # SQLite runs, events, approvals, token ledger
  artifacts.py                     # versioned artifact store
  static/                          # dependency-free control room
tests/                             # 14 passing tests
target_ui_example/                 # generated placeholder reference image
ui_ux-agent_company.md             # full constitution
```

### Architecture at a glance

```mermaid
flowchart LR
  D["Dictator"] --> C["Control room"]
  C <--> G["LangGraph orchestrator"]
  G --> P["Tier 1–5 agent pods"]
  P <--> A["Versioned artifacts"]
  T["Target_ui"] --> P
  G -.-> L["LangSmith traces / evals"]
  A -.-> L
  L --> E["Org evolution auditor"]
  E --> C
```

---

## Control room

The SPA is a narrow left icon rail plus tabs:

- **Overview** — decree composer, recent runs, live timeline, compact org summary, approvals, budget, artifacts, Target_ui preview, evolution quick list.
- **Live Flow** — full-page hierarchical graph with CEO/Chief/tier leads/specialists, codenames A1–A36, five status categories: **Idle, Running, Completed, Waiting, Error**; current work labels, thin colored signal lines, company feed, zoom controls, and click-through agent detail. Terminal runs clear their live context; use History to restore.
- **Approvals** — sub-tabs for **Pending** and **Decision history**; source agent/codename, due time, overdue badge, approve/reject/dictate, and a **View proposal** link for evolution approvals. Stale escalations from terminal runs are auto-dismissed into history.
- **Artifacts** — run selector, preview, screenshot upload, Re-run QA.
- **Contract** — versioned design-contract editor with Save version and Re-run critique.
- **History** — global combined history of company runs and evolution audits; tap any entry to restore its flow, timeline, artifacts, contract, and outputs. When no company run or evolution is active, the live panels clear automatically; nothing is lost because it is in History.
- **Target_ui** — target + current-UI scan, recursive folder search, confidence/conflicts, and a rectangle annotation editor mapped to components.
- **Evolution** — Observe → Diagnose → Evaluate → Approve → Apply → Canary cards, stop, approve/reject/dictate, automated metric canary, promote, and registry rollback.
- **Settings** — active project switcher, project/current UI/Target_ui folders, all OpenCode models with a searchable picker, model test, editable token budget, autonomy, project validation, webhook/browser notifications, session/RBAC, audit log, and mock-data cleanup.

Hierarchy: **CEO A1 → Chief A2 → five tier leads A3–A7 → specialists A8+**. Tier leads now run real review gates before design, critique, implementation, and manager evaluation. The Chief receives a compact `company_brief` with the org chart, capabilities, gates, playbooks, and governance. A structured **company feed** records short handoffs, reviews, artifacts, feedback, escalations, and instructions — visible to the run, not a free-form chat.

---

## Commands

| Command | Purpose |
|---|---|
| `uiux demo` | Developer smoke test with deterministic mock models. Zero tokens. The production dashboard never uses mock data. |
| `uiux run "<decree>" --mode design` | Run a real design-only decree. |
| `uiux run "<decree>" --mode build` | Design + implementation plan. |
| `uiux run "<decree>" --mode ship` | Design + implementation + QA + release candidate. |
| `uiux serve` | Start the control room at `http://127.0.0.1:8765`. |
| `uiux target-ui scan ./target_ui_example --analyze` | Scan and vision-analyze Target_ui images. |
| `uiux evolve --focus quality` | Ask the independent org auditor for an evolution proposal. |
| `uiux budget` | Show token spend and remaining budget. |
| `uiux purge-mock` | Remove old mock/demo runs, approvals, artifacts, and mock token rows from the local DB. |
| `uiux registry` | Print the registry summary and Mermaid org chart. |
| `uiux models` | Show model aliases and the OpenCode backend. |
| `uiux health` | Check local configuration. |

---

## Model gateway: why OpenCode?

You asked to reuse the models already configured in OpenCode. The gateway shells out to:

```bash
opencode run --agent uiux-worker --format json --model <model> [--file image] "<prompt>"
```

`opencode.jsonc` defines `uiux-worker` with **all tools denied** (no edits, no shell, no reads, no web). It is a pure text/vision worker. The app never copies provider keys; OpenCode owns provider configuration.

Model aliases live in `org/architecture_registry.yaml`:

- `reasoner` — architecture, synthesis, critique, evolution
- `vision` — Target_ui analysis and visual review
- `coder` — implementation planning
- `critic` — independent review (use a different model in production for true independence)
- `cheap` — budget-pressure routing

Change the model in the registry or `.env`:

```bash
UIUX_OPENCODE_MODEL=modal/deepseek-v4.1-flash-1017
```

**Quota note:** free/paid OpenCode models can hit provider limits. If that happens, switch the registry model to one your account can reach. `uiux demo` always works without provider tokens.

---

## Target_ui

Drop reference images into `target_ui_example/` (or point `UIUX_TARGET_UI_PATH` at a folder). The company:

1. Hashes each image.
2. Maps it to a screen.
3. Uses a vision model to extract layout, components, color roles, typography, states, and confidence.
4. Writes `artifacts/target_ui/manifest.json`.
5. Uses the manifest in visual design and visual QA.

**Precedence:** target wins layout/composition; design tokens and Android/a11y law win color, type, and accessibility; conflicts escalate to the Dictator.

`target_ui_example/profile_reference.png` is a generated placeholder, not a real product screenshot. For competitor screenshots: use structure only, replace brand assets, and run legal/IP review.

---

## Evolve / Improve

The dashboard button and `uiux evolve` run a separate graph:

```
observe → diagnose → propose → evaluate → Dictator approval → apply safe changes
```

The auditor is independent from the orchestrator. It cannot approve or apply its own proposal. Safe mutations are limited to agent enabled/disabled, model alias swaps, gate thresholds, and budget caps. The previous registry version is backed up to `org/registry_history/`.

The current shadow evaluation is a heuristic MVP. Production should use LangSmith datasets/experiments and a golden task set before approval.

---

## Roborazzi is removed

Visual QA uses a **Screen Capture Adapter**, not Roborazzi:

- Primary: deterministic emulator/Waydroid state + UiAutomator idle wait + `adb exec-out screencap -p`.
- Optional: Gradle Managed Devices, Firebase Test Lab, manual upload.
- Diff: Pillow MVP; OpenCV/SSIM + OCR + color/contrast + region checks in production.
- Why: real GPU output for Liquid Glass/AGSL/RenderEffect; no JVM layoutlib limitations; tool-agnostic.

Upload screenshots to the control room via `POST /api/runs/{run_id}/screenshots` when you have real captures.

---

## Dashboard controls

- **Project directory:** Settings → Project directory → Browse. The selected path is injected into new-run context and validated for Gradle/Manifest/Compose/tokens.
- **Target_ui directory:** Settings or Target_ui tab → Browse; use **Search recursively** to find `target_ui` / `reference_ui` folders. Scan or Scan + analyze.
- **Current UI directory:** Settings → Current UI directory → Browse; use recursive search for `current_ui` / `screenshots_of_current_ui`. This is the audit/regression baseline.
- **Model:** Header model selector or Settings → OpenCode model. It lists every model returned by `opencode models`; the selected model is saved as the default for new runs. Click **Test** to ask the selected model who it is.
- **Budget:** Click the header budget pill or Settings → Monthly token budget → Edit. Changes persist to `org/budget_policy.yaml` and `.runtime/settings.json`.
- **Live Flow:** Full-page hierarchical graph. Icons show idle / running / completed / error / waiting. Agent cards show codenames and current work labels. Thin colored lines show handoffs, reviews, artifacts, feedback, escalations, and instructions. Click any agent for status, last event, artifacts, and token usage.
- **Modes:** Explore, Design, Build, Ship, Audit, Fix.
- **Run stop:** Live run and Live Flow have a Stop button. Default is graceful; the current model call is cancelled and the run/approvals are marked stopped.
- **Evolution:** Start audit, watch Observe → Diagnose → Evaluate → Approve → Apply → Canary cards, stop the audit, Approve / Reject / Dictate, and rollback an applied registry change. **Auto-apply safe changes** skips the approval pause only when you pre-approve it; unsafe structural changes still require deployment.
- **Design contract:** Contract tab → edit authoritative decisions/acceptance matrix → Save version → Re-run critique.
- **QA re-check:** Artifacts → upload screenshots → Re-run QA. It reuses the existing run and does not redo design.
- **Approvals:** Every pending decision shows its source agent/codename and due time. Approve / Reject / Dictate; decided cards move to Decision History.

## Production infrastructure

- **Projects:** `org/projects/*.yaml`; switch the active profile in Settings. Runs keep their historical state.
- **Auth/RBAC:** copy `org/users.yaml.example` to `org/users.yaml`, set `UIUX_AUTH=true`, and log in with a token. Roles: viewer, engineer, design_lead, dictator. Mutating endpoints are role-gated and audited.
- **Postgres checkpoints:** `docker compose up -d postgres minio`, then set:
  ```bash
  UIUX_CHECKPOINT_BACKEND=postgres
  UIUX_POSTGRES_DSN=postgresql://uiux:uiux_dev_password@localhost:5432/uiux
  UIUX_S3_BUCKET=uiux-artifacts
  UIUX_S3_ENDPOINT=http://localhost:9000
  UIUX_S3_ACCESS_KEY=uiux
  UIUX_S3_SECRET_KEY=uiux_dev_password
  ```
  SQLite remains the fallback. Artifacts are written locally and mirrored to S3/MinIO when configured.
- **Webhook notifications:** Settings → Notifications → webhook URL. Approval/failure events are POSTed as JSON. Browser alerts are opt-in.

## Token budget

`org/budget_policy.yaml` defaults to **100,000,000 tokens/month** as a placeholder. Replace it with your actual ceiling in the dashboard or:

```bash
UIUX_MONTHLY_TOKEN_BUDGET=250000000
```

The ledger records every model call (estimated tokens by default). At 80% non-critical work routes to the cheap alias; at 95% new runs require approval; at 100% non-critical work stops. 15% is reserved for incidents.

The dashboard shows used/remaining tokens, estimated cost, top agents, and top models.

---

## Code editing (build / ship / fix)

The company separates **design ownership** from **code ownership**, the same way a real product org does:

- **Design tier (A16–A18, A22):** owns tokens, screen specs, the design contract, and independent critique. It writes design artifacts, not application code.
- **Implementation tier (A19 Compose Implementer, A5 Engineering Lead):** owns code changes. After the Dictator approves the design gate, A19 invokes a real OpenCode `build` worker against the target Android repository.
- **Review and release:** A5 reviews the implementation contract, A6/A7 run QA, and the normal human/release gates still apply.

Code edits happen only in `build`, `ship`, and `fix` runs. `explore`, `design`, and `audit` never touch project code. The worker writes `code_edit_result.json` and `code_diff.patch` into the run artifacts.

**Modes** (Settings → Code editing):

- **Isolated worktree (default, recommended):** snapshots the target's current working tree (including tracked and untracked changes), creates branch `uiux/<run_id>` under `.runtime/worktrees/<run_id>`, edits and commits there, and captures a reviewable patch. The target checkout is untouched, so uncommitted local work is preserved and included as the baseline.
- **Direct working tree (unsafe):** edits the target checkout in place. Use only when you explicitly want that.

Toggle **Allow build/ship/fix agents to edit project files** and choose the mode in Settings. The defaults can also be set with `UIUX_CODE_EDITS`, `UIUX_CODE_EDIT_MODE`, `UIUX_CODE_WORKER_AGENT`, `UIUX_CODE_WORKER_MODEL`, and `UIUX_CODE_WORKER_TIMEOUT`.

---

## Current limitations (honest)

**Implemented in this build:** left rail, full-page flow, CEO/5 leads/codenames, real tier-lead review gates, company feed and signal lines, approval provenance/SLA/overdue, decision history, modes, model test, run stop, recursive folder search, current-UI baseline + visual diff, project validation, mock-data cleanup, QA re-check, design-contract editor, evolution rollback + metric canary, searchable model picker, flow zoom, target annotations, multi-project active switcher, token RBAC + audit log, webhook/browser notifications, Postgres checkpoint support, S3/MinIO artifact mirroring, and real build/ship/fix code workers with worktree isolation and reviewable diffs.

**Still limited / queued:**
- App metadata still uses SQLite unless migrated; Postgres currently backs LangGraph checkpoints.
- MinIO mirroring is best-effort; local artifacts remain primary.
- Auth is token-based RBAC, not OIDC/SSO.
- Multi-project switch is active-profile only; no simultaneous project runs or per-project budget isolation.
- Dynamic `add_agent` updates the registry/org chart; extra leads/specialists need a graph rebuild or generic task dispatch to execute inside the main pipeline.
- Canary is an automated metric heuristic over recent runs, not a full LangSmith golden-task shadow eval.
- Target annotations are rectangles with labels, not freehand regions.
- Notifications are webhook/browser; no email.
- Deeper LangSmith datasets/experiments and judge calibration remain.

Other limitations:

- **Local MVP:** SQLite + in-process event bus.
- **Code worker:** real OpenCode `build` edits in an isolated worktree by default; merge/promotion remains human-owned. Direct mode edits the checkout and is explicitly unsafe.
- **Visual QA** has the adapter contract and upload path; the full emulator/ADB automation is the next production step.
- **Functional/a11y QA** accepts test evidence; it does not yet drive UIAutomator/Espresso itself.
- **Evolution shadow eval** is heuristic; production needs LangSmith experiments and golden tasks.
- **Figma** is optional; the default source of truth is spec-first + Compose + screenshots.

The full production upgrade path is in [`ui_ux-agent_company.md`](ui_ux-agent_company.md), sections 14–19.

---

## API

Start the server, then:

```bash
curl http://127.0.0.1:8765/api/health
curl http://127.0.0.1:8765/api/registry
curl http://127.0.0.1:8765/api/budget
curl -X POST http://127.0.0.1:8765/api/runs \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Redesign the profile screen","mode":"design","backend":"mock","demo":true,"auto_approve":true}'
```

SSE: `GET /api/runs/{run_id}/events`

Approvals: `GET /api/approvals`, `POST /api/approvals/{id}/decide`

Evolution: `POST /api/evolve`, `GET /api/evolve`, `POST /api/evolve/{id}/approve`

---

## Tests

```bash
.venv/bin/pytest -q
```

Current coverage: registry validation, Target_ui scan, model gateway, mock graph end-to-end, API health, evolution proposal/reject.
