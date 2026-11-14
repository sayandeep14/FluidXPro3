"""The IR as readable text (`fx3c ir file.fx3`): for looking at what the backends get, and for tests."""

from __future__ import annotations

from . import nodes as I


def show(modules: list[I.Module]) -> str:
    return "\n".join(_module(m) for m in modules)


def _module(m: I.Module) -> str:
    out = [f"module {m.name or '(main)'}  # {m.file}"]
    if m.imports:
        out += [f"  import {mod}: {', '.join(names)}" for mod, names in m.imports]
    if m.std:
        out.append(f"  std {', '.join(m.std)}")
    for g in m.globals:
        out.append(f"  global {'const ' if g.const else ''}{g.name}: {g.ty}")
    for e in m.enums:
        vs = ", ".join(v.name + (f"({', '.join(f'{n}: {t}' for n, t in v.fields)})" if v.fields else "")
                       + (f" = {v.raw!r}" if v.raw is not None else "") for v in e.variants)
        out.append(f"  enum {e.name}{f'<{e.raw}>' if e.raw else ''}{' ordered' if e.ordered else ''} {{ {vs} }}")
        out += [_func(f, "    ") for f in e.methods]
    for c in m.classes:
        head = f"  class {c.name}"
        if c.parent:
            head += f" : {c.parent}"
        if c.interfaces:
            head += f" implements {', '.join(c.interfaces)}"
        if c.flags:
            head += f" [{' '.join(sorted(c.flags))}]"
        if c.derive:
            head += f" derive({', '.join(sorted(c.derive))})"
        out.append(head)
        for f in c.fields:
            extra = f" = {_x(f.default)}" if f.default is not None else ""
            checks = "".join(f" @{k}({', '.join(_x(a) for a in args)})" for k, args in f.checks)
            out.append(f"    field {'static ' if f.static else ''}{f.name}: {f.ty}{checks}{extra}")
        if c.injected:
            out.append(f"    injected {', '.join(f'{k} <- {v}' for k, v in c.injected.items())}")
        out += [_func(f, "    ") for f in c.ctors + c.methods]
    out += [_func(f, "  ") for f in m.funcs]
    if m.init:
        out.append("  init")
        out += _stmts(m.init, "    ")
    if m.entry:
        out.append(f"  entry {m.entry}")
    return "\n".join(out)


def _func(f: I.Func, ind: str) -> str:
    params = ", ".join(f"{'ref ' if p.ref else ''}{p.name}: {p.ty}" + (f" = {_x(f.defaults[p.name])}"
                       if p.name in f.defaults else "") for p in f.params)
    flags = "".join(f" @{x}" for x in sorted(f.flags))
    fails = f" fails {', '.join(f.fails)}" if f.fails else ""
    head = f"{ind}func {f.name}({params}) -> {f.ret}{fails}{flags}"
    if f.extern:
        return head + " extern " + ", ".join(f"{k}: {v!r}" for k, v in f.extern.items())
    if f.body is None:
        return head + " (no body)"
    return "\n".join([head] + _stmts(f.body.stmts, ind + "  "))


def _stmts(stmts, ind: str) -> list[str]:
    out = []
    for s in stmts:
        out += _stmt(s, ind)
    return out


def _block(b: I.Block | None, ind: str) -> list[str]:
    return _stmts(b.stmts, ind + "  ") if b is not None else []


