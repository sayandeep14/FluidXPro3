# Compilers

```
main.fluid ──fluidc──▶ main.fx3 ──fx3c──▶ checked IR ──▶ Python (C++ and others later)
```

| Folder | What it does | Status |
|--------|--------------|--------|
| [`fx3c/`](fx3c) | `.fx3` → target language. Deterministic, with no LLM | **v0.2**: core language + standard library → Python, 102 tests |
| [`fluidc/`](fluidc) | `.fluid` → `.fx3` with Claude; every result verified by `fx3c` | **v0.1**: 30 tests |
| (later) `fluid` | master command: runs either stage, or both (`.fluid` → target in one go) | planned |

The two compilers are independent, so `.fx3` can be written or edited by hand and compiled with `fx3c` on its own.

```sh
pip install -e fx3c -e fluidc
# put your key in ../secrets.env (ANTHROPIC_API_KEY=...): fluidc and fx3c read it automatically
fluidc run fluidc/examples/grades.fluid --scripted fluidc/examples/grades.replies.json   # no API key needed
```
