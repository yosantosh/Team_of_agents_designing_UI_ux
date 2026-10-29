from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agents import AgentRunner
from .artifacts import ArtifactStore
from .auth import AuthService
from .config import Settings, save_runtime_settings
from .events import EventBus
from .evolution import EvolutionEngine
from .gateway import ModelGateway
from .graph import CompanyGraph
from .models import ApprovalDecision, EvolutionRequest, RunRequest, new_id
from .notifications import Notifier
from .project_validation import validate_android_project
from .registry import list_projects, load_budget, load_profile, load_registry, load_yaml
from .storage import Storage
from .target_ui import (
    analyze_manifest,
    discover_current_ui_path,
    load_current_manifest,
    load_manifest,
    save_manifest,
    scan_current_ui,
    scan_target_ui,
)


class TargetScanRequest(BaseModel):
    path: str | None = None
    analyze: bool = True


class TargetAnnotationRequest(BaseModel):
    image_id: str
    annotations: list[dict[str, Any]] = []


class SettingsUpdate(BaseModel):
    model: str | None = None
    monthly_token_budget: int | None = None
    project_dir: str | None = None
    target_ui_dir: str | None = None
    current_ui_dir: str | None = None
    autonomy_level: str | None = None
    webhook_url: str | None = None


class ModelTestRequest(BaseModel):
    model: str


class DesignContractUpdate(BaseModel):
    contract: dict[str, Any]


class CanaryRequest(BaseModel):
    scope: str = "one_surface"
    runs: int = 5


class AuthLogin(BaseModel):
    token: str


