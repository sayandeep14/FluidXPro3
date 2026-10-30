"""The master `fluid` command (ROADMAP E-06)."""

from __future__ import annotations

import json

from fl_helpers import Recorder, project, reply, run_build

from fluid.cli import main as fluid_main
from fluid.doctor import credentials, environment

HELLO = 'function[<string>|<string>] @example("Ada" -> "Hi Ada") hi = Function(string name) { return "Hi " + name; }\n' \
        'function[<>|<>] @entry main = Function() { print(hi("world")); }\n'


def test_new_creates_a_project(tmp_path, capfd):
    target = tmp_path / "my-app"
    assert fluid_main(["new", str(target)]) == 0
    names = sorted(p.name for p in target.iterdir())
    assert names == [".gitignore", "README.md", "fluid.config", "fluid.config.schema.json", "main.fluid",
                     "secrets.env"]
    assert "secrets.env" in (target / ".gitignore").read_text(encoding="utf-8")
    assert "app My App" in (target / "main.fluid").read_text(encoding="utf-8")
    assert fluid_main(["new", str(target)]) == 1          # never overwrites
    # the scaffolded config is valid against the schema
    from fluidc.settings import load_config
    assert load_config(target / "fluid.config", required=True).present


def test_fx3_files_go_straight_to_fx3c(tmp_path, capfd):
    f = tmp_path / "hi.fx3"
    f.write_text(HELLO, encoding="utf-8")
    assert fluid_main(["check", str(f)]) == 0
    assert fluid_main(["run", str(f)]) == 0
    assert capfd.readouterr().out.strip() == "Hi world"
    assert fluid_main(["test", str(f)]) == 0
    out = capfd.readouterr()
    assert "1 passed" in out.out and "sandbox" in out.err
    assert fluid_main(["build", str(f), "-o", str(tmp_path / "py")]) == 0
    assert (tmp_path / "py" / "hi.py").exists() and (tmp_path / "py" / "requirements.txt").exists()


def test_fluid_files_build_to_python_and_check_in_ci(tmp_path, capfd):
    f = project(tmp_path)
    assert run_build(f, Recorder([reply()])).ok            # the "online" build, with a scripted model
    capfd.readouterr()
    assert fluid_main(["check", str(f)]) == 0
    assert "fluid.lock is current" in capfd.readouterr().err
    assert fluid_main(["build", str(f), "--offline"]) == 0
    assert (tmp_path / "build" / "python" / "grades.py").exists()
    assert fluid_main(["run", str(f), "--offline"]) == 0
    assert capfd.readouterr().out.splitlines()[-1] == "2 of 3 passed"
    assert fluid_main(["test", str(f), "--offline"]) == 0
    assert "2 passed" in capfd.readouterr().out
    # change the source: check fails (would need the model) and writes nothing
    before = (tmp_path / "fluid.lock").read_text(encoding="utf-8")
    f.write_text(f.read_text(encoding="utf-8").replace("pass mark is 40", "pass mark is 45"), encoding="utf-8")
    assert fluid_main(["check", str(f)]) == 1
    assert "out of date" in capfd.readouterr().err
    assert (tmp_path / "fluid.lock").read_text(encoding="utf-8") == before


def test_requirements_list_only_what_is_used(tmp_path):
    from fx3c.driver import compile_source
    plain = compile_source(HELLO, "hi.fx3").output.files["requirements.txt"]
    assert "anthropic" not in plain
    ai = compile_source('import Ai;\nfunction[<>|<>] @entry main = Function() { print("x"); }\n', "a.fx3")
    assert "anthropic>=2.3,<3" in ai.output.files["requirements.txt"]


def test_doctor_never_shows_the_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / "secrets.env").write_text("ANTHROPIC_API_KEY=sk-ant-doctor-test-value-123456\n", encoding="utf-8")
    checks = credentials(tmp_path)
    text = " ".join(c.render() for c in checks)
    assert "set (from" in text and "sk-ant" not in text
    assert all(c.status != "fail" for c in environment())
    project(tmp_path)
    assert fluid_main(["doctor", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "sk-ant" not in out and "grades.fluid" in out


def test_version_lists_every_part(capsys):
    try:
        fluid_main(["--version"])
    except SystemExit as e:
        assert e.code == 0
    out = capsys.readouterr().out
    assert all(part in out for part in ("fluid ", "fluidc ", "fx3c ", "language spec"))


def test_settings_reach_the_scaffold_schema():
    from fluidc.settings import PACKAGED_SCHEMA
    schema = json.loads(PACKAGED_SCHEMA.read_text(encoding="utf-8"))
    assert "sandbox" in json.dumps(schema)
