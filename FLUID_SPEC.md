# The .fluid layer

FluidXPro3 has two layers:

```
main.fluid ──fluid build (LLM)──▶ main.fx3 ──fx3 build (deterministic + LLM only on misses)──▶ IR ──▶ C++ / Python
```

| Layer   | Who writes it                   | Style                            | Spec                     |
|---------|---------------------------------|----------------------------------|--------------------------|
| `.fluid`| the developer                   | plain English, indentation-based | this file                |
| `.fx3`  | generated, and editable by hand | strict, braces, canonical        | `Syntax/00_overview.fx3` |

## 1. File rules

- A **keyword** starts a block. It is written in lowercase at the start of a line.
- Everything indented under a keyword belongs to that block. Indentation (4 spaces) is the only structure.
- Everything else is prose. `//` starts a comment.
- `->` means "gives back", or "expect" inside examples and tests.
- Inside prose, a quoted term (`"this month"`) refers to a definition in `words`. A term in backticks (`` `how worried should I be` ``) refers to another block by name.

## 2. Keywords

### Program setup
| Keyword    | Purpose | Becomes in fx3 |
|------------|---------|----------------|
| `app`      | name and a one-paragraph description of the program | project metadata, plus context for every LLM call |
| `target`   | `python` or `cpp`. **Required** here or in `fluid.config`; if both give it, they must match | `fx3.toml [build] target` |
| `ask`      | how often the builder stops to ask (§4). Applies to the whole file at top level, or to one block when written inside it | builder setting |
| `uses`     | libraries, databases and services | `import` lines + `fx3.toml [dependencies]` |
| `secrets`  | names of environment variables holding secrets | `Env.get(...)`, never logged or embedded |
| `settings` | named constants | `@const` values |
| `words`    | glossary: what each term means across the whole file | context for every LLM call in the file |

### Data and logic
| Keyword   | Purpose | Becomes in fx3 |
|-----------|---------|----------------|
| `thing`   | a kind of data, with fields described in words | `class` (with `@derive`, `@range`, ...) |
| `to`      | a function: `to name (inputs) -> output` + intent | `function` |
| `check`   | a decision written as a table: `condition -> outcome` | `checker` + `switch` |
| `flow`    | numbered steps, with failure behaviour | `pipeline` |
| `ai`      | a function answered by a model **at runtime** | `function` that calls `Ai.ask` (stdlib) |
| `example` | input → output inside a `to` / `check` / `flow` block | `@example(...)` (a test the generated code must pass) |
| `exact`   | a fenced fx3 code block; the LLM won't change it | used as written |

### Behaviour
| Keyword   | Purpose | Becomes in fx3 |
|-----------|---------|----------------|
| `web`     | HTTP endpoints, one per line: `METHOD path -> action` | `Server` setup |
| `page`    | a screen described in words | generated HTML/JS served by `web` |
| `command` | a terminal command, given as an example of its input | CLI parser + test |
| `on`      | lifecycle events: `on start`, `on stop`, `on error` | entry/exit hooks |
| `every`   | schedules: `every day at 9pm`, `every 5 minutes` | timer task |
| `when`    | reactions to data changes: `when a big expense is added` | hooks in the generated save/update code |

### Guarantees
| Keyword | Purpose | Becomes in fx3 |
|---------|---------|----------------|
| `rules` | hard limits that must always hold | `@range`, `@const`, `ensure(...)`, generated tests, timing checks. A rule the compiler can't verify is a **warning** by default (§9) |
| `never` | things the program must not do or include | checks on the generated code (for example, no network calls except the allowed ones). Unverifiable lines are warnings by default (§9) |
| `test`  | a scenario in words: given / do / `->` expect | `@test` function |

### Block settings
| Keyword | Purpose |
|---------|---------|
| `with`  | overrides any `fluid.config` setting for one block (or for the whole file, at top level): `with safety paranoid, comments detailed` (§8) |

### Aliases

