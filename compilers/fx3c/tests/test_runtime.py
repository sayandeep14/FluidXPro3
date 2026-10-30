"""Runtime rules: panics stop the program with a clear message and exit code 101."""

from __future__ import annotations

import pytest

from helpers import run_fx3

PANICS = {
    "64-bit overflow": ("int x = 9223372036854775807; x += 1;", "integer overflow"),
    "u8 overflow": ("u8 x = 250; x += 10;", "doesn't fit in u8"),
    "index out of range": ("vector<int> v = [1, 2]; print(v[5]);", "index out of range"),
    "int division by zero": ("int a = 1; int b = 0; print(a ~/ b);", "division by zero"),
    "missing map key": ('map<string, int> m = {}; print(m["k"]);', "is not in the map"),
    "unwrap on none": ("int? x = none; print(x.unwrap());", "unwrap() on none"),
    "failed ensure": ('ensure(1 > 2, "maths is broken");', "maths is broken"),
    "@range at runtime": ("int @range(0, 10) x = 5; repeat(10) { x += 1; }", "outside @range"),
    "checked cast": ("int big = 300; u8 b = big as u8;", "doesn't fit in u8"),
}


@pytest.mark.parametrize("name", list(PANICS))
def test_panics(name: str):
    src, msg = PANICS[name]
    proc = run_fx3(src)
    assert proc.returncode == 101, (proc.stdout, proc.stderr)
    assert msg in proc.stderr
    assert "Traceback" not in proc.stderr


def test_float_division_by_zero_is_inf():
    proc = run_fx3("float a = 1.0; float b = 0.0; print(a / b, -a / b);")
    assert proc.stdout.strip() == "inf -inf"


def test_no_overflow_check_needed_for_floats():
    proc = run_fx3("float f = 1e308; print(f * 10);")
    assert proc.stdout.strip() == "inf"
