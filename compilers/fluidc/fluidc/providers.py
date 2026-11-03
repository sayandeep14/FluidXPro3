"""OpenAI and Google models, over their REST APIs (no extra packages). Claude is in llm.py.

Each generator keeps one append-only conversation per build, like ClaudeGenerator, and asks for
replies that match OUTPUT_SCHEMA. Both providers cache a repeated prompt prefix on their side.

Replies are streamed. A model can think for minutes before it writes anything, and networks drop
a connection that stays silent that long (seen live: closed after 60 s). OpenAI's Responses API
sends a keepalive event every 30 s while the model thinks; Gemini streams its thought summaries.
`post` is the HTTP call, so the tests can replace it.
"""

from __future__ import annotations

import http.client
import json
import os
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

from .errors import FluidError, Message
from .llm import Usage
from .prompts import OUTPUT_SCHEMA

# post(url, headers, body, timeout, stream) -> (status, parsed JSON body). With stream=True a
# successful reply is {"events": [(event name, data), ...]} from the server-sent events, without
# keepalives and `*.delta` events. Network errors raise OSError or http.client.HTTPException.
Post = Callable[[str, dict, dict, float, bool], "tuple[int, dict]"]

RETRY_STATUS = {408, 429, 500, 502, 503, 504, 529}
PROVIDERS = ("anthropic", "openai", "google")
DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "openai": "gpt-5", "google": "gemini-2.5-pro"}
KEY_NAMES = {"anthropic": ("ANTHROPIC_API_KEY",), "openai": ("OPENAI_API_KEY",),
             "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY")}
KEY_PAGES = {"anthropic": "https://console.anthropic.com", "openai": "https://platform.openai.com/api-keys",
             "google": "https://aistudio.google.com/apikey"}


def provider_for(llm: dict) -> str:
    """The provider for the `llm` settings. `provider` defaults to anthropic, so a model name that
    plainly belongs to another provider wins over that default."""
    model = str(llm.get("model") or "").lower()
    named = llm.get("provider") or "anthropic"
    if named != "anthropic":
        return named
    if model.startswith(("gpt-", "chatgpt-", "o1", "o3", "o4")):
        return "openai"
    if model.startswith(("gemini-", "gemma-")):
        return "google"
    return "anthropic"


def resolve(llm: dict) -> tuple[str, str]:
    """(provider, model). The schema's default model is a Claude one, so `"provider": "openai"`
    alone gets that provider's default model."""
    provider = provider_for(llm)
    model = str(llm.get("model") or "")
    if not model or (provider != "anthropic" and model == DEFAULT_MODELS["anthropic"]):
        model = DEFAULT_MODELS[provider]
    return provider, model


def api_key(provider: str) -> str | None:
    for name in KEY_NAMES[provider]:
        if os.environ.get(name):
            return os.environ[name]
    return None


def http_post(url: str, headers: dict, body: dict, timeout: float, stream: bool = False) -> tuple[int, dict]:
    if not url.startswith(("https://", "http://")):
        raise FluidError(Message("error", "FL0312", f"llm.baseUrl must be an http(s) address, not {url!r}"))
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",  # noqa: S310 (checked above)
                                 headers={"content-type": "application/json", **headers})
    try:
        # `timeout` is per read: a stream may run longer as long as something keeps arriving
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            if stream:
                return resp.status, {"events": read_events(resp)}
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read() or b"{}")
        except ValueError:
            data = {}
        return e.code, data


def read_events(lines) -> list[tuple[str, dict]]:
    """Server-sent events from an iterable of byte lines."""
    out: list[tuple[str, dict]] = []
    event, data = "message", []
    for raw in lines:
        line = raw.decode("utf-8").rstrip("\r\n")
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif not line and data:
            text = "\n".join(data)
            if text != "[DONE]" and event != "keepalive" and not event.endswith(".delta"):
                try:
                    out.append((event, json.loads(text)))
                except ValueError:
                    pass
            event, data = "message", []
    return out


