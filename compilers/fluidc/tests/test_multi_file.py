"""Projects with many .fluid files (ROADMAP E-05): `uses` other files, one lock, interfaces in the prompt."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fl_helpers import Recorder, codes, no_model, run_build

from fluid.cli import main as fluid_main
from fluidc.project import import_path
from fluidc.settings import CliSettings

MAIN = """app Shop
    Price a small cart.

target python

uses
    pricing.fluid for prices and tax

on start
    Print the total of a cart with tea at 2.5 and cake at 4.0.
"""

PRICING = """thing Item
    name: text
    price: a number

to total of (items) -> a number
    The sum of the prices plus 20% tax, rounded to 2 places.

    example
        no items -> 0.0
"""

PRICING_REPLY = {"blocks": [
    {"id": "thing Item", "fx3": "class Item = Class() {\n    string @public name;\n    float @public price;\n}"},
    {"id": "to total of", "fx3": "function[<vector<Item>>|<float>] @example([] -> 0.0) total = Function(vector<Item> items) {\n"
                                 "    float sum = 0.0;\n    repeat(Item it in items) { sum += it.price; }\n"
                                 "    return round(sum * 1.2, 2);\n}"},
]}

MAIN_REPLY = {"blocks": [
    {"id": "on start", "fx3": "from ./pricing import Item, total;\n\n"
                              "function[<>|<>] @entry main = Function() {\n"
                              "    print(total([Item(\"tea\", 2.5), Item(\"cake\", 4.0)]));\n}"},
]}


def shop(tmp: Path, main: str = MAIN, pricing: str = PRICING) -> Path:
    (tmp / "main.fluid").write_text(main, encoding="utf-8")
    (tmp / "pricing.fluid").write_text(pricing, encoding="utf-8")
    return tmp / "main.fluid"


def messages(res) -> list[str]:
    return [m.render() for m in res.report.messages]


def test_used_files_are_built_first_and_share_one_lock(tmp_path):
    f = shop(tmp_path)
    rec = Recorder([PRICING_REPLY, MAIN_REPLY])
    res = run_build(f, rec)
    assert res.ok, messages(res)
    assert res.files == ["pricing.fluid", "main.fluid"]
    assert (tmp_path / "build/fx3/pricing.fx3").exists() and (tmp_path / "build/fx3/main.fx3").exists()
    lock = json.loads((tmp_path / "fluid.lock").read_text())
    assert {"pricing.fluid:to total of", "main.fluid:on start"} <= set(lock["blocks"])
    assert set(lock["verification"]) == {"pricing.fluid", "main.fluid"}
    # the main file's request carries pricing's interface (signatures, no bodies) and the import path
    main_prompt = rec.prompts[1]
    assert "pricing.fluid  (import from `./pricing`)" in main_prompt
    assert "total = Function(vector<Item> items);" in main_prompt and "sum +=" not in main_prompt
    # pricing has no program block, so it was asked for as a module
    assert "## This file is a module" in rec.prompts[0] and "## This file is a module" not in main_prompt
    # nothing changed -> no model call
    again = run_build(f, no_model, CliSettings(offline=True))
    assert again.ok and set(again.reused) == {"on start"}, messages(again)


def test_the_program_runs_and_tests_every_file(tmp_path, capfd):
    f = shop(tmp_path)
    assert run_build(f, Recorder([PRICING_REPLY, MAIN_REPLY])).ok
    assert fluid_main(["run", str(f), "--offline"]) == 0
    assert capfd.readouterr().out.strip().endswith("7.8")
    assert fluid_main(["test", str(f), "--offline"]) == 0
    assert "pricing: example 1 of total" in capfd.readouterr().out


def test_check_only_writes_nothing_but_still_verifies_imports(tmp_path):
    f = shop(tmp_path)
    assert run_build(f, Recorder([PRICING_REPLY, MAIN_REPLY])).ok
    (tmp_path / "build").rename(tmp_path / "old-build")          # no .fx3 on disk at all
    assert fluid_main(["check", str(f)]) == 0
    assert not (tmp_path / "build").exists()


def test_an_interface_change_regenerates_the_files_that_use_it(tmp_path):
    f = shop(tmp_path)
    assert run_build(f, Recorder([PRICING_REPLY, MAIN_REPLY])).ok
    # pricing's prose changes and its regenerated code has a new signature
    (tmp_path / "pricing.fluid").write_text(PRICING.replace("(items)", "(cart)"))
    new_pricing = json.loads(json.dumps(PRICING_REPLY))
    new_pricing["blocks"][1]["fx3"] = new_pricing["blocks"][1]["fx3"].replace(
        "Function(vector<Item> items)", "Function(vector<Item> cart)").replace("in items", "in cart")
    offline = run_build(f, no_model, CliSettings(offline=True))
    assert not offline.ok and "FL0502" in codes(offline)
    rec = Recorder([new_pricing, MAIN_REPLY])
    res = run_build(f, rec)
    assert res.ok, messages(res)
    assert res.generated == ["on start"] and "Function(vector<Item> cart);" in rec.prompts[1]


def test_a_used_file_with_a_program_block(tmp_path):
    f = shop(tmp_path, pricing=PRICING + "\non start\n    Print hello.\n")
    res = run_build(f, no_model)
    assert not res.ok and "FL0422" in codes(res)


def test_files_that_use_each_other(tmp_path):
    f = shop(tmp_path, pricing="uses\n    main.fluid\n\n" + PRICING)
    res = run_build(f, no_model)
    assert not res.ok and "FL0420" in codes(res) or "FL0422" in codes(res)
    (tmp_path / "a.fluid").write_text("uses\n    b.fluid\n\n" + PRICING)
    (tmp_path / "b.fluid").write_text("uses\n    a.fluid\n\n" + PRICING)
    res = run_build(tmp_path / "a.fluid", no_model)
    assert "FL0420" in codes(res)
    assert "a.fluid -> b.fluid -> a.fluid" in messages(res)[0]


@pytest.mark.parametrize("uses, code", [("missing.fluid", "FL0421"), ("../outside.fluid", "FL0423")])
def test_bad_uses(tmp_path, uses, code):
    (tmp_path / "outside.fluid").write_text(PRICING)
    proj = tmp_path / "proj"
    proj.mkdir()
    f = shop(proj, main=MAIN.replace("pricing.fluid", uses))
    assert code in codes(run_build(f, no_model))


def test_files_in_folders(tmp_path):
    (tmp_path / "shop").mkdir()
    f = shop(tmp_path, main=MAIN.replace("pricing.fluid", "shop/pricing.fluid"))
    (tmp_path / "pricing.fluid").rename(tmp_path / "shop/pricing.fluid")
    main_reply = json.loads(json.dumps(MAIN_REPLY).replace("./pricing", "./shop/pricing"))
    rec = Recorder([PRICING_REPLY, main_reply])
    res = run_build(f, rec)
    assert res.ok, messages(res)
    assert (tmp_path / "build/fx3/shop/pricing.fx3").exists()
    assert "shop/pricing.fluid:to total of" in json.loads((tmp_path / "fluid.lock").read_text())["blocks"]
    out = subprocess.run([sys.executable, "-m", "fx3c", "run", str(tmp_path / "build/fx3/main.fx3"), "-q"],
                         capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "7.8", out.stderr


def test_import_paths():
    assert import_path("main.fx3", "pricing.fx3") == "./pricing"
    assert import_path("main.fx3", "shop/pricing.fx3") == "./shop/pricing"
    assert import_path("billing/invoice.fx3", "pricing.fx3") == "../pricing"
    assert import_path("billing/invoice.fx3", "billing/tax.fx3") == "./tax"


def test_a_single_file_keeps_its_lock_keys(tmp_path):
    """No `uses` of other files: block keys are exactly what they were before projects existed."""
    from fluidc.lock import block_key
    assert block_key("h", "fp", {}) == block_key("h", "fp", {}, "")


def test_doctor_checks_used_files_through_their_user(tmp_path, capfd):
    from fluid.doctor import project_files
    f = shop(tmp_path)
    assert run_build(f, Recorder([PRICING_REPLY, MAIN_REPLY])).ok
    checks = project_files(tmp_path)
    assert [c.what for c in checks] == ["main.fluid"] and checks[0].status == "ok"
    assert "with pricing.fluid" in checks[0].detail