def _stmt(s: I.Stmt, ind: str) -> list[str]:
    if isinstance(s, I.Block):
        return [f"{ind}block"] + _block(s, ind)
    if isinstance(s, I.Let):
        cell = " cell" if s.local.cell else ""
        return [f"{ind}let {s.local.name}: {s.local.ty}{cell}" + (f" = {_x(s.init)}" if s.init is not None else "")]
    if isinstance(s, I.Assign):
        return [f"{ind}{_x(s.target)} = {_x(s.value)}"]
    if isinstance(s, I.SharedSet):
        return [f"{ind}shared {_x(s.holder)} {s.op} {_x(s.value)}"]
    if isinstance(s, I.Unpack):
        names = ", ".join(t.name if t is not None else "_" for t in s.targets) + \
            (f", ...{s.rest.name}" if s.rest is not None else "")
        return [f"{ind}unpack [{names}] = {_x(s.value)}"]
    if isinstance(s, I.ExprStmt):
        return [f"{ind}{_x(s.expr)}"]
    if isinstance(s, I.If):
        out = [f"{ind}if {_x(s.cond)}"] + _block(s.then, ind)
        if s.else_ is not None:
            out += [f"{ind}else"] + _block(s.else_, ind)
        return out
    if isinstance(s, I.While):
        return [f"{ind}while{' (at least once)' if s.at_least_once else ''} {_x(s.cond)}"] + _block(s.body, ind)
    if isinstance(s, I.Loop):
        return [f"{ind}loop"] + _block(s.body, ind)
    if isinstance(s, I.ForRange):
        var = s.var.name if s.var is not None else "_"
        step = f" step {_x(s.step)}" if s.step is not None else ""
        return [f"{ind}for {var} in {_x(s.start)}{'..=' if s.inclusive else '..'}{_x(s.end)}{step}"] + \
            _block(s.body, ind)
    if isinstance(s, I.ForEach):
        names = ", ".join(v.name if v is not None else "_" for v in s.vars)
        return [f"{ind}for {names} in {s.kind} {_x(s.iterable)}"] + _block(s.body, ind)
    if isinstance(s, I.CFor):
        return [f"{ind}cfor"] + _stmts(s.init, ind + "  init ") + \
            [f"{ind}  cond {_x(s.cond) if s.cond is not None else 'true'}"] + _stmts(s.step, ind + "  step ") + \
            _block(s.body, ind)
    if isinstance(s, I.Break):
        return [f"{ind}break{' ' + s.label if s.label else ''}"]
    if isinstance(s, I.Continue):
        return [f"{ind}continue{' ' + s.label if s.label else ''}"]
    if isinstance(s, I.Return):
        return [f"{ind}return" + (f" {_x(s.value)}" if s.value is not None else "")]
    if isinstance(s, I.Fail):
        return [f"{ind}fail {_x(s.error)}"]
    if isinstance(s, I.Match):
        out = [f"{ind}match {s.subject.name}"]
        for a in s.arms:
            pat = _x(a.pattern) if a.pattern is not None else "default"
            out.append(f"{ind}  case {pat}" + (f" where {_x(a.guard)}" if a.guard is not None else ""))
            out += _block(a.body, ind + "  ")
        return out
    if isinstance(s, I.Try):
        out = [f"{ind}try"] + _block(s.body, ind)
        for c in s.catches:
            out += [f"{ind}catch {c.error}" + (f" {c.var.name}" if c.var is not None else "")] + _block(c.body, ind)
        if s.finally_ is not None:
            out += [f"{ind}finally"] + _block(s.finally_, ind)
        return out
    if isinstance(s, I.Defer):
        return [f"{ind}defer"] + _block(s.body, ind)
    if isinstance(s, I.With):
        return [f"{ind}with {_x(s.holder)}"] + _block(s.body, ind)
    if isinstance(s, I.Group):
        lim = f" limit {_x(s.limit)}" if s.limit is not None else ""
        fails = f" fails {', '.join(s.fails)}" if s.fails else ""
        return [f"{ind}group{lim}{fails}"] + _block(s.body, ind)
    if isinstance(s, I.Select):
        out = [f"{ind}select"]
        for c in s.cases:
            what = {"recv": f"recv {_x(c.channel)}" + (f" -> {c.bind.name}" if c.bind is not None else "")
                    + (" (skip closed)" if c.skip_closed else ""),
                    "send": f"send {_x(c.channel)} <- {_x(c.value)}",
                    "after": f"after {_x(c.value)}"}.get(c.kind, "default")
            out += [f"{ind}  case {what}"] + _block(c.body, ind + "  ")
        return out
    return [f"{ind}<{type(s).__name__}>"]


