"""Messages from fluidc (codes FLxxxx), shown in the same style as fx3c."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Message:
    severity: str                  # "error" | "warning" | "note"
    code: str
    text: str
    file: str = ""
    line: int = 0
    block: str = ""
    fix: str | None = None

    def render(self, sources: dict[str, str] | None = None) -> str:
        head = f"{self.severity}[{self.code}]: {self.text}"
        out = [head]
        if self.file and self.line:
            where = f"  --> {self.file}:{self.line}"
            if self.block:
                where += f"  (block \"{self.block}\")"
            out.append(where)
            src = (sources or {}).get(self.file)
            if src:
                lines = src.splitlines()
                if 0 < self.line <= len(lines):
                    num = str(self.line)
                    out.append(f"{' ' * len(num)} |")
                    out.append(f"{num} | {lines[self.line - 1]}")
        elif self.block:
            out.append(f"  --> block \"{self.block}\"")
        if self.fix:
            out.append(f"   = fix: {self.fix}")
        return "\n".join(out)


class FluidError(Exception):
    """Stops the build. Carries the messages to show."""

    def __init__(self, messages: "list[Message] | Message"):
        self.messages = messages if isinstance(messages, list) else [messages]
        super().__init__("; ".join(m.text for m in self.messages))


@dataclass
class Report:
    messages: list[Message] = field(default_factory=list)

    def add(self, severity: str, code: str, text: str, **kw) -> Message:
        m = Message(severity, code, text, **kw)
        self.messages.append(m)
        return m

    def error(self, code: str, text: str, **kw) -> Message:
        return self.add("error", code, text, **kw)

    def warning(self, code: str, text: str, **kw) -> Message:
        return self.add("warning", code, text, **kw)

    def note(self, code: str, text: str, **kw) -> Message:
        return self.add("note", code, text, **kw)

    def has_errors(self) -> bool:
        return any(m.severity == "error" for m in self.messages)
