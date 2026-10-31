# Changelog

All notable changes are listed here. Versions follow [VERSIONING.md](VERSIONING.md); the format follows [Keep a Changelog](https://keepachangelog.com).

## [Unreleased]

Milestones M1, "Safe & shippable", and M2, "Real projects" (see [ROADMAP.md](ROADMAP.md)).

### Added
- **Review workflow (E-15):**
  - `fluid review -i` accepts or rejects each model-written block. A rejected block is regenerated with your note.
  - `fluid check --require-reviewed` fails CI while code is unreviewed.
  - `fluid diff` shows hand edits since the last build.
  - `fluidc describe <block>` rewrites a block's text to match its hand edit.
- **Deployment (E-19):**
  - `fluid package` writes `build/package/`: `Dockerfile` (non-root, health check), `app.pyz`, and `.env.example` with names only.
  - Server caps connections at once (`SERVER_MAX_CONNECTIONS`, default 64); above that it answers 503.
- **Scheduling and other programs (E-27):**
  - `Schedule.every(1d, job, at = "21:00")`, `Schedule.run()` and `runPending()`, with saved last runs; `every` blocks are no longer unsupported.
  - `Process.run([program, args...])` never uses a shell, and fluidc allows it only in blocks that ask to run a program.
  - `Function() { ... }` with a body can be passed as a value.
- **Errors point at your source (E-08):** panics, failed tests and server handler crashes end with `(notes.fx3:42, block "to all notes", notes.fluid:22)` instead of pointing at generated Python.
- **Safe-by-default standard library (E-04):**
  - Server:
    - defaults: 1 MB bodies, 30s timeouts, no CORS, a JSON access log, `nosniff`, graceful shutdown on SIGTERM
    - new: `s.limits`, `s.allowOrigins`, `s.requireHeader`, `s.accessLog`; `SERVER_PORT`
  - Db:
    - FX0480: an error when SQL text is built from values
    - new: `db.transaction`, `begin` / `commit` / `rollback`, `db.migrate(folder)`
    - foreign keys are enforced
  - Secrets: values are masked as `****` in print, Log, panics and error responses.
  - Http: TLS is always verified; timeouts must be positive; GET / PUT / DELETE retry with backoff (`retries = 2`).
- **Projects with many files (E-05):**
  - `.fx3` files import each other:
    - `from ./pricing import total, Item;`, `from billing.invoice import render;`, `from ../x import y as z;`
    - fx3c writes one Python module per file, and `--fx3-test` runs every module's tests.
    - New errors FX0315–FX0323: circular imports, private or non-constant imports, statements in an imported file, duplicate class names, and others.
    - `fx3c --root` sets the project folder.
  - `.fluid` files use each other: `uses pricing.fluid`.
    - Used files are built first and share one `fluid.lock`.
    - The model sees their interfaces (signatures only), and a change to an interface regenerates the files that use it.
    - New errors FL0420–FL0424.
- **Open-source files (E-20):**
  - Apache-2.0 `LICENSE`
  - `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `VERSIONING.md`
  - issue and PR templates
- **Sandbox for generated code (E-01):** `fx3c.sandbox`.
  - fluidc's verification runs the generated tests with no network, no secrets, no files outside their folder, no processes, and CPU / memory / time limits.
  - On macOS (`sandbox-exec`) and Linux (`bwrap`), the OS sandbox is added.
  - New settings: `verification.sandbox` and `verification.testTimeout`.
  - New flag: `--trust`.
- **Prompt-injection hardening (E-02):**
  - The system prompt now says the `.fluid` file is data.
  - New `fluidc/guard.py`:
    - Network calls are allowed only in blocks that ask for them.
    - Addresses must come from the source.
    - Secret values in generated code are always rejected.
    - Secrets in the `.fluid` file or config are never sent to the model (FL1110).
  - New setting: `verification.capabilities`.
- **Reproducible builds (E-03):** `fluid.lock` format 2.
  - It records the model and prompt version for every block, plus the toolchain and the verification status.
  - FL0403 warns when blocks were made with another prompt or model; `--refresh` regenerates them.
  - `fluidc lock migrate` upgrades a lock. A lock in a newer format is refused (FL0402).
- **The `fluid` command (E-06):** `new`, `build`, `run`, `test`, `check` (CI: is `fluid.lock` current?), `review`, `doctor`.
- **One package (E-07):**
  - The package is `fluidxpro3` and installs `fluid`, `fluidc` and `fx3c`.
  - fx3c writes a `requirements.txt` next to each generated program.
- **CI (E-21):**
  - GitHub Actions on Linux, macOS and Windows with Python 3.10–3.14
  - ruff, plus a pyright ratchet
  - a test that the generated code is valid Python 3.10
  - a smoke test that installs the wheel
  - nightly fuzzing
  - a release workflow

### Changed
- **Versions:** `fx3c`, `fluidc` and `fluid` now share one version, 0.3.0. `fx3c.SPEC_VERSION` is 0.2.
- **`fluidc run`** refuses code whose tests were skipped, failed (as warnings) or ran without a sandbox, unless given `--trust`.
- **FL0503** for builds from the lock now lists the actual problems.

### Fixed
- **`--ask` no longer regenerates code.** The ask level was part of each block's lock key, so building with `--ask=soft` and then running `fluid test` called the model again. Now it only decides when to ask. A question assumed at a softer level is asked when a later build needs an answer; keeping the default records it with no model call. Locks made by 0.3.0 at any level stay valid.
- **CI:** `Math.hypot` now has an inspectable signature on Python < 3.14; `fluid doctor` falls back to ASCII marks when the terminal can't print ✓/✗ (Windows cp1252); the generated-code check looks for syntax errors and undefined names only, not style.
- `File.write` and `File.append` no longer turn `\n` into `\r\n` on Windows, so files and `File.size` are the same on every OS.
- The fx3c package now includes `fx3std.py`; before, only editable installs worked.
- The `NoReturn` annotation in the generated runtime (`fx3rt.py`) is now imported.

## [0.2.0] - 2026-10-30

The baseline before the roadmap.

### Added
- **Language spec**:
  - 16 `Syntax/*.fx3` files
  - `FLUID_SPEC.md`
  - `fluid.config` with its JSON schema
- **fx3c 0.2**: `.fx3` → Python.
  - Language and checks: the core language, alias-tolerant types and annotations, the method bank, an English condition mini-grammar, `maybe`, `result`, `@fails`, 64-bit overflow checks, `@test` and `@example`, durations.
  - 14 standard modules: Math, Random, Time, File, Path, Csv, Json, Env, Log, Http, Server, Db, Ai, Email.
- **fluidc 0.1**: `.fluid` → `.fx3` with Claude.
  - Build process: block markers, `fluid.lock`, hand-edit keeping, `pin` / `unpin`, the verify-and-fix loop with fx3c.
  - Settings and checks: ask levels, `rules` / `never` guarantees, custom config keys.
