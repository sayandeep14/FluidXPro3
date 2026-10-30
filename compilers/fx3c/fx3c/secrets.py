"""Loading secrets.env (shared by fx3c and fluidc).

Searches the given folder and every folder above it for `secrets.env`, and sets each
NAME=value in os.environ, unless that variable is already set (the real environment wins).
Values are never printed.
"""

from __future__ import annotations

import os
from pathlib import Path

FILE_NAME = "secrets.env"


def find_secrets(start: Path) -> Path | None:
    start = start.resolve()
    for folder in [start, *start.parents]:
        candidate = folder / FILE_NAME
        if candidate.is_file():
            return candidate
    return None


def parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if name:
            out[name] = value
    return out


def load_secrets(start: Path) -> Path | None:
    """Load secrets.env into os.environ. Empty values are skipped. Returns the file used."""
    path = find_secrets(start if start.is_dir() else start.parent)
    if path is None:
        return None
    for name, value in parse(path.read_text(encoding="utf-8")).items():
        if value and not os.environ.get(name):
            os.environ[name] = value
    return path
