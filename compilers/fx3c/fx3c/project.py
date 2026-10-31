"""Projects with many .fx3 files: finding the modules a file imports, ordering them, and what each one
exports (Syntax/modules.fx3 sections 1-2; ROADMAP E-05).

- The folder of the entry file is the project root. `geo/shapes.fx3` there is module `geo.shapes`.
- `import geo.shapes` / `from geo.shapes import ...` look from the root; `./x` and `../x` from the
  importing file. A standard module (Math, File, ...) wins over a file with the same name.
- Modules are checked dependencies first. Circular imports are an error.
- A module other than the entry file holds declarations only: classes, functions and variables.
  Its functions, classes and constants (@const) can be imported unless marked @private.
"""

from __future__ import annotations

import keyword
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable
from typing import Optional

from . import nodes as A
from .aliases import typo_match
from .diagnostics import CompileError, Diagnostics, Span
from .parser import parse
from .stdlib import MODULES as STD_MODULES

Reader = Callable[[Path], Optional[str]]

# Names a generated module must not take: they would hide the Python module the program needs.
_TAKEN = set(getattr(sys, "stdlib_module_names", ())) | {"fx3rt", "fx3std", "anthropic"}


def read_file(path: Path) -> Optional[str]:
    """The default reader: the file's text, or None when it doesn't exist (with this exact spelling:
    `import Helpers` must not find helpers.fx3 on a case-insensitive disk, or Linux would break)."""
    try:
        if path.name not in os.listdir(path.parent):
            return None
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return None


def python_name(module: str) -> str:
    """`geo.shapes` -> `geo.shapes`; a part that is a Python keyword, or a first part that would hide
    a Python module (json, time, ...), gets a trailing `_`."""
    parts = module.split(".")
    out = []
    for k, p in enumerate(parts):
        if keyword.iskeyword(p) or (k == 0 and p in _TAKEN):
            p += "_"
        out.append(p)
    return ".".join(out)


@dataclass
class Module:
    name: str                       # dotted, from the project root: "geo.shapes"
    path: Path
    source: str
    program: Optional[A.Program] = None
    imports: list[str] = field(default_factory=list)       # project modules this one imports
    checker: object = None

    @property
    def py(self) -> str:
        return python_name(self.name)

    @property
    def py_file(self) -> str:
        return self.py.replace(".", "/") + ".py"

    @property
    def py_alias(self) -> str:
        return "_fx_" + self.py.replace(".", "_")

    def import_path(self, from_file: str = "") -> str:
        return self.name

    def _symbols(self):
        ck = self.checker
        for name, sym in ck.module.syms.items():  # type: ignore[attr-defined]
            if getattr(sym, "import_from", None) is not None:
                continue                                     # no re-exports
            if sym.kind == "class":
                if sym.cls is None or sym.cls.builtin or name in ck.foreign_classes:  # type: ignore[attr-defined]
                    continue
            elif sym.kind == "func":
                if sym.func is None or sym.func.builtin or sym.func.decl is None:
                    continue
            elif sym.kind != "var":
                continue
            yield name, sym

    def exports(self) -> dict:
        return {n: s for n, s in self._symbols() if not self._why_hidden(s)}

    def hidden(self, name: str) -> Optional[str]:
        for n, s in self._symbols():
            if n == name:
                return self._why_hidden(s)
        return None

    @staticmethod
    def _why_hidden(sym) -> Optional[str]:
        if sym.kind == "func" and sym.func.private:
            return "it is @private"
        if sym.kind == "class" and "private" in sym.cls.annots:
            return "it is @private"
        if sym.kind == "var":
            if "private" in sym.annots:
                return "it is @private"
            if not sym.const:
                return "only constants (@const) can be shared between files; share changing state " \
                       "through functions"
        return None


@dataclass
class Project:
    root: Path
    entry: Module
    modules: dict[str, Module]      # every module but the entry, by name
    order: list[Module]             # dependencies first, entry last

    @property
    def libraries(self) -> list[Module]:
        return [m for m in self.order if m is not self.entry]


def _module_name(target: Path, root: Path) -> Optional[str]:
    try:
        rel = target.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return ".".join(rel.with_suffix("").parts)


def _resolve(s: A.ImportStmt, importer: Path, root: Path) -> tuple[Optional[str], Optional[Path]]:
    """(module name, file) for a project import; (None, None) for a standard module."""
    spec = s.module
    if "/" in spec or spec.startswith("."):
        target = (importer.parent / (spec + ".fx3"))
        return _module_name(target, root), target
    if spec in STD_MODULES or spec == "General":
        return None, None
    return spec, root.joinpath(*spec.split(".")).with_suffix(".fx3")


