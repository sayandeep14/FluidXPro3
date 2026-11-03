"""OpenAI and Google models, over their REST APIs (no extra packages). Claude is in llm.py.

Each generator keeps one append-only conversation per build, like ClaudeGenerator, and asks for
replies that match OUTPUT_SCHEMA. Both providers cache a repeated prompt prefix on their side.
`post` is the HTTP call, so the tests can replace it.
"""

from __future__ import annotations

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

# post(url, headers, body, timeout) -> (status, parsed JSON body). Network errors raise OSError.
Post = Callable[[str, dict, dict, float], "tuple[int, dict]"]

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


def http_post(url: str, headers: dict, body: dict, timeout: float) -> tuple[int, dict]:
    if not url.startswith(("https://", "http://")):
        raise FluidError(Message("error", "FL0312", f"llm.baseUrl must be an http(s) address, not {url!r}"))
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",  # noqa: S310 (checked above)
                                 headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read() or b"{}")
        except ValueError:
            data = {}
        return e.code, data


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

    def _request(self, url: str, headers: dict, body: dict) -> dict:
        """POST with retries on rate limits, overload and network errors; maps failures to FL03xx."""
        for attempt in range(self.max_retries + 1):
            last = attempt == self.max_retries
            try:
                status, data = self.post(url, headers, body, self.timeout)
            except OSError:
                if last:
                    raise FluidError(Message("error", "FL0308", f"can't reach the {self.name} API",
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
    """OpenAI's Chat Completions API. `base_url` also reaches OpenAI-compatible servers."""
    name = "OpenAI"
    provider = "openai"

    def _send(self, system: str, turns: list[tuple[str, str]], structured: bool) -> str:
        body: dict = {"model": self.model,
                      "messages": [{"role": "developer", "content": system}]
                      + [{"role": r, "content": t} for r, t in turns]}
        if structured:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "fx3_blocks", "strict": True, "schema": OUTPUT_SCHEMA}}
        if _reasoning_model(self.model):
            body["reasoning_effort"] = {"low": "low", "medium": "medium"}.get(self.effort, "high")
        url = (self.base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
        data = self._request(url, {"authorization": f"Bearer {self._key}"}, body)
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


def _reasoning_model(model: str) -> bool:
    return model.lower().startswith(("o1", "o3", "o4", "gpt-5"))


@dataclass
class GeminiGenerator(_RestGenerator):
    """Google's Gemini API (generateContent)."""
    name = "Google"
    provider = "google"

    def _send(self, system: str, turns: list[tuple[str, str]], structured: bool) -> str:
        config: dict = {}
        if structured:
            config = {"responseMimeType": "application/json", "responseJsonSchema": OUTPUT_SCHEMA}
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "model" if r == "assistant" else "user", "parts": [{"text": t}]}
                             for r, t in turns],
                "generationConfig": config}
        url = ((self.base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
               + f"/models/{self.model}:generateContent")
        data = self._request(url, {"x-goog-api-key": self._key}, body)
        u = data.get("usageMetadata") or {}
        cached = u.get("cachedContentTokenCount", 0) or 0
        self.usage.count(input_tokens=(u.get("promptTokenCount", 0) or 0) - cached,
                         output_tokens=(u.get("candidatesTokenCount", 0) or 0) + (u.get("thoughtsTokenCount", 0) or 0),
                         cache_read=cached)
        if (data.get("promptFeedback") or {}).get("blockReason"):
            raise _refused()
        cand = (data.get("candidates") or [{}])[0]
        reason = cand.get("finishReason", "STOP")
        if reason == "MAX_TOKENS":
            raise _truncated()
        if reason in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"):
            raise _refused()
        parts = (cand.get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts if not p.get("thought"))
