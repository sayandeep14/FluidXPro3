"""Code generators. Each backend turns the checked tree (the IR) into one target language.

To add a language: subclass `Backend`, implement `emit`, and register it in BACKENDS.
"""

from __future__ import annotations

from .base import Backend, BackendError
from .python import PythonBackend

BACKENDS: dict[str, type[Backend]] = {
    "python": PythonBackend,
}

# Every target the language defines (Syntax/targets.fx3 section 1), in the order the backends are built
TARGETS = ("python", "go", "java", "cpp", "rust")
TARGET_ALIASES = {"py": "python", "python3": "python", "golang": "go", "jvm": "java", "c++": "cpp", "cxx": "cpp",
                  "rs": "rust"}


def canonical_target(name: str) -> str:
    """`golang` -> `go`, `C++` -> `cpp`. Unknown names are returned lower-cased, as written."""
    n = name.strip().lower()
    return TARGET_ALIASES.get(n, n)


def get_backend(name: str) -> Backend:
    name = canonical_target(name)
    if name in BACKENDS:
        return BACKENDS[name]()
    if name in TARGETS:
        raise BackendError(f"the {name} backend isn't available yet (ROADMAP E-33). .fx3 is target-neutral, so "
                           f"this file will build for {name} unchanged once it is; for now: python")
    raise BackendError(f"unknown target `{name}`; the targets are {', '.join(TARGETS)}")


__all__ = ["Backend", "BackendError", "BACKENDS", "TARGETS", "canonical_target", "get_backend"]
