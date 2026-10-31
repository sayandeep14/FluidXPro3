"""Checking generated fx3 with the real fx3c compiler, and running its tests."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import fx3c  # noqa: F401
except ImportError:  # the two compilers live side by side in compilers/
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fx3c"))
    import fx3c  # noqa: F401

from fx3c.diagnostics import render  # noqa: E402
from fx3c import sandbox  # noqa: E402
from fx3c.driver import compile_source  # noqa: E402
from fx3c.project import read_file  # noqa: E402


@dataclass
class Problem:
    block: str | None
    text: str                     # one line, for the model and for the developer
    severity: str = "error"


@dataclass
class Verdict:
    ok: bool
    problems: list[Problem] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)
    test_output: str = ""
    tests_failed: bool = False
    report: str = ""              # fx3c's own formatted messages
    tests_ran: bool = False
    sandbox: str = ""             # the sandbox level the tests ran in ("os" | "process" | "off")


def check(text: str, fx3_name: str, target: str, ranges: dict[str, tuple[int, int]],
          run_tests: bool = True, sandbox_level: str = "auto", hide: list[str] | None = None,
          timeout: float = 120, read=None, root: str | None = None, library: bool = False) -> Verdict:
    """Compile the fx3 and run its tests. The tests are model-written code, so they run in the
    sandbox (fx3c.sandbox): no network, no secrets, no files outside their own folder."""
    def block_at(line: int) -> str | None:
        for bid, (a, b) in ranges.items():
            if a <= line <= b:
                return bid
        return None

    res = compile_source(text, fx3_name, target=target, read=read or read_file, root=root, library=library)
    v = Verdict(ok=res.ok, report=res.report())
    for d in res.diags.items:
        bid = block_at(d.span.line)
        where = f'block "{bid}", ' if bid else ""
        start = ranges.get(bid, (1, 1))[0] if bid else 1
        line_in_block = d.span.line - start + 1 if bid else d.span.line
        lines = text.splitlines()
        src = lines[d.span.line - 1].strip() if 0 < d.span.line <= len(lines) else ""
        msg = f"{where}line {line_in_block}: {d.severity}[{d.code}] {d.message}"
        if d.fix:
            msg += f" (fix: {d.fix})"
        if src:
            msg += f"   | {src}"
        p = Problem(bid, msg, d.severity)
        (v.problems if d.severity == "error" else v.warnings).append(p)
    if not res.ok or not run_tests:
        return v
    has_tests = bool(res.checker.tests) or any(cases for _, cases in res.checker.examples)
    if not has_tests:
        return v
    # sandbox.SandboxUnavailable is not the model's problem to fix: the caller reports it
    ran = sandbox.run(res.output.files, res.output.entry_file, ["--fx3-test"], level=sandbox_level,
                      timeout=timeout, hide=hide)
    v.tests_ran, v.sandbox = True, ran.level
    out, failed = ran.output, ran.returncode != 0
    if ran.timed_out:
        out = out.rstrip() + f"\nthe tests did not finish within {timeout:g} seconds\n"
    v.test_output = out
    if failed:
        v.ok = False
        v.tests_failed = True
        for what in ran.blocked:
            v.problems.append(Problem(None, f"test: the sandbox blocked {what}. Tests run with no network, "
                                            "no secrets and only their own folder; don't depend on those"))
        for line in out.splitlines():
            if line.strip().startswith("FAIL") or line.startswith("panic:"):
                v.problems.append(Problem(None, f"test: {line.strip()}"))
        if not v.problems:
            v.problems.append(Problem(None, "the tests failed: " + out.strip()[-500:]))
    return v


__all__ = ["check", "Verdict", "Problem", "render"]