class AgentTaskRequest(BaseModel):
    agent_id: str
    goal: str
    context: dict[str, Any] = {}
    artifact_name: str | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    registry = load_registry(settings)
    profile = load_profile(settings)
    budget = load_budget(settings)
    if settings.project_dir:
        profile.data.setdefault("project", {})["repo"] = str(settings.project_dir)
    if settings.model_override:
        settings.opencode_model = settings.model_override
    storage = Storage(settings.storage_db)
    bus = EventBus(storage, Notifier(settings))
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
    app.state.run_tasks: dict[str, asyncio.Task] = {}
    app.state.evolution_tasks: dict[str, asyncio.Task] = {}
    app.state.opencode_models_cache: dict[str, Any] | None = None
    app.state.opencode_models_cached_at: float = 0.0
    app.state.project_validation_cache: dict[str, Any] = {}
    auth = AuthService(settings)
    app.state.auth = auth

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        exempt = (
            path in {"/", "/api/health", "/api/auth/login"}
            or path.startswith("/static/")
            or path.startswith("/docs")
            or path.startswith("/openapi")
            or path.startswith("/redoc")
        )
        user = auth.authenticate(None)
        if not exempt and auth.enabled:
            header = request.headers.get("authorization", "")
            token = header[7:].strip() if header.lower().startswith("bearer ") else request.headers.get("x-api-key")
            user = auth.authenticate(token)
            if user is None:
                return JSONResponse({"detail": "authentication required"}, status_code=401)
            if not auth.can_write(user, request.method, path):
                return JSONResponse({"detail": "forbidden for this role"}, status_code=403)
        request.state.user = user
        response = await call_next(request)
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and not exempt:
            actor = getattr(request.state, "user", None)
            storage.add_audit(
                getattr(actor, "name", "Local Dictator") if actor else "anonymous",
                getattr(actor, "role", "none") if actor else "none",
                f"{request.method.upper()} {path}",
                request.method,
                path,
                response.status_code,
            )
        return response

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
            "model": settings.model_override or settings.opencode_model,
            "project_dir": str(settings.project_dir or profile.project.get("repo") or ""),
            "target_ui_dir": str(settings.target_ui_path),
            "current_ui_dir": str(settings.current_ui_path),
            "current_ui_dir": str(settings.current_ui_path),
            "project_validation": _project_validation(),
            "autonomy_level": settings.autonomy_level,
            "governance": profile.autonomy.get("governance", {}),
            "budget": storage.budget_summary(settings.monthly_token_budget),
            "roborezzi": False,
            "visual_qa_backend": profile.visual_qa.get("backend", "emulator_adb"),
        }

    @app.post("/api/auth/login")
    async def auth_login(request: AuthLogin):
        user = auth.authenticate(request.token)
        if user is None:
            raise HTTPException(status_code=401, detail="invalid token")
        return {"name": user.name, "role": user.role, "auth_enabled": auth.enabled}

    @app.get("/api/auth/me")
    async def auth_me(request: Request):
        user = getattr(request.state, "user", None)
        if user is None:
            if not auth.enabled:
                return {"name": "Local Dictator", "role": "dictator", "auth_enabled": False}
            raise HTTPException(status_code=401, detail="authentication required")
        return {"name": user.name, "role": user.role, "auth_enabled": auth.enabled}

    @app.get("/api/audit")
    async def get_audit(limit: int = 200):
        return {"audit": storage.list_audit(limit)}

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

    @app.post("/api/models/test")
    async def test_model(request: ModelTestRequest):
        if not request.model:
            raise HTTPException(status_code=400, detail="model is required")
        return await gateway.test_model(request.model)

    @app.get("/api/budget")
    async def get_budget():
        summary = storage.budget_summary(settings.monthly_token_budget)
        summary["policy"] = budget.data
        return summary

    def _effective_project_dir() -> str:
        return str(settings.project_dir or profile.project.get("repo") or "")

    def _enrich_approval(approval: dict[str, Any]) -> dict[str, Any]:
        """Backfill provenance for approvals created before the source field existed."""
        gate_sources = {
            "human_design_approval": "independent_design_critic",
            "dictator_escalation": "chief_orchestrator",
            "evolution_approval": "org_evolution_auditor",
        }
        if not approval.get("from_agent"):
            approval["from_agent"] = gate_sources.get(str(approval.get("gate")), "chief_orchestrator")
        if not approval.get("from_codename"):
            approval["from_codename"] = registry.codename(str(approval["from_agent"]))
        return approval

    def _project_validation(force: bool = False) -> dict[str, Any]:
        path = _effective_project_dir()
        cached = app.state.project_validation_cache
        now = time.time()
        if not force and cached.get("path") == path and now - float(cached.get("at", 0)) < 300:
            return cached.get("data", {})
        data = validate_android_project(path or settings.root)
        app.state.project_validation_cache = {"path": path, "at": now, "data": data}
        return data

    @app.get("/api/settings")
    async def get_settings():
        return {
            "project_id": settings.project_id,
            "project_dir": _effective_project_dir(),
            "target_ui_dir": str(settings.target_ui_path),
            "current_ui_dir": str(settings.current_ui_path),
            "model": settings.model_override or settings.opencode_model,
            "monthly_token_budget": settings.monthly_token_budget,
            "autonomy_level": settings.autonomy_level,
            "governance": profile.autonomy.get("governance", {}),
            "backend": settings.model_backend,
            "webhook_url": settings.webhook_url,
            "registry_version": registry.version,
            "project_validation": _project_validation(),
        }

    @app.put("/api/settings")
    async def update_settings(request: SettingsUpdate):
        updates: dict[str, Any] = {}
        if request.model:
            settings.model_override = request.model
            settings.opencode_model = request.model
            updates["model"] = request.model
        if request.monthly_token_budget is not None:
            if request.monthly_token_budget < 1_000_000:
                raise HTTPException(status_code=400, detail="monthly_token_budget must be at least 1,000,000")
            settings.monthly_token_budget = int(request.monthly_token_budget)
            budget.data["monthly_total_tokens"] = int(request.monthly_token_budget)
            # Preserve comments/formatting in the policy file; only replace the value.
            original = budget.path.read_text(encoding="utf-8")
            updated_text = re.sub(
                r"(?m)^(monthly_total_tokens:\s*)\d+",
                rf"\g<1>{int(request.monthly_token_budget)}",
                original,
            )
            budget.path.write_text(updated_text, encoding="utf-8")
            updates["monthly_token_budget"] = int(request.monthly_token_budget)
        if request.project_dir:
            path = Path(request.project_dir).expanduser().resolve()
            if not path.is_dir():
                raise HTTPException(status_code=400, detail="project_dir must be an existing directory")
            settings.project_dir = path
            profile.data.setdefault("project", {})["repo"] = str(path)
            app.state.project_validation_cache = {}
            updates["project_dir"] = str(path)
        if request.target_ui_dir:
            path = Path(request.target_ui_dir).expanduser().resolve()
            if not path.is_dir():
                raise HTTPException(status_code=400, detail="target_ui_dir must be an existing directory")
            settings.target_ui_path = path
            profile.data.setdefault("target_ui", {})["path"] = str(path)
            updates["target_ui_dir"] = str(path)
        if request.current_ui_dir:
            path = Path(request.current_ui_dir).expanduser().resolve()
            if not path.is_dir():
                raise HTTPException(status_code=400, detail="current_ui_dir must be an existing directory")
            settings.current_ui_path = path
            profile.data.setdefault("current_ui", {})["path"] = str(path)
            updates["current_ui_dir"] = str(path)
        if request.autonomy_level:
            if request.autonomy_level not in {"L0", "L1", "L2", "L3", "L4"}:
                raise HTTPException(status_code=400, detail="autonomy_level must be L0..L4")
            settings.autonomy_level = request.autonomy_level
            updates["autonomy_level"] = request.autonomy_level
        if request.webhook_url is not None:
            settings.webhook_url = request.webhook_url
            updates["webhook_url"] = request.webhook_url
        if updates:
            save_runtime_settings(settings, **updates)
        return await get_settings()

    @app.get("/api/fs/list")
    async def list_directories(path: str | None = None):
        root = Path(path).expanduser().resolve() if path else Path.home()
        if not root.exists() or not root.is_dir():
            raise HTTPException(status_code=400, detail="path is not a directory")
        directories = []
        try:
            for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
                if entry.is_dir() and not entry.name.startswith("."):
                    directories.append({"name": entry.name, "path": str(entry.resolve())})
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="permission denied") from exc
        return {
            "path": str(root),
            "parent": str(root.parent) if root.parent != root else None,
            "directories": directories,
        }

    @app.post("/api/admin/purge-mock")
    async def purge_mock_data():
        result = storage.purge_mock_data()
        for run_id in result.get("purged_run_ids", []):
            shutil.rmtree(settings.artifacts_dir / "runs" / run_id, ignore_errors=True)
        await bus.publish(
            "admin",
            "admin.purged_mock",
            "Mock/demo data purged",
            "dictator",
            result,
        )
        return result

    @app.get("/api/opencode/models")
    async def opencode_models(refresh: bool = False):
        now = time.time()
        if (
            not refresh
            and app.state.opencode_models_cache
            and now - app.state.opencode_models_cached_at < 120
        ):
            return app.state.opencode_models_cache
        try:
            proc = await asyncio.create_subprocess_exec(
                settings.opencode_bin,
                "models",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=60)
            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")
            models = []
            for line in stdout.splitlines():
                candidate = line.strip()
                if not candidate or " " in candidate or "/" not in candidate:
                    continue
                models.append(candidate)
            grouped: dict[str, list[str]] = {}
            for model in models:
                provider = model.split("/", 1)[0]
                grouped.setdefault(provider, []).append(model)
            payload = {
                "models": models,
                "grouped": grouped,
                "default": settings.model_override or settings.opencode_model,
                "raw": stdout[-4000:],
                "stderr": stderr[-2000:],
            }
            app.state.opencode_models_cache = payload
            app.state.opencode_models_cached_at = now
            return payload
        except Exception as exc:
            return {
                "models": [],
                "grouped": {},
                "default": settings.model_override or settings.opencode_model,
                "error": str(exc),
            }

    # -------------------------------------------------------------- target ui
    @app.get("/api/target-ui")
    async def target_ui_status():
        manifest = load_manifest(settings, profile)
        return manifest.model_dump()

    @app.post("/api/target-ui/scan")
    async def target_ui_scan(request: TargetScanRequest):
        path = Path(request.path).expanduser() if request.path else settings.target_ui_path
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
                model=settings.model_override,
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

    @app.post("/api/target-ui/annotations")
    async def save_target_annotations(request: TargetAnnotationRequest):
        manifest = load_manifest(settings, profile)
        image = next((item for item in manifest.images if item.image_id == request.image_id), None)
        if image is None:
            raise HTTPException(status_code=404, detail="target image not found")
        image.annotations = request.annotations
        manifest.status = "annotated"
        save_manifest(settings, profile, manifest)
        await bus.publish(
            "target_ui",
            "target_ui.annotated",
            f"{len(request.annotations)} annotation(s) saved for {request.image_id}",
            "dictator",
            {"image_id": request.image_id, "annotations": request.annotations},
        )
        return image.model_dump()

    @app.get("/api/current-ui")
    async def current_ui_status():
        return load_current_manifest(settings, profile).model_dump()

    @app.post("/api/current-ui/scan")
    async def current_ui_scan(request: TargetScanRequest):
        path = Path(request.path).expanduser() if request.path else settings.current_ui_path
        manifest = scan_current_ui(settings, profile, path=path)
        return manifest.model_dump()

    @app.get("/api/current-ui/image")
    async def current_ui_image(path: str = Query(...)):
        roots = [settings.current_ui_path]
        for raw in profile.data.get("current_ui", {}).get("candidate_paths", []):
            candidate = Path(str(raw)).expanduser()
            if not candidate.is_absolute():
                candidate = settings.root / candidate
            roots.append(candidate)
        safe = _safe_path(path, roots)
        if not safe.exists():
            raise HTTPException(status_code=404, detail="image not found")
        return FileResponse(safe)

    @app.get("/api/fs/search")
    async def search_folders(
        root: str | None = None,
        patterns: str = "target_ui,current_ui,screenshots_of_current_ui,reference_ui",
        max_depth: int = 5,
    ):
        base = Path(root).expanduser().resolve() if root else settings.root
        if not base.is_dir():
            raise HTTPException(status_code=400, detail="root is not a directory")
        pattern_list = [p.strip().lower() for p in patterns.split(",") if p.strip()]
        skip = {".git", "node_modules", "build", ".gradle", ".idea", ".runtime", "artifacts", "__pycache__"}
        results = []
        base_depth = len(base.parts)
        for current, dirs, _files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
            current_path = Path(current)
            if len(current_path.parts) - base_depth > max_depth:
                dirs[:] = []
                continue
            name = current_path.name.lower()
            if any(pattern in name for pattern in pattern_list):
                images = [
                    p for p in current_path.iterdir()
                    if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                ]
                results.append({
                    "name": current_path.name,
                    "path": str(current_path),
                    "image_count": len(images),
                    "match": next((p for p in pattern_list if p in name), ""),
                })
                if len(results) >= 100:
                    break
        return {"root": str(base), "patterns": pattern_list, "results": results}

    @app.get("/api/projects")
    async def get_projects():
        return {"active": settings.project_id, "projects": list_projects(settings)}

    @app.post("/api/projects/{project_id}/activate")
    async def activate_project(project_id: str):
        path = settings.org_dir / "projects" / f"{project_id}.yaml"
        if not path.exists():
            raise HTTPException(status_code=404, detail="project profile not found")
        data = load_yaml(path)
        profile.data = data
        profile.path = path
        settings.project_id = project_id
        settings.profile_path = path
        repo = profile.project.get("repo")
        if repo:
            settings.project_dir = Path(str(repo)).expanduser()
        target = profile.target_ui.get("path")
        if target:
            candidate = Path(str(target)).expanduser()
            if not candidate.is_absolute():
                candidate = settings.root / candidate
            if candidate.exists():
                settings.target_ui_path = candidate
        current = profile.data.get("current_ui", {}).get("path")
        if current:
            candidate = Path(str(current)).expanduser()
            if not candidate.is_absolute():
                candidate = settings.root / candidate
            if candidate.exists():
                settings.current_ui_path = candidate
        save_runtime_settings(
            settings,
            project_id=project_id,
            project_dir=str(settings.project_dir) if settings.project_dir else None,
            target_ui_dir=str(settings.target_ui_path),
            current_ui_dir=str(settings.current_ui_path),
        )
        app.state.project_validation_cache = {}
        await bus.publish("project", "project.activated", f"Project {project_id} activated", "dictator", {"project_id": project_id})
        return await get_settings()

    @app.get("/api/project/validate")
    async def validate_project(path: str | None = None, refresh: bool = False):
        if path:
            return validate_android_project(path)
        return _project_validation(force=refresh)

    # ------------------------------------------------------------------ runs
    @app.post("/api/runs")
    async def create_run(request: RunRequest):
        run_id = new_id("run")
        task = _spawn(
            company.run(
                request.prompt,
                mode=request.mode,
                auto_approve=request.auto_approve,
                target_ui_enabled=request.target_ui_enabled,
                demo=request.demo,
                backend=request.backend,
                model=request.model or settings.model_override,
                autonomy_level=request.autonomy_level,
                run_id=run_id,
            )
        )
        app.state.run_tasks[run_id] = task
        return {"run_id": run_id, "status": "queued", "events_url": f"/api/runs/{run_id}/events"}

    def _contract_versions(run_id: str) -> list[dict[str, Any]]:
        artifacts = storage.list_artifacts(run_id)
        return [
            artifact for artifact in artifacts
            if artifact["name"].startswith("design_contract") and artifact["name"].endswith(".json")
        ]

    def _latest_contract(run_id: str) -> dict[str, Any] | None:
        versions = _contract_versions(run_id)
        if not versions:
            return None
        latest = sorted(versions, key=lambda item: item["created_at"])[-1]
        try:
            return json.loads(Path(latest["path"]).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    @app.get("/api/runs/{run_id}/design-contract")
    async def get_design_contract(run_id: str):
        if not storage.get_run(run_id):
            raise HTTPException(status_code=404, detail="run not found")
        contract = _latest_contract(run_id)
        if contract is None:
            raise HTTPException(status_code=404, detail="no design contract for this run")
        versions = [
            {"name": item["name"], "created_at": item["created_at"], "sha256": item["sha256"]}
            for item in _contract_versions(run_id)
        ]
        return {"contract": contract, "versions": versions}

    @app.put("/api/runs/{run_id}/design-contract")
    async def update_design_contract(run_id: str, request: DesignContractUpdate):
        if not storage.get_run(run_id):
            raise HTTPException(status_code=404, detail="run not found")
        contract = request.contract
        if not isinstance(contract, dict) or not contract:
            raise HTTPException(status_code=400, detail="contract must be a non-empty object")
        versions = _contract_versions(run_id)
        next_version = len(versions) + 1
        artifacts.write_json(run_id, f"design_contract.v{next_version}.json", contract, created_by="dictator")
        artifacts.write_json(run_id, "design_contract.json", contract, created_by="dictator")
        await bus.publish(run_id, "design_contract.updated", f"Design contract v{next_version} saved", "dictator", {"version": next_version})
        return {"contract": contract, "version": next_version, "versions": versions}

    @app.post("/api/runs/{run_id}/recritique")
    async def recritique_run(run_id: str):
        if not storage.get_run(run_id):
            raise HTTPException(status_code=404, detail="run not found")
        contract = _latest_contract(run_id)
        try:
            return await company.recritique(run_id, contract)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/runs/{run_id}/recheck-qa")
    async def recheck_run_qa(run_id: str):
        run = storage.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        try:
            return await company.recheck_qa(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/runs/{run_id}/stop")
    async def stop_run(run_id: str):
        run = storage.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        task = app.state.run_tasks.get(run_id)
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        return await company.stop(run_id)

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
            "messages": storage.list_agent_messages(run_id, limit=300),
            "approvals": [
                _enrich_approval(a)
                for a in storage.list_approvals(None, 200)
                if a.get("run_id") == run_id
            ],
        }

    @app.post("/api/runs/{run_id}/agent-task")
    async def dispatch_agent_task(run_id: str, request: AgentTaskRequest):
        if not storage.get_run(run_id):
            raise HTTPException(status_code=404, detail="run not found")
        try:
            return await company.run_agent_task(
                run_id,
                request.agent_id,
                request.goal,
                request.context,
                request.artifact_name,
            )
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}/messages")
    async def list_run_messages(run_id: str, after_id: int = 0):
        if not storage.get_run(run_id):
            raise HTTPException(status_code=404, detail="run not found")
        return {"messages": storage.list_agent_messages(run_id, after_id=after_id, limit=500)}

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
        query_status = None if status in {None, "", "all"} else status
        return {"approvals": [_enrich_approval(a) for a in storage.list_approvals(query_status, limit)]}

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
        proposal_id = new_id("evo")
        storage.create_evolution(
            {
                "proposal_id": proposal_id,
                "title": "Evolution audit running",
                "problem": "The independent auditor is observing traces, rework, cost, and gates.",
                "evidence": [],
                "changes": [],
                "expected_impact": {},
                "risks": [],
                "eval_plan": [],
                "rollback_plan": "No change applied yet.",
                "status": "evaluating",
            },
            focus=request.focus,
        )

        async def _run_evolution():
            try:
                await evolution.start(
                    focus=request.focus,
                    auto_approve=bool(request.auto_approve) if request.auto_approve is not None else False,
                    backend=settings.model_backend,
                    model=settings.model_override,
                    proposal_id=proposal_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await bus.publish(
                    proposal_id,
                    "evolution.failed",
                    f"Evolution failed: {exc}",
                    "org_evolution_auditor",
                    {"error": str(exc)},
                )

        task = _spawn(_run_evolution())
        app.state.evolution_tasks[proposal_id] = task
        return {"proposal_id": proposal_id, "status": "running"}

    @app.get("/api/evolve")
    async def list_evolutions(limit: int = 50):
        return {"proposals": storage.list_evolutions(limit)}

    @app.get("/api/evolve/{proposal_id}")
    async def get_evolution(proposal_id: str):
        proposal = storage.get_evolution(proposal_id)
        if not proposal:
            raise HTTPException(status_code=404, detail="proposal not found")
        state = await evolution.get_state(proposal_id)
        return {
            "proposal": proposal,
            "state": state,
            "artifacts": storage.list_artifacts(proposal_id),
            "events": storage.list_events(proposal_id, limit=300),
            "pending": bool(storage.pending_approval_for_run(proposal_id, "evolution_approval")),
        }

    @app.get("/api/evolve/{proposal_id}/events")
    async def stream_evolution_events(proposal_id: str, after_id: int = 0):
        if not storage.get_evolution(proposal_id):
            raise HTTPException(status_code=404, detail="proposal not found")

        async def gen():
            async for event in bus.stream(proposal_id, after_id=after_id):
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/evolve/{proposal_id}/canary")
    async def canary_evolution(proposal_id: str, request: CanaryRequest):
        try:
            return await evolution.canary(proposal_id, scope=request.scope, runs=request.runs)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/evolve/{proposal_id}/promote")
    async def promote_evolution(proposal_id: str):
        try:
            return await evolution.promote(proposal_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/evolve/{proposal_id}/rollback")
    async def rollback_evolution(proposal_id: str):
        proposal = storage.get_evolution(proposal_id)
        if not proposal:
            raise HTTPException(status_code=404, detail="proposal not found")
        try:
            return await evolution.rollback(proposal_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/evolve/{proposal_id}/stop")
    async def stop_evolution(proposal_id: str):
        proposal = storage.get_evolution(proposal_id)
        if not proposal:
            raise HTTPException(status_code=404, detail="proposal not found")
        task = app.state.evolution_tasks.get(proposal_id)
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        return await evolution.stop(proposal_id)

    @app.post("/api/evolve/{proposal_id}/approve")
    async def approve_evolution(proposal_id: str, decision: ApprovalDecision):
        proposal = storage.get_evolution(proposal_id)
        if not proposal:
            raise HTTPException(status_code=404, detail="proposal not found")
        if proposal["status"] in {"stopped", "rejected"}:
            raise HTTPException(status_code=409, detail=f"proposal is {proposal['status']}")
        result = await evolution.resume(proposal_id, decision.model_dump())
        return result

    @app.get("/api/evolve/{proposal_id}/artifacts")
    async def list_evolution_artifacts(proposal_id: str):
        return {"artifacts": storage.list_artifacts(proposal_id)}

    return app


app = create_app()
