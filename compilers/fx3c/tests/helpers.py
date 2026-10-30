from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from fx3c.driver import compile_source, write_output


def run_fx3(source: str, args: list[str] | None = None, file: str = "prog.fx3") -> subprocess.CompletedProcess:
    res = compile_source(source, file)
    assert res.ok, res.report()
    with tempfile.TemporaryDirectory() as tmp:
        write_output(res.output, Path(tmp))
        return subprocess.run([sys.executable, res.output.entry_file, *(args or [])], cwd=tmp,
                              capture_output=True, text=True, timeout=60)


def codes(source: str) -> list[str]:
    res = compile_source(source, "t.fx3", check_only=True)
    return [d.code for d in res.diags.items]


def error_codes(source: str) -> list[str]:
    res = compile_source(source, "t.fx3", check_only=True)
    return [d.code for d in res.diags.items if d.severity == "error"]
