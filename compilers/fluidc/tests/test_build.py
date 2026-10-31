"""The whole pipeline with a scripted model: generate, verify, retry, lock, edits, questions."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fl_helpers import GRADES, Recorder, codes, no_model, project, reply, run_build, with_block

from fluidc.assemble import parse_existing
from fluidc.cli import main as fluidc_main
from fluidc.settings import CliSettings


def fx3_of(path: Path) -> Path:
    return path.parent / "build" / "fx3" / "grades.fx3"


def test_build_then_offline_rebuild(tmp_path):
    f = project(tmp_path)
    rec = Recorder([reply()])
    res = run_build(f, rec)
    assert res.ok, [m.render() for m in res.report.messages]
    assert len(res.generated) == 8 and fx3_of(f).exists() and (tmp_path / "fluid.lock").exists()
    assert "FL0801" in codes(res)                  # the [warn] rule line
    # the request lists every block and asks only for the code blocks
    first = rec.prompts[0]
    assert '"to average of"' in first and "[GENERATE]" in first and "Target language: python" in first
    # second build: nothing changed -> no model call
    res2 = run_build(f, no_model, CliSettings(offline=True))
    assert res2.ok and len(res2.reused) == 8 and not res2.generated


def test_compiler_errors_are_fed_back(tmp_path):
    f = project(tmp_path)
    broken = with_block(reply(), "check grade for",
                        'function[<float>|<string>] grade_for = Function(float average) { return 5; }')
    fixed = {"blocks": [b for b in reply()["blocks"] if b["id"] == "check grade for"]}
    rec = Recorder([broken, fixed])
    res = run_build(f, rec)
    assert res.ok, [m.render() for m in res.report.messages]
    fix_prompt = rec.prompts[1]
    assert "fx3c rejected the code" in fix_prompt and 'block "check grade for"' in fix_prompt
    assert "FX0401" in fix_prompt


def test_failing_tests_are_fed_back(tmp_path):
    f = project(tmp_path)
    wrong = with_block(reply(), "check grade for",
                       'function[<float>|<string>] grade_for = Function(float average) { return "B"; }')
    fixed = {"blocks": [b for b in reply()["blocks"] if b["id"] == "check grade for"]}
    rec = Recorder([wrong, fixed])
    res = run_build(f, rec)
    assert res.ok
    assert "Test output" in rec.prompts[1] and "FAIL" in rec.prompts[1]


def test_gives_up_after_max_retries(tmp_path):
    f = project(tmp_path, config='{"llm": {"maxRetries": 1}}')
    bad = with_block(reply(), "settings", "int @const PASS_MARK = \"forty\";")
    rec = Recorder([bad, {"blocks": []}])
    res = run_build(f, rec)
    assert not res.ok and "FL0503" in codes(res)
    assert not fx3_of(f).exists()                  # nothing written on failure


def test_hand_edits_are_kept(tmp_path):
    f = project(tmp_path)
    assert run_build(f, Recorder([reply()])).ok
    p = fx3_of(f)
    text = p.read_text(encoding="utf-8").replace("int @const PASS_MARK = 40;", "int @const PASS_MARK = 50;")
    p.write_text(text, encoding="utf-8")
    res = run_build(f, no_model, CliSettings(offline=True))
    assert res.ok and res.kept_edits == ["settings"]
    assert "PASS_MARK = 50" in p.read_text(encoding="utf-8")
    assert parse_existing(p.read_text(encoding="utf-8"))["settings"].edited


def test_edit_plus_fluid_change_is_a_conflict(tmp_path):
    f = project(tmp_path)
    assert run_build(f, Recorder([reply()])).ok
    p = fx3_of(f)
    p.write_text(p.read_text(encoding="utf-8").replace("= 40;", "= 50;"), encoding="utf-8")
    f.write_text(GRADES.replace("pass mark is 40", "pass mark is 45"), encoding="utf-8")
    res = run_build(f, no_model, CliSettings(offline=True))
    assert not res.ok and "FL0501" in codes(res)
    # choosing "regenerate" sends the block to the model again
    rec = Recorder([{"blocks": [{"id": "settings", "fx3": "int @const PASS_MARK = 45;\nint @const TOP_GRADE = 90;"}]}])
    res = run_build(f, rec, asker=lambda q, opts, d: "regenerate from the .fluid text")
    assert res.ok and res.generated == ["settings"]
    assert "PASS_MARK = 45" in p.read_text(encoding="utf-8")


def test_only_changed_blocks_are_regenerated(tmp_path):
    f = project(tmp_path)
    assert run_build(f, Recorder([reply()])).ok
    f.write_text(GRADES.replace("pass mark is 40", "pass mark is 45"), encoding="utf-8")
    rec = Recorder([{"blocks": [{"id": "settings", "fx3": "int @const PASS_MARK = 45;\nint @const TOP_GRADE = 90;"}]}])
    res = run_build(f, rec)
    assert res.ok and res.generated == ["settings"] and len(res.reused) == 7
    assert "[GENERATE]" in rec.prompts[0] and "Current fx3 of the other blocks" in rec.prompts[0]


def test_exact_code_is_never_generated(tmp_path):
    fluid = GRADES.replace(
        "settings\n    pass mark is 40\n    top grade needs 90\n",
        "settings\n    pass mark is 40\n    top grade needs 90\n    exact\n        ```fx3\n"
        "        int @const PASS_MARK = 40;\n        int @const TOP_GRADE = 90;\n        ```\n")
    f = project(tmp_path, fluid)
    r = reply()
    r["blocks"] = [b for b in r["blocks"] if b["id"] != "settings"]
    rec = Recorder([r])
    res = run_build(f, rec)
    assert res.ok, [m.render() for m in res.report.messages]
    assert '"settings" (lines' in rec.prompts[0] and '"settings" (lines 9-15)  [GENERATE]' not in rec.prompts[0]
    assert "settings" not in res.generated


def _money_question():
    q = {"block": "to class report", "question": "Count a missing grade as a pass?", "options": ["no", "yes"],
         "default": "no", "impact": "money"}
    return reply(questions=[q])


def test_mid_asks_about_money_and_fails_in_ci(tmp_path):
    f = project(tmp_path)
    res = run_build(f, Recorder([_money_question()]))
    assert not res.ok and "FL0601" in codes(res)


def test_yes_accepts_defaults_and_soft_assumes(tmp_path):
    f = project(tmp_path)
    assert run_build(f, Recorder([_money_question()]), accept_defaults=True).ok
    g = project(tmp_path / "soft")
    res = run_build(g, Recorder([_money_question()]), CliSettings(ask="soft"))
    assert res.ok


def test_a_different_answer_regenerates(tmp_path):
    f = project(tmp_path)
    rec = Recorder([_money_question(), {"blocks": [b for b in reply()["blocks"] if b["id"] == "to class report"]}])
    res = run_build(f, rec, asker=lambda q, opts, d: "yes")
    assert res.ok
    assert "answered these questions differently" in rec.prompts[1]
    # the answer is remembered: the next build reuses everything
    assert run_build(f, no_model, CliSettings(offline=True)).ok


def test_unsupported_blocks(tmp_path):
    f = project(tmp_path, GRADES + "\nevery day at 9pm\n    Email the report to the teacher.\n")
    r = reply(unsupported=[{"block": "every day at 9pm", "reason": "schedules and email"}])
    r["blocks"].append({"id": "every day at 9pm", "fx3": ""})
    res = run_build(f, Recorder([r]))
    assert not res.ok and "FL0701" in codes(res)
    res = run_build(f, Recorder([r]), allow_unsupported=True)
    assert res.ok and "FL0701" in codes(res)


def test_error_tag_on_unverifiable_rule(tmp_path):
    f = project(tmp_path, GRADES.replace("[warn]", "[error]"))
    res = run_build(f, Recorder([reply()]))
    assert not res.ok and "FL0801" in codes(res)


def test_offline_with_changes_fails(tmp_path):
    f = project(tmp_path)
    res = run_build(f, no_model, CliSettings(offline=True))
    assert not res.ok and "FL0502" in codes(res)


def test_target_checks(tmp_path):
    f = project(tmp_path, GRADES.replace("target python\n", ""))
    assert "FL0208" in codes(run_build(f, no_model))
    g = project(tmp_path / "b", GRADES, config='{"target": {"language": "cpp"}}')
    assert "FL0209" in codes(run_build(g, no_model))


def test_pin_and_unpin(tmp_path):
    f = project(tmp_path)
    assert run_build(f, Recorder([reply()])).ok
    p = fx3_of(f)
    p.write_text(p.read_text(encoding="utf-8").replace("= 40;", "= 41;"), encoding="utf-8")
    assert fluidc_main(["unpin", str(f), "settings"]) == 0
    assert "= 40;" in p.read_text(encoding="utf-8")
    p.write_text(p.read_text(encoding="utf-8").replace("= 40;", "= 42;"), encoding="utf-8")
    assert fluidc_main(["pin", str(f), "settings"]) == 0
    assert "```fx3" in f.read_text(encoding="utf-8") and "PASS_MARK = 42" in f.read_text(encoding="utf-8")
    res = run_build(f, no_model)
    assert res.ok and "settings" not in res.generated


def test_generated_program_runs(tmp_path):
    f = project(tmp_path)
    assert run_build(f, Recorder([reply()])).ok
    fx3c_dir = Path(__file__).resolve().parents[2] / "fx3c"
    proc = subprocess.run([sys.executable, "-m", "fx3c", "run", "-q", str(fx3_of(f))], cwd=fx3c_dir,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["Sam     91.5 A", "Ria     72.3 C", "Tom     37.5 F", "2 of 3 passed"]


def test_block_imports_are_lifted_to_the_top(tmp_path):
    f = project(tmp_path)
    r = with_block(reply(), "to class report", next(b["fx3"] for b in reply()["blocks"]
                                                    if b["id"] == "to class report")
                   .replace('print(f"{passed} of {students.size()} passed");',
                            'Log.info("report done");\n    print(f"{passed} of {students.size()} passed");'))
    r = with_block(r, "to class report", "import Log;\n" + next(b["fx3"] for b in r["blocks"]
                                                                  if b["id"] == "to class report"))
    res = run_build(f, Recorder([r]))
    assert res.ok, [m.render() for m in res.report.messages]
    text = fx3_of(f).read_text(encoding="utf-8")
    assert text.index("import Log;") < text.index('// @from grades.fluid:9 "settings"')
    assert text.count("import Log;") == 1
    again = run_build(f, no_model, CliSettings(offline=True))
    assert again.ok and not again.kept_edits          # lifting imports is not a hand edit


def test_fx3c_finds_every_block_fluidc_marked(tmp_path):
    """The markers fluidc writes are what fx3c's source map reads (E-08)."""
    from fx3c.backends.python.emitter import fluid_blocks
    f = project(tmp_path)
    assert run_build(f, Recorder([reply()])).ok
    found = {block: (fluid_file, line) for _, _, block, fluid_file, line in fluid_blocks(fx3_of(f).read_text())}
    assert found["to average of"] == ("grades.fluid", GRADES.splitlines().index("to average of (student) -> a number") + 1)
    assert set(found) >= {"settings", "thing Student", "check grade for", "on start"}
