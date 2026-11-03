"""Tasks, channels, locked / atomic, group and select (Syntax/concurrency.fx3; ROADMAP E-33 Phase 2)."""

from __future__ import annotations

import time

import pytest

from helpers import error_codes, run_fx3

WORK = """function[<int>|<int>] @fails(ValueError) work = Function(int n) {
    if (n < 0) { fail ValueError("negative"); }
    sleep(5ms);
    return n * 2;
}
"""


def main(body: str, asyn: bool = True) -> str:
    return WORK + f"function[<>|<>] {'@async ' if asyn else ''}@entry main = Function() {{\n{body}\n}}\n"


@pytest.mark.parametrize("body,code,asyn", [
    ("task<int> t = spawn work(1);\nprint(await t ?? 0);", "FX0760", False),     # await needs @async
    ("task<int> t = spawn work(1);\nint x = await t;\nprint(x);", "FX0625", True),  # its failure must be handled
    ("locked<int> n = 0;\nprint(n);", "FX0762", True),                          # only inside with
    ("int n = 0;\ngroup { spawn Function() { n += 1; }; }\nprint(n);", "FX0764", True),   # a data race
    ("group { spawn work(1); }", "FX0763", True),                               # the group can fail
    ("atomic<string> s = \"x\";", "FX0765", True),
    ("int n = 1;\nwith (n) { print(n); }", "FX0766", True),
    ("task<int> t = spawn 5;", "FX0767", True),
    ("after(1s);", "FX0768", True),
    ("channel<int> c;\nselect { case (print(1)) { } }", "FX0770", True),
])
def test_concurrency_errors(body, code, asyn):
    assert code in error_codes(main(body, asyn))


def test_a_ref_parameter_cant_be_given_to_a_task():
    src = """function[<vector<int> @ref>|<>] grow = Function(vector<int> @ref v) { v.add(1); }
function[<>|<>] @entry main = Function() {
    vector<int> xs = [];
    task<> t = spawn grow(xs);
    run(t);
}
"""
    assert "FX0764" in error_codes(src)


def test_shared_values_are_fine():
    ok = main("""atomic<int> n = 0;
locked<vector<int>> seen = [];
group {
    repeat(int i in 0..10) {
        spawn Function() { n += 1; with (seen) { seen.add(1); } };
    }
}
with (seen) { print(n, seen.size()); }""")
    assert error_codes(ok) == []
    proc = run_fx3(ok)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "10 10\n"


def test_a_failing_task_cancels_its_group_and_fails_it():
    src = WORK + """function[<>|<int>] slow = Function() {
    sleep(5s);           // cancelled long before this ends
    return 1;
}
function[<>|<>] @entry main = Function() {
    try {
        group {
            spawn slow();
            spawn work(-1);
        }
    } catch (ValueError e) {
        print("group failed:", e.message);
    }
}
"""
    start = time.monotonic()
    proc = run_fx3(src)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "group failed: negative\n"
    assert time.monotonic() - start < 4


def test_tasks_get_copies_of_collections():
    src = """function[<vector<int>>|<int>] total = Function(vector<int> v) { sleep(20ms); return v.sum(); }
function[<>|<>] @entry main = Function() {
    vector<int> xs = [1, 2, 3];
    task<int> t = spawn total(xs);
    xs.add(100);                     // after the task started: it has its own copy
    print(run(t));
}
"""
    proc = run_fx3(src)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "6\n"


def test_select_on_a_closed_channel():
    src = """function[<>|<>] @entry main = Function() {
    channel<int> c;
    c.close();
    select {
        case (int? v = c.receive()) { print("closed gives", v); }
        case (after(1s)) { print("timeout"); }
    }
    select {
        case (int v = c.receive()) { print(v); }      // skipped: the channel is closed
        case (after(10ms)) { print("only the timer"); }
    }
}
"""
    proc = run_fx3(src)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "closed gives none\nonly the timer\n"