def _x(e) -> str:
    if e is None:
        return "default"
    if isinstance(e, I.Lit):
        if e.value is None:
            return "none"
        if isinstance(e.value, bool):
            return "true" if e.value else "false"
        if e.ty.kind == "duration":
            return f"{e.value}s"
        return repr(e.value)
    if isinstance(e, I.Var):
        return e.local.name
    if isinstance(e, I.GlobalRef):
        return f"{e.module + '.' if e.module else ''}{e.name}"
    if isinstance(e, I.FuncRef):
        return f"&{e.module + '.' if e.module else ''}{e.owner + '.' if e.owner else ''}{e.name}"
    if isinstance(e, I.TypeRef):
        return e.name
    if isinstance(e, I.This):
        return "this"
    if isinstance(e, I.Field):
        return f"{_x(e.obj)}.{e.name}"
    if isinstance(e, I.SafeField):
        return f"{_x(e.obj)}?.{e.name}"
    if isinstance(e, I.StaticField):
        return f"{e.cls}::{e.name}"
    if isinstance(e, I.Index):
        return f"{_x(e.obj)}[{_x(e.index)}]"
    if isinstance(e, I.Slice):
        return f"{_x(e.obj)}[{_x(e.start) if e.start else ''}{'..=' if e.inclusive else '..'}{_x(e.end) if e.end else ''}]"
    if isinstance(e, I.Unary):
        return f"{e.op}({_x(e.operand)})"
    if isinstance(e, (I.Binary, I.Compare, I.Logical)):
        return f"{e.op}({_x(e.left)}, {_x(e.right)})"
    if isinstance(e, I.Cond):
        return f"if({_x(e.cond)}, {_x(e.then)}, {_x(e.else_)})"
    if isinstance(e, I.Range):
        return f"range({_x(e.start)}{'..=' if e.inclusive else '..'}{_x(e.end)})"
    if isinstance(e, I.Copy):
        return f"copy({_x(e.value)})"
    if isinstance(e, I.Unwrap):
        return f"unwrap({_x(e.value)})"
    if isinstance(e, I.Coalesce):
        return f"coalesce({_x(e.value)}, {_x(e.default)})"
    if isinstance(e, I.Call):
        args = ", ".join(_x(a) for a in e.args)
        fails = f" !{'|'.join(e.fails)}" if e.fails else ""
        std = "std " if e.std else ""
        if e.kind == "value":
            return f"call {_x(e.recv)}({args})"
        if e.recv is not None:
            return f"{std}{_x(e.recv)}.{e.target}({args}){fails}"
        where = f"{e.module}." if e.module else ""
        return f"{std}{where}{e.owner + '.' if e.owner else ''}{e.target}({args}){fails}"
    if isinstance(e, I.New):
        return f"new {e.cls}{'.' + e.ctor if e.ctor else ''}({', '.join(_x(a) for a in e.args)})"
    if isinstance(e, I.Intrinsic):
        parts = ([_x(e.recv)] if e.recv is not None else []) + [f"{k}={_x(v)}" for k, v in e.args.items()] + \
            [_x(v) for v in e.extra]
        return f"#{e.op}({', '.join(parts)})"
    if isinstance(e, I.Cast):
        return f"cast{e.mode[2:] if len(e.mode) > 2 else ''}<{e.ty}>({_x(e.value)})"
    if isinstance(e, I.Format):
        return "f(" + ", ".join(repr(t) if v is None else _x(v) + (f":{s}" if s else "") for v, t, s in e.parts) + ")"
    if isinstance(e, I.ListMake):
        return "[" + ", ".join(_x(i) for i in e.items) + "]"
    if isinstance(e, I.SetMake):
        return ("unordered" if e.unordered else "") + "{" + ", ".join(_x(i) for i in e.items) + "}"
    if isinstance(e, I.MapMake):
        return "{" + ", ".join(f"{_x(k)}: {_x(v)}" for k, v in e.items) + ("}" if e.items else ":}")
    if isinstance(e, I.TupleMake):
        return "(" + ", ".join(_x(i) for i in e.items) + ")"
    if isinstance(e, I.Lambda):
        caps = ", ".join(f"{loc.name} {mode}" for loc, mode in e.captures)
        body = "; ".join(line.strip() for s in e.body.stmts for line in _stmt(s, ""))
        return f"fn({', '.join(p.name for p in e.params)}){f' [{caps}]' if caps else ''} {{ {body} }}"
    if isinstance(e, I.Propagate):
        return f"{_x(e.value)}?"
    if isinstance(e, I.OrElse):
        return f"orelse({_x(e.value)}, {_x(e.default)})"
    if isinstance(e, I.Capture):
        return f"capture({_x(e.value)})"
    if isinstance(e, I.VariantMake):
        return f"{e.enum}.{e.variant}" + (f"({', '.join(_x(a) for a in e.args)})" if e.args else "")
    if isinstance(e, I.VariantTest):
        binds = ", ".join(f"{loc.name}={k}" for loc, k in e.binds)
        return f"is({_x(e.value)}, {e.enum}.{e.variant}{' ' + binds if binds else ''})"
    if isinstance(e, I.Spawn):
        return f"spawn {_x(e.work)}"
    if isinstance(e, I.Await):
        return f"await {_x(e.task)}" + (f" !{'|'.join(e.fails)}" if e.fails else "")
    if isinstance(e, I.SharedGet):
        return f"shared({_x(e.holder)})"
    if isinstance(e, I.ServiceGet):
        return f"service({e.cls})"
    return f"<{type(e).__name__}>"
