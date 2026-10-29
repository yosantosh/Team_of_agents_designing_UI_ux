from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agents import AgentRunner
from .artifacts import ArtifactStore
from .config import Settings
from .events import EventBus
from .evolution import EvolutionEngine
from .gateway import ModelGateway
from .graph import CompanyGraph
from .models import ApprovalDecision, EvolutionRequest, RunRequest, new_id
from .registry import load_budget, load_profile, load_registry
from .storage import Storage
from .target_ui import analyze_manifest, load_manifest, save_manifest, scan_target_ui


class TargetScanRequest(BaseModel):
    path: str | None = None
    analyze: bool = True


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    registry = load_registry(settings)
    profile = load_profile(settings)
    budget = load_budget(settings)
    storage = Storage(settings.storage_db)
    bus = EventBus(storage)
    artifacts = ArtifactStore(settings, storage)
    gateway = ModelGateway(settings, registry, storage)
    agents = AgentRunner(gateway, registry)
    company = CompanyGraph(
        settings,
        registry=registry,
        profile=profile,
        budget=budget,
        storage=storage,
        bus=bus,
        artifacts=artifacts,
        gateway=gateway,
        agents=agents,
    )
    evolution = EvolutionEngine(
        settings,
        registry=registry,
        storage=storage,
        bus=bus,
        artifacts=artifacts,
        gateway=gateway,
        agents=agents,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await company.aclose()
        await evolution.aclose()
        storage.close()

    app = FastAPI(
        title="UI/UX Agentic Company",
        version="0.1.0",
        description="LangGraph control plane, OpenCode model gateway, Target_ui ingestion, evolution loop, and Dictator control room.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = settings
    app.state.registry = registry
    app.state.profile = profile
    app.state.budget = budget
    app.state.storage = storage
    app.state.bus = bus
    app.state.artifacts = artifacts
    app.state.company = company
    app.state.evolution = evolution
    app.state.tasks = set()

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    def _spawn(coro):
        task = asyncio.create_task(coro)
        app.state.tasks.add(task)
        task.add_done_callback(lambda t: app.state.tasks.discard(t))
        return task

    def _safe_path(raw: str, allowed_roots: list[Path]) -> Path:
        path = Path(raw).expanduser().resolve()
        for root in allowed_roots:
            root = root.expanduser().resolve()
            try:
                path.relative_to(root)
                return path
            except ValueError:
                continue
        raise HTTPException(status_code=403, detail="path is outside the allowed roots")

    # ------------------------------------------------------------------ meta
    @app.get("/")
    async def index():
        index_path = static_dir / "index.html"
        if not index_path.exists():
            return JSONResponse({"service": "uiux-agent-company", "dashboard": "not installed"})
        return FileResponse(index_path)

    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "service": "uiux-agent-company",
            "version": "0.1.0",
            "registry_version": registry.version,
            "model_backend": settings.model_backend,
            "model": settings.opencode_model,
            "autonomy_level": settings.autonomy_level,
            "budget": storage.budget_summary(settings.monthly_token_budget),
            "roborezzi": False,
            "visual_qa_backend": profile.visual_qa.get("backend", "emulator_adb"),
        }

    @app.get("/api/registry")
    async def get_registry():
        return {
            "summary": registry.summary(),
            "registry": registry.data,
            "mermaid": registry.to_mermaid(),
        }

    @app.get("/api/project")
    async def get_project():
        return profile.data

    @app.get("/api/models")
    async def get_models():
        return gateway.list_models()

    @app.get("/api/budget")
    async def get_budget():
        summary = storage.budget_summary(settings.monthly_token_budget)
        summary["policy"] = budget.data
        return summary

    # -------------------------------------------------------------- target ui
    @app.get("/api/target-ui")
    async def target_ui_status():
        manifest = load_manifest(settings, profile)
        return manifest.model_dump()

    @app.post("/api/target-ui/scan")
    async def target_ui_scan(request: TargetScanRequest):
        path = Path(request.path).expanduser() if request.path else None
        manifest = scan_target_ui(
            settings,
            profile,
            path=path,
            analyze=False,
        )
        if request.analyze and manifest.images:
            manifest = await analyze_manifest(
                manifest,
                agent_runner=agents,
                backend=settings.model_backend,
                max_images=int(profile.target_ui.get("max_images_per_run", 8)),
            )
            save_manifest(settings, profile, manifest)
        return manifest.model_dump()

    @app.get("/api/target-ui/image")
    async def target_ui_image(path: str = Query(...)):
        roots = [settings.target_ui_path]
        for raw in profile.target_ui.get("candidate_paths", []):
            candidate = Path(str(raw)).expanduser()
            if not candidate.is_absolute():
                candidate = settings.root / candidate
            roots.append(candidate)
        safe = _safe_path(path, roots)
        if not safe.exists():
            raise HTTPException(status_code=404, detail="image not found")
        return FileResponse(safe)

    # ------------------------------------------------------------------ runs
    @app.post("/api/runs")
    async def create_run(request: RunRequest):
        run_id = new_id("run")
        _spawn(
            company.run(
                request.prompt,
                mode=request.mode,
                auto_approve=request.auto_approve,
                target_ui_enabled=request.target_ui_enabled,
                demo=request.demo,
                backend=request.backend,
                autonomy_level=request.autonomy_level,
                run_id=run_id,
            )
        )
        return {"run_id": run_id, "status": "queued", "events_url": f"/api/runs/{run_id}/events"}

    @app.get("/api/runs")
    async def list_runs(limit: int = 50):
        return {"runs": storage.list_runs(limit)}

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str):
        run = storage.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        state = await company.get_state(run_id)
        return {
            "run": run,
            "state": state,
            "artifacts": storage.list_artifacts(run_id),
            "events": storage.list_events(run_id, limit=200),
            "approvals": [a for a in storage.list_approvals(None, 200) if a.get("run_id") == run_id],
        }

    @app.get("/api/runs/{run_id}/events")
    async def stream_events(run_id: str, after_id: int = 0):
        run = storage.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")

        async def gen():
            async for event in bus.stream(run_id, after_id=after_id):
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/runs/{run_id}/artifacts")
    async def list_run_artifacts(run_id: str):
        return {"artifacts": storage.list_artifacts(run_id)}

    @app.get("/api/artifacts/{run_id}/{name}")
    async def get_artifact(run_id: str, name: str):
        artifact = storage.get_artifact(run_id, name)
        if not artifact:
            raise HTTPException(status_code=404, detail="artifact not found")
        path = Path(artifact["path"]).resolve()
        run_root = (settings.artifacts_dir / "runs" / run_id).resolve()
        try:
            path.relative_to(run_root)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="artifact path outside run directory") from exc
        if not path.exists():
            raise HTTPException(status_code=404, detail="artifact file missing")
        return FileResponse(path, media_type=artifact.get("content_type") or "application/octet-stream")

    @app.post("/api/runs/{run_id}/screenshots")
    async def upload_screenshots(run_id: str, files: list[UploadFile] = File(...)):
        run = storage.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        target = artifacts.screenshots_dir(run_id)
        saved = []
        for upload in files:
            suffix = Path(upload.filename or "shot.png").suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                raise HTTPException(status_code=400, detail=f"unsupported image: {upload.filename}")
            data = await upload.read()
            if len(data) > 12 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="image exceeds 12MB")
            name = "".join(ch for ch in (upload.filename or "shot.png") if ch.isalnum() or ch in "._-")
            path = target / name
            path.write_bytes(data)
            saved.append(str(path))
        await bus.publish(run_id, "screenshots.uploaded", f"{len(saved)} screenshot(s) uploaded", "dictator", {"files": saved})
        return {"saved": saved, "note": "QA evidence is now available; rerun QA or start a new run with the same spec."}

    @app.post("/api/runs/{run_id}/approve")
    async def approve_run(run_id: str, decision: ApprovalDecision):
        run = storage.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        approval = storage.pending_approval_for_run(run_id)
        if not approval:
            raise HTTPException(status_code=409, detail="no pending approval for this run")
        payload = decision.model_dump()
        _spawn(company.resume(run_id, payload))
        return {"status": "resumed", "approval_id": approval["approval_id"], "decision": payload}

    @app.get("/api/approvals")
    async def list_approvals(status: str | None = "pending", limit: int = 100):
        return {"approvals": storage.list_approvals(status, limit)}

    @app.post("/api/approvals/{approval_id}/decide")
    async def decide_approval(approval_id: str, decision: ApprovalDecision):
        approval = storage.get_approval(approval_id)
        if not approval:
            raise HTTPException(status_code=404, detail="approval not found")
        if approval["status"] != "pending":
            raise HTTPException(status_code=409, detail="approval already decided")
        payload = decision.model_dump()
        if approval["gate"] == "evolution_approval":
            _spawn(evolution.resume(approval["run_id"], payload))
        elif approval.get("run_id"):
            _spawn(company.resume(approval["run_id"], payload))
        else:
            raise HTTPException(status_code=409, detail="approval has no run")
        return {"status": "resumed", "approval_id": approval_id, "decision": payload}

    # -------------------------------------------------------------- evolution
    @app.post("/api/evolve")
    async def evolve(request: EvolutionRequest):
        result = await evolution.start(
            focus=request.focus,
            auto_approve=bool(request.auto_approve) if request.auto_approve is not None else False,
            backend=settings.model_backend,
        )
        return result

    @app.get("/api/evolve")
    async def list_evolutions(limit: int = 50):
        return {"proposals": storage.list_evolutions(limit)}

    @app.get("/api/evolve/{proposal_id}")
    async def get_evolution(proposal_id: str):
        proposal = storage.get_evolution(proposal_id)
        if not proposal:
            raise HTTPException(status_code=404, detail="proposal not found")
        state = await evolution.get_state(proposal_id)
        return {"proposal": proposal, "state": state, "artifacts": storage.list_artifacts(proposal_id)}

    @app.post("/api/evolve/{proposal_id}/approve")
    async def approve_evolution(proposal_id: str, decision: ApprovalDecision):
        proposal = storage.get_evolution(proposal_id)
        if not proposal:
            raise HTTPException(status_code=404, detail="proposal not found")
        result = await evolution.resume(proposal_id, decision.model_dump())
        return result

    @app.get("/api/evolve/{proposal_id}/artifacts")
    async def list_evolution_artifacts(proposal_id: str):
        return {"artifacts": storage.list_artifacts(proposal_id)}

    return app


app = create_app()