Each keyword has aliases. The builder turns an alias into its canonical keyword, and `fluid fmt --canonical` rewrites the file that way.

| Canonical | Aliases |
|-----------|---------|
| `app`      | program, project |
| `target`   | language, build for |
| `ask`      | questions |
| `uses`     | use, needs, requires, import |
| `secrets`  | secret, env |
| `settings` | constants, defaults |
| `words`    | glossary, terms, definitions |
| `thing`    | data, type, model, record, entity |
| `to`       | function, fn, def, how to |
| `check`    | decide, decision, table, classify |
| `flow`     | steps, process, workflow, pipeline |
| `ai`       | smart, llm |
| `example`  | e.g., sample |
| `exact`    | code, fx3, verbatim |
| `web`      | api, http, server, endpoints |
| `page`     | screen, view, ui |
| `command`  | cmd, cli |
| `on`       | upon |
| `every`    | schedule |
| `when`     | whenever |
| `rules`    | rule, always, must, constraints |
| `never`    | must not, forbidden, do not |
| `test`     | scenario |
| `with`     | using |

How keywords are recognised (so that prose is never mistaken for one):
- **Block keywords** (`app`, `to`, `rules`, ...) are only recognised at **column 0**. Prose is always indented, so a prose line that begins "Always ..." is never read as `rules`.
- **Sub-keywords** (`example`, `exact`, `ask`, `with`) are only recognised one indent level inside a block, and only when the line holds **nothing but** the keyword and its arguments. A line like "For example, if the month is empty..." stays prose.
- Each alias resolves to exactly one keyword. A user block name that matches an alias gives a warning.

## 3. How precise to be

Each block can be written at whichever of these levels it needs. Each step down takes guessing away from the LLM:

1. **intent only**: "Find the cheapest flight."
2. **+ inputs / outputs**: `to cheapest flight (from, to, date) -> flight`
3. **+ `example`**: these become tests the generated code must pass
4. **+ `rules` / `words`**: hard limits and shared definitions
5. **+ `exact`**: write the fx3 yourself

## 4. Ask levels: `--ask=soft | mid | hard`

When an intent can be read more than one way, the builder either **asks** or **assumes**. Every assumption it makes is recorded in `fluid.lock` and listed by `fluid review`.

| Level  | Asks about | Everything else |
|--------|------------|-----------------|
| `soft` | nothing | assumes a sensible default, records it, and prints "N assumptions made" |
| `mid` (default) | choices that affect **data, money, security, anything sent outside the program** (emails, API shapes) or that **can't be undone** | assumes and records |
| `hard` | every ambiguity | nothing is assumed |

- Priority: `ask` inside a block > `--ask` on the command line > `ask` at the top of the file > `fluid.config` > `mid` (full order in §10).
- In non-interactive runs (CI), every question becomes a **build error** that names the line, so nothing is assumed silently.
- An answered question is saved in `fluid.lock` and never asked again unless that block's text changes.

## 5. Editing the generated .fx3 by hand

Every generated block in `.fx3` carries a marker:

```
// @from main.fluid:58 "to monthly summary" fluid=3fa1… fx3=9c2e…
```

The two hashes are `fluid` (the source block) and `fx3` (the generated code, as generated).

| .fluid block | .fx3 block | What `fluid build` does |
|--------------|------------|-------------------------|
| unchanged    | unchanged  | nothing |
| changed      | unchanged  | regenerates the fx3 block |
| unchanged    | **edited by hand** | **keeps your edit** and never overwrites it |
| changed      | **edited by hand** | **conflict**: shows both and asks, even at `soft` |

Commands:
- `fluid pin <name>`: copies your hand-edited fx3 into the `.fluid` file as an `exact` block, so the source of truth is visible in one place.
- `fluid describe <name>`: the LLM rewrites the `.fluid` intent to match your edited fx3. The edit stays, and the prose catches up.
- `fluid unpin <name>`: drops the hand edit and regenerates it from the intent.

## 6. Command line

