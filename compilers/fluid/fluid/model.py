"""`fluid model`: which model writes the code, and switching it.

    fluid model                    the provider and model in use, and which model wrote each block (from fluid.lock)
    fluid model gpt-5              use gpt-5 from now on (edits fluid.config, keeping its comments)
    fluid model my-model --provider openai

The choice lives in fluid.config (`llm.model`, `llm.provider`). fluid.lock records which model wrote
each block, so a build stays reproducible: blocks are regenerated with the new model on the next
online build, and `fluid build --offline` keeps working only while the lock matches.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from fluidc import providers
from fluidc.settings import defaults, load_config, load_jsonc, validate


def config_path(folder: Path) -> Path:
    return folder / "fluid.config"


def current(folder: Path) -> tuple[str, str, bool]:
    """(provider, model, whether fluid.config sets the model)."""
    cfg = load_config(config_path(folder))
    llm = {**(defaults(cfg.schema).get("llm") or {}), **(cfg.data.get("llm") or {})}
    provider, model = providers.resolve(llm)
    return provider, model, "model" in (cfg.data.get("llm") or {})


def lock_models(folder: Path) -> Counter:
    """How many generated blocks each model wrote, from every fluid.lock in the folder."""
    out: Counter = Counter()
    lock = folder / "fluid.lock"
    if not lock.exists():
        return out
    try:
        raw = json.loads(lock.read_text(encoding="utf-8"))
    except ValueError:
        return out
    for e in (raw.get("blocks") or {}).values():
        if e.get("origin") == "llm":
            out[e.get("model") or "unknown"] += 1
    return out


def _mask(text: str) -> str:
    """`text` with comments and string contents blanked out (same length), so braces and keys can
    be found with plain searches while the indexes still point into `text`."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        if text[i] == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            for k in range(i + 1, min(j, n)):
                out[k] = " "
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out[i:j] = " " * (j - i)
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out[i:j] = [" " if c != "\n" else c for c in text[i:j]]
            i = j
        else:
            i += 1
    return "".join(out)


def _object_span(text: str, mask: str, key: str, start: int = 0, end: int | None = None) -> tuple[int, int] | None:
    """(index of `{`, index after `}`) of `"key": { ... }` at the top level of the object in start..end."""
    end = len(text) if end is None else end
    depth = 0
    i = start
    while i < end:
        c = mask[i]
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        elif c == '"' and depth == 1:
            j = mask.index('"', i + 1)
            if text[i + 1:j] == key:
                m = re.compile(r"\s*:\s*\{").match(mask, j + 1)
                if m:
                    open_ = m.end() - 1
                    d, k = 0, open_
                    while k < end:
                        if mask[k] in "{[":
                            d += 1
                        elif mask[k] in "}]":
                            d -= 1
                            if d == 0:
                                return open_, k + 1
                        k += 1
                    return None
            i = j
        i += 1
    return None


def _set_key(text: str, obj: tuple[int, int], key: str, value: str) -> str:
    """Set `"key": "value"` directly inside the object at `obj`, replacing an existing string value."""
    mask = _mask(text)
    lo, hi = obj
    depth, i = 0, lo
    while i < hi:
        c = mask[i]
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        elif c == '"' and depth == 1:
            j = mask.index('"', i + 1)
            if text[i + 1:j] == key:
                m = re.compile(r'\s*:\s*"').match(mask, j + 1)
                if m:
                    vend = mask.index('"', m.end())
                    return text[:m.end()] + _escape(value) + text[vend:]
            i = j
        i += 1
    body = text[lo + 1:hi - 1]
    sep = ", " if body.strip() else " "
    return text[:lo + 1] + f' "{key}": "{_escape(value)}"{sep}' + text[lo + 1:].lstrip(" ")


def _escape(value: str) -> str:
    return json.dumps(value)[1:-1]


def set_model(folder: Path, model: str, provider: str | None = None) -> tuple[Path, str]:
    """Write `llm.model` (and `llm.provider` when it is needed) to fluid.config. Returns (file, provider)."""
    provider = provider or providers.provider_for({"model": model})
    path = config_path(folder)
    if not path.exists():
        llm = {"model": model} if provider == providers.provider_for({"model": model}) else \
            {"provider": provider, "model": model}
        text = "// fluid.config: settings for fluidc (see fluid.config.schema.json)\n" \
               + json.dumps({"llm": llm}, indent=2) + "\n"
    else:
        text = path.read_text(encoding="utf-8")
        load_jsonc(text, str(path))                       # refuse to edit a file that is already broken
        mask = _mask(text)
        top = mask.find("{")
        llm_span = _object_span(text, mask, "llm", top)
        if llm_span is None:
            empty = not load_jsonc(text, str(path))
            text = text[:top + 1] + ('\n  "llm": { }\n' if empty else '\n  "llm": { },\n') + text[top + 1:].lstrip("\n")
            llm_span = _object_span(text, _mask(text), "llm", top)
        assert llm_span is not None
        text = _set_key(text, llm_span, "model", model)
        llm_span = _object_span(text, _mask(text), "llm", top)
        assert llm_span is not None
        if "provider" in load_jsonc(text[llm_span[0]:llm_span[1]], str(path)) or \
                provider != providers.provider_for({"model": model}):
            text = _set_key(text, llm_span, "provider", provider)
    data = load_jsonc(text, str(path))
    cfg = load_config(None)
    errs = [e for e in validate(data, cfg.schema, str(path)) if e.severity == "error"]
    if errs:
        raise ValueError("; ".join(e.text for e in errs))
    path.write_text(text, encoding="utf-8")
    return path, provider

