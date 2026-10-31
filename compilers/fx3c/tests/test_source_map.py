"""Runtime errors point at the .fx3 line, and at the .fluid block when fluidc made the file (ROADMAP E-08)."""

from __future__ import annotations

import subprocess
import sys

from helpers import run_fx3

from fx3c.backends.python.emitter import fluid_blocks
from fx3c.driver import compile_source, write_output

MARKED = '''// @from notes.fluid:22 "to all notes" fluid=a fx3=b
function[<vector<int>>|<int>] third = Function(vector<int> xs) {
    int n = len(xs);
    return xs[2];
}
// @end "to all notes"
'''


def test_a_panic_names_the_fx3_line_and_the_fluid_block():
    proc = run_fx3(MARKED + "print(third([1, 2]));\n", file="notes.fx3")
    assert proc.returncode == 101
    assert proc.stderr.strip() == 'panic: index out of range  (notes.fx3:4, block "to all notes", notes.fluid:22)'


def test_without_markers_only_the_fx3_line():
    proc = run_fx3("int a = 9223372036854775807;\nint b = 1;\nprint(a + b);\n", file="ov.fx3")
    assert proc.stderr.strip().endswith("(ov.fx3:3)")


def test_failed_tests_say_where():
    src = MARKED + "function[<>|<>] @test t = Function() {\n    ensure(third([1]) == 1);\n}\n"
    proc = run_fx3(src, ["--fx3-test"], file="notes.fx3")
    assert 'FAIL  t: index out of range  (notes.fx3:4, block "to all notes", notes.fluid:22)' in proc.stdout


def test_a_panic_inside_an_imported_module(tmp_path):
    (tmp_path / "lib.fx3").write_text("function[<int>|<int>] half = Function(int x) {\n    return 10 ~/ x;\n}\n")
    main = tmp_path / "main.fx3"
    main.write_text("from ./lib import half;\nprint(half(0));\n")
    res = compile_source(main.read_text(), str(main))
    assert res.ok, res.report()
    write_output(res.output, tmp_path / "out")
    proc = subprocess.run([sys.executable, "main.py"], cwd=tmp_path / "out", capture_output=True, text=True)
    assert proc.returncode == 101 and "lib.fx3:2)" in proc.stderr, proc.stderr


def test_fluid_blocks():
    assert fluid_blocks(MARKED) == [(2, 5, "to all notes", "notes.fluid", 22)]
    assert fluid_blocks("// @from a.fluid:1 \"x\" fluid=1 fx3=2\nint a = 1;\n") == []     # never closed
