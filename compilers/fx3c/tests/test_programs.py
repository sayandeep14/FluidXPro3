"""Every tests/programs/X.fx3 must compile, run, and print exactly X.out."""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers import run_fx3

PROGRAMS = sorted(Path(__file__).parent.joinpath("programs").glob("*.fx3"))


@pytest.mark.parametrize("path", PROGRAMS, ids=[p.stem for p in PROGRAMS])
def test_program_output(path: Path):
    expected = path.with_suffix(".out").read_text(encoding="utf-8")
    proc = run_fx3(path.read_text(encoding="utf-8"), file=path.name)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == expected


def test_tests_and_examples_run():
    src = Path(__file__).parent.joinpath("programs", "functions.fx3").read_text(encoding="utf-8")
    proc = run_fx3(src, ["--fx3-test"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "3 passed, 0 failed" in proc.stdout


def test_failing_example_is_reported():
    src = 'function[<int>|<int>] @example(2 -> 5) double = Function(int x) => x * 2;\n'
    proc = run_fx3(src, ["--fx3-test"])
    assert proc.returncode == 1
    assert "FAIL" in proc.stdout and "expected 5, got 4" in proc.stdout
