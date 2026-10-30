# Changelog

All notable changes are listed here. Versions follow [VERSIONING.md](VERSIONING.md); the format follows [Keep a Changelog](https://keepachangelog.com).

## [Unreleased]

Milestone M1, "Safe & shippable" (see [ROADMAP.md](ROADMAP.md)).

### Added
- Open-source files: Apache-2.0 `LICENSE`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `VERSIONING.md`, issue and PR templates (E-20).

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