def load(entry_source: str, entry_file: str, diags: Diagnostics, read: Reader = read_file,
         sources: Optional[dict[str, str]] = None) -> Optional[Project]:
    """Parse the entry file and every project module it reaches (their text goes into `sources`, for
    error messages). None after a parse error or a circular import."""
    sources = {} if sources is None else sources
    entry_path = Path(entry_file)
    root = entry_path.parent
    entry = Module(entry_path.stem, entry_path, entry_source)
    modules: dict[str, Module] = {}
    queue = [entry]
    ok = True
    while queue:
        mod = queue.pop(0)
        try:
            mod.program = parse(mod.source, str(mod.path))
        except CompileError as e:
            diags.items.append(e.diagnostic)
            ok = False
            continue
        for s in mod.program.stmts:
            if not isinstance(s, A.ImportStmt):
                continue
            name, target = _resolve(s, mod.path, root)
            if target is None:
                continue
            if name is None:
                diags.error("FX0321", f"`{s.module}` is outside the project folder ({root})", s.span,
                            fix="every module must be in the entry file's folder or below it")
                s.missing = True  # type: ignore[attr-defined]
                continue
            if name == entry.name and target.resolve() == entry_path.resolve():
                s.target = name  # type: ignore[attr-defined]
                mod.imports.append(name)
                continue
            if name not in modules:
                text = read(target)
                if text is None:
                    guess = typo_match(s.module, [*STD_MODULES, "General"])
                    diags.error("FX0310", f"there is no module `{s.module}`", s.span,
                                fix=f"did you mean `{guess}`?" if guess else
                                    f"no file {target}, and the standard modules are: "
                                    f"{', '.join(sorted(STD_MODULES))}")
                    s.missing = True  # type: ignore[attr-defined]
                    continue
                modules[name] = Module(name, target, text)
                sources[str(target)] = text
                queue.append(modules[name])
            s.target = name  # type: ignore[attr-defined]
            if name not in mod.imports:
                mod.imports.append(name)
    for name, mod in modules.items():
        inside = sorted(m for m in modules if m.startswith(name + "."))
        if inside:
            diags.error("FX0323", f"`{name}` is both a file and a folder of modules ({inside[0]})", mod.program.span
                        if mod.program else Span(str(mod.path), 1, 1),
                        fix=f"rename {mod.path.name} or the {mod.path.with_suffix('').name}/ folder")
            ok = False
    if not ok:
        return None
    order = _order(entry, modules, diags)
    if order is None:
        return None
    return Project(root, entry, modules, order)


def _order(entry: Module, modules: dict[str, Module], diags: Diagnostics) -> Optional[list[Module]]:
    """Dependencies first. A cycle is an error that names every module on it."""
    every = {**modules, entry.name: entry}
    done: list[Module] = []
    state: dict[str, int] = {}          # 1 = being visited, 2 = done
    stack: list[str] = []

    def visit(m: Module) -> bool:
        state[m.name] = 1
        stack.append(m.name)
        for dep in m.imports:
            if state.get(dep) == 1:
                cycle = stack[stack.index(dep):] + [dep]
                span = next(s.span for s in m.program.stmts  # type: ignore[union-attr]
                            if isinstance(s, A.ImportStmt) and getattr(s, "target", None) == dep)
                diags.error("FX0320", f"circular import: {' -> '.join(cycle)}", span,
                            fix="move what both need into a third module that imports neither")
                return False
            if state.get(dep) is None and not visit(every[dep]):
                return False
        stack.pop()
        state[m.name] = 2
        done.append(m)
        return True

    return done if visit(entry) else None


_LIBRARY_STMTS = (A.ImportStmt, A.ClassDecl, A.FuncDecl, A.VarDecl)


def check_library(mod: Module, diags: Diagnostics) -> None:
    """Rules for a module that isn't the entry file."""
    assert mod.program is not None
    for s in mod.program.stmts:
        if not isinstance(s, _LIBRARY_STMTS):
            diags.error("FX0319", "only the entry file can run statements at the top level", s.span,
                        fix=f"{mod.path.name} is imported by another file: put this inside a function")
    ck = mod.checker
    if ck is not None and ck.entry is not None:  # type: ignore[attr-defined]
        diags.error("FX0322", "only the entry file can have an @entry function", ck.entry.decl.span,  # type: ignore[attr-defined]
                    fix=f"{mod.path.name} is imported by another file: remove @entry")
