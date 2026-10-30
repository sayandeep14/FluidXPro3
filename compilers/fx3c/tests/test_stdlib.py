"""The standard library: registry <-> runtime consistency, import rules, and failure paths."""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from helpers import codes, run_fx3

from fx3c.backends.python.emitter import mangle
from fx3c.lexer import tokenize
from fx3c.parser import Parser
from fx3c.driver import compile_source, write_output
from fx3c.stdlib import MODULES

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "fx3c" / "backends" / "python"


def _runtime():
    sys.path.insert(0, str(RUNTIME_DIR))
    try:
        import fx3std
        return fx3std
    finally:
        sys.path.pop(0)


@pytest.mark.parametrize("module", sorted(MODULES))
def test_every_member_exists_in_the_python_runtime(module):
    std = _runtime()
    for t in MODULES[module]:
        cls = getattr(std, t.name, None)
        assert cls is not None, f"fx3std has no {t.name}"
        for c in t.constants:
            assert hasattr(cls, mangle(c)), f"{t.name}.{c}"
        for m in t.members:
            fn = getattr(cls, mangle(m.name), None)
            assert fn is not None, f"fx3std.{t.name} has no {mangle(m.name)} (for {m.name})"
            # parameter names must match, because calls may pass them by name
            parser = Parser(tokenize(m.sig.split("->")[0].strip(), "<sig>"), "<sig>")
            parser.class_names.add("handler")
            want = [mangle(p.name) for p in parser.parse_params()]
            sig = inspect.signature(fn)
            got = [n for n in sig.parameters if n != "self"]
            if any(p.kind == p.VAR_POSITIONAL for p in sig.parameters.values()):
                continue          # variadic, e.g. Path.join(*parts)
            assert got == want, f"{t.name}.{m.name}: runtime parameters {got}, registry {want}"


BAD = {
    "module not imported": ("print(Math.sqrt(4.0));", "FX0313"),
    "type not imported": ("Time t;", "FX0313"),
    "unknown module": ("import Maths;", "FX0310"),
    "unknown member in from-import": ("from Math import squareroot;", "FX0311"),
    "std value made with a constructor": ("import Time; Time t = Time();", "FX0314"),
    "unhandled std failure": ('import File; string s = File.read("x");', "FX0625"),
    "handler with the wrong shape": ("import Server; function[<int>|<int>] f = Function(int x) => x; "
                                     "Server s = Server.create(); s.get(\"/\", f);", "FX0479"),
    "sleep needs a duration": ("sleep(2);", "FX0477"),
    "imports go at the top": ("int x = 1; import Math; print(x);", "FX0312"),
    "duration arithmetic": ("print(2s + 3);", "FX0446"),
}


@pytest.mark.parametrize("name", list(BAD))
def test_bad_std_use(name):
    src, code = BAD[name]
    assert code in codes(src), codes(src)


def test_from_import_and_alias():
    proc = run_fx3("from Math import sqrt as root, pi;\nimport Random as R;\nR.seed(1);\n"
                   "print(root(9.0), pi > 3.0, R.int(5, 5));")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "3.0 true 5"


def test_ai_without_a_key_fails_cleanly():
    src = ('import Ai;\ntry {\n    print(Ai.ask("hi"));\n} catch (AiError e) {\n    print("AiError");\n}\n')
    res = compile_source(src, "ai.fx3")
    assert res.ok, res.report()
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    with tempfile.TemporaryDirectory() as tmp:      # outside the project: no secrets.env is found
        write_output(res.output, Path(tmp))
        proc = subprocess.run([sys.executable, res.output.entry_file], cwd=tmp, env=env,
                              capture_output=True, text=True, timeout=60)
    assert proc.stdout.strip() == "AiError", proc.stdout + proc.stderr


def test_server_handler_failure_gives_500():
    src = """import Server;
import Http;
function[<Request>|<Response>] @fails(ValueError) boom = Function(Request r) {
    fail ValueError("bad thing");
}
Server s = Server.create(port = 0);
s.get("/boom", boom);
s.start();
try {
    HttpResponse r = Http.get(f"http://127.0.0.1:{s.port}/boom");
    print(r.status, r.body);
} catch (NetworkError e) {
    print("network", e.message);
}
s.stop();
"""
    proc = run_fx3(src)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == '500 {"error":"bad thing"}'
