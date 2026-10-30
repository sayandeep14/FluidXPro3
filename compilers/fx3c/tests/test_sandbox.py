"""The sandbox for generated code (ROADMAP E-01): hostile programs can't reach the machine."""

from __future__ import annotations

import os

import pytest

from fx3c import sandbox
from fx3c.driver import compile_source

HOSTILE = """
import File;
import Http;
import Env;
import Path;

function[<>|<>] @entry main = Function() {
    try {
        string s = File.read("CANARY");
        print(f"LEAKED file: {s}");
    } catch (IOError e) {
        print("file blocked");
    }
    try {
        vector<string> names = Path.listDir("SSH");
        print(f"LEAKED listing: {names.size()}");
    } catch (IOError e) {
        print("listing blocked");
    }
    try {
        File.write("OUTSIDE", "x");
        print("LEAKED write");
    } catch (IOError e) {
        print("write blocked");
    }
    try {
        HttpResponse r = Http.get("http://example.com/", timeout = 2s);
        print(f"LEAKED network: {r.status}");
    } catch (NetworkError e) {
        print("network blocked");
    }
    string key = Env.get("FX3_TEST_SECRET") ?? "none";
    print(f"secret: {key}");
    try {
        File.write("inside.txt", "ok");
        print(File.read("inside.txt"));
    } catch (IOError e) {
        print("LEAKED: can't use its own folder");
    }
}
"""


def _hostile(tmp_path):
    canary = tmp_path / "canary.txt"
    canary.write_text("top secret", encoding="utf-8")
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    (ssh / "id_ed25519").write_text("key", encoding="utf-8")
    src = (HOSTILE.replace("CANARY", canary.as_posix()).replace("SSH", ssh.as_posix())
           .replace("OUTSIDE", (tmp_path / "written.txt").as_posix()))
    res = compile_source(src, "hostile.fx3")
    assert res.ok, res.report()
    return res


def _levels():
    out = ["process"]
    if sandbox.os_layer():
        out.append("os")
    return out


@pytest.mark.parametrize("level", _levels())
def test_hostile_program_is_contained(tmp_path, monkeypatch, level):
    res = _hostile(tmp_path)
    monkeypatch.setenv("FX3_TEST_SECRET", "s3cr3t-value")
    ran = sandbox.run(res.output.files, res.output.entry_file, level=level, hide=[str(tmp_path)], timeout=60)
    assert ran.level == level
    assert ran.returncode == 0, ran.output
    assert "LEAKED" not in ran.stdout, ran.output
    for line in ["file blocked", "listing blocked", "write blocked", "network blocked", "secret: none", "ok"]:
        assert line in ran.stdout, ran.output
    assert not (tmp_path / "written.txt").exists()
    assert any("reading" in b for b in ran.blocked) and any("network" in b or "looking up" in b for b in ran.blocked)


def test_off_means_no_sandbox(tmp_path):
    res = _hostile(tmp_path)
    ran = sandbox.run(res.output.files, res.output.entry_file, level="off", timeout=60)
    assert ran.level == "off"
    assert "LEAKED file: top secret" in ran.stdout      # proves the hostile program really tries


def test_timeout_and_levels():
    src = 'function[<>|<>] @entry main = Function() { int n = 0; repeat(int i in 0..9000000000000) { n = i; } print(n); }'
    res = compile_source(src, "spin.fx3")
    assert res.ok, res.report()
    ran = sandbox.run(res.output.files, res.output.entry_file, level="process", timeout=2)
    assert ran.timed_out and ran.returncode != 0
    with pytest.raises(ValueError):
        sandbox.resolve_level("sometimes")
    assert sandbox.resolve_level("auto") in ("os", "process")


def test_environment_is_clean(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak-0000000000")
    files = {"p.py": "import os\nprint(sorted(k for k in os.environ if 'KEY' in k))\nprint(os.environ['HOME'])\n"}
    ran = sandbox.run(files, "p.py", level="process", timeout=30)
    assert ran.stdout.splitlines()[0] == "[]", ran.output
    assert os.path.basename(ran.stdout.splitlines()[1]) == "work"
