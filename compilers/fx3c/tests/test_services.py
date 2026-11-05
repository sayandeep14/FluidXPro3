"""Services, field constraints, @derive(json) and @transactional (Syntax/classes.fx3 7b-7c, stdlib.fx3 Db;
ROADMAP E-33 Phase 2)."""

from __future__ import annotations

import pytest

from helpers import error_codes, run_fx3

DB = "import Db;\nfunction[<>|<Db>] @provides @fails(IOError) appDb = Function() => Db.open(\":memory:\")?;\n"


@pytest.mark.parametrize("src,code", [
    # services
    (DB + "class @service A = Class() { int n; }", "FX0771"),                    # n has no value
    (DB + "class @service A = Class() { B b; }\nclass @service B = Class() { A a; }", "FX0772"),  # a circle
    (DB + "class @service A = Class() { int @public n = 0;\n"
          "  function[<>|<>] bump = Function() { this.n += 1; } }", "FX0773"),     # shared: not atomic
    (DB + "class @service A = Class() { Db db; }\n"
          "function[<>|<>] @entry main = Function() { A a = A(); }", "FX0782"),  # made by the program
    (DB + "class P = Class() { int n; }\nfunction[<>|<>] @entry main = Function() { print(service(P)); }",
     "FX0781"),
    ("function[<int>|<int>] @provides f = Function(int x) => x;", "FX0774"),
    # field constraints
    ("class C = Class() { int @length(1, 5) n; }", "FX0778"),
    ("class C = Class() { string @range(1, 5) s; }", "FX0778"),
    ("class C = Class() { string @pattern(\"[a-\") s; }", "FX0777"),
    ("class C = Class() { int @range(9, 1) n; }", "FX0779"),
    ("class D = Class() { int n; }\nclass @derive(json) C = Class() { D d; }", "FX0780"),  # D can't be read
    # transactions
    ("function[<int>|<>] @transactional @fails(DbError) f = Function(int x) { }", "FX0775"),
    ("import Db;\nfunction[<Db>|<>] @transactional f = Function(Db db) { }", "FX0776"),
])
def test_errors(src, code):
    assert code in error_codes(src + "\n")


def test_atomic_and_locked_fields_of_a_service_can_change():
    src = DB + """class @service Counter = Class() {
    atomic<int> @public hits = 0;
    locked<vector<string>> seen = [];
    function[<string>|<>] hit = Function(string who) {
        this.hits += 1;
        with (this.seen) { this.seen.add(who); }
    }
    function[<>|<int>] distinct = Function() {
        with (this.seen) { return this.seen.size(); }
    }
}
function[<>|<>] @entry main = Function() {
    Counter c = service(Counter);
    group { repeat(int i in 0..20) { spawn Function() { service(Counter).hit("x"); }; } }
    print(c.hits, c.distinct());
}
"""
    assert error_codes(src) == []
    proc = run_fx3(src)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "20 20\n"


def test_a_service_that_cant_start_stops_the_program_first():
    src = """import Db;
function[<>|<Db>] @provides @fails(IOError) appDb = Function() => Db.open("/no/such/folder/x.db")?;
class @service Store = Class() { Db db; }
function[<>|<>] @entry main = Function() { print("main ran"); }
"""
    proc = run_fx3(src)
    assert proc.returncode == 1
    assert "main ran" not in proc.stdout and "a service couldn't start" in proc.stderr
