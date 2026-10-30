# Changelog

All notable changes are listed here. Versions follow [VERSIONING.md](VERSIONING.md); the format follows [Keep a Changelog](https://keepachangelog.com).

## [Unreleased]

Milestone M1, "Safe & shippable" (see [ROADMAP.md](ROADMAP.md)).

### Added
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
