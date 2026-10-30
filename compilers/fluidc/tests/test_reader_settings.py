"""The deterministic front half: reading .fluid files and combining settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from fluidc.errors import FluidError
from fluidc.reader import read
from fluidc.settings import CliSettings, effective, load_config, strip_jsonc, suspicious_keys

MAIN_FLUID = Path(__file__).resolve().parents[3] / "main.fluid"
MAIN_CONFIG = Path(__file__).resolve().parents[3] / "fluid.config"


def test_reads_main_fluid():
    doc = read(MAIN_FLUID.read_text(encoding="utf-8"), "main.fluid")
    ids = [b.id for b in doc.blocks]
    assert "to monthly summary" in ids and "check how worried should I be" in ids and "rules" in ids
    assert doc.file_target == "python" and doc.file_ask == "mid"
    split = doc.block("to split bill")
    assert split.exact is not None and "splitBill" in split.exact
    cancel = doc.block("to cancel expense")
    assert cancel.ask == "hard" and ("safety", "paranoid") in cancel.with_settings
    assert doc.block("rules").tags == {"expenses are never deleted, only cancelled": "error"}
    assert len(doc.block("to monthly summary").examples) == 1


def test_aliases_and_signatures():
    doc = read("how to add up (a, b = 2) -> a number\n    Add them.\nglossary\n    \"x\" a thing\n", "f.fluid")
    b = doc.blocks[0]
    assert b.keyword == "to" and b.written == "how to"
    assert b.name == "add up" and b.inputs == ["a", "b = 2"] and b.output == "a number"
    assert doc.blocks[1].keyword == "words"


def test_prose_is_never_a_sub_keyword():
    doc = read("to f (x)\n    For example, if x is 2 it gives 4.\n    With care.\n    example\n        f 2 -> 4\n",
               "f.fluid")
    b = doc.blocks[0]
    assert len(b.examples) == 1
    assert b.with_settings == []
    assert "For example, if x is 2 it gives 4." in b.body


def test_comments_and_urls():
    doc = read("// top comment\nto fetch (url)\n    Read https://example.com/x  // a note\n", "f.fluid")
    assert doc.blocks[0].body == ["Read https://example.com/x"]


def test_unknown_keyword_suggests():
    with pytest.raises(FluidError) as e:
        read("thng Expense\n    amount: money\n", "f.fluid")
    m = e.value.messages[0]
    assert m.code == "FL0101" and "thing" in (m.fix or "")


def test_jsonc():
    import json
    text = strip_jsonc('{"a": "x // not a comment", // comment\n "b": [1, 2,], /* c */}')
    assert json.loads(text) == {"a": "x // not a comment", "b": [1, 2]}


def test_priority_order(tmp_path):
    cfg = load_config(MAIN_CONFIG)
    doc = read(MAIN_FLUID.read_text(encoding="utf-8"), "main.fluid")
    cancel = doc.block("to cancel expense")
    s = effective(cfg, doc, cancel, CliSettings(ask="soft", sets=["comments=minimal"]))
    assert s.get("ask") == "hard"                     # block beats --ask
    assert s.sources["ask"].startswith("`ask` in block")
    assert s.get("codegen.comments") == "detailed"    # block `with` beats --set
    assert s.get("codegen.safety") == "paranoid"
    assert s.get("verification.rules") == "error"     # from overrides[0]
    other = effective(cfg, doc, doc.block("to monthly summary"), CliSettings(ask="soft", sets=["comments=minimal"]))
    assert other.get("ask") == "soft" and other.get("codegen.comments") == "minimal"
    assert "auditLog" not in other.custom and "commitMessageStyle" in other.custom


def test_bad_with_value_is_an_error():
    cfg = load_config(MAIN_CONFIG)
    doc = read("to f (x)\n    with safety superstrong\n    Do it.\n", "f.fluid")
    with pytest.raises(FluidError) as e:
        effective(cfg, doc, doc.blocks[0], CliSettings())
    assert e.value.messages[0].code == "FL0204"


def test_typo_keys_are_suspicious(tmp_path):
    p = tmp_path / "fluid.config"
    p.write_text('{"codegen": {"comment": "detailed"}, "copyrightHeader": "x"}', encoding="utf-8")
    cfg = load_config(p)
    assert suspicious_keys(cfg) == [("codegen.comment", "codegen.comments")]
