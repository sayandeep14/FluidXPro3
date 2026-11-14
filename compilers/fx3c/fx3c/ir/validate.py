"""Invariants every IR module must hold. Backends rely on them, so the lowering is tested against them."""

from __future__ import annotations

from .. import nodes as A
from . import nodes as I
from .lower import walk


def check(modules: list[I.Module]) -> list[str]:
    """Problems, as text (empty when the IR is sound)."""
    out: list[str] = []
    for m in modules:
        for f in _funcs(m):
            out += _check_func(m, f)
        out += _check_body(m, "(top level)", m.init, set())
    return out


def _funcs(m: I.Module):
    yield from m.funcs
    for c in m.classes:
        yield from c.ctors
        yield from c.methods
    for e in m.enums:
        yield from e.methods


def _declared(stmts) -> set[int]:
    """Every local a body introduces: lets, loop variables, unpacking, catches, matches, binds, lambda parameters."""
    out: set[int] = set()
    for root in stmts:
        for n in walk(root):
            if isinstance(n, I.Let):
                out.add(id(n.local))
            elif isinstance(n, I.ForRange) and n.var is not None:
                out.add(id(n.var))
            elif isinstance(n, I.ForEach):
                out.update(id(v) for v in n.vars if v is not None)
            elif isinstance(n, I.Unpack):
                out.update(id(v) for v in n.targets if v is not None)
                if n.rest is not None:
                    out.add(id(n.rest))
            elif isinstance(n, I.Catch) and n.var is not None:
                out.add(id(n.var))
            elif isinstance(n, I.Match):
                out.add(id(n.subject))
            elif isinstance(n, I.VariantTest):
                out.update(id(loc) for loc, _ in n.binds)
            elif isinstance(n, I.Lambda):
                out.update(id(p) for p in n.params)
            elif isinstance(n, I.SelectCase) and n.bind is not None:
                out.add(id(n.bind))
    return out


def _check_func(m: I.Module, f: I.Func) -> list[str]:
    if f.body is None:
        return []
    return _check_body(m, f.name, f.body.stmts, {id(p) for p in f.params})


def _check_body(m: I.Module, where: str, stmts, params: set[int]) -> list[str]:
    out: list[str] = []
    known = params | _declared(stmts)
    for root in stmts:
        for n in walk(root):
            if isinstance(n, (A.Node, A.Expr)):
                out.append(f"{m.file}: {where}: a syntax-tree node was left in the IR ({type(n).__name__})")
            if isinstance(n, I.Expr):
                if n.ty is None:
                    out.append(f"{m.file}: {where}: {type(n).__name__} has no type")
                elif n.ty.kind == "unknown" and not isinstance(n, I.Lit):
                    out.append(f"{m.file}: {where}: {type(n).__name__} has an unknown type")
            if isinstance(n, I.Var) and id(n.local) not in known:
                out.append(f"{m.file}: {where}: `{n.local.name}` is used but never declared here")
            if isinstance(n, I.Call) and n.kind in ("method", "super") and n.recv is None:
                out.append(f"{m.file}: {where}: method call `{n.target}` without an object")
    return out
