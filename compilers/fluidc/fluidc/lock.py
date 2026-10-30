"""fluid.lock: every decision fluidc made, so the same sources always build the same .fx3.

A block is reused from the lock when its text, its effective settings and the developer's
answers are all unchanged (its `key`). Nothing is sent to the model for it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import __version__
from .errors import FluidError, Message

LOCK_VERSION = 1


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


@dataclass
class Lock:
    version: int = LOCK_VERSION
    fluidc: str = __version__
    model: str = ""
    blocks: dict[str, BlockEntry] = field(default_factory=dict)
    custom_keys: dict[str, dict] = field(default_factory=dict)   # key -> {value_hash, interpretation, status, check}

    @staticmethod
    def load(path: Path) -> "Lock":
        if not path.exists():
            return Lock()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise FluidError(Message("error", "FL0401", f"{path.name} is damaged: {e}",
                                     fix="restore it from version control, or delete it to regenerate everything")) from None
        if raw.get("version") != LOCK_VERSION:
            raise FluidError(Message("error", "FL0402", f"{path.name} was written by an incompatible fluidc"))
        lock = Lock(raw["version"], raw.get("fluidc", ""), raw.get("model", ""))
        for bid, e in raw.get("blocks", {}).items():
            lock.blocks[bid] = BlockEntry(**e)
        lock.custom_keys = raw.get("custom_keys", {})
        return lock

    def save(self, path: Path) -> None:
        data = {
            "version": self.version,
            "fluidc": __version__,
            "model": self.model,
            "blocks": {bid: asdict(e) for bid, e in sorted(self.blocks.items())},
            "custom_keys": dict(sorted(self.custom_keys.items())),
        }
        path.write_text(json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False) + "\n", encoding="utf-8")


def block_key(fluid_hash: str, settings_fp: str, answers: dict[str, str]) -> str:
    raw = json.dumps([fluid_hash, settings_fp, sorted(answers.items())])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def value_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:12]
