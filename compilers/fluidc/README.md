# fluidc: the `.fluid` → `.fx3` compiler

`fluidc` turns plain-English `.fluid` files (see [`../../FLUID_SPEC.md`](../../FLUID_SPEC.md)) into canonical `.fx3` using Claude. Every result is then **verified by the real fx3c compiler**: its examples and tests must pass. `fx3c` then compiles the `.fx3` to the target language.

```
.fluid ──reader──▶ blocks ──settings──▶ for each block: reuse (fluid.lock) │ keep hand edit │ use `exact` │ generate
                                                                                                       │
              fluid.lock + marked .fx3 ◀── guarantees / custom keys / questions ◀── fx3c check + tests ◀┘
                                                                         ▲            │ errors
                                                                         └── Claude ◀─┘ (fix-up turns)
```

## Install and use

```sh
pip install -e .              # from the repository root: fx3c, fluidc and the `fluid` command
# put ANTHROPIC_API_KEY=... in secrets.env (project root), or export it, or: ant auth login

fluidc build  app.fluid               # writes build/fx3/app.fx3 and fluid.lock
fluidc run    app.fluid               # build, then compile and run with fx3c
fluidc review app.fluid               # every assumption, answer, guarantee and custom-key reading
fluidc blocks app.fluid               # how the file was read (no model call)
fluidc explain-settings app.fluid "to monthly summary"   # final settings + where each came from
fluidc pin    app.fluid "settings"    # copy a hand edit of the .fx3 into the .fluid file as `exact`
fluidc unpin  app.fluid "settings"    # drop a hand edit; restore the generated code
fluidc lock migrate app.fluid         # upgrade fluid.lock to the current format (no model calls)
```

Safety (milestone M1):
- **Sandbox.** The generated tests run in a sandbox (`verification.sandbox`, default `auto`): no network, no secrets, no files outside their own folder.
- **`--trust`.** It turns the sandbox off. `fluidc run` then refuses code that wasn't verified in a sandbox unless it gets `--trust` too.
- **Deterministic checks** (`guard.py`):
  - Generated code may call Http / Server / Email / Ai only in blocks whose text asks for it.
  - It may use only addresses that the source names.
  - It never contains secrets.
  - Secrets in the `.fluid` file are never sent to the model.
- **`fluid.lock` records the toolchain.** That means the model and prompt version of every block. A newer prompt keeps the build as it is and warns you; `--refresh` regenerates.

Build options:
- `--ask soft|mid|hard`
- `--rules` / `--never off|warn|error`
- `--set key=value` (any `fluid.config` key)
- `--offline` (use `fluid.lock` only, never the model)
- `--yes` (accept default answers)
- `--no-input` (CI: questions become errors)
- `--allow-unsupported`
- `-v` (also show fx3c's report)

## How it behaves

- **The reader is deterministic.** It handles block keywords and their aliases at column 0, the sub-keywords `example` / `exact` / `ask` / `with` (only when alone on their line), `[error]` / `[warn]` / `[off]` tags, and `//` comments that leave URLs alone.
- **Settings are combined per block** in the FLUID_SPEC §10 order: line tag > block `with`/`ask` > CLI > file top > config overrides > config > schema defaults. `fluid.config` is validated against its schema. A custom key that looks like a typo of a known key (`comment` vs `comments`) always stops the build.
- **Only changed blocks go to the model.** A block is reused from `fluid.lock` while its text, its settings and its answers are unchanged. The request contains:
  - the whole file
  - the settings
  - the custom keys and instructions
  - the current code of every other block, as context
- **Verify, then fix.** The generated `.fx3` is compiled by fx3c, and its `@example` / `@test` cases are run. Errors go back to the model as a fix-up turn in the same conversation, up to `llm.maxRetries` times. Nothing is written until everything passes.
- **Hand edits are kept.** Every block sits between `// @from ... fx3=<hash>` and `// @end` markers. Edited code is kept (FLUID_SPEC §5). If both the `.fluid` block and its `.fx3` changed, the build always asks which one wins.
- **Questions:** the model reports every ambiguity with a default and an impact. fluidc decides deterministically:
  - `soft` records every one as an assumption.
  - `mid` asks only about data, money, security, external effects or anything irreversible.
  - `hard` asks about everything.
  - In CI (`--no-input`) a question becomes an error. An answer that differs from the default regenerates the block.
- **Guarantees:** every `rules` / `never` line is reported as either enforced (and how) or unverifiable. Unverifiable lines follow the line tag, else `verification.rules` / `verification.never`.
- **Custom keys and instructions:** the model's reading of each one is stored in `fluid.lock` and shown by `fluidc review`. A conflict is an error, an unclear key is a warning, and a key that can't be checked follows `verification.customKeys`.
- **Unsupported blocks:** blocks needing features fx3c doesn't have yet (web, files, email, schedules, `ai`, …) are reported. They are never faked.

## The model call

`claude-opus-5-5` is the default, set by `llm.model` and `llm.effort` in `fluid.config`.

| Part of the request | What it does |
|---|---|
| Streaming with `max_tokens` 64000 | long outputs don't hit request timeouts |
| Structured outputs (`output_config.format`, JSON schema) | every reply has exactly the shape fluidc parses |
| Cached system prompt | the instructions plus `data/fx3_reference.md`, about 4.3K tokens, are reused across fix-up turns and builds |
| Append-only conversation | earlier turns are never edited, so the cached prefix stays valid |
| Server-side refusal fallback (`fallbacks: "default"`) | a declined request continues on a fallback model; turn it off in `fluidc/llm.py` if you don't want it |

Two things are deliberately left out:
- **No sampling settings.** Current models reject `temperature`. Builds are made repeatable by `fluid.lock` instead.
- **No thinking configuration.** Adaptive thinking is the model's default.

`data/fx3_reference.md` describes only what fx3c 0.1 compiles. `tests/reference_examples.fx3` holds every example from it, and a test makes sure fx3c accepts them, so the model is never taught code the compiler rejects.

## Try it without an API key

```sh
fluidc run examples/grades.fluid --scripted examples/grades.replies.json
```

`--scripted` replays a prepared model reply. It's how the tests work, and it shows the whole pipeline running.

## Limits (v0.1)

- **What can compile:** programs within fx3c's features, which now include the standard library (web server, HTTP, SQLite, files, JSON, time, email, Ai). The model's reference for it is generated from fx3c's registry. In [`../../main.fluid`](../../main.fluid), only `every day at 9pm` (scheduling) is still unsupported.
- **Comments:** fx3c doesn't carry comments into the Python yet, so `codegen.comments` only affects the `.fx3`.
- **One `.fluid` file per folder:** each folder has a single `fluid.lock`, so keep one `.fluid` file per folder for now.
- **`fluidc describe`** (rewrite the prose to match a hand edit) is not implemented yet.

## Tests

```sh
pip install -e ".[test]" && pytest
```

30 tests, none of which call the API. They cover:
- the reader and the order settings are applied in
- reusing blocks from the lock, and offline builds
- compiler errors and failing tests being fed back to the model, and giving up after the retry limit
- hand edits, conflicts, `exact`, `pin` / `unpin`
- the ask levels, unsupported blocks and guarantee tags
- running the generated program
- the exact shape of the Claude request (checked against a fake SDK client)
