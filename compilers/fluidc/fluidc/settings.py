"""fluid.config and every other place a setting can come from (FLUID_SPEC.md sections 8-11).

Priority, highest first:
  1. a tag at the end of a rules/never line ([error])      (handled by the builder)
  2. `with` / `ask` inside a block
  3. command-line flags (--ask, --rules, --never, --set)
  4. `with` / `ask` / `target` at the top of the .fluid file
  5. fluid.config -> overrides (later entries win)
  6. fluid.config -> top-level keys
  7. built-in defaults (from fluid.config.schema.json)
"""

from __future__ import annotations

import copy
import difflib
import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import FluidError, Message

PACKAGED_SCHEMA = Path(__file__).with_name("data") / "fluid.config.schema.json"


# ---------------------------------------------------------------------------
# JSON with comments
# ---------------------------------------------------------------------------

def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments and trailing commas, leaving strings untouched."""
    out: list[str] = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise ValueError("a /* comment is never closed")
            i = j + 2
            continue
        out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def load_jsonc(text: str, file: str) -> dict:
    try:
        data = json.loads(strip_jsonc(text))
    except (ValueError, json.JSONDecodeError) as e:
        line = getattr(e, "lineno", 0)
        raise FluidError(Message("error", "FL0201", f"fluid.config is not valid JSON: {e}", file, line)) from None
    if not isinstance(data, dict):
        raise FluidError(Message("error", "FL0201", "fluid.config must be a JSON object { ... }", file))
    return data


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def load_schema(config_path: Path | None) -> dict:
    candidates = []
    if config_path is not None:
        candidates.append(config_path.parent / "fluid.config.schema.json")
    candidates.append(PACKAGED_SCHEMA)
    for c in candidates:
        if c.exists():
            return json.loads(c.read_text(encoding="utf-8"))
    raise FluidError(Message("error", "FL0202", "fluid.config.schema.json was not found"))


def _resolve(node: dict, schema: dict) -> dict:
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        assert ref.startswith("#/")
        cur: Any = schema
        for part in ref[2:].split("/"):
            cur = cur[part]
        node = cur
    return node


def schema_at(path: list[str], schema: dict) -> dict | None:
    node: Any = schema
    for p in path:
        node = _resolve(node, schema)
        props = node.get("properties", {}) if isinstance(node, dict) else {}
        if p not in props:
            return None
        node = props[p]
    return _resolve(node, schema)


def defaults(schema: dict) -> dict:
    def walk(node: dict) -> Any:
        node = _resolve(node, schema)
        if "properties" in node:
            out = {}
            for k, sub in node["properties"].items():
                if k.startswith("$"):
                    continue
                v = walk(sub)
                if v is not None:
                    out[k] = v
            if "default" in node:
                return node["default"]
            return out or None
        return node.get("default")
    d = walk(schema) or {}
    d.pop("overrides", None)
    return d


def custom_keys(cfg: Any, schema: dict, path: list[str] | None = None) -> list[list[str]]:
    """Keys the schema doesn't know (they become LLM-read requirements)."""
    path = path or []
    found: list[list[str]] = []
    if not isinstance(cfg, dict):
        return found
    node = schema_at(path, schema) if path else schema
    props = (node or {}).get("properties", {}) if node is not None else {}
    for k, v in cfg.items():
        if k.startswith("$"):
            continue
        if node is None or k not in props:
            found.append(path + [k])
            continue
        sub = _resolve(props[k], schema)
        if sub.get("type") == "object" or "properties" in sub:
            found += custom_keys(v, schema, path + [k])
    return found


def known_keys_at(path: list[str], schema: dict) -> list[str]:
    node = schema_at(path, schema) if path else schema
    if node is None:
        return []
    return [k for k in node.get("properties", {}) if not k.startswith("$")]


def validate(data: dict, schema: dict, file: str) -> list[Message]:
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - jsonschema is a dependency
        return [Message("warning", "FL0203", "jsonschema is not installed; fluid.config was not validated", file)]
    v = jsonschema.Draft202012Validator(schema)
    out = []
    for err in sorted(v.iter_errors(data), key=lambda e: list(e.path)):
        where = ".".join(str(p) for p in err.path) or "(top level)"
        out.append(Message("error", "FL0204", f"{where}: {err.message}", file))
    return out


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------

def _set_path(d: dict, path: list[str], value: Any) -> None:
    for p in path[:-1]:
        nxt = d.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            d[p] = nxt
        d = nxt
    d[path[-1]] = value


def _flatten(d: Any, prefix: list[str] | None = None) -> dict[str, Any]:
    prefix = prefix or []
    out: dict[str, Any] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            if k.startswith("$"):
                continue
            if isinstance(v, dict) and v and k not in ("readme",):
                out.update(_flatten(v, prefix + [k]))
            else:
                out[".".join(prefix + [k])] = v
    return out


def parse_value(text: str) -> Any:
    t = text.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return t.strip("'\"")


def key_path(key: str, schema: dict) -> list[str]:
    """Where a short `with` / --set key lives: `safety` -> codegen.safety, `cpp.x` -> target.cpp.x."""
    parts = key.split(".")
    top = known_keys_at([], schema)
    if parts[0] in top:
        return parts
    if parts[0] in ("python", "cpp"):
        return ["target", *parts]
    for section in ("codegen", "verification", "llm", "project"):
        if parts[0] in known_keys_at([section], schema):
            return [section, *parts]
    return parts       # a custom key


