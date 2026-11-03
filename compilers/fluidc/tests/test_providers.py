"""OpenAI and Google generators, against a fake HTTP layer (no network, no keys)."""

from __future__ import annotations

import json

import pytest
from fl_helpers import GRADES_REPLY, project, run_build

from fluidc import providers
from fluidc.builder import _default_generator
from fluidc.errors import FluidError
from fluidc.lock import Lock
from fluidc.prompts import OUTPUT_SCHEMA
from fluidc.providers import GeminiGenerator, OpenAIGenerator


class FakeHttp:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, json.loads(json.dumps(body))))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def openai_ok(content: dict | str, finish="stop", **message):
    text = content if isinstance(content, str) else json.dumps(content)
    return 200, {"choices": [{"message": {"content": text, **message}, "finish_reason": finish}],
                 "usage": {"prompt_tokens": 1000, "completion_tokens": 50,
                           "prompt_tokens_details": {"cached_tokens": 800}}}


def gemini_ok(content: dict | str, finish="STOP"):
    text = content if isinstance(content, str) else json.dumps(content)
    return 200, {"candidates": [{"content": {"role": "model", "parts": [{"text": "thinking...", "thought": True},
                                                                         {"text": text}]},
                                 "finishReason": finish}],
                 "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 40, "thoughtsTokenCount": 10,
                                   "cachedContentTokenCount": 600}}


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setattr(providers._RestGenerator, "_wait", lambda self, attempt: None)


@pytest.mark.parametrize("llm,expected", [
    ({}, ("anthropic", "claude-opus-5-5")),
    ({"model": "claude-sonnet-5-5"}, ("anthropic", "claude-sonnet-5-5")),
    ({"provider": "anthropic", "model": "gpt-5"}, ("openai", "gpt-5")),       # the schema default loses to the name
    ({"model": "o3-mini"}, ("openai", "o3-mini")),
    ({"model": "gemini-2.5-flash"}, ("google", "gemini-2.5-flash")),
    ({"provider": "openai", "model": "claude-opus-5-5"}, ("openai", "gpt-5")),  # only the default model was set
    ({"provider": "openai", "model": "my-local-model"}, ("openai", "my-local-model")),
    ({"provider": "google"}, ("google", "gemini-2.5-pro")),
])
def test_provider_and_model(llm, expected):
    assert providers.resolve(llm) == expected


def test_default_generator_picks_the_provider(keys):
    assert isinstance(_default_generator({"llm": {"model": "gpt-5"}}), OpenAIGenerator)
    g = _default_generator({"llm": {"model": "gemini-2.5-pro", "baseUrl": "https://proxy.example/v1beta"}})
    assert isinstance(g, GeminiGenerator) and g.base_url == "https://proxy.example/v1beta"


def test_openai_request_and_conversation(keys):
    http = FakeHttp(openai_ok(GRADES_REPLY), openai_ok({**GRADES_REPLY, "blocks": []}))
    g = OpenAIGenerator("gpt-5", "xhigh", post=http)
    assert g.start("SYSTEM", "REQUEST")["blocks"] == GRADES_REPLY["blocks"]
    g.follow_up("FIX IT")
    url, headers, body = http.calls[1]
    assert url == "https://api.openai.com/v1/chat/completions"
    assert headers["authorization"] == "Bearer sk-test-openai"
    assert body["response_format"]["json_schema"] == {"name": "fx3_blocks", "strict": True, "schema": OUTPUT_SCHEMA}
    assert body["reasoning_effort"] == "high"
    # append-only: the first turns are sent again unchanged, so the provider can reuse its cache
    assert [m["role"] for m in body["messages"]] == ["developer", "user", "assistant", "user"]
    assert body["messages"][0]["content"] == "SYSTEM" and body["messages"][3]["content"] == "FIX IT"
    assert http.calls[0][2]["messages"] == body["messages"][:2]
    assert (g.usage.calls, g.usage.input_tokens, g.usage.cache_read, g.usage.output_tokens) == (2, 400, 1600, 100)


def test_openai_non_reasoning_models_get_no_effort(keys):
    http = FakeHttp(openai_ok(GRADES_REPLY))
    OpenAIGenerator("gpt-4.1", post=http, base_url="http://localhost:11434/v1/").start("S", "R")
    url, _, body = http.calls[0]
    assert url == "http://localhost:11434/v1/chat/completions" and "reasoning_effort" not in body