@dataclass
class _RestGenerator:
    model: str
    effort: str = "high"
    max_retries: int = 2
    base_url: str = ""
    post: Post = http_post
    timeout: float = 600.0
    usage: Usage = field(default_factory=Usage)
    name = ""              # provider name, in messages
    provider = ""

    def __post_init__(self) -> None:
        key = api_key(self.provider)
        if key is None:
            names = " or ".join(KEY_NAMES[self.provider])
            raise FluidError(Message("error", "FL0302", f"no {self.name} API key found",
                                     fix=f"put {names}=... in secrets.env ({KEY_PAGES[self.provider]}); "
                                         "or build with --offline if fluid.lock is up to date"))
        self._key = key
        self.system = ""
        self.turns: list[tuple[str, str]] = []        # (role "user" | "assistant", text)

    # ---- the Generator protocol -------------------------------------------
    def start(self, system: str, request: str) -> dict:
        self.system = system
        self.turns = [("user", request)]
        return self._call()

    def follow_up(self, text: str) -> dict:
        self.turns.append(("user", text))
        return self._call()

    def text(self, system: str, prompt: str) -> str:
        """One plain-text answer, outside the build conversation (fluidc describe)."""
        return self._send(system, [("user", prompt)], structured=False).strip()

    # ---- shared ---------------------------------------------------------------
    def _call(self) -> dict:
        text = self._send(self.system, self.turns, structured=True)
        self.turns.append(("assistant", text))
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise FluidError(Message("error", "FL0311", f"the {self.name} model's reply was not valid JSON")) from None
        if not isinstance(data, dict):
            raise FluidError(Message("error", "FL0311", f"the {self.name} model's reply was not a JSON object"))
        return data

    def _request(self, url: str, headers: dict, body: dict, stream: bool = False) -> dict:
        """POST with retries on rate limits, overload and network errors; maps failures to FL03xx."""
        for attempt in range(self.max_retries + 1):
            last = attempt == self.max_retries
            try:
                status, data = self.post(url, headers, body, self.timeout, stream)
            except (OSError, http.client.HTTPException) as e:
                if last:
                    raise FluidError(Message("error", "FL0308", f"can't reach the {self.name} API "
                                             f"({type(e).__name__}: {e})",
                                             fix="check the network, or build with --offline from fluid.lock")) \
                        from None
                self._wait(attempt)
                continue
            if status < 400:
                return data
            if status in RETRY_STATUS and not last:
                self._wait(attempt)
                continue
            raise self._error(status, data)
        raise AssertionError("unreachable")

    def _wait(self, attempt: int) -> None:
        time.sleep(min(30.0, 2.0 ** attempt) * (0.5 + random.random()))  # noqa: S311 (jitter)

    def _error(self, status: int, data: dict) -> FluidError:
        err = data.get("error")
        detail = (err.get("message") if isinstance(err, dict) else str(err or "")) or f"HTTP {status}"
        if status == 401:
            m = Message("error", "FL0302", f"the {self.name} API rejected the credentials",
                        fix=f"check {' or '.join(KEY_NAMES[self.provider])} in secrets.env")
        elif status == 403:
            m = Message("error", "FL0303", f"the API key can't use {self.model}: {detail}")
        elif status == 404:
            m = Message("error", "FL0304", f"model `{self.model}` was not found: {detail}",
                        fix="check llm.model in fluid.config")
        elif status == 429:
            m = Message("error", "FL0305", f"rate limited by the {self.name} API (after retries)",
                        fix="try again in a minute")
        elif status == 400:
            m = Message("error", "FL0306", f"the API rejected the request: {detail}")
        else:
            m = Message("error", "FL0307", f"{self.name} API error {status}: {detail}")
        return FluidError(m)

    def _send(self, system: str, turns: list[tuple[str, str]], structured: bool) -> str:
        raise NotImplementedError


def _refused() -> FluidError:
    return FluidError(Message("error", "FL0309", "the model declined to generate this code",
                              fix="rephrase the blocks involved"))


def _truncated() -> FluidError:
    return FluidError(Message("error", "FL0310", "the model ran out of output space",
                              fix="split the .fluid file into smaller files"))


