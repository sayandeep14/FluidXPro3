"""The real Claude request shape (with a fake SDK client), and the fx3 reference staying truthful."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fluidc import prompts
from fluidc.llm import FALLBACK_BETA, ClaudeGenerator
from fluidc.verify import compile_source


class _FakeStream:
    def __init__(self, recorder, kwargs):
        self.kwargs = kwargs
        recorder.append({**kwargs, "messages": list(kwargs["messages"])})   # snapshot

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        data = {"blocks": [{"id": "x", "fx3": "int a = 1;"}], "assumptions": [], "questions": [],
                "guarantees": [], "custom_keys": [], "unsupported": []}
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=json.dumps(data))],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=100,
                                  cache_creation_input_tokens=0))


def test_claude_request_shape(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    g = ClaudeGenerator(model="claude-opus-5-5", effort="high")
    calls: list[dict] = []
    g.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(
        stream=lambda **kw: _FakeStream(calls, kw))))
    data = g.start(prompts.SYSTEM_PROMPT, "generate please")
    assert data["blocks"][0]["fx3"] == "int a = 1;"
    kw = calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}   # the big reference is cached
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["output_config"]["effort"] == "high"
    assert kw["fallbacks"] == "default" and kw["betas"] == [FALLBACK_BETA]
    assert "thinking" not in kw and "temperature" not in kw
    # a follow-up appends to the conversation (the whole earlier reply is kept)
    g.follow_up("fix this")
    msgs = calls[1]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert any(getattr(b, "type", "") == "thinking" for b in msgs[1]["content"])
    assert g.usage.calls == 2 and g.usage.cache_read == 200


def test_output_schema_is_strict():
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node["required"]) == set(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(prompts.OUTPUT_SCHEMA)


def test_reference_examples_compile():
    """Every example in fx3_reference.md is copied into these files; fx3c must accept them."""
    for name in ("reference_examples.fx3", "reference_stdlib_examples.fx3"):
        src = Path(__file__).with_name(name).read_text(encoding="utf-8")
        res = compile_source(src, name)
        assert res.ok, res.report()


def test_stdlib_reference_is_generated_from_the_registry():
    from fx3c.stdlib import MODULES
    for mod, types in MODULES.items():
        for t in types:
            for m in t.members:
                head = f"{t.name}.{m.name}(" if m.static else f"value.{m.name}("
                assert head in prompts.REFERENCE, head
