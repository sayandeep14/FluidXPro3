from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

from fluidc.builder import BuildOptions, build
from fluidc.llm import ScriptedGenerator
from fluidc.settings import CliSettings

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
GRADES = (EXAMPLES / "grades.fluid").read_text(encoding="utf-8")
GRADES_REPLY = json.loads((EXAMPLES / "grades.replies.json").read_text(encoding="utf-8"))[0]


def reply(**changes) -> dict:
    r = copy.deepcopy(GRADES_REPLY)
    r.update(changes)
    return r


def with_block(r: dict, block_id: str, code: str) -> dict:
    r = copy.deepcopy(r)
    for b in r["blocks"]:
        if b["id"] == block_id:
            b["fx3"] = code
    return r


class Recorder:
    """A generator factory that remembers every generator it made."""

    def __init__(self, replies: list[dict]):
        self.replies = replies
        self.made: list[ScriptedGenerator] = []

    def __call__(self, settings):
        g = ScriptedGenerator(self.replies)
        self.made.append(g)
        return g

    @property
    def prompts(self) -> list[str]:
        return [p for g in self.made for p in g.prompts]


def no_model(settings):
    raise AssertionError("the model must not be called")


def project(tmp: Path, fluid: str = GRADES, config: str | None = None) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    f = tmp / "grades.fluid"
    f.write_text(fluid, encoding="utf-8")
    if config is not None:
        (tmp / "fluid.config").write_text(config, encoding="utf-8")
        shutil.copy(Path(__file__).resolve().parents[1] / "fluidc" / "data" / "fluid.config.schema.json", tmp)
    return f


def run_build(path: Path, factory, cli: CliSettings | None = None, **kw):
    opts = BuildOptions(path, cli=cli or CliSettings(), generator_factory=factory, **kw)
    return build(opts)


def codes(res) -> list[str]:
    return [m.code for m in res.report.messages]
