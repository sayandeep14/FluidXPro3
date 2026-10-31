"""Projects with many .fx3 files (Syntax/modules.fx3; ROADMAP E-05)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from fx3c.driver import compile_source, write_output
from fx3c.project import python_name


def project(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path / "main.fx3"


def build(entry: Path):
    return compile_source(entry.read_text(encoding="utf-8"), str(entry))


def run(entry: Path, *args: str) -> subprocess.CompletedProcess:
    res = build(entry)
    assert res.ok, res.report()
    out = entry.parent / "out"
    write_output(res.output, out)
    return subprocess.run([sys.executable, res.output.entry_file, *args], cwd=out,
                          capture_output=True, text=True, timeout=60)


def errors(entry: Path) -> list[str]:
    return [d.code for d in build(entry).diags.items if d.severity == "error"]


SHOP = {
    "main.fx3": """
from ./pricing import total, Item, TAX as rate;
from billing.invoice import render;
from ./util/text import shout as loud;

vector<Item> cart = [Item("tea", 2.5), Item("cake", 4.0)];
print(total(cart), rate);
print(render(cart));
print(loud("done"));
""",
    "pricing.fx3": """
float @const TAX = 0.2;

class Item = Class() {
    string @public name;
    float @public price;
}

function[<vector<Item>>|<float>] total = Function(vector<Item> items) {
    float sum = 0.0;
    repeat(Item it in items) {
        sum += it.price;
    }
    return round(sum * (1.0 + TAX), 2);
}

function[<>|<>] @test totalOfNothing = Function() {
    ensure(total([]) == 0.0);
}
""",
    "billing/invoice.fx3": """
from pricing import Item, total;

function[<vector<Item>>|<string>] render = Function(vector<Item> items) {
    return f"{len(items)} items, {total(items)} with tax";
}

function[<>|<>] @test rendersEmpty = Function() {
    ensure(render([]) == "0 items, 0.0 with tax");
}
""",
    "util/text.fx3": """
