"""The contract every target-language backend implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class BackendError(Exception):
    pass


@dataclass
class Output:
    """Files to write, as {relative path: text}, plus how to run the result."""
    files: dict[str, str] = field(default_factory=dict)
    entry_file: str = ""
    run_command: list[str] = field(default_factory=list)


class Backend(ABC):
    name: str = ""

    @abstractmethod
    def emit(self, program, checker, stem: str, diags) -> Output:
        """Generate code for a checked program.

        `program` is the checked AST (nodes.Program), `checker` gives access to
        classes, functions, tests and examples, `stem` is the output file name
        without extension. Problems the backend can't handle are reported to
        `diags` with code FX10xx.
        """
