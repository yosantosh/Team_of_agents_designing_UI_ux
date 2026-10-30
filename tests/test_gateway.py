from __future__ import annotations

import asyncio
from pathlib import Path

from ui_ux_company.agents import AgentRunner
from ui_ux_company.config import Settings
from ui_ux_company.gateway import ModelGateway
from ui_ux_company.registry import load_registry
from ui_ux_company.storage import Storage


def test_mock_gateway_returns_json_and_records_usage(settings):
    registry = load_registry(settings)
    storage = Storage(settings.storage_db)
    gateway = ModelGateway(settings, registry, storage)

    result = asyncio.run(
        gateway.generate(
            "product_analyst",
            "Make a brief",
            {"prompt": "profile redesign"},
            backend="mock",
            run_id="run_test",
            task_id="task_test",
        )
    )
    assert result.data["change_type"] == "redesign"
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    budget = storage.budget_summary(settings.monthly_token_budget)
    assert budget["used_tokens"] > 0
    storage.close()


def test_agent_runner_maps_prompt_key(settings):
    registry = load_registry(settings)
    storage = Storage(settings.storage_db)
    gateway = ModelGateway(settings, registry, storage)
    runner = AgentRunner(gateway, registry)

    result = asyncio.run(
        runner.run(
            "android_platform_designer",
            "Extract Android constraints",
            {"prompt": "profile"},
            backend="mock",
            run_id="run_test",
        )
    )
    assert "constraints" in result.data
    storage.close()


def test_gateway_sends_large_prompt_via_stdin(settings, tmp_path):
    script = tmp_path / "fake-opencode"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "prompt = sys.stdin.read()\n"
        "print(json.dumps({'type': 'text', 'part': {'text': json.dumps({'ok': True, 'chars': len(prompt)})}}))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    settings.opencode_bin = str(script)
    settings.opencode_cwd = tmp_path
    registry = load_registry(settings)
    storage = Storage(settings.storage_db)
    gateway = ModelGateway(settings, registry, storage)
    try:
        result = asyncio.run(
            gateway.generate(
                "ia_interaction_designer",
                "Synthesize flows",
                {"blob": "x" * 150000},
                backend="opencode",
                run_id="run_stdin",
                task_id="synthesis",
            )
        )
        assert result.data["ok"] is True
        assert result.data["chars"] > 100000
    finally:
        storage.close()
