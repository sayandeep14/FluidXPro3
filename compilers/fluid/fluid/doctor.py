"""`fluid doctor`: is this machine (and project) ready? Never prints secret values."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

OK, WARN, FAIL = "ok", "warn", "fail"
MARK = {OK: "✓", WARN: "!", FAIL: "✗"}
ASCII_MARK = {OK: "+", WARN: "!", FAIL: "x"}


def _marks() -> dict[str, str]:
    try:
        "".join(MARK.values()).encode(sys.stdout.encoding or "ascii")
    except (UnicodeEncodeError, LookupError):
        return ASCII_MARK  # e.g. a Windows console or pipe using cp1252
    return MARK


@dataclass
class Check:
    status: str
    what: str
    detail: str = ""

    def render(self) -> str:
        return f"  {_marks()[self.status]} {self.what}" + (f": {self.detail}" if self.detail else "")


def _version(dist: str) -> str | None:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None


def environment() -> list[Check]:
    out: list[Check] = []
    v = sys.version_info
    out.append(Check(OK if v >= (3, 10) else FAIL, "Python", f"{v.major}.{v.minor}.{v.micro}"
                     + ("" if v >= (3, 10) else " (FluidXPro3 needs 3.10 or newer)")))

    import fluidc
    import fx3c

    import fluid
    versions = {"fluid": fluid.__version__, "fluidc": fluidc.__version__, "fx3c": fx3c.__version__}
    same = len(set(versions.values())) == 1
    out.append(Check(OK if same else WARN, "toolchain", ", ".join(f"{k} {v}" for k, v in versions.items())
                     + f", language spec {fx3c.SPEC_VERSION}" + ("" if same else " (versions differ: reinstall)")))
    for dist, need, why in [("anthropic", "2.3", "fluidc calls Claude with it"),
                            ("jsonschema", "4", "checks fluid.config")]:
        got = _version(dist)
        if got is None:
            out.append(Check(FAIL, dist, f"not installed ({why}): pip install '{dist}>={need}'"))
        else:
            major_ok = int(got.split(".")[0]) >= int(need.split(".")[0])
            out.append(Check(OK if major_ok else FAIL, dist, got + ("" if major_ok else f" (need >= {need})")))

    from fx3c import sandbox
    layer = sandbox.os_layer()
    if layer:
        out.append(Check(OK, "sandbox", f"OS level ({layer}) + process level"))
    else:
        hint = "install bubblewrap for the OS level" if sys.platform.startswith("linux") else \
            "no OS sandbox on this platform"
        out.append(Check(WARN, "sandbox", f"process level only ({hint})"))
    return out


def credentials(project: Path) -> list[Check]:
    from fx3c.secrets import find_secrets, parse
    out: list[Check] = []
    path = find_secrets(project)
    from_file = bool(path and parse(path.read_text(encoding="utf-8")).get("ANTHROPIC_API_KEY"))
    from_env = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if from_env or from_file:
        where = "environment" if from_env else str(path)
        out.append(Check(OK, "Claude API key", f"set (from {where}; value not shown)"))
    else:
        out.append(Check(WARN, "Claude API key", "not set: builds can only reuse fluid.lock (--offline). "
                         "Put ANTHROPIC_API_KEY=... in secrets.env"))
    if path is not None:
        ignored = _git_ignores(path)
        if ignored is False:
            out.append(Check(FAIL, "secrets.env", f"{path} is NOT git-ignored: add `secrets.env` to .gitignore"))
        elif ignored is True:
            out.append(Check(OK, "secrets.env", "git-ignored"))
    return out


def _git_ignores(path: Path) -> bool | None:
    """True / False inside a git work tree, None when there's no git or no repository."""
    git = shutil.which("git")
    if git is None:
        return None
    try:
        inside = subprocess.run([git, "rev-parse", "--is-inside-work-tree"], cwd=path.parent,
                                capture_output=True, text=True, timeout=10)
        if inside.returncode != 0:
            return None
        r = subprocess.run([git, "check-ignore", "-q", path.name], cwd=path.parent, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.returncode == 0


def project_files(project: Path) -> list[Check]:
    """Is every .fluid file's fluid.lock current (would a build need the model)?"""
    from fluidc.builder import BuildOptions, build
    from fluidc.settings import CliSettings
    out: list[Check] = []
    for f in sorted(project.glob("*.fluid")):
        res = build(BuildOptions(f, cli=CliSettings(offline=True), check_only=True))
        if res.ok:
            out.append(Check(OK, f.name, f"fluid.lock is current ({len(res.reused)} block(s))"))
        else:
            first = next((m for m in res.report.messages if m.severity == "error"), None)
            text = first.text if first else "doesn't build"
            status = WARN if first is not None and first.code == "FL0502" else FAIL
            out.append(Check(status, f.name, text[:200]))
    return out


def run(project: Path) -> int:
    sections = [("Environment", environment()), ("Credentials", credentials(project))]
    files = project_files(project)
    if files:
        sections.append((f"Project {project}", files))
    worst = OK
    for title, checks in sections:
        print(title)
        for c in checks:
            print(c.render())
            if c.status == FAIL or (c.status == WARN and worst == OK):
                worst = c.status
    print("\nfluid doctor: " + {OK: "all good", WARN: "usable, with warnings", FAIL: "problems found"}[worst])
    return 1 if worst == FAIL else 0
