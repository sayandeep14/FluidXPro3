"""Pyright with a ratchet: no file may get more type errors than tools/pyright-baseline.json allows.

The compilers were written without a type checker, so they start with known errors (mostly
Optional narrowing pyright can't follow). New code must not add any, and when a file improves,
`python tools/typecheck_gate.py --update` lowers its baseline.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tools" / "pyright-baseline.json"


def current() -> Counter:
    proc = subprocess.run(["pyright", "--outputjson", "--pythonpath", sys.executable], cwd=ROOT,
                          capture_output=True, text=True)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(proc.stdout + proc.stderr)
        raise SystemExit("typecheck_gate: pyright did not produce JSON") from None
    counts: Counter = Counter()
    for d in data["generalDiagnostics"]:
        if d["severity"] == "error":
            counts[Path(d["file"]).resolve().relative_to(ROOT).as_posix()] += 1
    return counts


def main() -> int:
    now = current()
    if "--update" in sys.argv:
        BASELINE.write_text(json.dumps(dict(sorted(now.items())), indent=2) + "\n", encoding="utf-8")
        print(f"typecheck_gate: baseline written ({sum(now.values())} errors)")
        return 0
    base = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    worse = {f: (base.get(f, 0), n) for f, n in now.items() if n > base.get(f, 0)}
    better = {f: (n, now.get(f, 0)) for f, n in base.items() if now.get(f, 0) < n}
    for f, (was, got) in sorted(worse.items()):
        print(f"typecheck_gate: {f}: {got} type errors (baseline {was}); run `pyright {f}` to see them")
    if better:
        print("typecheck_gate: fewer errors than the baseline in " + ", ".join(sorted(better))
              + "; run `python tools/typecheck_gate.py --update` and commit the new baseline")
    print(f"typecheck_gate: {sum(now.values())} errors (baseline {sum(base.values())})")
    return 1 if worse else 0


if __name__ == "__main__":
    raise SystemExit(main())