function[<string>|<string>] shout = Function(string s) => s.upper() + "!";
""",
}


def test_a_project_runs(tmp_path):
    proc = run(project(tmp_path, SHOP))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["7.8 0.2", "2 items, 7.8 with tax", "DONE!"]


def test_one_python_file_per_module(tmp_path):
    res = build(project(tmp_path, SHOP))
    assert {"main.py", "pricing.py", "billing/invoice.py", "billing/__init__.py", "util/text.py",
            "util/__init__.py", "fx3rt.py", "requirements.txt"} <= set(res.output.files)
    main = res.output.files["main.py"]
    assert "from pricing import Item" in main
    assert "import pricing as _fx_pricing" in main and "_fx_pricing.total(" in main
    assert "_fx_pricing.TAX" in main
    assert "__main__" not in res.output.files["pricing.py"]


def test_tests_of_every_module_run(tmp_path):
    proc = run(project(tmp_path, SHOP), "--fx3-test")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "pricing: totalOfNothing" in proc.stdout
    assert "billing.invoice: rendersEmpty" in proc.stdout
    assert "2 passed, 0 failed" in proc.stdout


def test_circular_imports(tmp_path):
    entry = project(tmp_path, {
        "main.fx3": "from ./a import f;\nprint(f());\n",
        "a.fx3": "from ./b import g;\nfunction[<>|<int>] f = Function() => g();\n",
        "b.fx3": "from ./a import f;\nfunction[<>|<int>] g = Function() => 1;\n",
    })
    res = build(entry)
    msgs = [d for d in res.diags.items if d.code == "FX0320"]
    assert msgs and "a -> b -> a" in msgs[0].message


@pytest.mark.parametrize("files, code", [
    ({"main.fx3": "from ./nope import f;\n"}, "FX0310"),
    ({"main.fx3": "from ./a import secret;\n",
      "a.fx3": "function[<>|<int>] @private secret = Function() => 1;\n"}, "FX0317"),
    ({"main.fx3": "from ./a import counter;\n", "a.fx3": "int counter = 0;\n"}, "FX0317"),
    ({"main.fx3": "from ./a import f;\n", "a.fx3": "function[<>|<int>] f = Function() => 1;\nprint(1);\n"},
     "FX0319"),
    ({"main.fx3": "from ./a import nothing;\n", "a.fx3": "function[<>|<int>] f = Function() => 1;\n"}, "FX0311"),
    ({"main.fx3": "import ./a;\n", "a.fx3": "function[<>|<int>] f = Function() => 1;\n"}, "FX0316"),
    ({"main.fx3": "from ./a import make;\nItem x = make();\n",
      "a.fx3": "class Item = Class() { int @public n = 0; }\n"
               "function[<>|<Item>] make = Function() => Item();\n"}, "FX0315"),
    ({"main.fx3": "from ./a import f;\nclass Item = Class() { int n = 0; }\n",
      "a.fx3": "class Item = Class() { int n = 0; }\nfunction[<>|<int>] f = Function() => 1;\n"}, "FX0318"),
    ({"main.fx3": "from ../outside import f;\n"}, "FX0321"),
    ({"main.fx3": "from geo import f;\nfrom geo.shapes import g;\n",
      "geo.fx3": "function[<>|<int>] f = Function() => 1;\n",
      "geo/shapes.fx3": "function[<>|<int>] g = Function() => 1;\n"}, "FX0323"),
    ({"main.fx3": "from ./a import f;\n",
      "a.fx3": "function[<>|<>] @entry f = Function() { print(1); }\n"}, "FX0322"),
], ids=["missing", "private", "not constant", "statements in a library", "unknown name", "whole module",
        "class not imported", "class in two modules", "outside the project", "file and folder", "entry"])
def test_project_errors(tmp_path, files, code):
    assert code in errors(project(tmp_path, files))


def test_import_spelling_must_match_the_file(tmp_path):
    entry = project(tmp_path, {"main.fx3": "from Helpers import f;\n",
                               "helpers.fx3": "function[<>|<int>] f = Function() => 1;\n"})
    assert "FX0310" in errors(entry)        # also on case-insensitive disks (macOS, Windows)


def test_the_error_names_the_imported_file(tmp_path):
    entry = project(tmp_path, {"main.fx3": "from ./a import f;\nprint(f());\n",
                               "a.fx3": "function[<>|<int>] f = Function() => \"x\";\n"})
    res = build(entry)
    assert not res.ok
    assert "a.fx3" in res.report()


def test_module_names_that_would_hide_python_modules(tmp_path):
    assert python_name("json") == "json_" and python_name("geo.json") == "geo.json"
    assert python_name("class") == "class_" and python_name("fx3rt") == "fx3rt_"
    entry = project(tmp_path, {"main.fx3": "from ./json import parse;\nprint(parse());\n",
                               "json.fx3": "function[<>|<int>] parse = Function() => 7;\n"})
    proc = run(entry)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "7\n"


def test_imported_names_can_be_renamed(tmp_path):
    entry = project(tmp_path, {
        "main.fx3": "from ./a import twice as double2;\n"
                    "function[<int>|<int>] twice = Function(int x) => x + x + x;\n"
                    "print(double2(2), twice(2));\n",
        "a.fx3": "function[<int>|<int>] twice = Function(int x) => x * 2;\n"})
    proc = run(entry)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "4 6\n"


def test_a_single_file_is_unchanged(tmp_path):
    entry = project(tmp_path, {"main.fx3": "import Math;\nprint(Math.sqrt(16.0));\n"})
    res = build(entry)
    assert res.ok and res.project is not None and not res.project.libraries
    assert "modules=" not in res.output.files["main.py"]


def test_a_named_root_lets_a_file_import_from_above_its_folder(tmp_path):
    project(tmp_path, {"pricing.fx3": "function[<>|<int>] price = Function() => 3;\n",
                       "billing/invoice.fx3": "from ../pricing import price;\nprint(price());\n"})
    inv = tmp_path / "billing/invoice.fx3"
    assert "FX0321" in [d.code for d in build(inv).diags.items]               # root = billing/
    res = compile_source(inv.read_text(), str(inv), root=str(tmp_path))
    assert res.ok, res.report()


def test_library_mode_checks_a_file_as_an_import(tmp_path):
    src = "function[<>|<int>] f = Function() => 1;\nprint(f());\n"
    assert compile_source(src, str(tmp_path / "a.fx3")).ok
    res = compile_source(src, str(tmp_path / "a.fx3"), library=True)
    assert "FX0319" in [d.code for d in res.diags.items]


def test_interface_shows_signatures_only():
    from fx3c.project import interface
    text = interface(SHOP["pricing.fx3"] + "function[<int>|<int>] @private helper = Function(int x) => x;\n"
                     "int counter = 0;\nfunction[<int>|<int>] twice = Function(int x) => x * 2;\n")
    assert "float @const TAX = 0.2;" in text and "string @public name;" in text
    assert "total = Function(vector<Item> items);" in text and "twice = Function(int x);" in text
    assert "sum" not in text and "helper" not in text and "counter" not in text and "totalOfNothing" not in text
