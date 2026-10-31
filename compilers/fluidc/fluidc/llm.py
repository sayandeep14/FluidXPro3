"""Talking to the model. One conversation per build: a first request, then fix-up turns.

The conversation is append-only (earlier turns are never edited), so the cached prefix (the
system prompt with the fx3 reference) is reused on every turn.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from .errors import FluidError, Message
from .prompts import OUTPUT_SCHEMA

FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def add(self, u) -> None:
        self.calls += 1
        self.input_tokens += getattr(u, "input_tokens", 0) or 0
        self.output_tokens += getattr(u, "output_tokens", 0) or 0
        self.cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
        self.cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0

    def summary(self) -> str:
        return (f"{self.calls} model call(s): {self.input_tokens} input tokens "
                f"(+{self.cache_read} read from cache, {self.cache_write} written to cache), "
                f"{self.output_tokens} output tokens")


class Generator(Protocol):
    usage: Usage

    def start(self, system: str, request: str) -> dict: ...

    def follow_up(self, text: str) -> dict: ...

    def text(self, system: str, prompt: str) -> str: ...


@dataclass
class ClaudeGenerator:
    """Generates fx3 with Claude through the official Anthropic SDK."""
    model: str = "claude-opus-5-5"
    effort: str = "high"
    max_retries: int = 2
    usage: Usage = field(default_factory=Usage)

    def __post_init__(self) -> None:
        try:
            import anthropic
        except ImportError:
            raise FluidError(Message("error", "FL0301", "the `anthropic` package is not installed",
                                     fix="pip install anthropic")) from None
        self._anthropic = anthropic
        # Credentials come from the environment: ANTHROPIC_API_KEY, or an `ant auth login` profile.
        self.client = anthropic.Anthropic(max_retries=self.max_retries)
        self.system: str = ""
        self.messages: list[dict] = []

    def start(self, system: str, request: str) -> dict:
        self.system = system
        self.messages = [{"role": "user", "content": request}]
        return self._call()

    def follow_up(self, text: str) -> dict:
        self.messages.append({"role": "user", "content": text})
        return self._call()

    def text(self, system: str, prompt: str) -> str:
        """One plain-text answer, outside the build conversation (fluidc describe)."""
        msg = self._send(system, [{"role": "user", "content": prompt}], structured=False)
        return next((b.text for b in msg.content if b.type == "text"), "").strip()

    def _call(self) -> dict:
        msg = self._send(self.system, self.messages, structured=True)
        # Append the whole reply (including thinking blocks) so later turns stay valid.
        self.messages.append({"role": "assistant", "content": msg.content})
        text = next((b.text for b in msg.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise FluidError(Message("error", "FL0311", "the model's reply was not valid JSON")) from None
        return data

    def _send(self, system: str, messages: list[dict], structured: bool):
        a = self._anthropic
        output_config: dict = {"effort": self.effort}
        if structured:
            output_config["format"] = {"type": "json_schema", "schema": OUTPUT_SCHEMA}
        try:
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=64000,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=messages,
                cache_control={"type": "ephemeral"},      # also cache the growing conversation
                output_config=output_config,
                betas=[FALLBACK_BETA],
                fallbacks="default",                      # if the model declines, a fallback model continues
            ) as stream:
                msg = stream.get_final_message()
        except a.AuthenticationError:
            raise FluidError(Message("error", "FL0302", "the Anthropic API rejected the credentials",
                                     fix="set ANTHROPIC_API_KEY, or run `ant auth login`")) from None
        except a.PermissionDeniedError as e:
            raise FluidError(Message("error", "FL0303", f"the API key can't use {self.model}: {e.message}")) from None
        except a.NotFoundError as e:
            raise FluidError(Message("error", "FL0304", f"model `{self.model}` was not found: {e.message}",
                                     fix="check llm.model in fluid.config")) from None
        except a.RateLimitError:
            raise FluidError(Message("error", "FL0305", "rate limited by the Anthropic API (after retries)",
                                     fix="try again in a minute")) from None
        except a.BadRequestError as e:
            raise FluidError(Message("error", "FL0306", f"the API rejected the request: {e.message}")) from None
        except a.APIStatusError as e:
            raise FluidError(Message("error", "FL0307", f"Anthropic API error {e.status_code}: {e.message}")) from None
        except a.APIConnectionError:
            raise FluidError(Message("error", "FL0308", "can't reach the Anthropic API",
                                     fix="check the network, or build with --offline from fluid.lock")) from None
        except TypeError as e:
            # the SDK raises this before sending anything when no credentials are configured
            if "authentication" not in str(e).lower():
                raise
            raise FluidError(Message("error", "FL0302", "no Anthropic credentials found",
                                     fix="set ANTHROPIC_API_KEY, or run `ant auth login`; or build with "
                                         "--offline if fluid.lock is up to date")) from None
        self.usage.add(msg.usage)
        if msg.stop_reason == "refusal":
            raise FluidError(Message("error", "FL0309", "the model declined to generate this code",
                                     fix="rephrase the blocks involved"))
        if msg.stop_reason == "max_tokens":
            raise FluidError(Message("error", "FL0310", "the model ran out of output space",
                                     fix="split the .fluid file into smaller files"))
        return msg


@dataclass
class ScriptedGenerator:
    """Replays prepared replies. Used by the tests, and handy for trying fluidc without an API key."""
    replies: list[dict]
    usage: Usage = field(default_factory=Usage)
    prompts: list[str] = field(default_factory=list)
    system: str = ""

    def start(self, system: str, request: str) -> dict:
        self.system = system
        self.prompts.append(request)
        return self._next()

    def follow_up(self, text: str) -> dict:
        self.prompts.append(text)
        return self._next()

    def text(self, system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        return str(self._next().get("text", ""))

    def _next(self) -> dict:
        self.usage.calls += 1
        if not self.replies:
            raise FluidError(Message("error", "FL0399", "the scripted generator has no more replies"))
        r = self.replies.pop(0)
        out = {"blocks": [], "assumptions": [], "questions": [], "guarantees": [], "custom_keys": [],
               "unsupported": []}
        out.update(r)
        return out
