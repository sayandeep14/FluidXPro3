# Contributing to FluidXPro3

Thanks for helping! FluidXPro3 is a language with two compilers:

```
main.fluid ──fluidc (Claude)──▶ main.fx3 ──fx3c──▶ Python (C++ and others later)
```

To see where the project is heading and what's free to pick up, read [`ROADMAP.md`](ROADMAP.md).

## Set up

```sh
git clone <repo> && cd FluidXPro3
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # fx3c, fluidc, fluid + pytest, ruff, pyright
fluid doctor                      # checks Python, packages and credentials
```

Python 3.10 or newer is needed. You don't need an API key for the tests: fluidc's tests use a scripted model.

## Run the checks

```sh
pytest                           # all three test suites
ruff check .
pyright compilers
```

CI runs these on Linux, macOS and Windows with Python 3.10–3.14. A PR needs them all green.

To try a real model build, put your key in `secrets.env` (`ANTHROPIC_API_KEY=...`) and build the files in `playground/`. This costs money and is never part of the automated tests.

## Where things live

| Path | What |
|---|---|
| `Syntax/*.fx3` | The language spec, one area per file. Rules marked *locked* follow [`VERSIONING.md`](VERSIONING.md) |
| `FLUID_SPEC.md` | The `.fluid` layer: keywords, ask levels, hand edits, config |
| `compilers/fx3c` | `.fx3` → Python. Deterministic; no LLM |
| `compilers/fluidc` | `.fluid` → `.fx3` with Claude; every result is checked by fx3c |
| `compilers/fluid` | The master `fluid` command |

## Rules for changes

- **Every behaviour change comes with a test.**
  - fx3c: a program in `tests/programs/` with its expected output, or a case in `test_diagnostics.py`
  - fluidc: a scripted-model case in `test_build.py`
- **Every new error code has a message that says how to fix it** (`fix=...`).
- **Standard-library changes** go in three places: `stdlib.py` (the registry), `fx3std.py` (the runtime) and `Syntax/stdlib.fx3`. The tests check that the registry and runtime agree.
- **Language changes** (syntax, locked rules) start as an issue using the "language change" template.
- **Prompt or reference changes in fluidc** change what the model generates. Say in the PR which real builds you tried, and what they cost.
- **Never commit secrets.** `secrets.env` is git-ignored. Don't paste keys into issues or test files.
- **Code style.** Match the code around you: plain names, short functions, comments only where the *why* isn't obvious.

## Commit messages

Write a short summary line (under 72 characters), then a blank line, then the why. Put the roadmap ID in the summary when there is one, e.g. `E-01: run verification in a sandbox`.

## Releasing (maintainers)

1. Update the version in `pyproject.toml` and the three `__init__.py` files (`fluid doctor` warns if they disagree).
2. Move the `Unreleased` section of `CHANGELOG.md` under the new version.
3. Tag `vX.Y.Z` and push. CI builds the package.

By contributing, you agree that your contributions are licensed under the Apache License 2.0 (see [`LICENSE`](LICENSE)).
