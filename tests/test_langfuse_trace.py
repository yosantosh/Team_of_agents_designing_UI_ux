from __future__ import annotations

from ui_ux_company.langfuse_trace import LangfuseTracer


def test_langfuse_disabled_is_a_safe_noop(settings):
    tracer = LangfuseTracer(settings)
    assert tracer.enabled is False
    assert tracer.langchain_callbacks("abc") == []
    assert tracer.create_trace_id(seed="run-1") is None
    with tracer.span("agent:test") as span:
        assert span is None
    tracer.score("abc", "critic_score", 0.9)
    tracer.score_run("abc", {"critique_gate": "pass"})
    health = tracer.health()
    assert health["status"] == "disabled"
    assert health["connected"] is False


def test_langfuse_trace_url_uses_configured_project(settings):
    settings.langfuse_enabled = True
    settings.langfuse_public_key = "pk-lf-test"
    settings.langfuse_secret_key = "sk-lf-test"
    settings.langfuse_base_url = "http://langfuse.local:3000/"
    settings.langfuse_project_id = "project-123"
    tracer = LangfuseTracer(settings)
    assert tracer.trace_url("abc123") == "http://langfuse.local:3000/project/project-123/traces/abc123"
