"""@native and @extern: target-specific code, reviewed like @unsafe (Syntax/targets.fx3 section 6)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from helpers import error_codes

from fx3c.driver import compile_source


@pytest.mark.parametrize("src,code", [
    ('function[<int>|<int>] @extern f = Function(int a) => a;', "FX0783"),           # @extern needs Native(...)
    ('function[<int>|<int>] f = Native(int a) { python = "a"; }', "FX0783"),          # Native(...) needs @extern
    ('function @extern f = Native(int a) { python = "a"; }', "FX0783"),              # and a signature
    ('function[<int>|<int>] @extern f = Native(int a) { cobol = "a"; }', "FX0784"),   # not a target
    ('function[<int>|<int>] @native(fortran = "x") f = Function(int a) => a;', "FX0784"),
])
def test_errors(src, code):
    assert code in error_codes(src + "\n")


def test_a_target_without_a_body_cant_be_built():
    res = compile_source('function[<int>|<int>] @extern f = Native(int a) { rust = "a * 2"; }\n', "t.fx3")
    assert not res.ok and "FX1004" in [d.code for d in res.diags.items]


def test_python_bodies_files_and_listing(tmp_path):
    (tmp_path / "native").mkdir()
    (tmp_path / "native" / "maths.py").write_text("def triple(x):\n    return x * 3\n", encoding="utf-8")
    src = """function[<int>|<int>] @extern triple = Native(int x) {
    python = "native/maths.py:triple";
    go = "native/maths.go:Triple";
}
function[<int, int>|<int>] @extern @example((2, 3) -> 5) add = Native(int a, int b) { python = "a + b"; }
function[<>|<>] @entry main = Function() { print(triple(4), add(1, 2)); }
"""
    prog = tmp_path / "prog.fx3"
    prog.write_text(src, encoding="utf-8")
    res = compile_source(src, str(prog))
    assert res.ok, res.report()
    assert "native/maths.py" in res.output.files and "native/__init__.py" in res.output.files
    run = subprocess.run([sys.executable, "-m", "fx3c", "run", str(prog)], capture_output=True, text=True)
    assert run.stdout == "12 3\n", run.stderr
    tests = subprocess.run([sys.executable, "-m", "fx3c", "test", str(prog)], capture_output=True, text=True)
    assert "1 passed, 0 failed" in tests.stdout
    listed = subprocess.run([sys.executable, "-m", "fx3c", "check", str(prog), "--native"],
                            capture_output=True, text=True)
    assert "@extern triple: python, go" in listed.stdout and "@extern add: python" in listed.stdout


def test_a_missing_native_file_is_reported(tmp_path):
    prog = tmp_path / "prog.fx3"
    src = 'function[<int>|<int>] @extern f = Native(int a) { python = "native/nope.py:f"; }\n'
    prog.write_text(src, encoding="utf-8")
    res = compile_source(src, str(prog))
    assert "FX1006" in [d.code for d in res.diags.items]


def test_native_text_for_python_is_used_as_written():
    src = """function[<int>|<int>] @native(python = "import functools\\n@functools.cache", java = "@Cacheable") fib =
    Function(int n) { if (n < 2) { return n; } return fib(n - 1) + fib(n - 2); }
function[<>|<>] @entry main = Function() { print(fib(80)); }
"""
    res = compile_source(src, "t.fx3")
    assert res.ok, res.report()
    code = res.output.files["t.py"]
    assert "import functools" in code and "@functools.cache" in code and "Cacheable" not in code