def test_gemini_request_and_conversation(keys):
    http = FakeHttp(gemini_ok(GRADES_REPLY), gemini_ok(GRADES_REPLY))
    g = GeminiGenerator("gemini-2.5-pro", post=http)
    g.start("SYSTEM", "REQUEST")
    g.follow_up("FIX IT")
    url, headers, body = http.calls[1]
    assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent"
    assert headers["x-goog-api-key"] == "test-gemini"
    assert body["systemInstruction"] == {"parts": [{"text": "SYSTEM"}]}
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert json.loads(body["contents"][1]["parts"][0]["text"]) == GRADES_REPLY      # thoughts are left out
    assert body["generationConfig"] == {"responseMimeType": "application/json", "responseJsonSchema": OUTPUT_SCHEMA}
    assert (g.usage.input_tokens, g.usage.cache_read, g.usage.output_tokens) == (800, 1200, 100)


def test_plain_text_answers(keys):
    http = FakeHttp(openai_ok("  The block averages the grades.  "))
    assert OpenAIGenerator("gpt-5", post=http).text("S", "describe") == "The block averages the grades."
    assert "response_format" not in http.calls[0][2]
    http = FakeHttp(gemini_ok("Averages."))
    assert GeminiGenerator("gemini-2.5-pro", post=http).text("S", "describe") == "Averages."
    assert http.calls[0][2]["generationConfig"] == {}


def _code(answer, gen=OpenAIGenerator, model="gpt-5", retries=2) -> str:
    with pytest.raises(FluidError) as e:
        gen(model, post=FakeHttp(*answer) if isinstance(answer, list) else FakeHttp(answer),
            max_retries=retries).start("S", "R")
    return e.value.messages[0].code


def test_failures_have_clear_codes(keys):
    assert _code((401, {"error": {"message": "bad key"}})) == "FL0302"
    assert _code((403, {"error": {"message": "no access"}})) == "FL0303"
    assert _code((404, {"error": {"message": "no such model"}})) == "FL0304"
    assert _code((400, {"error": {"message": "bad schema"}})) == "FL0306"
    assert _code([(429, {})] * 3) == "FL0305"
    assert _code([OSError("down")] * 3) == "FL0308"
    assert _code([(503, {})] * 3, GeminiGenerator, "gemini-2.5-pro") == "FL0307"
    assert _code(openai_ok("", refusal="I can't help with that")) == "FL0309"
    assert _code(openai_ok("{\"blocks\": [", finish="length")) == "FL0310"
    assert _code(openai_ok("not json")) == "FL0311"
    assert _code(gemini_ok("{", finish="MAX_TOKENS"), GeminiGenerator, "gemini-2.5-pro") == "FL0310"
    assert _code(gemini_ok("", finish="SAFETY"), GeminiGenerator, "gemini-2.5-pro") == "FL0309"
    assert _code((200, {"promptFeedback": {"blockReason": "OTHER"}}), GeminiGenerator, "gemini-2.5-pro") == "FL0309"


def test_rate_limits_and_outages_are_retried(keys):
    http = FakeHttp((429, {}), OSError("reset"), (503, {}), openai_ok(GRADES_REPLY))
    assert OpenAIGenerator("gpt-5", post=http, max_retries=3).start("S", "R")["blocks"]
    assert len(http.calls) == 4


def test_a_missing_key_names_the_variable(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(FluidError) as e:
        GeminiGenerator("gemini-2.5-pro")
    m = e.value.messages[0]
    assert m.code == "FL0302" and "GEMINI_API_KEY or GOOGLE_API_KEY" in (m.fix or "")


def test_a_build_with_openai_records_the_model(tmp_path, keys):
    f = project(tmp_path, config='{"llm": {"model": "gpt-5"}}')
    http = FakeHttp(openai_ok(GRADES_REPLY))
    res = run_build(f, lambda settings: OpenAIGenerator(providers.resolve(settings["llm"])[1], post=http))
    assert res.ok, [m.render() for m in res.report.messages]
    lock = Lock.load(tmp_path / "fluid.lock")
    assert lock.toolchain["model"] == "gpt-5"
    assert {e.model for e in lock.blocks.values() if e.origin == "llm"} == {"gpt-5"}
