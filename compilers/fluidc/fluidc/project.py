"""Projects with many .fluid files (ROADMAP E-05).

- The project root is the folder of the file being built. It holds the one fluid.lock, and every .fx3
  goes to the same place under project.fx3Out
  (billing/invoice.fluid -> build/fx3/billing/invoice.fx3).
- A `uses` block that names another .fluid file makes this file depend on it:
      uses
          pricing.fluid for prices and tax
          the sqlite database in shop.db
  Paths are relative to the file that names them and must stay inside the root. Used files are built
  first; a cycle is an error.
- A file that another file uses must be a module: no program blocks (`on`, `web`, `page`, `command`,
  `every`, `when`). Its generated fx3 holds declarations only, and fx3c checks it that way.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .errors import FluidError, Message
from .reader import Document, read

PROGRAM_KEYWORDS = {"on", "web", "page", "command", "every", "when"}
FLUID_REF = re.compile(r"(?<![\w./-])((?:\.{1,2}/)*[\w-]+(?:/[\w-]+)*\.fluid)\b")


def rel_name(path: Path, root: Path) -> str:
    """`billing/invoice.fluid`: the name of a file in messages and in fluid.lock keys."""
    return Path(os.path.relpath(path.resolve(), root.resolve())).as_posix()


def is_library(doc: Document) -> bool:
    return not any(b.keyword in PROGRAM_KEYWORDS for b in doc.blocks)


def used_files(doc: Document, fluid_path: Path) -> list[tuple[Path, int]]:
    """(file, line) for every .fluid file the `uses` blocks name, in order, without repeats."""
    out: list[tuple[Path, int]] = []
    seen: set[Path] = set()
    for b in doc.blocks:
        if b.keyword != "uses":
            continue
        for k, line in enumerate([b.header, *b.body]):
            for m in FLUID_REF.finditer(line):
                p = (fluid_path.parent / m.group(1)).resolve()
                if p not in seen:
                    seen.add(p)
                    out.append((p, b.line_no + k))
    return out


def import_path(from_fx3: str, to_fx3: str) -> str:
    """The fx3 import path from one .fx3 to another, both relative to the fx3 root: `./pricing`, `../pricing`."""
    rel = os.path.relpath(os.path.splitext(to_fx3)[0], os.path.dirname(from_fx3) or ".")
    rel = Path(rel).as_posix()
    return rel if rel.startswith("../") else "./" + rel


def plan(entry: Path, root: Path) -> list[Path]:
    """Every .fluid file the entry needs, used files first, the entry last."""
    entry = entry.resolve()
    order: list[Path] = []
    state: dict[Path, int] = {}
    stack: list[Path] = []
    docs: dict[Path, Document] = {}

    def doc_of(p: Path) -> Document:
        if p not in docs:
            docs[p] = read(p.read_text(encoding="utf-8"), rel_name(p, root))
        return docs[p]

    def visit(p: Path) -> None:
        state[p] = 1
        stack.append(p)
        doc = doc_of(p)
        for dep, line in used_files(doc, p):
            where = rel_name(p, root)
            if not dep.is_file():
                raise FluidError(Message("error", "FL0421", f"`uses` names {rel_name(dep, root)}, which doesn't exist",
                                         where, line, fix="fix the file name (paths are relative to this file)"))
            try:
                dep.relative_to(root.resolve())
            except ValueError:
                raise FluidError(Message("error", "FL0423", f"{dep} is outside the project folder ({root})", where,
                                         line, fix="files a project uses must be in the folder of the file you "
                                                   "build, or below it")) from None
            if state.get(dep) == 1:
                cycle = [rel_name(x, root) for x in stack[stack.index(dep):]] + [rel_name(dep, root)]
                raise FluidError(Message("error", "FL0420", "files use each other in a circle: " + " -> ".join(cycle),
                                         where, line, fix="move what they share into a third file that uses neither"))
            dep_doc = doc_of(dep)
            program = [b for b in dep_doc.blocks if b.keyword in PROGRAM_KEYWORDS]
            if program:
                raise FluidError(Message("error", "FL0422", f"{rel_name(dep, root)} is used by {where}, so it can't "
                                         f"have a `{program[0].written}` block (line {program[0].line_no})",
                                         where, line, fix="keep program blocks in the file that runs; move them out "
                                                          f"of {rel_name(dep, root)}"))
            if state.get(dep) is None:
                visit(dep)
        stack.pop()
        state[p] = 2
        order.append(p)

    visit(entry)
    return order
