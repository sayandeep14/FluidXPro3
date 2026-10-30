"""Python backend: checked fx3 -> Python 3.10+ source."""

from __future__ import annotations

from pathlib import Path

from ..base import Backend, Output
from .emitter import PyEmitter

RUNTIME_PATH = Path(__file__).with_name("fx3rt.py")
STD_PATH = Path(__file__).with_name("fx3std.py")


class PythonBackend(Backend):
    name = "python"

    def __init__(self, overflow_checks: bool = True):
        self.overflow_checks = overflow_checks

    def emit(self, program, checker, stem: str, diags) -> Output:
        em = PyEmitter(checker, diags, source_file=program.file, overflow_checks=self.overflow_checks)
        code = em.emit_program(program)
        out = Output()
        main = f"{stem}.py"
        out.files[main] = code
        out.files["fx3rt.py"] = RUNTIME_PATH.read_text(encoding="utf-8")
        if checker.std_modules:
            out.files["fx3std.py"] = STD_PATH.read_text(encoding="utf-8")
        out.entry_file = main
        out.run_command = ["python3", main]
        return out
