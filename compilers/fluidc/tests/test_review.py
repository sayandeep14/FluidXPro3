"""The review workflow (ROADMAP E-15): accept / reject blocks, --require-reviewed, diff, describe."""

from __future__ import annotations

import json

from fl_helpers import Recorder, no_model, project, reply, run_build

from fluidc.cli import main as fluidc_main
from fluidc.cli import review_interactively, unreviewed
from fluidc.lock import Lock
from fluidc.settings import CliSettings

AVG = "to average of"


def built(tmp_path):
    f = project(tmp_path)
    assert run_build(f, Recorder([reply()])).ok
    return f


def entries(f):
    lock = Lock.load(f.parent / "fluid.lock")
    return lock, {k.split(":", 1)[1]: v for k, v in lock.blocks.items()}


def test_every_generated_block_starts_unreviewed(tmp_path, capfd):
    f = built(tmp_path)
    todo = unreviewed(f)
    assert AVG in todo and "settings" in todo
    assert fluidc_main(["review", str(f), "--require-reviewed"]) == 1
    assert "not reviewed" in capfd.readouterr().err


def test_accepting_everything(tmp_path, capfd):
    f = built(tmp_path)
    lock, es = entries(f)
    assert review_interactively(f, lock, es, ask=lambda q, o, d: "accept") == 0
    assert unreviewed(f) == []
    assert fluidc_main(["review", str(f), "--require-reviewed"]) == 0
    saved = json.loads((tmp_path / "fluid.lock").read_text())["blocks"][f"grades.fluid:{AVG}"]["review"]
    assert saved["status"] == "accepted" and saved["fx3"] and saved["by"]
    # an offline rebuild keeps the reviews
    assert run_build(f, no_model, CliSettings(offline=True)).ok
    assert unreviewed(f) == []


def test_a_rejected_block_is_regenerated_with_the_note(tmp_path):
    f = built(tmp_path)
    lock, es = entries(f)
    answers = iter(["reject (regenerate with a note)"])
    review_interactively(f, lock, es, ask=lambda q, o, d: next(answers, "stop reviewing") if AVG in q else "skip",
                         ask_note=lambda p: "round to 2 places, not 1")
    offline = run_build(f, no_model, CliSettings(offline=True))
    assert not offline.ok and AVG in offline.report.messages[0].text          # needs the model now
    fixed = {"blocks": [b for b in reply()["blocks"] if b["id"] == AVG]}
    rec = Recorder([fixed])
    res = run_build(f, rec)
    assert res.ok and res.generated == [AVG]
    assert "The developer rejected the previous code" in rec.prompts[0] and "round to 2 places" in rec.prompts[0]
    _, es = entries(f)
    assert es[AVG].feedback == "" and es[AVG].review == {} and AVG in unreviewed(f)


def test_new_code_needs_a_new_review(tmp_path):
    f = built(tmp_path)
    lock, es = entries(f)
    review_interactively(f, lock, es, ask=lambda q, o, d: "accept")
    f.write_text(f.read_text().replace("has average 0.", "has an average of 0."))
    new_code = next(b["fx3"] for b in reply()["blocks"] if b["id"] == AVG) + "\n// mean of the marks"
    assert run_build(f, Recorder([{"blocks": [{"id": AVG, "fx3": new_code}]}])).ok
    assert unreviewed(f) == [AVG]                                  # only the regenerated block


def test_diff_shows_hand_edits(tmp_path, capfd):
    f = built(tmp_path)
    fx3 = tmp_path / "build/fx3/grades.fx3"
    fx3.write_text(fx3.read_text().replace("int @const PASS_MARK = 40;", "int @const PASS_MARK = 50;"))
    capfd.readouterr()
    assert fluidc_main(["diff", str(f)]) == 0
    out = capfd.readouterr().out
    assert "■ settings  (edited by hand)" in out and "-int @const PASS_MARK = 40;" in out
    assert "+int @const PASS_MARK = 50;" in out and AVG not in out


def test_describe_rewrites_the_prose_and_keeps_the_edit(tmp_path, capfd):
    f = built(tmp_path)
    fx3 = tmp_path / "build/fx3/grades.fx3"
    fx3.write_text(fx3.read_text().replace("int @const PASS_MARK = 40;", "int @const PASS_MARK = 50;"))
    new_text = "settings\n    pass mark is 50\n    top grade needs 90"
    replies = tmp_path / "replies.json"
    replies.write_text(json.dumps([{"text": new_text}]))
    assert fluidc_main(["describe", str(f), "settings", "--yes", "--scripted", str(replies)]) == 0
    assert "pass mark is 50" in f.read_text()
    # the .fluid file changed, the .fx3 still has the edit: no conflict, no model call
    res = run_build(f, no_model, CliSettings(offline=True))
    assert res.ok, [m.render() for m in res.report.messages]
    assert "settings" in res.kept_edits and "PASS_MARK = 50" in fx3.read_text()
    assert run_build(f, no_model, CliSettings(offline=True)).ok                 # and stays that way


def test_describe_refuses_text_that_isnt_the_block(tmp_path, capfd):
    f = built(tmp_path)
    fx3 = tmp_path / "build/fx3/grades.fx3"
    fx3.write_text(fx3.read_text().replace("int @const PASS_MARK = 40;", "int @const PASS_MARK = 50;"))
    replies = tmp_path / "replies.json"
    replies.write_text(json.dumps([{"text": "words\n    nothing"}]))
    before = f.read_text()
    assert fluidc_main(["describe", str(f), "settings", "--yes", "--scripted", str(replies)]) == 1
    assert f.read_text() == before and "FL1004" in capfd.readouterr().err


def test_fluid_check_can_require_reviews(tmp_path, capfd):
    from fluid.cli import main as fluid_main
    f = built(tmp_path)
    assert fluid_main(["check", str(f)]) == 0
    assert fluid_main(["check", str(f), "--require-reviewed"]) == 1
    lock, es = entries(f)
    review_interactively(f, lock, es, ask=lambda q, o, d: "accept")
    assert fluid_main(["check", str(f), "--require-reviewed"]) == 0
    assert fluid_main(["diff", str(f)]) == 0
