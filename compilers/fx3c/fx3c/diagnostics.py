"""Diagnostics: errors, warnings and how they are shown (Syntax/compiler.fx3, section 6)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Span:
    file: str
    line: int
    col: int
    length: int = 1

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.col}"


NO_SPAN = Span("<builtin>", 0, 0)


@dataclass
class Diagnostic:
    severity: str          # "error" | "warning"
    code: str              # e.g. "FX0412"
    message: str
    span: Span
    fix: str | None = None
    notes: list[str] = field(default_factory=list)


class CompileError(Exception):
    """Raised to stop compilation when continuing makes no sense (lexer / parser errors)."""

    def __init__(self, diagnostic: Diagnostic):
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


class Diagnostics:
    def __init__(self) -> None:
        self.items: list[Diagnostic] = []

    def error(self, code: str, message: str, span: Span, fix: str | None = None) -> Diagnostic:
        d = Diagnostic("error", code, message, span, fix)
        self.items.append(d)
        return d

    def warning(self, code: str, message: str, span: Span, fix: str | None = None) -> Diagnostic:
        d = Diagnostic("warning", code, message, span, fix)
        self.items.append(d)
        return d

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.items if d.severity == "error"]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.items if d.severity == "warning"]

    def has_errors(self) -> bool:
        return any(d.severity == "error" for d in self.items)


def render(diag: Diagnostic, sources: dict[str, str]) -> str:
    """Format one diagnostic like:

        error[FX0412]: vector holds one type, but "x" is a string
          --> main.fx3:12:24
           |
        12 | vector<int> v = [1, "x"];
           |                     ^^^
           = fix: use `list` for mixed values
    """
    out = [f"{diag.severity}[{diag.code}]: {diag.message}"]
    sp = diag.span
    if sp.line > 0:
        out.append(f"  --> {sp}")
        text = sources.get(sp.file)
        if text is not None:
            lines = text.splitlines()
            if 0 < sp.line <= len(lines):
                src_line = lines[sp.line - 1]
                num = str(sp.line)
                pad = " " * len(num)
                out.append(f"{pad} |")
                out.append(f"{num} | {src_line}")
                caret = " " * (sp.col - 1) + "^" * max(1, sp.length)
                out.append(f"{pad} | {caret}")
    if diag.fix:
        out.append(f"   = fix: {diag.fix}")
    for note in diag.notes:
        out.append(f"   = note: {note}")
    return "\n".join(out)
