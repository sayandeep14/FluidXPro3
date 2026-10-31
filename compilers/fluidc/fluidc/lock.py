"""fluid.lock: every decision fluidc made, so the same sources always build the same .fx3.

A block is reused from the lock when its text, its effective settings and the developer's
answers are all unchanged (its `key`). Nothing is sent to the model for it.

Format 2 (ROADMAP E-03) also records the toolchain:
- the model and prompt version that generated each block
- the fluidc and fx3c versions that wrote the lock
- how the code was verified

A model or prompt change never silently changes a build. Blocks are reused as they are, and
fluidc lists the ones that `--refresh` would regenerate.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import __version__
from .errors import FluidError, Message

LOCK_FORMAT = 2          # bump when the file layout changes; add a step to _migrate()


@dataclass
class BlockEntry:
    key: str                      # hash of block text + settings + answers
    fluid_hash: str
    fx3: str                      # the code as generated (or as written in `exact`)
    fx3_hash: str
    origin: str                   # "llm" | "exact" | "none" (context-only block) | "unsupported"
    assumptions: list[str] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)
    answers: dict[str, str] = field(default_factory=dict)
    guarantees: list[dict] = field(default_factory=list)
    unsupported: str = ""
    model: str = ""               # the model that generated it ("" for exact code / before format 2)
    prompt: str = ""              # prompts.prompt_version() when it was generated ("" = unknown)
    # review (E-15); written only when set, so locks without reviews look as they did
    review: dict = field(default_factory=dict)   # {"status": accepted | rejected, "fx3": fx3_hash, "by", "at", "note"}
    feedback: str = ""            # a rejected block's note: sent to the model when it is regenerated
    described: str = ""           # code_hash of a hand edit that `fluidc describe` wrote the prose for

    def reviewed(self) -> bool:
        """Accepted, for exactly the code in the lock now."""
        return self.review.get("status") == "accepted" and self.review.get("fx3") == self.fx3_hash

    def needs_review(self) -> bool:
        return self.origin == "llm" and bool(self.fx3.strip()) and not self.reviewed()


_OPTIONAL = ("review", "feedback", "described")


def _entry_json(e: BlockEntry) -> dict:
    d = asdict(e)
    for k in _OPTIONAL:
        if not d[k]:
            del d[k]
    return d


@dataclass
class Lock:
    format: int = LOCK_FORMAT
    toolchain: dict = field(default_factory=dict)       # fluidc, fx3c, model, prompt of the last build
    verification: dict = field(default_factory=dict)    # per .fluid file: {tests, sandbox, status}
    blocks: dict[str, BlockEntry] = field(default_factory=dict)
    custom_keys: dict[str, dict] = field(default_factory=dict)   # key -> {value_hash, interpretation, status, check}
    migrated_from: int | None = None                     # set when an older format was read

    @property
    def model(self) -> str:
        return self.toolchain.get("model", "")

    @staticmethod
    def load(path: Path) -> Lock:
        if not path.exists():
            return Lock()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise FluidError(Message("error", "FL0401", f"{path.name} is damaged: {e}",
                                     fix="restore it from version control, or delete it to regenerate everything")) from None
        fmt = raw.get("format", raw.get("version"))
        if not isinstance(fmt, int) or fmt > LOCK_FORMAT:
            raise FluidError(Message("error", "FL0402", f"{path.name} was written by a newer fluidc "
                                     f"({raw.get('toolchain', {}).get('fluidc') or raw.get('fluidc') or '?'}, "
                                     f"lock format {fmt}); this is fluidc {__version__} (format {LOCK_FORMAT})",
                                     fix="upgrade fluidc: pip install -U fluidxpro3"))
        old = fmt
        raw = _migrate(raw, fmt)
        lock = Lock(LOCK_FORMAT, dict(raw.get("toolchain", {})), dict(raw.get("verification", {})))
        known = set(BlockEntry.__dataclass_fields__)
        for bid, e in raw.get("blocks", {}).items():
            lock.blocks[bid] = BlockEntry(**{k: v for k, v in e.items() if k in known})   # newer keys: ignored
        lock.custom_keys = raw.get("custom_keys", {})
        lock.migrated_from = old if old != LOCK_FORMAT else None
        return lock

    def to_json(self) -> str:
        data = {
            "format": LOCK_FORMAT,
            "toolchain": dict(sorted(self.toolchain.items())),
            "verification": dict(sorted(self.verification.items())),
            "blocks": {bid: _entry_json(e) for bid, e in sorted(self.blocks.items())},
            "custom_keys": dict(sorted(self.custom_keys.items())),
        }
        return json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False) + "\n"

    def save(self, path: Path) -> None:
        path.write_text(self.to_json(), encoding="utf-8")


def _migrate(raw: dict, fmt: int) -> dict:
    """Bring an older lock up to LOCK_FORMAT, one step at a time. Never calls the model."""
    if fmt == 1:
        # format 1: {"version": 1, "fluidc", "model", "blocks", "custom_keys"}
        model = raw.get("model", "")
        blocks = {}
        for bid, e in raw.get("blocks", {}).items():
            e = dict(e)
            e.setdefault("model", model if e.get("origin") == "llm" else "")
            e.setdefault("prompt", "")          # unknown: not compared
            blocks[bid] = e
        raw = {"format": 2, "toolchain": {"fluidc": raw.get("fluidc", ""), "model": model},
               "verification": {}, "blocks": blocks, "custom_keys": raw.get("custom_keys", {})}
        fmt = 2
    return raw


def toolchain_now(model: str, prompt: str) -> dict:
    try:
        from fx3c import __version__ as fx3c_version
    except ImportError:
        fx3c_version = ""
    return {"fluidc": __version__, "fx3c": fx3c_version, "model": model, "prompt": prompt}


def stale_blocks(lock: Lock, keys: list[str], model: str, prompt: str) -> dict[str, str]:
    """Generated blocks whose model or prompt version differs from now: block key -> why."""
    out: dict[str, str] = {}
    for k in keys:
        e = lock.blocks.get(k)
        if e is None or e.origin != "llm":
            continue
        why = []
        if e.model and model and e.model != model:
            why.append(f"model {e.model} -> {model}")
        if e.prompt and prompt and e.prompt != prompt:
            why.append(f"prompt {e.prompt} -> {prompt}")
        if why:
            out[k] = ", ".join(why)
    return out


def block_key(fluid_hash: str, settings_fp: str, answers: dict[str, str], context: str = "") -> str:
    """`context`: what else the block's code depends on (the interfaces of the files it uses)."""
    parts: list = [fluid_hash, settings_fp, sorted(answers.items())]
    if context:
        parts.append(context)           # only when there is one, so older keys stay valid
    raw = json.dumps(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def value_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:12]
