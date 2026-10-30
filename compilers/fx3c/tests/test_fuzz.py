"""Fuzzing the front end (ROADMAP E-21): broken input must give diagnostics, never a crash.

Mutates every example program and spec file, then compiles it. An internal error (FX9999,
which the driver reports instead of a Python traceback) fails the test. Set FX3_FUZZ_CASES for
a longer run (CI uses the default; nightly can use thousands).
"""

from __future__ import annotations

import os
import random
from pathlib import Path

import pytest

from fx3c.driver import compile_source

ROOT = Path(__file__).resolve().parents[3]
CORPUS = sorted([*(ROOT / "compilers" / "fx3c" / "tests" / "programs").glob("*.fx3"),
                 *(ROOT / "Syntax").glob("*.fx3"), *(ROOT / "compilers" / "fx3c" / "examples").glob("*.fx3")])
TOKENS = ["{", "}", "(", ")", "[", "]", "<", ">", ";", ",", ".", "?", "??", "?.", "@", "=", "==", "+", "-",
          "~/", "/", "*", "%", "..", "..=", '"', "'", "f\"", "`", "\\", "//", "/*", "*/", "\n", " ", "0", "1e999",
          "9223372036854775808", "int", "vector<", "map<", "function[<", "|", "Function(", "Class()", "if",
          "repeat(", "try", "catch", "fail", "return", "import", "@entry", "@test", "currVal", "none", "true",
          "2s", "10mb", "auto", "string?", "result<", "maybe<", "->", "\t", "é", "​", "\x00"]
CASES = int(os.environ.get("FX3_FUZZ_CASES", "400"))


def _mutate(text: str, rng: random.Random) -> str:
    for _ in range(rng.randint(1, 4)):
        n = len(text)
        if n == 0:
            return rng.choice(TOKENS)
        a = rng.randrange(n)
        b = min(n, a + rng.randint(0, 40))
        op = rng.randrange(5)
        if op == 0:
            text = text[:a] + text[b:]                          # delete a slice
        elif op == 1:
            text = text[:a] + rng.choice(TOKENS) + text[a:]     # insert a token
        elif op == 2:
            text = text[:a] + text[a:b] * 2 + text[b:]          # duplicate a slice
        elif op == 3:
            c = rng.randrange(n)
            text = text[:a] + text[c:c + (b - a)] + text[b:]    # copy a slice from elsewhere
        else:
            text = text[:a]                                     # truncate
    return text


def test_corpus_is_there():
    assert len(CORPUS) >= 10


@pytest.mark.parametrize("seed", range(4))
def test_mutated_programs_never_crash_the_compiler(seed):
    rng = random.Random(seed)
    sources = [p.read_text(encoding="utf-8") for p in CORPUS]
    crashes = []
    for k in range(CASES // 4):
        src = _mutate(rng.choice(sources), rng)
        res = compile_source(src, "fuzz.fx3", check_only=True)
        bad = [d for d in res.diags.items if d.code == "FX9999"]
        if bad:
            crashes.append((seed, k, bad[0].message, src))
    if crashes:
        seed_, k, msg, src = crashes[0]
        out = ROOT / "build" / "fuzz-crash.fx3"
        out.parent.mkdir(exist_ok=True)
        out.write_text(src, encoding="utf-8")
        pytest.fail(f"{len(crashes)} crash(es); first (seed {seed_}, case {k}): {msg}\n(saved to {out})")
