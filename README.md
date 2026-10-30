# FluidXPro3

Write programs in plain English and get checked, tested code.

```
main.fluid ──fluidc (Claude)──▶ main.fx3 ──fx3c──▶ Python   (C++ and more later)
 plain English                 strict, readable,          deterministic,
 organised in blocks           editable by hand           no LLM
```

```fluid
app Grade Book
    Keep students' marks and print a report card for the class.

target python

thing Student
    name: text
    marks: a list of whole numbers from 0 to 100

to average of (student) -> a number
    The mean of the marks, rounded to 1 decimal place. No marks means 0.

    example
        Student "Ria" with marks 80, 90 -> 85.0
```

Here's what happens to a file like this:
- **fluidc** turns each block into **fx3**, a small, strict language with fixed shapes and flexible spellings.
- **fx3c** type-checks it, runs every `example` and `test`, and then generates Python.
- **If anything fails,** the compiler's exact messages go back to the model until the code passes.
- **Every decision is recorded** in `fluid.lock`, so the next build is reproducible and needs no API calls.

## Install

Python 3.10 or newer:

```sh
git clone <this repo> && cd FluidXPro3
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
fluid doctor
```

## Use

```sh
fluid new my-app && cd my-app
# put your key in secrets.env: ANTHROPIC_API_KEY=...
fluid run main.fluid        # .fluid -> .fx3 -> Python, then run it
fluid test main.fluid       # the examples and tests, in the sandbox
fluid review main.fluid     # what the model assumed, answered and decided
fluid check main.fluid      # CI: fails if fluid.lock is out of date; writes nothing
```

`.fx3` files work with the same commands (`fluid run prog.fx3`), and they never touch a model.

## Safe by default

- **Tests are sandboxed.** Model-written tests run with no network, no secrets and no files outside their own folder. They use the OS sandbox where there is one (macOS, Linux with bubblewrap).
- **The model never runs anything directly.** It writes fx3, and fx3c decides what Python that becomes.
- **The `.fluid` file is data, not instructions.** Deterministic checks reject:
  - network calls in blocks that don't ask for them
  - addresses the source never mentions
  - secrets in code

  Secrets in a `.fluid` file are never sent to the model.
- **Builds are reproducible.** `fluid.lock` records the model and prompt version of every block.

See [SECURITY.md](SECURITY.md) to report a problem.

## Learn more

| | |
|---|---|
| [`FLUID_SPEC.md`](FLUID_SPEC.md) | The `.fluid` layer: keywords, ask levels, hand edits, `fluid.config` |
| [`Syntax/`](Syntax) | The fx3 language, one area per file |
| [`compilers/`](compilers) | `fx3c`, `fluidc` and `fluid` |
| [`ROADMAP.md`](ROADMAP.md) | Where the project is going, and progress |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Set-up, tests, rules for changes |
| [`VERSIONING.md`](VERSIONING.md) | Versions and the deprecation policy |

Licensed under the [Apache License 2.0](LICENSE).
