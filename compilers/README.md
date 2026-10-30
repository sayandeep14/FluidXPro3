# Compilers

```
main.fluid ──fluidc──▶ main.fx3 ──fx3c──▶ checked IR ──▶ Python (C++ and others later)
```

| Folder | What it does | Status |
|--------|--------------|--------|
| [`fx3c/`](fx3c) | `.fx3` → target language. Deterministic, with no LLM | **v0.3**: core language + standard library → Python, sandbox, 112 tests |
| [`fluidc/`](fluidc) | `.fluid` → `.fx3` with Claude; every result verified by `fx3c` | **v0.3**: lock format 2, injection guard, sandboxed verification, 49 tests |
| [`fluid/`](fluid) | master command: `new`, `build`, `run`, `test`, `check`, `review`, `doctor` (`.fluid` → target in one go) | **v0.3**: 7 tests |

The two compilers are independent, so `.fx3` can be written or edited by hand and compiled with `fx3c` on its own.

```sh
pip install -e ..            # the repository root: one package with all three commands
# put your key in ../secrets.env (ANTHROPIC_API_KEY=...): fluidc and fx3c read it automatically
fluidc run fluidc/examples/grades.fluid --scripted fluidc/examples/grades.replies.json   # no API key needed
fluid doctor                  # Python, packages, credentials (never shown), sandbox
```
