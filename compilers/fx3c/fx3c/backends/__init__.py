"""Code generators. Each backend turns the checked tree (the IR) into one target language.

To add a language: subclass `Backend`, implement `emit`, and register it in BACKENDS.
"""

from __future__ import annotations

from .base import Backend, BackendError
from .python import PythonBackend

BACKENDS: dict[str, type[Backend]] = {
    "python": PythonBackend,
}


def get_backend(name: str) -> Backend:
    try:
        return BACKENDS[name]()
    except KeyError:
        raise BackendError(f"unknown target `{name}`; available: {', '.join(sorted(BACKENDS))}") from None


__all__ = ["Backend", "BackendError", "BACKENDS", "get_backend"]
