"""What fluidc sends to the model: a stable system prompt (cached) and a per-build request."""

from __future__ import annotations

import json
from pathlib import Path

def _stdlib_reference() -> str:
    """The standard-library section, generated from fx3c's own registry (so it can never drift)."""
    from .verify import fx3c  # noqa: F401  (puts fx3c on the path)
    from fx3c.stdlib import MODULES
    out = ["# Standard library (fx3c)", "",
           "Import a module at the top of the file to use it: `import Time;`. Importing a module also brings in "
           "the types listed under it. `value.method(...)` means a method on a value of that type.", ""]
    for mod, types in MODULES.items():
        for t in types:
            title = f"## {t.name}" if t.name == mod else f"### {t.name} (comes with `import {mod};`)"
            out += [title + f": {t.doc}"]
            for c, ty in t.constants.items():
                out.append(f"- `{t.name}.{c}`: {ty}")
            for f, ty in t.fields.items():
                out.append(f"- `value.{f}`: {ty} (read-only field)")
            for m in t.members:
                params, _, ret = m.sig.partition("->")
                ret = ret.strip()
                head = f"{t.name}.{m.name}" if m.static else f"value.{m.name}"
                line = f"- `{head}{params.strip()}`" + (f" -> {ret}" if ret and ret != "void" else "")
                if m.fails:
                    line += f"   @fails({', '.join(m.fails)})"
                if m.doc:
                    line += f"   ({m.doc})"
                out.append(line)
            out.append("")
    out.append("`handler` means a function[<Request>|<Response>]: pass the function's name, without ( ).")
    return "\n".join(out)


REFERENCE = (Path(__file__).with_name("data") / "fx3_reference.md").read_text(encoding="utf-8") + \
    "\n\n" + _stdlib_reference()

IMPACTS = ["data", "money", "security", "external", "irreversible", "other"]

OUTPUT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["blocks", "assumptions", "questions", "guarantees", "custom_keys", "unsupported"],
    "properties": {
        "blocks": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False, "required": ["id", "fx3"],
                "properties": {"id": {"type": "string"}, "fx3": {"type": "string"}},
            },
        },
        "assumptions": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False, "required": ["block", "text"],
                "properties": {"block": {"type": "string"}, "text": {"type": "string"}},
            },
        },
        "questions": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["block", "question", "options", "default", "impact"],
                "properties": {
                    "block": {"type": "string"},
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "default": {"type": "string"},
                    "impact": {"type": "string", "enum": IMPACTS},
                },
            },
        },
        "guarantees": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False, "required": ["block", "line", "status", "how"],
                "properties": {
                    "block": {"type": "string"},
                    "line": {"type": "string"},
                    "status": {"type": "string", "enum": ["enforced", "unverifiable"]},
                    "how": {"type": "string"},
                },
            },
        },
        "custom_keys": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["key", "interpretation", "status", "check"],
                "properties": {
                    "key": {"type": "string"},
                    "interpretation": {"type": "string"},
                    "status": {"type": "string", "enum": ["applied", "unclear", "conflict"]},
                    "check": {"type": "string"},
                },
            },
        },
        "unsupported": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False, "required": ["block", "reason"],
                "properties": {"block": {"type": "string"}, "reason": {"type": "string"}},
            },
        },
    },
}

