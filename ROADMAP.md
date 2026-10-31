# FluidXPro3: road to an enterprise-grade tool

How to use this file: each item has an ID, a priority and a checkbox. Tick the box when an item is done, and add a line to **Progress log** at the bottom.

**Priority:**
- **P0**: blocks trust or adoption; do first
- **P1**: needed for teams and production use
- **P2**: growth and breadth

**Size:** S < 1 day · M 1–3 days · L 1–2 weeks · XL > 2 weeks

---

## Where we are (2026-10-30)

| Part | State |
|---|---|
| Language spec | 16 `Syntax/*.fx3` files + `FLUID_SPEC.md`, decisions locked |
| `fx3c` (.fx3 → Python) | v0.2: core language + 14 standard modules, 102 tests |
| `fluidc` (.fluid → .fx3 with Claude) | v0.1: lock file, hand-edit keeping, ask levels, verify-and-fix loop, 31 tests |
| Real-model check | `grades.fluid` and a web + SQLite API built and ran correctly (see `playground/`) |

### Known gaps found so far

These are things we already know about, linked to the item that fixes each one:
- [x] Generated code (its tests) runs on the developer's machine with no sandbox → **E-01**
- [x] Only one `.fluid` file per folder (one `fluid.lock`); no imports between `.fx3` files → **E-05**
- [ ] Runtime errors point at the generated Python, not at the `.fx3` / `.fluid` line → **E-08**
- [ ] Comments in `.fx3` are not carried into the generated Python (`codegen.comments` only affects the `.fx3`) → **E-14**
- [ ] Spec features not compiled yet: enums, interfaces, generics, loopers / checkers / pipelines, structures, async, scheduling, `@label` → **E-09**
- [ ] Annotations that are accepted but not enforced (`@maxsize`, `@sorted`, `@unique`, `@static`, ...) → **E-10**
- [ ] `fluidc describe` is not implemented; `fluidc review` only shows decisions (it can't accept or reject them) → **E-15**
- [ ] `Email` and live `Ai` calls have no automated tests → **E-30**
- [x] Only tested on macOS with Python 3.14 → **E-21** (CI is green on Linux, macOS and Windows with Python 3.10–3.14)
- [x] The project isn't a git repository yet, and has no license, CI or release process → **E-20**
- [ ] `main.fluid` can't be built end to end yet (`every` / scheduling is unsupported) → **E-27**

---

## P0: trust, safety, correctness

### E-01 · Sandbox for generated code · P0 · M ✅
- [x] Generated programs and their tests run in an isolated process: a temporary folder, no network, CPU / memory / time limits, and no access to `secrets.env`.
- [x] `fluidc` refuses to run unverified code outside the sandbox unless told to with `--trust`.
- **Why:** fluidc runs model-written code to verify it. In an enterprise, that has to be contained.
- **Done when:** a test program that tries to read `~/.ssh` or call the network fails inside verification.
- **Status:** done.
  - `fx3c/sandbox.py` has two layers:
    - process: a clean environment, rlimits and an audit hook
    - OS: macOS `sandbox-exec`, Linux `bwrap`
  - `verification.sandbox` sets the level; `--trust` turns the sandbox off.
  - `fluid.lock` records how the build was verified.
  - Tests: `test_sandbox.py`, `test_safety.py`.
  - Windows gets the process layer only.

### E-02 · Treat `.fluid` text as data (prompt-injection hardening) · P0 · S ✅
- [x] The system prompt states that the file's prose and string literals are requirements, never instructions to the generator.
- [x] Deterministic post-checks on generated code: no imports outside the allowed modules (`codegen.dependencies`), no secrets embedded as literals, no network calls in blocks that don't mention them.
- [ ] Adversarial eval cases are added to E-12. (Scripted adversarial cases are in `fluidc/tests/test_safety.py`; move them into E-12 when it exists.)
- [x] Secrets in the `.fluid` file or config are never sent to the model (FL1110).
- **Status:** `fluidc/guard.py`. Its level is set by `verification.capabilities`; secrets in code are always an error.

### E-03 · Reproducibility guarantees · P0 · S ✅
- [x] `fluid.lock` records the model id, the prompt version (hash of the system prompt and reference), and the fx3c and fluidc versions.
- [x] If any of those change, a warning lists which blocks would regenerate. `--offline` keeps building from the lock. (FL0403; `--refresh` regenerates them)
- [x] A lock file format version, with a migration command. (format 2; `fluidc lock migrate`; a newer format is refused)

### E-04 · Safe-by-default standard library · P0 · M ✅
- [x] **Server:**
  - request-size limit (1 MB by default, 413 without reading the body)
  - socket timeouts (30s; slow clients are disconnected)
  - CORS (none unless `s.allowOrigins([...])`)
  - API-key check (`s.requireHeader("X-Api-Key", Env.require("API_KEY"), ["/health"])`, compared in constant time)
  - JSON access log (no query strings)
  - graceful shutdown on Ctrl+C / SIGTERM
  - `nosniff` header; `SERVER_PORT`
- [x] **Db:**
  - FX0480: SQL text built from values is a compile error, inline or through a variable; literals and `@const` values are allowed
  - `db.transaction(work)`, `begin` / `commit` / `rollback`
  - `db.migrate(folder)` (numbered .sql files, each in a transaction, recorded in `fx3_migrations`)
  - foreign keys enforced
- [x] **Secrets:** values from `secrets.env`, and values Env reads from secret-named variables, show as `****` in `print`, `Log`, panics, failed tests and `Response.error`. (The runtime can't see the `.fluid` `secrets` block, so it goes by names and by secrets.env.)
- [x] **Http:** TLS verification always on; the timeout must be > 0; GET / PUT / DELETE retried with backoff and jitter after connection errors, timeouts and 502/503/504 (POST never).
- **Status:**
  - `fx3std.py`
  - `checker._check_sql_text`
  - Tests: `fx3c/tests/test_safe_stdlib.py`
  - The fluidc prompt tells the model when to use each option.
- **Not covered:** a value passed through a function call (`db.query(build(name))`) is not tracked by FX0480.

### E-05 · Multi-file projects · P0 · L ✅
- [x] A project root with any number of `.fluid` / `.fx3` files. (the folder of the file you build; `uses pricing.fluid` in a `.fluid` file, imports in `.fx3`)
- [x] `from ./billing import ...;` between `.fx3` files (modules.fx3 §2); cycle detection. (FX0315–FX0323, FL0420–FL0424)
- [x] One `fluid.lock` per project, keyed by file path.
- [x] fluidc sends other files as interfaces (signatures) only, and a block regenerates when an interface it depends on changes. (Within one file, the other blocks' code is still sent in full; trimming that is part of E-13.)
- **Status:**
  - `fx3c/project.py`: modules, order, exports, `interface()`
  - `fluidc/project.py`: `uses` files, build order
  - Tests: `fx3c/tests/test_modules.py`, `fluidc/tests/test_multi_file.py`

### E-06 · Master `fluid` command · P0 · M ✅
- [x] `fluid build | run | test | review | check`: runs `fluidc` and/or `fx3c` as needed (.fluid → target in one go).
- [x] `fluid new <name>` creates a project scaffold (config, `secrets.env` template, `.gitignore`, example).
- [x] `fluid doctor` checks Python, installed packages, credentials (without showing them), and whether the lock is current.

### E-07 · Release-quality packaging · P0 · S
- [x] One installable package that brings both compilers; version pinning. (root `pyproject.toml` → `fluidxpro3` with `fluid`, `fluidc` and `fx3c`; `anthropic` and `jsonschema` pinned to one major version)
- [ ] Published on PyPI (`pipx install fluidxpro3`). Needs a PyPI project with trusted publishing. `release.yml` already builds the files and checks the version on a tag.
- [x] The generated project includes `pyproject.toml` / `requirements.txt` built from what it actually imports (`anthropic` only if `Ai` is used).

---

## P1: correctness and completeness of the language

### E-08 · Source maps and fx3-level errors · P1 · M
- [ ] Every generated Python line maps back to a `.fx3` line, and through the fluidc markers to a `.fluid` block.
- [ ] Panics show `panic: index out of range  (notes.fx3:42, block "to all notes", notes.fluid:22)`.

### E-09 · Remaining spec features in fx3c · P1 · XL
Each of these lands with tests, fluidc reference examples, and a backend conformance case (E-17):
- [ ] enums and pattern matching (`enums.fx3`)
- [ ] interfaces, generics, type aliases
- [ ] loopers + `repeat(a; ...)`, checkers + `switch(b; ...)`, pipelines (`constructs.fx3`)
- [ ] data structures (`structures.fx3`): start with tree / bst, graph, heap variants, disjoint set, cache
- [ ] async / await, spawn, channels, `locked<T>` / `atomic<T>` (`concurrency.fx3`)
- [ ] labelled `stop`, `repeat @parallel`, `@unsafe` (or declare `@unsafe` out of scope for Python)

### E-10 · Enforce every accepted annotation · P1 · M
- [ ] `@maxsize`, `@sorted`, `@unique`, `@capacity`, `@ordered`, `@min` / `@max`, `@wrapping`, `@saturating`, `@unchecked`, `@static`, `@lazy`, `@nonzero`, `@unsigned`.
- [ ] Rule: an annotation is either enforced or an error. Remove the "accepted, not enforced" warning (FX0505).

### E-11 · Static analysis upgrades · P1 · M
- [ ] Definite assignment through `try` / `catch` and loops (full flow analysis).
- [ ] Collections changed while being looped over (`collections.fx3` / `structures.fx3` checks).
- [ ] Unreachable `catch`, unused functions, unused parameters (`@unused` to silence).
- [ ] `@pure` checks every side effect (file, network, database, `Log`), not just `print`.

---

## P1: the LLM layer (fluidc)

### E-12 · Evaluation suite for code generation · P1 · L
- [ ] A corpus of 50+ `.fluid` programs: easy → hard, across every keyword, plus adversarial ones (E-02). Each has behaviour tests the generated program must pass.
- [ ] Metrics:
  - first-attempt pass rate
  - fix-up turns per build
  - questions asked (and how many were unnecessary)
  - tokens and cost per block
  - unsupported-block rate
- [ ] Run on every change to the prompt, the reference, the model or fx3c. A drop blocks the merge.
- [ ] Use it to tune `llm.effort` and the prompt with measurements, not guesses.

### E-13 · Cost and speed controls · P1 · M
- [ ] `llm.maxCostPerBuild` / `llm.maxTokensPerBuild` with a clear stop message; `fluid build --estimate` (token count before calling).
- [ ] Generate independent blocks in parallel for large changes; Batch API for full rebuilds (`--batch`).
- [ ] Cache-hit reporting and alerting (`cache_read_input_tokens` should be high after the first call).
- [ ] Optional per-block model choice (for example, a cheaper model for `settings` / `thing` blocks). Opt-in only, measured with E-12 first.

### E-14 · Comments and docs in the target code · P1 · M
- [ ] Carry `.fx3` comments and `///` doc comments into Python (docstrings) and future backends.
- [ ] `codegen.comments` / `docstrings` take effect in the final code.

### E-15 · Review workflow · P1 · M
- [ ] `fluid review` is interactive: accept or reject each assumption, answer, custom-key reading and generated block. Rejected items regenerate.
- [ ] `fluidc describe <block>`: rewrite the prose to match a hand edit.
- [ ] `fluid diff`: show what changed in the `.fx3` since the last build, per block.
- [ ] CI mode: fail if there are unreviewed LLM decisions (`--require-reviewed`).

### E-16 · Provider and platform options · P1 · S
- [ ] `llm.platform`: `anthropic` (default), `bedrock`, `vertex`, `foundry`, using the SDK's platform clients, for companies that must use their cloud account.
- [ ] `llm.inferenceGeo` (data residency) passed through.
- [ ] Clear behaviour on refusals (already uses server-side fallback), rate limits and outages (retry, then suggest `--offline`).

---

## P1: targets, runtime, deployment

### E-17 · Backend conformance suite · P1 · M
- [ ] The same `tests/programs/*.fx3` must give byte-identical output on every backend. This is the gate for adding any backend.
- [ ] A standard-library conformance test per module (same names, same failures, same printing).

### E-18 · C++ backend · P1 · XL
- [ ] A C++20 code generator implementing `target.cpp` settings (memory model, `std::expected` errors, overflow checks, libraries).
- [ ] `fx3std` for C++: start with Math, Time, File, Json, Http client; Server and Db next.
- [ ] CMake project generation; sanitizers in debug builds.

### E-19 · Deployment helpers · P1 · M
- [ ] `fluid package`: a Dockerfile, health check, environment template (secret names only), a runnable zip / pyz.
- [ ] Production `Server` settings (host, port, workers) taken from `fluid.config` / environment.

### E-27 · Scheduling and background work · P1 · M
- [ ] An `every` block maps to a scheduler in the standard library (`Schedule.every(1d, at = "21:00", job)`), with persistence of the last run.
- [ ] `Process` module (run commands safely: no shell, argument lists).

### E-28 · More standard library · P1 · L
- [ ] Postgres / MySQL drivers (same API as `Db`), connection pooling.
- [ ] Crypto: hashing, HMAC, secure random, UUID; password hashing.
- [ ] Config files (TOML / YAML), CLI argument parsing (`Args`), internationalisation (number / date formats).
- [ ] Test helpers: fake `Time.now`, fake `Http` responses, temporary `Db`.

---

## P1: developer experience

### E-22 · Editor support (VS Code first) · P1 · L
- [ ] Syntax highlighting for `.fluid` and `.fx3`.
- [ ] A language server on top of `fx3c check`: diagnostics as you type, hover types, go to definition, rename. Code actions apply the `fix:` text fx3c already produces.
- [ ] For `.fluid`: block outline, "build this block", show the generated fx3 side by side.

### E-23 · Formatter and explanations · P1 · S
- [ ] `fx3 fmt` / `fx3 fmt --canonical` (aliases → canonical spellings); `fluid fmt`.
- [ ] `fx3 explain FX0401`: a longer explanation and examples for every error code.

### E-24 · Documentation site · P1 · M
- [ ] Tutorial (first program in 10 minutes), language reference (from `Syntax/`), standard library reference (generated from `stdlib.py`), cookbook, error-code index.
- [ ] An examples gallery that is built and tested in CI.

---

## P1: enterprise governance

### E-25 · Organisation policies · P1 · M
- [ ] A base `fluid.config` that projects inherit (`"extends": "org/fluid.base.config"`), with locked keys a project can't loosen (for example `codegen.safety >= strict`, `verification.rules = error`, allowed modules and dependencies).
- [ ] Policy report in `fluid doctor` and CI.

### E-26 · Audit trail · P1 · S
- [ ] `fluid.lock` records:
  - who accepted each decision, and when
  - the model and prompt version
  - the token cost
- [ ] Optional OpenTelemetry traces of builds (opt-in; no source code or secrets in spans).
- [ ] Generated-code attribution header (customisable, e.g. a copyright line).

---

## P2: engineering hygiene and growth

### E-20 · Open-source readiness · P2 · S ✅
- [x] `git init`, LICENSE, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY.md (how to report vulnerabilities), issue templates, CHANGELOG.
- [x] Semantic versioning for the language spec and both compilers, plus a deprecation policy for locked rules.

### E-21 · CI matrix and quality gates · P2 · S
- [x] GitHub Actions: Linux / macOS / Windows × Python 3.10–3.14. All 15 jobs are green (first runs on 2026-10-31 found and fixed issues on Python < 3.14 and on Windows).
- [x] Lint and type-check the compilers: ruff is clean; pyright has a ratchet (`tools/typecheck_gate.py`, starting from 186 known errors).
- [x] Fuzz the lexer and parser: `test_fuzz.py` runs 400 cases per CI run and 50,000 nightly. 20,000 local cases found no crash.
- [ ] Coverage target.
- [ ] Bring pyright down to zero.

### E-29 · Performance · P2 · M
- [ ] Benchmarks for the compilers (large files) and for the generated code (overflow-check cost).
- [ ] `codegen.optimizeFor = speed` drops checks that static analysis can prove safe (for example, loop counters).

### E-30 · Live-service tests (opt-in) · P2 · S
- [ ] `pytest -m live`: real `Ai.ask` / `Ai.classify`, a real fluidc build of the examples, `Email` against a local SMTP test server.
- [ ] Skipped automatically when there are no credentials; never run in the default test run.

### E-31 · More targets · P2 · XL
- [ ] TypeScript / Node, then Go or Rust, each passing E-17 before release.

---

## Suggested milestones

| Milestone | Items | Outcome |
|---|---|---|
| **M1: Safe & shippable** | E-01, E-02, E-03, E-06, E-07, E-20, E-21 | Others can install it and run it safely; it's versioned and in CI |
| **M2: Real projects** | E-05, E-04, E-08, E-27, E-19, E-15 | Multi-file apps with safe servers and databases, debuggable and deployable |
| **M3: Trustworthy AI layer** | E-12, E-13, E-16, E-14, E-26 | Measured quality and cost, reviewable decisions, enterprise model hosting |
| **M4: Full language** | E-09, E-10, E-11, E-17, E-28 | The whole spec compiles, and every accepted annotation is enforced |
| **M5: Ecosystem** | E-22, E-23, E-24, E-25, E-18, E-31, E-29, E-30 | Editor support, docs, policies, C++ and more targets |

---

## Progress log

| Date | Item | Note |
|---|---|---|
| 2026-10-30 | — | Roadmap created. Baseline: fx3c v0.2 (102 tests), fluidc v0.1 (31 tests), real-model builds verified |
| 2026-10-30 | E-20 | git repo, Apache-2.0, CONTRIBUTING / CODE_OF_CONDUCT / SECURITY / VERSIONING / CHANGELOG, issue templates |
| 2026-10-30 | E-01, E-02, E-03 | Sandboxed verification, injection guard, lock format 2. Both playground locks migrated offline with no model calls, and their tests passed in the macOS sandbox |
| 2026-10-30 | E-06, E-07 | `fluid` command; one `fluidxpro3` package (checked by installing the wheel in a clean venv); per-program `requirements.txt`. A real build of the `fluid new` app with claude-opus-5-5 passed first time |
| 2026-10-30 | E-21 (partly) | CI and release workflows, ruff clean, pyright ratchet, fuzzing. Remaining: first green CI run on GitHub, coverage, pyright to zero. M1 is complete apart from PyPI publishing and that CI run |
| 2026-10-31 | E-21 | First CI runs on GitHub: fixed Python < 3.14, Windows console encoding and newlines; all 15 jobs green |
| 2026-10-31 | E-05 | Multi-file projects in fx3c (imports, one Python module per file) and fluidc (`uses other.fluid`, interfaces in the prompt, one lock) |
| 2026-10-31 | E-04 | Safe-by-default Server, Db (FX0480, transactions, migrations), secret masking, Http retries / TLS |
