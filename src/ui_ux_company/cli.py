from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .agents import AgentRunner
from .artifacts import ArtifactStore
from .config import Settings
from .events import EventBus
from .evolution import EvolutionEngine
from .gateway import ModelGateway
from .graph import CompanyGraph
from .models import ApprovalDecision
from .registry import load_budget, load_profile, load_registry
from .storage import Storage
from .target_ui import analyze_manifest, load_manifest, save_manifest, scan_target_ui


app = typer.Typer(help="Native Android UI/UX Agent Company — LangGraph + OpenCode.")
console = Console()


def _settings() -> Settings:
    return Settings.from_env()


def _stack(settings: Settings):
    registry = load_registry(settings)
    profile = load_profile(settings)
    budget = load_budget(settings)
    storage = Storage(settings.storage_db)
    bus = EventBus(storage)
    artifacts = ArtifactStore(settings, storage)
    gateway = ModelGateway(settings, registry, storage)
    agents = AgentRunner(gateway, registry)
    return registry, profile, budget, storage, bus, artifacts, gateway, agents


def _print_summary(result: dict, *, artifacts: ArtifactStore | None = None) -> None:
    run_id = result.get("run_id")
    status = result.get("status")
    console.print(Panel.fit(f"[bold]Run[/bold] {run_id}\n[bold]Status[/bold] {status}", title="UI/UX Agent Company"))
    if result.get("critique_gate"):
        console.print(f"Design critique: [bold]{result.get('critique_gate')}[/bold]")
    if result.get("eval_gate"):
        console.print(f"Manager evaluation: [bold]{result.get('eval_gate')}[/bold]")
    if result.get("release"):
        release = result["release"]
        console.print(f"Release artifact: [bold]{release.get('artifact_type')}[/bold] simulated={release.get('simulated')}")
    if artifacts and run_id:
        table = Table(title="Artifacts")
        table.add_column("Name")
        table.add_column("Path")
        for artifact in artifacts.list(run_id):
            table.add_row(artifact["name"], artifact["path"])
        console.print(table)
    if result.get("__interrupt__"):
        console.print(Panel(json.dumps(result["__interrupt__"], indent=2, default=str), title="Paused for Dictator"))


@app.command()
def demo(
    prompt: str = typer.Argument("Redesign the profile screen for a premium, minimal social app"),
):
    """Run the full design flow with deterministic mock models. No provider tokens are spent."""
    settings = _settings()
    settings.model_backend = "mock"
    settings.durable_checkpoints = False
    registry, profile, budget, storage, bus, artifacts, gateway, agents = _stack(settings)
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
    async def _execute():
        try:
            return await company.run(
                prompt,
                mode="design",
                demo=True,
                auto_approve=True,
                backend="mock",
            )
        finally:
            await company.aclose()

    try:
        result = asyncio.run(_execute())
        _print_summary(result, artifacts=artifacts)
        console.print("\n[dim]Mock demo only. Real QA requires screenshots and the emulator/ADB capture harness.[/dim]")
    finally:
        storage.close()


@app.command()
def run(
    prompt: str = typer.Argument(..., help="The Dictator decree"),
    mode: str = typer.Option("design", "--mode", "-m", help="design | implement | full"),
    backend: Optional[str] = typer.Option(None, "--backend", help="opencode | mock (default from env)"),
    target_ui: bool = typer.Option(True, "--target-ui/--no-target-ui", help="Use the Target_ui folder when present"),
    auto_approve: bool = typer.Option(True, "--auto-approve/--interactive", help="Auto-approve design or pause for the Dictator"),
    demo: bool = typer.Option(False, "--demo", help="Allow simulated QA and label it clearly"),
):
    """Run one change through the company."""
    settings = _settings()
    registry, profile, budget, storage, bus, artifacts, gateway, agents = _stack(settings)
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
    async def _execute():
        try:
            result = await company.run(
                prompt,
                mode=mode,
                auto_approve=auto_approve,
                target_ui_enabled=target_ui,
                demo=demo,
                backend=backend,
            )
            _print_summary(result, artifacts=artifacts)
            while result.get("__interrupt__"):
                interrupt_value = result["__interrupt__"][0]["value"]
                console.print(Panel(json.dumps(interrupt_value, indent=2, default=str), title="Dictator inbox"))
                choice = typer.prompt("Decision (approve/reject/override)", default="approve")
                decision = {"decision": choice, "notes": typer.prompt("Optional note", default="")}
                result = await company.resume(result["run_id"], decision)
                _print_summary(result, artifacts=artifacts)
            return result
        finally:
            await company.aclose()

    try:
        asyncio.run(_execute())
    finally:
        storage.close()


@app.command()
def serve(
    host: str = typer.Option(None, "--host"),
    port: int = typer.Option(None, "--port"),
    reload: bool = typer.Option(False, "--reload"),
):
    """Start the control room and API."""
    import uvicorn
    from .api import create_app

    settings = _settings()
    if reload:
        # Uvicorn requires an import string for reload mode.
        uvicorn.run(
            "ui_ux_company.api:app",
            host=host or settings.host,
            port=port or settings.port,
            reload=True,
        )
    else:
        app_instance = create_app(settings)
        uvicorn.run(
            app_instance,
            host=host or settings.host,
            port=port or settings.port,
        )


