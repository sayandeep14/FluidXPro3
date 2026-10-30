# Versioning and deprecation policy

FluidXPro3 has three things that are versioned:
- the language spec (`Syntax/*.fx3`, `FLUID_SPEC.md`)
- the two compilers (`fx3c`, `fluidc`)
- the `fluid` command

All three use [semantic versioning](https://semver.org): `MAJOR.MINOR.PATCH`.

## One release number

The `fluidxpro3` package ships `fx3c`, `fluidc` and `fluid` together, under one version number. `fluid --version` prints all three, plus the spec version they implement.

| Part | Version lives in |
|---|---|
| package | `pyproject.toml` (root) |
| fx3c | `compilers/fx3c/fx3c/__init__.py` |
| fluidc | `compilers/fluidc/fluidc/__init__.py` |
| fluid | `compilers/fluid/fluid/__init__.py` |
| language spec | `SPEC_VERSION` in `compilers/fx3c/fx3c/__init__.py` |

## What each part of the number means

**Before 1.0** (where we are now), a MINOR release may break things. Every break is listed in `CHANGELOG.md` under **Breaking**, with how to migrate.

**From 1.0 on:**
- **MAJOR**: a locked language rule changes meaning, valid `.fx3` stops compiling, or `fluid.lock` needs migrating.
- **MINOR**: new syntax, annotations, standard-library modules, targets, keywords or config keys. Existing programs still compile and behave the same.
- **PATCH**: bug fixes, better messages, faster compilers.

## Locked rules and deprecation

The rules marked *locked* in `Syntax/` (for example: `int` is 64-bit and overflow is an error, `/` always gives a float, no truthiness) change only like this:
1. **Proposal.** Open an issue with the "language change" template: what changes, why, and how existing code is affected.
2. **Deprecation (MINOR release).** The old form still works but warns (`FX05xx` / `FL08xx`). The warning says what to write instead. `fx3 fmt` fixes it where it can.
3. **Removal (next MAJOR release, at least 6 months later).** The old form becomes an error with the same advice.

Aliases (alternative spellings such as `i32` for `int`) are never removed within a major version.

## `fluid.lock` format

`fluid.lock` has its own `format` number. When a newer fluidc reads an older lock, it migrates it. `fluidc lock migrate` writes the migrated file and makes no model calls. A lock written by a *newer* fluidc is refused with a clear message instead of being guessed at.

## Generated code

The same `.fx3` with the same compiler version always gives the same target code. A PATCH release may change generated code only to fix a bug. Such changes are listed in the CHANGELOG.