| Command | Does |
|---------|------|
| `fluid build [--ask=soft\|mid\|hard]` | .fluid → .fx3 |
| `fluid build --rules=off\|warn\|error --never=off\|warn\|error` | how unverifiable guarantees are treated (§9) |
| `fluid build --set codegen.comments=detailed` | override any `fluid.config` key for one run |
| `fluid fmt [--canonical]` | format the file; `--canonical` replaces aliases with their canonical keywords |
| `fluid config check` | validate `fluid.config` against `fluid.config.schema.json` |
| `fluid review` | list the assumptions and LLM decisions to accept or reject |
| `fluid pin / unpin / describe <name>` | see §5 |
| `fx3 build` | .fx3 → IR → target (see `Syntax/compiler.fx3`) |
| `fluid run` | both builders, then run the program |

## 7. Lock files

- `fluid.lock`: .fluid → .fx3 decisions (assumptions, answers to questions, hashes)
- `fx3.lock`: .fx3 → target decisions (name resolution, Action bodies)

Commit both. The same sources plus the same locks always give the same program.

## 8. fluid.config (controls the generated code)

An optional **JSON with comments** (JSONC) file next to the `.fluid` files. Every key is optional. The full list of keys, allowed values and defaults is in `fluid.config.schema.json`, so editors can autocomplete and validate it. An example is in `fluid.config`.

- It is JSON with comments, as in VS Code's `settings.json`: `// line` and `/* block */` comments are allowed, and so are trailing commas. Everything else is standard JSON.
- `"$comment"` keys are still accepted and ignored, for tools that only read plain JSON.
- A **known** key with a wrong value is a **build error**, never ignored.
- An **unknown** key is allowed and read by the LLM as a requirement (§11).
- `fluid config check` validates the file on its own.

| Section | Controls |
|---------|----------|
| `project` | entry file and output folders |
| `target.language` | `python` / `cpp`. Only the section for this language is used |
| `target.python` | version, packaging, type checker, formatter / linter, docstring style, data models, web / CLI / test frameworks, speed-ups (§8.1) |
| `target.cpp` | standard, compiler, build system, package manager, file layout, memory style, error style, safety checks, libraries, optimisation, warnings, sanitizers, analysis, guidelines (§8.2) |
| `ask` | default ask level (§4) |
| `verification` | how unverifiable `rules` / `never` lines, failing examples, and blocks with no tests are treated (§9) |
| `codegen.modularity` | `single-file` · `low` · `medium` · `high`, plus `maxFunctionLines` |
| `codegen.safety` | `basic` · `standard` · `strict` · `paranoid` |
| `codegen.comments` | `none` · `minimal` · `normal` · `detailed` · `teaching`, plus `docstrings` |
| `codegen.typing` | `inferred` · `explicit` |
| `codegen.optimizeFor` | `readability` · `balanced` · `speed` · `size` |
| `codegen.errorHandling` | `fail-fast` · `recover` |
| `codegen.logging` / `async` | logging level, and when to generate async code |
| `codegen.naming` / `format` | naming style per kind of name, indent, line length |
| `codegen.dependencies` | `stdlib-only` · `allow-listed` (+ `allowed`) · `any` |
| `codegen.tests` | how many tests to generate, and a coverage target |
| `llm` | provider, model, effort, retries, confidence, offline, extra context files |
| `instructions` | free-text requirements, as a list of strings (§11) |
| `overrides` | any of `ask` / `verification` / `codegen` / `instructions` / `target.*` / custom keys, for particular blocks, keywords or files |

### 8.1 Python target (`target.python`)