@app.command("target-ui")
def target_ui(
    action: str = typer.Argument("scan", help="scan"),
    path: Optional[str] = typer.Argument(None, help="Folder containing reference images"),
    analyze: bool = typer.Option(True, "--analyze/--no-analyze"),
):
    """Scan and optionally analyze the Target_ui folder."""
    settings = _settings()
    registry, profile, budget, storage, bus, artifacts, gateway, agents = _stack(settings)
    try:
        manifest = scan_target_ui(
            settings,
            profile,
            path=Path(path).expanduser() if path else settings.target_ui_path,
            analyze=False,
            artifact_store=artifacts,
        )
        if analyze and manifest.images:
            manifest = asyncio.run(
                analyze_manifest(
                    manifest,
                    agent_runner=agents,
                    backend=settings.model_backend,
                    max_images=int(profile.target_ui.get("max_images_per_run", 8)),
                )
            )
            save_manifest(settings, profile, manifest)
        console.print(Panel(json.dumps(manifest.model_dump(), indent=2, default=str), title="Target_ui manifest"))
    finally:
        storage.close()


@app.command()
def registry():
    """Show the architecture registry summary and Mermaid graph."""
    settings = _settings()
    reg = load_registry(settings)
    console.print(Panel(json.dumps(reg.summary(), indent=2), title="Architecture registry"))
    console.print(reg.to_mermaid())


@app.command()
def budget():
    """Show token spend and budget status."""
    settings = _settings()
    storage = Storage(settings.storage_db)
    try:
        console.print(Panel(json.dumps(storage.budget_summary(settings.monthly_token_budget), indent=2), title="Token budget"))
    finally:
        storage.close()


@app.command()
def evolve(
    focus: str = typer.Option("quality", "--focus"),
    auto_approve: bool = typer.Option(False, "--auto-approve/--interactive"),
    backend: Optional[str] = typer.Option(None, "--backend"),
):
    """Ask the independent org auditor for an evidence-backed evolution proposal."""
    settings = _settings()
    registry, profile, budget, storage, bus, artifacts, gateway, agents = _stack(settings)
    engine = EvolutionEngine(
        settings,
        registry=registry,
        storage=storage,
        bus=bus,
        artifacts=artifacts,
        gateway=gateway,
        agents=agents,
    )
    async def _execute():
        try:
            result = await engine.start(
                focus=focus,
                auto_approve=auto_approve,
                backend=backend,
                model=settings.model_override,
            )
            console.print(Panel(json.dumps(result.get("proposal", result), indent=2, default=str), title="Evolution proposal"))
            if result.get("evaluation"):
                console.print(Panel(json.dumps(result["evaluation"], indent=2, default=str), title="Evaluation"))
            while result.get("__interrupt__"):
                interrupt_value = result["__interrupt__"][0]["value"]
                console.print(Panel(json.dumps(interrupt_value, indent=2, default=str), title="Approve evolution"))
                choice = typer.prompt("Decision (approve/reject)", default="approve")
                result = await engine.resume(result["proposal_id"], {"decision": choice})
                console.print(Panel(json.dumps(result, indent=2, default=str), title="Evolution result"))
            return result
        finally:
            await engine.aclose()

    try:
        asyncio.run(_execute())
    finally:
        storage.close()


@app.command("purge-mock")
def purge_mock():
    """Remove mock/demo runs, approvals, artifacts, and token rows from the local database."""
    settings = _settings()
    storage = Storage(settings.storage_db)
    try:
        result = storage.purge_mock_data()
        for run_id in result.get("purged_run_ids", []):
            import shutil
            shutil.rmtree(settings.artifacts_dir / "runs" / run_id, ignore_errors=True)
        console.print(Panel(json.dumps(result, indent=2), title="Mock data purged"))
    finally:
        storage.close()


@app.command()
def models():
    """Show the model aliases and backend configured for the company."""
    settings = _settings()
    registry = load_registry(settings)
    storage = Storage(settings.storage_db)
    try:
        gateway = ModelGateway(settings, registry, storage)
        console.print(Panel(json.dumps(gateway.list_models(), indent=2), title="Models"))
    finally:
        storage.close()


@app.command()
def health():
    """Check local configuration."""
    settings = _settings()
    registry = load_registry(settings)
    profile = load_profile(settings)
    storage = Storage(settings.storage_db)
    try:
        console.print(
            Panel(
                json.dumps(
                    {
                        "root": str(settings.root),
                        "registry": registry.summary(),
                        "project": profile.project.get("name"),
                        "target_ui": str(settings.target_ui_path),
                        "model_backend": settings.model_backend,
                        "model": settings.opencode_model,
                        "budget": storage.budget_summary(settings.monthly_token_budget),
                    },
                    indent=2,
                ),
                title="Health",
            )
        )
    finally:
        storage.close()


if __name__ == "__main__":
    app()
