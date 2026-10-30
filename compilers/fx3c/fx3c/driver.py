"""The compile pipeline: parse -> check -> generate (Syntax/compiler.fx3, section 1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .backends import get_backend
from .backends.base import Output
from .backends.python import PythonBackend
from .checker import Checker
from .diagnostics import CompileError, Diagnostics, render
from .parser import parse


@dataclass
class CompileResult:
    diags: Diagnostics
    sources: dict[str, str]
    output: Optional[Output] = None
    program: object = None
    checker: Optional[Checker] = None

    @property
    def ok(self) -> bool:
        return not self.diags.has_errors()

    def report(self, warnings: bool = True) -> str:
        items = [d for d in self.diags.items if warnings or d.severity == "error"]
        items.sort(key=lambda d: (d.span.file, d.span.line, d.span.col))
        return "\n\n".join(render(d, self.sources) for d in items)


def compile_source(source: str, file: str = "<input>", target: str = "python",
                   check_only: bool = False, overflow_checks: bool = True, stem: str | None = None) -> CompileResult:
    diags = Diagnostics()
    sources = {file: source}
    result = CompileResult(diags, sources)
    try:
        program = parse(source, file)
    except CompileError as e:
        diags.items.append(e.diagnostic)
        return result
    result.program = program
    checker = Checker(program, diags)
    try:
        checker.check()
    except CompileError as e:
        diags.items.append(e.diagnostic)
    except Exception as e:  # noqa: BLE001 - a bug in fx3c itself
        if diags.has_errors():
            return result      # most likely a follow-on from an earlier error
        diags.error("FX9999", f"internal compiler error: {type(e).__name__}: {e}", program.span,
                    "this is a bug in fx3c; please report it with the file that caused it")
        return result
    result.checker = checker
    if diags.has_errors() or check_only:
        return result
    backend = get_backend(target)
    if isinstance(backend, PythonBackend):
        backend.overflow_checks = overflow_checks
    out = backend.emit(program, checker, stem or Path(file).stem, diags)
    if not diags.has_errors():
        result.output = out
    return result


def write_output(out: Output, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for rel, text in out.files.items():
        p = out_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        written.append(p)
    return written
