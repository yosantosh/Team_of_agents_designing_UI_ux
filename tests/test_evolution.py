from __future__ import annotations

import asyncio

from ui_ux_company.agents import AgentRunner
from ui_ux_company.artifacts import ArtifactStore
from ui_ux_company.events import EventBus
from ui_ux_company.evolution import EvolutionEngine
from ui_ux_company.gateway import ModelGateway
from ui_ux_company.registry import load_registry
from ui_ux_company.storage import Storage


def test_evolution_proposal_and_reject(settings):
    registry = load_registry(settings)
    storage = Storage(settings.storage_db)
    bus = EventBus(storage)
    artifacts = ArtifactStore(settings, storage)
    gateway = ModelGateway(settings, registry, storage)
    agents = AgentRunner(gateway, registry)
    engine = EvolutionEngine(
        settings,
        registry=registry,
        storage=storage,
        bus=bus,
        artifacts=artifacts,
        gateway=gateway,
        agents=agents,
    )
    try:
        result = asyncio.run(engine.start(focus="quality", auto_approve=False, backend="mock"))
        assert result.get("proposal")
        proposal_id = result["proposal_id"]
        assert result.get("__interrupt__")
        resumed = asyncio.run(engine.resume(proposal_id, {"decision": "reject", "notes": "test"}))
        assert resumed["status"] == "rejected"
        stored = storage.get_evolution(proposal_id)
        assert stored["status"] == "rejected"
    finally:
        asyncio.run(engine.aclose())
        storage.close()