SYSTEM_PROMPT = """You are the code generator inside fluidc, the first stage of the FluidXPro3 compiler.

Developers write .fluid files: plain English organised into blocks, each starting with a keyword. You turn each requested block into canonical fx3 code. Another compiler, fx3c, then checks your code strictly and compiles it to the target language. If fx3c rejects your code, you will get its exact error messages and must fix the code. Your code is only accepted when fx3c accepts it and every example and test passes.

## How .fluid blocks map to fx3

- `thing Name`: a class. Fields are `name: description`; choose fx3 types from the words ("money" -> int in the smallest unit unless the file says otherwise, "yes or no" -> bool, "optional" -> T?, "defaults to X" -> a field default, "one of a, b, c" -> string plus a validating function, because fx3c has no enums). Make fields @public and add @derive(eq, print) unless told otherwise.
- `to name (inputs) -> output`: a function with a full signature. Choose parameter types from the file. Each `example` sub-block becomes an @example(input -> output) when inputs and outputs are plain literals, otherwise a @test function that sets up the data and uses ensure(...).
- `check name (inputs)`: a function that returns the outcome of the first matching row (a string, unless the outcomes are clearly another type).
- `flow name (inputs)`: a function that runs the numbered steps in order and honours the failure behaviour written in the block.
- `settings`: top-level @const variables.
- `test name`: a @test function: `function[<>|<>] @test name = Function() { ... }` that builds the given data, performs the steps and checks the expectations with ensure(...).
- `rules` / `never`: put the checks in the code they constrain (ensure, @range, validation in constructors and functions) and report EVERY line in "guarantees".
- `on start`: the @entry main function (there can be only one @entry in the whole program).
- `web`: one handler function per endpoint (function[<Request>|<Response>]) plus a function that creates the Server and registers the routes; the @entry main starts it with keepRunning(). Check headers and bodies the block requires.
- `page`: a handler that returns Response.html(...) with a complete, self-contained HTML page (inline CSS / JS), registered on the server.
- `ai`: a function that calls Ai.ask or Ai.classify (they fail with AiError).
- `secrets`: read them with Env.require("NAME") (fails with ValueError) or Env.get. Never print or log them.
- `uses` names the modules to use: "sqlite database in X" -> Db.open(X), "email" -> Email.send, "the terminal" -> print / input / Env.args().
- `command "example input"`: handle it in @entry main by reading Env.args() (parse the words as the example shows).
- `when ...`: call the reaction from the code that makes the change happen.
- `app`, `target`, `ask`, `with`, `words`, `uses` are context only. You will not be asked to generate them.
- Anything that needs a feature that is in neither the fx3 reference nor the standard library below (today: scheduling such as `every day at 9pm`, async): DON'T fake it. Report the block in "unsupported" with a one-line reason, and return its code as an empty string.

## Rules for the code you write

- Write only what the fx3 reference allows, using canonical spellings.
- Each block's code is a group of top-level declarations and statements. Blocks share one file, in the order of the .fluid file, so use names consistently. You will be shown the code of the other blocks.
- Put the `import` lines a block needs at the start of THAT block's code. fluidc moves every import to the top of the file for you, so never move imports into another block.
- Turn block names into code names using the naming styles in the settings (for example "monthly summary" -> monthly_summary for snake_case functions). Keep names stable between builds.
- Follow every setting and every custom key and instruction. They are binding. Where fx3 can't express a setting, apply it as far as possible and say so in "custom_keys".
- `words` definitions apply everywhere in the file.
- No comments are needed beyond what codegen.comments asks for. Never invent requirements the file doesn't state.

## When something is unclear

If a block can reasonably be read more than one way, still write the code using the most sensible reading, and add a "questions" entry:
- "options": the readings
- "default": the reading you used
- "impact": one of data | money | security | external | irreversible | other

fluidc decides deterministically whether to ask the developer. Record every smaller choice you made in "assumptions". Do not ask about things the file already settles.

## Output

Reply with the JSON object only:
- "blocks": [{"id", "fx3"}] for the requested blocks (or, when fixing errors, only the blocks you changed). Use the exact block ids you were given.
- "assumptions", "questions": as above.
- "guarantees": one entry per line of every rules/never block in the request. "status" is "enforced" if fx3c will check it (say how), otherwise "unverifiable".
- "custom_keys": one entry per custom key and per instruction:
  - "interpretation": what it means for this code
  - "status": applied | unclear | conflict
  - "check": how it is verified, or "none"
- "unsupported": blocks that need features fx3c doesn't have yet.

# fx3 reference

""" + REFERENCE


def build_request(*, doc, target: str, project_settings: dict, block_settings: dict[str, dict],
                  generate: list[str], existing: dict[str, str], answers: dict[str, dict[str, str]],
                  custom: dict, instructions: list[str], tags: dict[str, dict[str, str]]) -> str:
    """The user message for one build. Everything that changes between builds goes here (after the cache)."""
    index = "\n".join(
        f'- "{b.id}" (lines {b.line_no}-{b.end_line}){"  [GENERATE]" if b.id in generate else ""}'
        for b in doc.blocks)
    parts = [
        f"Target language: {target}",
        "",
        "## Project settings (JSON)",
        json.dumps(project_settings, indent=2, sort_keys=True),
    ]
    if custom:
        parts += ["", "## Custom keys (binding requirements, from fluid.config)",
                  json.dumps(custom, indent=2, sort_keys=True, default=str)]
    if instructions:
        parts += ["", "## Instructions (binding)"] + [f"- {i}" for i in instructions]
    parts += ["", f"## The .fluid file ({doc.file})", "```fluid", doc.source.rstrip(), "```",
              "", "## Blocks", index]
    diffs = {bid: s for bid, s in block_settings.items() if bid in generate and s}
    if diffs:
        parts += ["", "## Settings that differ for particular blocks (JSON)",
                  json.dumps(diffs, indent=2, sort_keys=True, default=str)]
    if tags:
        parts += ["", "## Line tags on rules/never lines", json.dumps(tags, indent=2, sort_keys=True)]
    if answers:
        parts += ["", "## Answers the developer already gave (use them)",
                  json.dumps(answers, indent=2, sort_keys=True)]
    if existing:
        parts += ["", "## Current fx3 of the other blocks (read-only context, do not return these)"]
        for bid, code in existing.items():
            parts += [f'### "{bid}"', "```fx3", code.rstrip() or "// (no code)", "```"]
    parts += ["", "## Your task",
              "Generate fx3 for every block marked [GENERATE]: " + ", ".join(f'"{g}"' for g in generate) + "."]
    return "\n".join(parts)


def fix_request(errors: list[str], failing_tests: str | None) -> str:
    parts = ["fx3c rejected the code. Fix these problems. In \"blocks\", return only the blocks you change "
             "(the others keep their code). Keep the other fields up to date.", ""]
    parts += [f"- {e}" for e in errors]
    if failing_tests:
        parts += ["", "Test output:", "```", failing_tests.strip(), "```"]
    return "\n".join(parts)


def answers_request(answers: dict[str, dict[str, str]]) -> str:
    return ("The developer answered these questions differently from your defaults. Regenerate the affected "
            "blocks with these answers (return only the blocks you change):\n"
            + json.dumps(answers, indent=2, sort_keys=True))