@dataclass
class OpenAIGenerator(_RestGenerator):
    """OpenAI's Responses API, streamed. With `base_url` (an OpenAI-compatible server or proxy) it
    uses Chat Completions instead, which those servers implement."""
    name = "OpenAI"
    provider = "openai"

    def _send(self, system: str, turns: list[tuple[str, str]], structured: bool) -> str:
        if self.base_url and "api.openai.com" not in self.base_url:
            return self._send_chat(system, turns, structured)
        body: dict = {"model": self.model, "instructions": system, "stream": True, "store": False,
                      "input": [{"role": r, "content": t} for r, t in turns]}
        if structured:
            body["text"] = {"format": {"type": "json_schema", "name": "fx3_blocks", "strict": True,
                                       "schema": OUTPUT_SCHEMA}}
        if _reasoning_model(self.model):
            body["reasoning"] = {"effort": _openai_effort(self.effort)}
        data = self._request("https://api.openai.com/v1/responses", self._auth(), body, stream=True)
        final: dict = {}
        for event, payload in data.get("events", []):
            if event == "error":
                raise self._error(500, {"error": payload})
            if event in ("response.completed", "response.incomplete", "response.failed"):
                final = payload.get("response") or {}
        if not final:
            raise FluidError(Message("error", "FL0308", "the OpenAI stream ended before the reply was complete",
                                     fix="try again; check the network"))
        u = final.get("usage") or {}
        cached = (u.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0
        self.usage.count(input_tokens=(u.get("input_tokens", 0) or 0) - cached,
                         output_tokens=u.get("output_tokens", 0) or 0, cache_read=cached)
        if final.get("status") == "failed":
            raise self._error(500, {"error": final.get("error") or {}})
        if final.get("status") == "incomplete":
            reason = (final.get("incomplete_details") or {}).get("reason", "")
            raise _refused() if reason == "content_filter" else _truncated()
        text = []
        for item in final.get("output") or []:
            if item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if part.get("type") == "refusal":
                    raise _refused()
                if part.get("type") == "output_text":
                    text.append(part.get("text", ""))
        return "".join(text)

    def _send_chat(self, system: str, turns: list[tuple[str, str]], structured: bool) -> str:
        body: dict = {"model": self.model,
                      "messages": [{"role": "developer", "content": system}]
                      + [{"role": r, "content": t} for r, t in turns]}
        if structured:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "fx3_blocks", "strict": True, "schema": OUTPUT_SCHEMA}}
        if _reasoning_model(self.model):
            body["reasoning_effort"] = _openai_effort(self.effort)
        data = self._request(self.base_url.rstrip("/") + "/chat/completions", self._auth(), body)
        u = data.get("usage") or {}
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
        self.usage.count(input_tokens=(u.get("prompt_tokens", 0) or 0) - cached,
                         output_tokens=u.get("completion_tokens", 0) or 0, cache_read=cached)
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        if msg.get("refusal") or choice.get("finish_reason") == "content_filter":
            raise _refused()
        if choice.get("finish_reason") == "length":
            raise _truncated()
        return msg.get("content") or ""

    def _auth(self) -> dict:
        return {"authorization": f"Bearer {self._key}"}


def _openai_effort(effort: str) -> str:
    return {"low": "low", "medium": "medium"}.get(effort, "high")


def _reasoning_model(model: str) -> bool:
    return model.lower().startswith(("o1", "o3", "o4", "gpt-5"))


@dataclass
class GeminiGenerator(_RestGenerator):
    """Google's Gemini API (streamGenerateContent, server-sent events)."""
    name = "Google"
    provider = "google"

    def _send(self, system: str, turns: list[tuple[str, str]], structured: bool) -> str:
        config: dict = {}
        if structured:
            config = {"responseMimeType": "application/json", "responseJsonSchema": OUTPUT_SCHEMA}
        if _thinking_gemini(self.model):
            config["thinkingConfig"] = {"includeThoughts": True}     # thought summaries keep the stream busy
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "model" if r == "assistant" else "user", "parts": [{"text": t}]}
                             for r, t in turns],
                "generationConfig": config}
        url = ((self.base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
               + f"/models/{self.model}:streamGenerateContent?alt=sse")
        data = self._request(url, {"x-goog-api-key": self._key}, body, stream=True)
        text: list[str] = []
        usage: dict = {}
        reason = ""
        for _event, chunk in data.get("events", []):
            if chunk.get("error"):
                raise self._error(int(chunk["error"].get("code") or 500), chunk)
            if (chunk.get("promptFeedback") or {}).get("blockReason"):
                raise _refused()
            usage = chunk.get("usageMetadata") or usage
            for cand in (chunk.get("candidates") or [])[:1]:
                reason = cand.get("finishReason") or reason
                for part in (cand.get("content") or {}).get("parts") or []:
                    if not part.get("thought"):
                        text.append(part.get("text", ""))
        cached = usage.get("cachedContentTokenCount", 0) or 0
        self.usage.count(input_tokens=(usage.get("promptTokenCount", 0) or 0) - cached,
                         output_tokens=(usage.get("candidatesTokenCount", 0) or 0)
                         + (usage.get("thoughtsTokenCount", 0) or 0),
                         cache_read=cached)
        if reason == "MAX_TOKENS":
            raise _truncated()
        if reason in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"):
            raise _refused()
        return "".join(text)


def _thinking_gemini(model: str) -> bool:
    return model.lower().startswith(("gemini-2.5", "gemini-3"))