| Key | Values (default first) |
|-----|------------------------|
| `version` | `"3.12"` |
| `packaging` | `pyproject-uv` · `pyproject-poetry` · `pyproject-pip` · `requirements-txt` |
| `typeChecker` / `typeCheckStrictness` | `pyright` · `mypy` · `none` / `standard` · `basic` · `strict` |
| `formatter` / `linter` | `ruff` · `black` · `none` / `ruff` · `pylint` · `flake8` · `none` |
| `docstringStyle` | `google` · `numpy` · `sphinx` |
| `dataModels` | `dataclasses` · `pydantic` · `attrs` · `plain-classes`. What a `thing` becomes |
| `asyncRuntime` | `asyncio` · `trio` · `anyio` |
| `webFramework` / `cli` / `testFramework` | `fastapi` · `flask` · `django` · `starlette` · `aiohttp` / `argparse` · `click` · `typer` / `pytest` · `unittest` |
| `fastPaths` | `none` · `numpy` · `cython` · `rust-extension`. How speed-critical code is made faster |

### 8.2 C++ target (`target.cpp`)

| Group | Key | Values (default first) |
|-------|-----|------------------------|
| Toolchain | `standard` | `c++20` · `c++17` · `c++23` · `c++26` |
| | `compiler` | `clang` · `gcc` · `msvc` · `any` |
| | `buildSystem` | `cmake` · `meson` · `bazel` · `make` · `xmake` |
| | `packageManager` | `vcpkg` · `conan` · `cpm` · `none` |
| | `output` / `linking` | `executable` · `static-library` · `shared-library` · `header-only` / `dynamic` · `static` |
| | `platforms` | any of `linux` `macos` `windows` `wasm` `embedded` |
| Layout | `fileLayout` | `header-source` (.hpp + .cpp) · `header-only` · `modules` (C++20 import) |
| | `headerExtension` / `includeGuards` / `namespace` | `.hpp` · `.h` · `.hh` / `pragma-once` · `ifndef` / any name |
| How fx3 maps to C++ | `memory` | `value-and-raii` (values + std containers, `unique_ptr` when needed) · `smart-pointers` (`shared_ptr` objects, closest to fx3) · `raw-allowed` (raw pointers in `@unsafe` code) |
| | `errorStyle` | `expected` (`std::expected<T, E>`) · `exceptions` · `error-codes`. How `@fails` is written |
| | `exceptions` / `rtti` | `true` · `false` (`-fno-exceptions` needs `expected` or `error-codes`) |
| | `integerTypes` | `fixed-width` (`std::int64_t`, exactly fx3's `int`) · `native` |
| | `overflowChecks` / `boundsChecks` | `builtin-checked` · `debug-only` · `off` / `always` (`.at()`) · `debug-only` · `off` |
| | `noexcept` / `constexpr` | `where-safe` · `never` · `everywhere-possible` / `where-natural` · `avoid` · `aggressive` |
| | `concurrency` | `jthread` · `std-thread` · `coroutines` · `tbb` · `openmp` |
| Libraries | `libraries.json` | `nlohmann-json` · `simdjson` · `rapidjson` · `boost-json` |
| | `libraries.http` | `cpp-httplib` · `drogon` · `crow` · `boost-beast` · `pistache` |
| | `libraries.sql` / `logging` / `testing` / `formatting` / `cli` | `sqlitecpp` … / `spdlog` … / `catch2` · `gtest` · `doctest` … / `std-format` · `fmt` / `cli11` … (any other job can be added as a custom key) |
| | `useBoost` | `false` · `true` |
| Build quality | `optimization` / `lto` / `march` | `O2` · `O0`–`O3` · `Os` · `Oz` / `false` / e.g. `native` |
| | `warnings` / `warningsAsErrors` | `extra` · `default` · `all` · `pedantic` / `true` |
| | `sanitizers` | `address` + `undefined` (debug builds) · also `thread` `memory` `leak` |
| | `staticAnalysis` / `formatStyle` | `clang-tidy` · `cppcheck` · `none` / `llvm` · `google` · `chromium` · `mozilla` · `webkit` · `microsoft` · `file` |
| | `guidelines` | any of `cpp-core-guidelines` `misra-cpp` `autosar` `google-style` `jsf`, checked with clang-tidy where a check exists |

Where a known setting conflicts with an fx3 guarantee (for example `overflowChecks: off` against fx3's "overflow is an error"), the build warns once and does what the config says. The config is the developer's decision.

### 8.3 Safety levels

What the safety levels mean in the generated code:

| Level | Adds |
|-------|------|
| `basic` | only the checks the language always does (bounds, overflow, `@fails`) |
| `standard` | + validation of everything coming from outside (web requests, files, user input, `ai` answers) |
| `strict` | + `@typesafe` everywhere, checked casts only, no `any` |
| `paranoid` | + validation at every function boundary, and a runtime check for every `rules` line |

### 8.4 In-file overrides with `with`

```
with comments teaching                    // top level: the whole file

to cancel expense (id)
    Mark it cancelled.
    with safety paranoid, ask hard        // this block only
```

`with` takes any config key as `key value`, with commas between them: known keys (`codegen` / `verification` / `target.cpp` ...) and custom keys alike, e.g. `with cpp.errorStyle exceptions` or `with auditLog "record old and new values"`. Nested keys use dots: `with naming.functions camelCase`. `ask hard` is short for `with ask hard`.

## 9. Unverifiable guarantees: tags

`rules` and `never` lines that the compiler can't verify are **warnings** by default. You can change that at four levels, from highest priority to lowest:

| Where | Syntax |
|-------|--------|
| one line | add a tag at the end: `the API answers within 200 ms [error]`. Tags: `[off]` `[warn]` `[error]` |
| one block | `rules strict` (= every line `[error]`), `rules loose` (= `[off]`), or `with verification.rules error` |
| one build | `fluid build --rules=error --never=warn` |
| project | `fluid.config` → `verification.rules` / `verification.never` |

A line that CAN be verified is always enforced, whatever its tag. Tags only decide what happens when it can't be verified.

## 10. Which setting wins

From highest priority to lowest:

1. a tag at the end of a line (`[error]`)
2. `with` / `ask` inside a block
3. command-line flags (`--ask`, `--rules`, `--set ...`)
4. `with` / `ask` / `target` at the top of the `.fluid` file
5. `fluid.config` → `overrides` (later entries win)
6. `fluid.config` → top-level keys
7. built-in defaults (`fluid.config.schema.json`)

`fluid build --explain-settings <block>` shows the final settings for a block, and where each one came from.

## 11. Custom keys and instructions

The config is **binding**. Whatever the target language and whatever the style, the generated code must follow every setting, including ones the schema doesn't know.

**Two ways to add your own requirements:**

```jsonc
"maxDependenciesPerModule": 3,                     // any key, anywhere (top level, inside a section, or in an override)
"readme": { "generate": true, "language": "English" },
"instructions": [                                  // free text
  "Never use recursion; use loops instead."
]
```

**What the builder does with each one:**

1. **Typo check (deterministic).** If an unknown key is close to a known one (`"comment"` vs `"comments"`), it asks "did you mean `comments`?" at **every** ask level, because a typo would silently lose a setting.
2. **Interpret.** The LLM works out what the requirement means for this project and target. Its reading is recorded in `fluid.lock`, and `fluid review` shows it:
   `maxDependenciesPerModule: 3 → each generated module imports at most 3 project modules`
3. **Ask when unclear.** This follows the `ask` level (§4):
   - `soft`: assume a reading and report it
   - `mid`: ask if the reading affects data, security or anything external; otherwise assume
   - `hard`: always confirm the reading
   - A custom key that **conflicts** with another setting, or with a `.fluid` rule, is always asked about.
4. **Apply.** It is passed to every LLM step that generates code.
5. **Verify.** Where possible the builder turns the requirement into a check: a lint rule, a clang-tidy / ruff setting, or a generated test. A requirement that can't be checked is reported according to `verification.customKeys` (`off` / `warn` / `error`; default `warn`).

A requirement only becomes a check after you have accepted its interpretation, so a custom key never silently changes meaning between builds. If you edit the key's value, it is interpreted again.
