"""fx3 is target-neutral (Syntax/targets.fx3, ROADMAP E-33 Phase 1)."""

from __future__ import annotations

import json

from fl_helpers import GRADES, Recorder, codes, no_model, project, reply, run_build

from fluidc.reader import canonical_target
from fluidc.settings import CliSettings, Settings


def test_the_model_never_sees_the_target(tmp_path):
    f = project(tmp_path, config='{"target": {"python": {"webFramework": "flask"}}, "codegen": {"comments": "minimal"}}')
    rec = Recorder([reply()])
    assert run_build(f, rec).ok
    request = rec.prompts[0]
    assert "Target language" not in request and "flask" not in request and "target-neutral" in request
    settings = json.loads(request.split("## Project settings (JSON)\n", 1)[1].split("\n\n", 1)[0])
    assert "target" not in settings and settings["codegen"]["comments"] == "minimal"


def test_changing_the_target_needs_no_model_call(tmp_path):
    f = project(tmp_path)
    assert run_build(f, Recorder([reply()])).ok
    # --target, the file's `target` line and the target settings are all outside the lock key
    res = run_build(f, no_model, CliSettings(offline=True, target="go"))
    assert res.ok and not res.generated and len(res.reused) == 8
    assert "FL0210" in codes(res)                # fx3c has no go backend yet: only the .fx3 is built
    (tmp_path / "grades.fluid").write_text(GRADES.replace("target python", "target rust"), encoding="utf-8")
    assert run_build(f, no_model, CliSettings(offline=True)).ok
    (tmp_path / "fluid.config").write_text('{"target": {"rust": {"web": "axum"}}}', encoding="utf-8")
    assert run_build(f, no_model, CliSettings(offline=True)).ok


def test_locks_with_the_target_in_the_key_stay_valid(tmp_path, monkeypatch):
    current = Settings.fingerprint
    # fluidc 0.3.0 hashed the target (and the real ask level)
    monkeypatch.setattr(Settings, "fingerprint",
                        lambda self, ask="mid", legacy="": current(self, self.values.get("ask", "mid"), "python"))
    f = project(tmp_path)
    assert run_build(f, Recorder([reply()]), CliSettings(ask="soft")).ok
    monkeypatch.setattr(Settings, "fingerprint", current)
    # an old Python lock, built straight for another target: still no model call
    res = run_build(f, no_model, CliSettings(offline=True, target="go"))
    assert res.ok and not res.generated
    # that build rewrote the keys in the new form, so the old forms are no longer tried
    monkeypatch.setattr(Settings, "fingerprint", lambda self, ask="mid", legacy="": current(self, ask, ""))
    assert run_build(f, no_model, CliSettings(offline=True)).ok


def test_target_spellings_and_unknown_targets(tmp_path):
    assert [canonical_target(t) for t in ("golang", "C++", "rs", "jvm", "Python3", "go")] == \
        ["go", "cpp", "rust", "java", "python", "go"]
    f = project(tmp_path, GRADES.replace("target python", "target golang"))
    res = run_build(f, Recorder([reply()]))
    assert res.ok and "FL0210" in codes(res)
    g = project(tmp_path / "b", GRADES.replace("target python", "target cobol"))
    assert "FL0211" in codes(run_build(g, no_model))


def test_target_flag_wins_over_the_file_and_the_config(tmp_path):
    f = project(tmp_path, GRADES, config='{"target": {"language": "java"}}')
    assert "FL0209" in codes(run_build(f, no_model))            # the file says python, the config java
    res = run_build(f, Recorder([reply()]), CliSettings(target="go"))
    assert res.ok, [m.render() for m in res.report.messages]


def test_fluidc_and_fx3c_agree_on_the_targets():
    from fx3c import backends
    from fluidc import reader
    assert reader.TARGETS == backends.TARGETS and reader.TARGET_ALIASES == backends.TARGET_ALIASES
