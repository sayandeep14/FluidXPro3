"""The IR every backend reads (ROADMAP E-33 Phase 3, fx3c/ir/)."""

from __future__ import annotations

from pathlib import Path

import pytest

from fx3c import ir
from fx3c.driver import compile_source
from fx3c.fxtypes import INT
from fx3c.ir import nodes as I

PROGRAMS = sorted(Path(__file__).parent.joinpath("programs").glob("*.fx3"))


def lowered(src: str, file: str = "t.fx3") -> list[I.Module]:
    res = compile_source(src, file, check_only=True)
    assert res.ok, res.report()
    return ir.lower(res)


def text(src: str) -> str:
    return ir.show(lowered(src))


@pytest.mark.parametrize("path", PROGRAMS, ids=[p.stem for p in PROGRAMS])
def test_every_program_lowers_to_sound_ir(path: Path):
    mods = lowered(path.read_text(encoding="utf-8"), path.name)
    assert ir.check(mods) == []
    assert ir.show(mods)


def main(body: str, before: str = "") -> str:
    return text(before + "function[<>|<>] @entry main = Function() {\n" + body + "\n}\n")


def test_collections_stored_from_a_variable_are_copied():
    out = main("vector<int> a = [1, 2];\nvector<int> b = a;\nb.add(3);\nprint(a, b);")
    assert "let b: vector<int> = copy(a)" in out


def test_narrowed_maybe_values_are_unwrapped():
    out = main("int? m = \"12\".toInt();\nif (m != none) { print(m + 1); }\nprint(m ?? 0);")
    assert "add(unwrap(m), 1)" in out and "coalesce(m, 0)" in out


def test_closures_say_how_they_capture():
    out = main("""int total = 0;
int base = 1;
vector<function[<>|<int>]> fs = [];
repeat(int i in 0..3) { fs.add(Function() => i + base); }
auto bump = Function() { total += 1; };
bump();
print(total, fs.size());""")
    assert "[i value, base read]" in out
    assert "let total: int cell = 0" in out and "[total cell]" in out


def test_task_variables_of_a_group_are_declared_before_it():
    out = main("group { task<int> t = spawn Function() => 1; }\nprint(run(t));")
    lines = [line.strip() for line in out.splitlines()]
    assert lines.index("let t: task<int>") < lines.index("group")
    assert "t = spawn" in out


def test_a_retyped_loose_variable_becomes_a_new_variable():
    out = main('auto x = 5;\nprint(x + 1);\nx = "five";\nprint(x);')
    assert "let x: int = 5" in out and "let x_2: string = 'five'" in out and "#print(x_2)" in out


def test_failures_are_explicit():
    before = """function[<int>|<int>] @fails(ValueError) half = Function(int n) {
    if (n % 2 != 0) { fail ValueError("odd"); }
    return n ~/ 2;
}
function[<int>|<int>] @fails(ValueError) quarter = Function(int n) => half(half(n)?)?;
"""
    out = main("print(half(3) ?? -1, quarter(8) ?? 0);", before)
    assert "fail new ValueError('odd')" in out
    assert "half(half(n) !ValueError?) !ValueError?" in out
    assert "orelse(half(3) !ValueError, -1)" in out
    assert "floordiv(n, 2)" in out


def test_switch_becomes_match_with_variant_tests():
    out = text("""enum Shape = Enum { Circle(float radius), Point }
function[<Shape>|<float>] size = Function(Shape s) {
    return switch(@none; s) {
        case(Circle(r) where r > 1.0).act(r);
        case(Circle(_)).act(1.0);
        case(Point).act(0.0);
    };
}
""")
    assert "enum Shape { Circle(radius: float), Point }" in out
    assert "case is(_s2, Shape.Circle r=0) where gt(r, 1.0)" in out
    assert "case is(_s2, Shape.Point)" in out


def test_method_bank_and_builtins_are_intrinsics():
    out = main('vector<int> v = [3, 1, 2];\nv.add(4);\nprint(v.size(), "ab".upper(), v.map(currVal * 2));')
    assert "#seq.add(v, value=4)" in out and "#seq.size(v)" in out and "#string.upper('ab')" in out
    assert "#seq.map(v, fn=fn(currVal) { return mul(currVal, 2) })" in out or "#seq.map(v" in out


def test_services_json_and_transactions_keep_their_meaning():
    out = text("""import Db;
function[<>|<Db>] @provides @fails(IOError) appDb = Function() => Db.open(":memory:")?;
class @service Store = Class() { Db db; }
class @derive(json) Item = Class() { string @length(1, 9) @public name; }
function[<Db>|<>] @transactional @fails(DbError) touch = Function(Db db) { db.exec("select 1")?; }
""")
    assert "class Store [service]" in out and "injected db <- Db" in out
    assert "field name: string @length(1, 9)" in out and "derive(json)" in out
    assert "func touch(db: Db) -> void fails DbError @transactional" in out
    assert "std Db.open(':memory:') !IOError?" in out


def test_projects_lower_dependencies_first(tmp_path):
    (tmp_path / "geo.fx3").write_text("function[<int>|<int>] double = Function(int x) => x * 2;\n", encoding="utf-8")
    entry = tmp_path / "main.fx3"
    entry.write_text("from ./geo import double;\nfunction[<>|<>] @entry main = Function() { print(double(4)); }\n",
                     encoding="utf-8")
    res = compile_source(entry.read_text(encoding="utf-8"), str(entry), check_only=True)
    assert res.ok, res.report()
    mods = ir.lower(res)
    assert [m.name for m in mods] == ["geo", ""]
    assert mods[1].imports == [("geo", ["double"])]
    assert "geo.double(4)" in ir.show(mods)


def test_the_validator_finds_broken_ir():
    stray = I.Local("ghost", INT)
    bad = I.Module("", "t.fx3", funcs=[I.Func("f", [], INT, [], I.Block([I.Return(I.Var(INT, stray))]))])
    assert any("`ghost` is used but never declared" in p for p in ir.check([bad]))


def test_fx3c_ir_command(tmp_path, capsys):
    from fx3c.cli import main as fx3c_main
    prog = tmp_path / "p.fx3"
    prog.write_text('function[<>|<>] @entry main = Function() { print("hi"); }\n', encoding="utf-8")
    assert fx3c_main(["ir", str(prog)]) == 0
    assert "#print('hi')" in capsys.readouterr().out
