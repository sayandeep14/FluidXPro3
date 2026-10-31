"""The compile pipeline: parse -> check -> generate (Syntax/compiler.fx3, section 1)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .backends import get_backend
from .backends.base import Output
from .backends.python import PythonBackend
from .checker import Checker
from .diagnostics import CompileError, Diagnostics, render
from .project import Module, Project, Reader, check_library, load, read_file


@dataclass
class CompileResult:
    diags: Diagnostics
    sources: dict[str, str]
    output: Optional[Output] = None
    program: object = None
    checker: Optional[Checker] = None
    project: Optional[Project] = None

    @property
    def ok(self) -> bool:
        return not self.diags.has_errors()

    def report(self, warnings: bool = True) -> str:
        items = [d for d in self.diags.items if warnings or d.severity == "error"]
        items.sort(key=lambda d: (d.span.file, d.span.line, d.span.col))
        return "\n\n".join(render(d, self.sources) for d in items)


def compile_source(source: str, file: str = "<input>", target: str = "python",
                   check_only: bool = False, overflow_checks: bool = True, stem: str | None = None,
                   read: Reader = read_file, root: str | None = None, library: bool = False) -> CompileResult:
    """Compile one file and every project module it imports (read from disk next to `file`).
    `root`: the project folder (default: the file's folder). `library`: check `file` as a module other
    files import (declarations only), while still emitting it as a program that can run its tests."""
    diags = Diagnostics()
    sources = {file: source}
    result = CompileResult(diags, sources)
    project = load(source, file, diags, read, sources, root)
    if project is None:
        return result
    program = project.entry.program
    assert program is not None
    result.program = program
    checked: dict[str, Module] = {}
    for mod in project.order:
        assert mod.program is not None
        checker = Checker(mod.program, diags, project=dict(checked))
        try:
            checker.check()
        except CompileError as e:
            diags.items.append(e.diagnostic)
        except Exception as e:  # noqa: BLE001 - a bug in fx3c itself
            if diags.has_errors():
                return result      # most likely a follow-on from an earlier error
            diags.error("FX9999", f"internal compiler error: {type(e).__name__}: {e}", mod.program.span,
                        "this is a bug in fx3c; please report it with the file that caused it")
            return result
        mod.checker = checker
        for fi in checker.functions.values():
            fi.home = mod.name  # type: ignore[attr-defined]
        if mod is not project.entry or library:
            check_library(mod, diags)
        if diags.has_errors():
            return result          # files that import this one would only repeat its problems
        checked[mod.name] = mod
    result.checker = project.entry.checker  # type: ignore[assignment]
    result.project = project
    if diags.has_errors() or check_only:
        return result
    backend = get_backend(target)
    if isinstance(backend, PythonBackend):
        backend.overflow_checks = overflow_checks
    out = backend.emit(program, result.checker, stem or Path(file).stem, diags, project=project)
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
