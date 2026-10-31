"""Schedule (every 10m / every day at 21:00, with saved last runs) and Process (no shell) (ROADMAP E-27)."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from helpers import codes, run_fx3

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "fx3c" / "backends" / "python"


@pytest.fixture
def std(tmp_path, monkeypatch):
    sys.path.insert(0, str(RUNTIME_DIR))
    try:
        import fx3rt
        import fx3std
    finally:
        sys.path.remove(str(RUNTIME_DIR))
    monkeypatch.setenv("SCHEDULE_STATE", str(tmp_path / "state.json"))
    fx3std.Schedule._jobs = []
    fx3std.Schedule._state = None
    yield fx3std, fx3rt
    fx3std.Schedule._jobs = []
    fx3std.Schedule._state = None


def test_jobs_run_when_due_and_the_last_run_is_saved(std, tmp_path):
    fx3std, _ = std
    ran: list[str] = []
    fx3std.Schedule.every(600.0, lambda: ran.append("sync"), name="sync")
    assert fx3std.Schedule.runPending() == 1           # never ran: due now
    assert fx3std.Schedule.runPending() == 0           # not again for 10 minutes
    assert ran == ["sync"]
    saved = json.loads((tmp_path / "state.json").read_text())
    assert set(saved) == {"sync"}
    # a restart (state read back from the file) doesn't run it again
    fx3std.Schedule._state = None
    assert fx3std.Schedule.runPending() == 0


def test_daily_at_a_time(std):
    fx3std, _ = std
    S = fx3std.Schedule
    S.every(86400.0, lambda: None, at="21:00", name="report")
    job = S._jobs[0]
    morning = datetime(2026, 10, 31, 9, 0)
    assert S._next(job, morning) == datetime(2026, 10, 31, 21, 0)              # first run: tonight
    late = datetime(2026, 10, 31, 22, 0)
    assert S._next(job, late) == datetime(2026, 11, 1, 21, 0)                  # too late today: tomorrow
    S._state = {"report": "2026-10-31T21:00:02"}
    assert S._next(job, late) == datetime(2026, 11, 1, 21, 0)                  # ran today: tomorrow
    week_later = datetime(2026, 11, 7, 10, 0)
    assert S._next(job, week_later) <= week_later                              # missed days: due now ...
    S._state = {"report": week_later.isoformat()}
    assert S._next(job, week_later) == datetime(2026, 11, 8, 21, 0)            # ... once


def test_a_failing_job_doesnt_stop_the_others(std, capfd):
    fx3std, fx3rt = std
    ran: list[str] = []

    def bad():
        raise fx3rt.Panic("broken job")
    fx3std.Schedule.every(60.0, bad, name="bad")
    fx3std.Schedule.every(60.0, lambda: ran.append("good"), name="good")
    assert fx3std.Schedule.runPending() == 2 and ran == ["good"]
    assert 'job "bad" failed: broken job' in capfd.readouterr().err


@pytest.mark.parametrize("call, message", [
    (lambda S: S.every(0.0, lambda: None), "more than 0"),
    (lambda S: S.every(86400.0, lambda: None, at="25:00"), "HH:MM"),
    (lambda S: S.every(3600.0, lambda: None, at="09:00"), "whole days"),
])
def test_bad_schedules_panic(std, call, message):
    fx3std, fx3rt = std
    with pytest.raises(fx3rt.Panic, match=message):
        call(fx3std.Schedule)


def test_schedule_from_fx3():
    src = ('import Schedule;\nfunction[<>|<>] tick = Function() { print("tick"); }\n'
           'Schedule.every(1h, tick, name = "tick");\n'
           'Schedule.every(1d, Function() { print("daily"); }, at = "00:00");\n'
           'print(Schedule.runPending() >= 1);\n')
    assert "FX0479" in codes('import Schedule;\nfunction[<int>|<int>] f = Function(int x) => x;\nSchedule.every(1h, f);\n')
    proc = run_fx3(src)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines()[0] == "tick" and proc.stdout.strip().endswith("true")


# ---- Process -----------------------------------------------------------------------

def test_process_runs_a_program_without_a_shell(std):
    fx3std, _ = std
    r = fx3std.Process.run([sys.executable, "-c", "import sys; print(sys.argv[1]); print('e', file=sys.stderr)",
                            "a; echo injected"])
    assert r.ok() and r.out == "a; echo injected\n" and r.err == "e\n"      # the ; is just text
    r = fx3std.Process.run([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert r.code == 3 and not r.ok()
    assert fx3std.Process.run([sys.executable, "-c", "print(input())"], input_="hello").out == "hello\n"


def test_process_failures(std):
    fx3std, fx3rt = std
    with pytest.raises(fx3rt.IOError, match="no program"):
        fx3std.Process.run(["definitely-not-a-program-fx3"])
    with pytest.raises(fx3rt.TimeoutError):
        fx3std.Process.run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.3)
    with pytest.raises(fx3rt.Panic, match="empty"):
        fx3std.Process.run([])


def test_process_from_fx3_must_handle_failures():
    assert "FX0625" in codes('import Process;\nProcessResult r = Process.run(["ls"]);\n')
    proc = run_fx3(f'import Process;\ntry {{\n    ProcessResult r = Process.run(["{Path(sys.executable).as_posix()}", "-c", "print(6 * 7)"]);\n'
                   '    print(r.code, r.out.trim());\n} catch (IOError e) {\n    print("io");\n}\n')
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "0 42"