@dataclass
class Settings:
    values: dict = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    custom: dict[str, Any] = field(default_factory=dict)   # dotted path -> value

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.values
        for p in dotted.split("."):
            if not isinstance(cur, dict) or p not in cur:
                return default
            cur = cur[p]
        return cur

    def apply(self, data: dict, source: str, schema: dict) -> None:
        for k, v in _flatten(data).items():
            if k.startswith("overrides") or k.split(".")[0] == "overrides":
                continue
            _set_path(self.values, k.split("."), copy.deepcopy(v))
            self.sources[k] = source
        for path in custom_keys(data, schema):
            if path[0] == "overrides":
                continue
            dotted = ".".join(path)
            cur: Any = data
            for p in path:
                cur = cur[p]
            self.custom[dotted] = cur

    def set(self, path: list[str], value: Any, source: str, schema: dict) -> None:
        _set_path(self.values, path, value)
        dotted = ".".join(path)
        self.sources[dotted] = source
        if schema_at(path, schema) is None:
            self.custom[dotted] = value

    def fingerprint(self) -> str:
        import hashlib
        return hashlib.sha256(json.dumps(self.values, sort_keys=True, default=str).encode()).hexdigest()[:12]


@dataclass
class Config:
    data: dict
    schema: dict
    file: str
    present: bool


def load_config(path: Path | None, required: bool = False) -> Config:
    if path is None or not path.exists():
        if required:
            raise FluidError(Message("error", "FL0205", f"config file not found: {path}"))
        return Config({}, load_schema(None), "fluid.config", False)
    text = path.read_text(encoding="utf-8")
    data = load_jsonc(text, str(path))
    schema = load_schema(path)
    errs = validate(data, schema, str(path))
    if [e for e in errs if e.severity == "error"]:
        raise FluidError(errs)
    return Config(data, schema, str(path), True)


def suspicious_keys(cfg: Config) -> list[tuple[str, str]]:
    """Custom keys that look like typos of known ones: [(written, did_you_mean)]."""
    out = []
    for path in custom_keys(cfg.data, cfg.schema):
        if path[0] == "overrides":
            continue
        known = known_keys_at(path[:-1], cfg.schema)
        m = difflib.get_close_matches(path[-1], known, n=1, cutoff=0.8)
        if m:
            out.append((".".join(path), ".".join(path[:-1] + [m[0]])))
    return out


@dataclass
class CliSettings:
    ask: str | None = None
    rules: str | None = None
    never: str | None = None
    sets: list[str] = field(default_factory=list)
    offline: bool = False


def _matches(override: dict, block, doc_file: str) -> bool:
    m = override.get("match", {})
    if "blocks" in m and block is not None:
        names = {n.lower() for n in m["blocks"]}
        if block.name.lower() in names or block.id.lower() in names:
            return True
    if "keyword" in m and block is not None and m["keyword"] == block.keyword:
        return True
    if "files" in m and any(fnmatch.fnmatch(Path(doc_file).name, pat) or fnmatch.fnmatch(doc_file, pat)
                            for pat in m["files"]):
        return True
    return False


def effective(cfg: Config, doc, block, cli: CliSettings) -> Settings:
    """The final settings for one block (or for the whole file when block is None)."""
    schema = cfg.schema
    s = Settings()
    s.apply(defaults(schema), "built-in default", schema)
    top = {k: v for k, v in cfg.data.items() if k != "overrides"}
    s.apply(top, f"{Path(cfg.file).name}", schema)
    for k, ov in enumerate(cfg.data.get("overrides", []) or []):
        if _matches(ov, block, doc.file):
            body = {x: y for x, y in ov.items() if x != "match"}
            s.apply(body, f"{Path(cfg.file).name} overrides[{k}]", schema)
    if doc.file_target:
        s.set(["target", "language"], doc.file_target, f"`target` in {doc.file}", schema)
    if doc.file_ask:
        s.set(["ask"], doc.file_ask, f"`ask` at the top of {doc.file}", schema)
    for key, val in doc.file_with:
        s.set(key_path(key, schema), parse_value(val), f"`with` at the top of {doc.file}", schema)
    if cli.ask:
        s.set(["ask"], cli.ask, "--ask", schema)
    if cli.rules:
        s.set(["verification", "rules"], cli.rules, "--rules", schema)
    if cli.never:
        s.set(["verification", "never"], cli.never, "--never", schema)
    for item in cli.sets:
        if "=" not in item:
            raise FluidError(Message("error", "FL0206", f"--set {item}: write --set key=value"))
        k, v = item.split("=", 1)
        s.set(key_path(k.strip(), schema), parse_value(v), "--set", schema)
    if block is not None:
        for key, val in block.with_settings:
            s.set(key_path(key, schema), parse_value(val), f"`with` in block \"{block.id}\"", schema)
        if block.ask:
            s.set(["ask"], block.ask, f"`ask` in block \"{block.id}\"", schema)
        if block.keyword in ("rules", "never") and block.modifier:
            level = "error" if block.modifier == "strict" else "off"
            s.set(["verification", block.keyword], level, f"`{block.keyword} {block.modifier}`", schema)
    errs = [e for e in validate({k: v for k, v in s.values.items()}, schema, doc.file) if e.severity == "error"]
    if errs:
        for e in errs:
            e.text = "after applying every setting: " + e.text
            if block is not None:
                e.block = block.id
        raise FluidError(errs)
    return s


def explain(s: Settings) -> list[tuple[str, Any, str]]:
    return sorted((k, v, s.sources.get(k, "?")) for k, v in _flatten(s.values).items())
