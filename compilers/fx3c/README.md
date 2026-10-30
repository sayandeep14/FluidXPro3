# fx3c: the `.fx3` compiler

`fx3c` compiles strict `.fx3` code (the language in [`../../Syntax/`](../../Syntax/00_overview.fx3)) into a target language. Right now that's **Python**; the backend interface is designed so C++ and other languages can be added without touching the front end.

```
.fx3 ──lexer──▶ tokens ──parser──▶ AST ──checker──▶ checked IR ──backend──▶ Python (+ fx3rt.py)
                                         ▲
                       aliases, English conditions, every deterministic check
```

`fx3c` never calls an LLM. Anything that would need one (an `Action:` body, an unknown method name, an English condition outside the mini-grammar) is reported as an error that says so. The `.fluid` compiler (`../fluidc`) will add the LLM step.

## Install and use

```sh
cd compilers/fx3c
pip install -e .            # installs the `fx3c` command (Python 3.10+)

fx3c check  prog.fx3        # check only: fast, for editors
fx3c build  prog.fx3        # writes build/python/prog.py + fx3rt.py
fx3c run    prog.fx3        # build and run
fx3c test   prog.fx3        # run @test functions and @example cases
fx3c show   prog.fx3        # print the generated Python
```

Options: `--target python`, `-o DIR`, `-q` (hide warnings), `--no-overflow-checks`.

Without installing, use `python3 -m fx3c ...` from this folder.

## Example

```fx3
vector<int> scores = [90, 85, 70, 40];
scores.modifyAt(index = 2..4, newVal = currVal + 5);
print(scores.filter("currVal is at least 80").map(currVal * 2));
```

becomes

```python
scores = [90, 85, 70, 40]
for _i1 in range(2, 4):
    _v2 = scores[_i1]
    scores[_i1] = _rt.i64(_v2 + 5)
_rt.fx_print([_rt.i64(currVal * 2) for currVal in [currVal for currVal in scores if currVal >= 80]])
```

The generated code imports `fx3rt.py`, a small runtime that implements the fx3 rules Python doesn't have:
- 64-bit overflow is an error
- `/` always gives a float, and `~/` is floor division
- none-safe helpers
- copy-on-assign for collections
- panics with clear messages (exit code 101)
- printing `true` / `false` / `none`

## What version 0.1 supports

| Area | Supported |
|---|---|
| Lexical | `//` `#` `/* */` comments, `;` rules, every literal form except durations and sizes, f-strings with format specs, raw and triple-quoted strings, backtick names |
| Variables | `type @annotations name = value;`, all type aliases (`i32`, `Vec`, `HashMap`, ...), `auto` / `number`, `@const`, `@typesafe`, `@range`, loose re-typing, declare-now-assign-later with definite-assignment checks |
| Types | 64-bit `int`, sized ints, `f32`/`float`, widening only, `as` / `as?` / `as!`, `maybe<T>` / `T?` with narrowing, `if (auto v = x)`, unpacking `[a, b]` with `...rest` |
| Operators | the full table in `operators.fx3`: chained comparisons, `??`, `?.`, `**`, `~/`, ranges with `step`, `?:` and `if`-expressions |
| Collections | `list`, `vector`, `array`, `set`, `map`, `tuple`; brackets checked; `@size` `@init` `@default`; ~90 bank methods with aliases; implicit `currVal` / `index` / `key` / `prevVal` / `nextVal` / `acc`; English conditions |
| Control flow | `if` / `else if`, every `repeat` short form, `@atleastonce`, C-style `repeat(@none; ...)` and `for`, `stop` / `skip`, inline `switch(@none; x)` with `case(...).act(...)` (also as a value) |
| Functions | signatures or inferred ones, `=>` bodies, defaults, named and variadic arguments, several return values, `@ref` collections, `@memo`, `@entry`, `@test`, `@example` |
| Classes | fields (private by default), generated and named (`@style` / `@using`) constructors, methods, `@static`, `@property`, single inheritance with `@open` / `@abstract` / `@override`, `@derive(eq, hash, order, print)` |
| Errors | `@fails`, `fail`, `try` / `catch` / `finally`, `??` on failing calls, postfix `?`, `result<T>`, custom `@error` classes, `ensure` / `panic`, `defer` |
| Standard library | `import` / `from ... import`. **Math, Random, Time, File, Path, Csv, Json, Env, Log, Http, Server, Db (SQLite), Ai (Claude), Email**, plus `duration` literals (`500ms`, `2s`) and `sleep`. API: [`Syntax/stdlib.fx3`](../../Syntax/stdlib.fx3) |

### Not yet (reported as `FX1001` "not supported yet")

These are listed so nobody is surprised:
- loopers / checkers / pipelines (the `repeat(a; ...)` and `switch(b; ...)` forms)
- structures (`structures.fx3`), enums, interfaces, generics, type aliases
- `async` / concurrency, scheduling, the Process module, regex, your own modules (importing other .fx3 files)
- `@unsafe` and raw pointers
- labelled `stop`
- annotations that are accepted but not enforced yet (they give a warning, `FX0505`)

## Adding a target language

1. Create `fx3c/backends/<lang>/` with a class that subclasses `Backend` (`backends/base.py`) and implements `emit(program, checker, stem, diags) -> Output`. Also implement the standard library in that language, using the names in `fx3c/stdlib.py` (see `backends/python/fx3std.py`).
2. Register it in `fx3c/backends/__init__.py` (`BACKENDS`).
3. Use the Python backend as the reference. The checked tree gives every expression's type (`expr.ty`), every name's symbol (`name.sym`), and every call's resolved target (`call.kind`, `call.target`, `call.bound`), with canonical method names. A backend never has to resolve anything itself.

## Tests

```sh
pip install -e ".[test]"
pytest
```

The tests have four parts:
- **`tests/programs/*.fx3`** are full programs. Each must print exactly what its `.out` file contains.
- **`test_diagnostics.py`** checks that every deterministic check fires with its error code, and that valid code compiles.
- **`test_runtime.py`** checks that panics stop the program with a clear message and exit code 101.
- **`test_stdlib.py`** checks that every registry function exists in the Python runtime with the same parameter names, and covers the import rules, a server handler that fails, and `Ai` without a key.
