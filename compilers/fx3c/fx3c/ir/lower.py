"""Checked syntax tree -> IR (ir/nodes.py). One Lowerer per module.

The rules it makes explicit are the ones the Python backend applies today (copies at the places it calls
`store()`, narrowing, failure handling), so the IR keeps the semantics that the tests already pin down.
"""

from __future__ import annotations

from typing import Optional

from .. import fxtypes as T
from .. import nodes as A
from ..fxtypes import Ty
from . import nodes as I

_BIN = {"+": "add", "-": "sub", "*": "mul", "/": "div", "~/": "floordiv", "%": "mod", "**": "pow",
        "&": "bitand", "|": "bitor", "^": "bitxor", "<<": "shl", ">>": "shr"}
_SET_BIN = {"|": "union", "&": "intersect", "-": "difference", "^": "symdiff", "+": "union"}
_CMP = {"==": "eq", "!=": "ne", "<": "lt", "<=": "le", ">": "gt", ">=": "ge", "===": "same", "in": "in",
        "not in": "notin"}
_UN = {"-": "neg", "not": "not", "~": "bitnot"}
_FUNC_FLAGS = {"async", "entry", "test", "pure", "memo", "property", "transactional", "provides", "abstract",
               "override", "private", "static"}
_CLASS_FLAGS = {"value", "service", "error", "abstract", "open", "immutable", "singleton", "private"}
_STORED_ARGS = {"value", "values", "newVal", "default"}      # bank arguments that are stored (copied)


class LowerError(Exception):
    pass


class Lowerer:
    def __init__(self, checker, module: str = "", file: str = ""):
        self.ck = checker
        self.module = module
        self.file = file
        self.problems: list[str] = []       # what this version can't lower (reported, never silent)
        self._reset_function()

    # ---- helpers ----------------------------------------------------------------------
    def _reset_function(self) -> None:
        self.locals: dict[int, I.Local] = {}
        self.used: set[str] = set()
        self.pre: list[I.Stmt] = []          # statements the current expression needs first
        self.subst: dict[int, I.Expr] = {}   # AST node -> IR expr (a switch subject read once)
        self.counter = 0

    def problem(self, what: str, node=None) -> None:
        line = getattr(getattr(node, "span", None), "line", 0)
        self.problems.append(f"{self.file}:{line}: {what}")

    def temp(self, base: str, ty: Ty) -> I.Local:
        self.counter += 1
        return self._new_local(f"_{base}{self.counter}", ty)

    def _new_local(self, name: str, ty: Ty, **kw) -> I.Local:
        unique, k = name, 1
        while unique in self.used:
            k += 1
            unique = f"{name}_{k}"
        self.used.add(unique)
        return I.Local(unique, ty, **kw)

    def local_for(self, sym, mode: str = "local") -> I.Local:
        if sym is None:
            raise LowerError("a name without a symbol")
        loc = self.locals.get(id(sym))
        if loc is None:
            kind = getattr(sym, "kind", "var")
            loc = self._new_local(sym.name, getattr(sym, "decl_ty", None) or sym.ty,
                                  mode="loop" if kind == "loopvar" else mode,
                                  ref=bool(getattr(sym, "is_ref", False)), const=bool(getattr(sym, "const", False)))
            self.locals[id(sym)] = loc
        return loc

    @staticmethod
    def is_place(e: A.Expr) -> bool:
        return isinstance(e, (A.Name, A.Member, A.Index))

    def store(self, e: Optional[A.Expr]) -> Optional[I.Expr]:
        """A value being stored: a collection read from a variable / field / index is copied (memory.fx3)."""
        if e is None:
            return None
        if isinstance(e, A.RangeExpr) and e.ty is not None and T.is_seq(e.ty):
            return I.Intrinsic(e.ty, "range.toList", None, {"range": self.range_value(e)})
        v = self.expr(e)
        if self.is_place(e) and e.ty is not None and T.is_collection(e.ty) and e.ty.kind != "tuple":
            return I.Copy(v.ty, v)
        return v

    def ty(self, e: A.Expr) -> Ty:
        return e.ty if getattr(e, "ty", None) is not None else T.UNKNOWN

    # ---- module ---------------------------------------------------------------------
    def lower_program(self, prog: A.Program) -> I.Module:
        m = I.Module(self.module, self.file)
        m.std = sorted(self.ck.std_modules)
        for s in prog.stmts:
            if isinstance(s, A.ImportStmt) and getattr(s, "target", None) is not None:
                m.imports.append((str(getattr(s, "target")), [n for n, _ in s.names]))
        for s in prog.stmts:
            if isinstance(s, A.EnumDecl):
                m.enums.append(self.lower_enum(s))
            elif isinstance(s, A.ClassDecl):
                m.classes.append(self.lower_class(s))
            elif isinstance(s, A.FuncDecl):
                m.funcs.append(self.lower_func(s))
        # top-level statements (module variables become globals, set in order)
        self._reset_function()
        for s in prog.stmts:
            if isinstance(s, (A.ImportStmt, A.ClassDecl, A.FuncDecl)):
                continue
            if isinstance(s, A.VarDecl):
                for item in s.items:
                    sym = item.sym
                    if sym is None:
                        continue
                    g = I.Global(sym.name, sym.ty, const=bool(sym.const), module=self.module)
                    m.globals.append(g)
                    init = self.var_init(s, item, sym.ty)
                    if init is not None:
                        if g.const:
                            g.init = init
                        m.init += self.flush()
                        m.init.append(I.Assign(I.GlobalRef(sym.ty, self.module, sym.name), init, line=s.span.line))
                continue
            stmts = self.stmt(s)
            m.init += self.flush() + stmts
        if self.ck.entry is not None:
            m.entry = self.ck.entry.name
        m.tests = [fi.name for fi in self.ck.tests]
        return m

    def flush(self) -> list[I.Stmt]:
        out, self.pre = self.pre, []
        return out

    # ---- declarations ---------------------------------------------------------------
    def lower_func(self, fd: A.FuncDecl, owner: str = "") -> I.Func:
        fi = fd.info
        self._reset_function()
        params = []
        for p, pi in zip(fd.params, fi.params):
            loc = self.local_for(p.sym, "param") if p.sym is not None else self._new_local(p.name, pi.ty, mode="param")
            loc.ref = pi.ref
            params.append(loc)
        f = I.Func(fd.name, params, fi.return_ty, list(fi.fails), None, owner=owner, static=fi.static,
                   flags=set(fi.annots) & _FUNC_FLAGS | ({"ctor"} if fi.is_ctor else set()),
                   type_params=list(getattr(fi, "type_params", [])), module=self.module, line=fd.span.line,
                   style=fi.style or "", tx=getattr(fi, "tx", None), extern=dict(getattr(fi, "extern", {}) or {}))
        for a in fd.annots:
            f.native.update(getattr(a, "native", None) or {})
        for p in fd.params:
            if p.default is not None:
                d = self.store(p.default)
                if d is not None:
                    f.defaults[p.name] = d
        if isinstance(fd.body, A.Block):
            f.body = self.block(fd.body)
        elif isinstance(fd.body, A.Expr):
            v = self.store(fd.body)
            assert v is not None
            body = self.flush()
            body.append(I.Return(v, line=fd.span.line) if fi.returns else I.ExprStmt(v, line=fd.span.line))
            f.body = I.Block(body)
        for efi, cases in getattr(self.ck, "examples", []):
            if efi is fi:
                f.examples = [([self.expr(x) for x in ins], self.expr(out)) for ins, out in cases]
        return f

    def lower_class(self, cd: A.ClassDecl) -> I.ClassDecl:
        ci = cd.info
        out = I.ClassDecl(cd.name, ci.parent or "", list(ci.interfaces), module=self.module, line=cd.span.line,
                          flags=set(ci.annots) & _CLASS_FLAGS | ({"interface"} if ci.is_interface else set()),
                          derive=set(ci.derive), type_params=list(ci.type_params),
                          injected=dict(getattr(ci, "injected", {}) or {}))
        for a in cd.annots:
            out.native.update(getattr(a, "native", None) or {})
        self._reset_function()
        for vd in cd.fields:
            for item in vd.items:
                f = ci.fields[item.name]
                native: dict = {}
                for a in vd.annots:
                    native.update(getattr(a, "native", None) or {})
                out.fields.append(I.FieldDecl(f.name, f.ty, self.store(item.init) if item.init is not None else None,
                                              f.private, f.static, f.const,
                                              [(k, [self.expr(x) for x in args]) for k, args in f.checks], native))
        for fd in cd.methods:
            if fd.info is None:
                continue
            lf = self.lower_func(fd, owner=cd.name)
            (out.ctors if fd.info.is_ctor else out.methods).append(lf)
        return out

    def lower_enum(self, cd: A.EnumDecl) -> I.EnumDecl:
        ci = cd.info
        ei = ci.enum
        out = I.EnumDecl(cd.name, [I.Variant(v.name, v.index, [(f.name, f.ty) for f in v.fields], v.raw)
                                   for v in ei.variants.values()],
                         ei.raw, ordered="order" in ci.derive, module=self.module, line=cd.span.line)
        for fd in cd.methods:
            if fd.info is not None:
                out.methods.append(self.lower_func(fd, owner=cd.name))
        return out

    # ---- statements -------------------------------------------------------------------
    def block(self, b: A.Block | list) -> I.Block:
        stmts = b.stmts if isinstance(b, A.Block) else b
        out: list[I.Stmt] = []
        saved = self.pre
        for s in stmts:
            self.pre = []
            lowered = self.stmt(s)
            out += self.pre + lowered
        self.pre = saved
        return I.Block(out)

    def stmt(self, s: A.Stmt) -> list[I.Stmt]:
        m = getattr(self, "st_" + type(s).__name__, None)
        if m is None:
            self.problem(f"can't lower {type(s).__name__} yet", s)
            return []
        out = m(s)
        line = s.span.line if getattr(s, "span", None) is not None else 0
        for x in out:
            if not x.line:
                x.line = line
        return out

    def var_init(self, s: A.VarDecl, item: A.DeclItem, ty: Ty) -> Optional[I.Expr]:
        annots = {a.name: a for a in s.annots}
        if item.init is not None:
            if ty.kind == "result":
                v: I.Expr = I.Capture(ty, self.expr(item.init))
            else:
                v = self.store(item.init)      # type: ignore[assignment]
            if "unordered" in annots and ty.kind == "set":
                v = v if not isinstance(v, I.SetMake) else I.SetMake(v.ty, v.items, unordered=True)
                if not isinstance(v, I.SetMake):
                    v = I.Intrinsic(ty, "set.unordered", None, {"value": v})
        elif "size" in annots:
            args = {"size": self.expr(annots["size"].args[0])}
            if "init" in annots:
                args["value"] = self.store(annots["init"].args[0])  # type: ignore[assignment]
            v = I.Intrinsic(ty, "seq.filled", None, args)
        elif ty.kind == "channel":
            cap = annots.get("capacity")
            v = I.Intrinsic(ty, "channel.make", None,
                            {"capacity": self.expr(cap.args[0])} if cap is not None and cap.args else {})
        elif T.is_collection(ty) and ty.kind != "tuple":
            v = I.Intrinsic(ty, "zero", None)
        elif ty.kind == "maybe":
            v = I.Lit(ty, None)
        else:
            return None
        rng = annots.get("range")
        if rng is not None and len(rng.args) == 2:
            v = I.Intrinsic(ty, "rangeCheck", v, {"low": self.expr(rng.args[0]), "high": self.expr(rng.args[1]),
                                                  "name": I.Lit(T.STRING, item.name)})
        return v

    def st_VarDecl(self, s: A.VarDecl) -> list[I.Stmt]:
        out: list[I.Stmt] = []
        for item in s.items:
            if item.sym is None:
                continue
            loc = self.local_for(item.sym)
            loc.const = bool(item.sym.const)
            init = self.var_init(s, item, item.sym.ty)
            out += self.flush()
            out.append(I.Let(loc, init))
        return out

    def st_UnpackDecl(self, s: A.UnpackDecl) -> list[I.Stmt]:
        value = self.expr(s.value)
        targets: list[Optional[I.Local]] = []
        rest = None
        for t in s.targets:
            if t.name == "_" or t.sym is None:
                targets.append(None)
            elif t.rest:
                rest = self.local_for(t.sym)
            else:
                targets.append(self.local_for(t.sym))
        return [I.Unpack(targets, value, rest)]

    def _target(self, e: A.Expr) -> I.Expr:
        return self.expr(e)

    def _range_wrapped(self, target: A.Expr, v: I.Expr) -> I.Expr:
        sym = getattr(target, "sym", None) if isinstance(target, A.Name) else None
        rng = getattr(sym, "annots", {}).get("range") if sym is not None else None
        if sym is not None and rng is not None and len(rng.args) == 2:
            return I.Intrinsic(v.ty, "rangeCheck", v, {"low": self.expr(rng.args[0]), "high": self.expr(rng.args[1]),
                                                       "name": I.Lit(T.STRING, sym.name)})
        return v

    def _holder(self, target: A.Expr) -> Optional[I.Expr]:
        """The atomic<T> / locked<T> holder a target writes through, if any."""
        dt = self.declared_ty(target)
        if dt is not None and dt.kind in ("atomic", "locked") and target.ty is not None and target.ty.kind != dt.kind:
            return self.place_raw(target, dt)
        return None

    def st_Assign(self, s: A.Assign) -> list[I.Stmt]:
        holder = self._holder(s.target)
        if s.op == "??=":
            tgt = self._target(s.target)
            return [I.If(I.Compare(T.BOOL, "eq", tgt, I.Lit(T.NONE, None)),
                         I.Block([I.Assign(self._target(s.target), self.store(s.value))]))]  # type: ignore[arg-type]
        if s.op == "=":
            v = self.store(s.value)
            assert v is not None
            if holder is not None:
                return [I.SharedSet(holder, "=", v)]
            sym = getattr(s.target, "sym", None) if isinstance(s.target, A.Name) else None
            if getattr(s, "_retyped", False) and sym is not None and not getattr(sym, "module_level", False):
                # a loose variable given another type (types.fx3 1): from here on it's a new variable
                new = self._new_local(sym.name, v.ty)
                self.locals[id(sym)] = new
                return [I.Let(new, v)]
            return [I.Assign(self._target(s.target), self._range_wrapped(s.target, v))]
        op = s.op[:-1]
        value = self.expr(s.value)
        if holder is not None:
            return [I.SharedSet(holder, self.bin_op(op, self.ty(s.target), value.ty), value)]
        cur = self.expr(s.target)
        new = I.Binary(getattr(s, "_op_ty", None) or cur.ty, self.bin_op(op, cur.ty, value.ty), cur, value)
        return [I.Assign(self._target(s.target), self._range_wrapped(s.target, new))]

    def st_IncDec(self, s: A.IncDec) -> list[I.Stmt]:
        op = "add" if s.op == "++" else "sub"
        one = I.Lit(self.ty(s.target), 1)
        holder = self._holder(s.target)
        if holder is not None:
            return [I.SharedSet(holder, op, one)]
        cur = self.expr(s.target)
        return [I.Assign(self._target(s.target), self._range_wrapped(s.target, I.Binary(cur.ty, op, cur, one)))]

    def st_ExprStmt(self, s: A.ExprStmt) -> list[I.Stmt]:
        return [I.ExprStmt(self.expr(s.expr))]

    def cond(self, c: A.Expr) -> tuple[I.Expr, list[I.Stmt]]:
        """A condition, and the statements the `then` block starts with (if-bind: the bound value)."""
        if isinstance(c, A.BindCond):
            value = self.expr(c.value)
            tmp = self.temp("m", value.ty)
            self.pre.append(I.Let(tmp, value))
            bound = self.local_for(c.sym)
            inner = value.ty.args[0] if value.ty.kind == "maybe" else value.ty
            return (I.Compare(T.BOOL, "ne", I.Var(value.ty, tmp), I.Lit(T.NONE, None)),
                    [I.Let(bound, I.Unwrap(inner, I.Var(value.ty, tmp)))])
        return self.expr(c), []

    def st_If(self, s: A.If) -> list[I.Stmt]:
        branches = [(s.cond, s.then), *s.elifs]
        else_block = self.block(s.else_) if s.else_ is not None else None
        result: Optional[list[I.Stmt]] = None
        for c, body in reversed(branches):
            saved = self.pre
            self.pre = []
            cond, prefix = self.cond(c)
            before = self.pre
            self.pre = saved
            then = self.block(body)
            node = I.If(cond, I.Block(prefix + then.stmts), else_block)
            result = before + [node]
            else_block = I.Block(result)
        assert result is not None
        return result

    def st_RepeatTimes(self, s: A.RepeatTimes) -> list[I.Stmt]:
        return [I.ForRange(None, I.Lit(T.INT, 0), self.expr(s.count), False, None, self.block(s.body))]

    def st_RepeatWhile(self, s: A.RepeatWhile) -> list[I.Stmt]:
        saved = self.pre
        self.pre = []
        c = self.expr(s.cond)
        if self.pre:
            self.problem("a loop condition that needs statements (e.g. switch as a value) isn't lowered yet", s)
        self.pre = saved
        return [I.While(c, self.block(s.body), s.at_least_once)]

    def st_RepeatForever(self, s: A.RepeatForever) -> list[I.Stmt]:
        return [I.Loop(self.block(s.body))]

    def range_value(self, r: A.RangeExpr) -> I.Range:
        start = self.expr(r.start) if r.start is not None else I.Lit(T.INT, 0)
        end = self.expr(r.end) if r.end is not None else I.Lit(T.INT, 0)
        return I.Range(T.range_of(T.INT), start, end, r.inclusive, self.expr(r.step) if r.step is not None else None)

    def st_RepeatEach(self, s: A.RepeatEach) -> list[I.Stmt]:
        vars_ = [None if t.name == "_" or t.sym is None else self.local_for(t.sym, "loop") for t in s.targets]
        if isinstance(s.iterable, A.RangeExpr) and len(vars_) == 1:
            r = self.range_value(s.iterable)
            return [I.ForRange(vars_[0], r.start, r.end, r.inclusive, r.step, self.block(s.body))]
        it = self.expr(s.iterable)
        k = it.ty.kind
        kind = {"map": "map", "set": "set", "string": "string", "channel": "channel", "range": "range"}.get(k, "list")
        return [I.ForEach(vars_, it, kind, self.block(s.body))]

    def st_RepeatC(self, s: A.RepeatC) -> list[I.Stmt]:
        init = self.stmt(s.init) if s.init is not None else []
        init = self.flush() + init
        c = self.expr(s.cond) if s.cond is not None else None
        step = self.stmt(s.step) if s.step is not None else []
        return [I.CFor(init, c, step, self.block(s.body))]

    def st_Stop(self, s: A.Stop) -> list[I.Stmt]:
        return [I.Break(s.label or "")]

    def st_Skip(self, s: A.Skip) -> list[I.Stmt]:
        return [I.Continue(s.label or "")]

    def st_Return(self, s: A.Return) -> list[I.Stmt]:
        if not s.values:
            return [I.Return(None)]
        if len(s.values) == 1:
            return [I.Return(self.store(s.values[0]))]
        vals = [self.store(v) for v in s.values]
        return [I.Return(I.TupleMake(T.multi(*[v.ty for v in vals if v is not None]), vals))]  # type: ignore[arg-type]

    def st_Fail(self, s: A.Fail) -> list[I.Stmt]:
        return [I.Fail(self.expr(s.error))]

    def st_Defer(self, s: A.Defer) -> list[I.Stmt]:
        return [I.Defer(self.block(s.body))]

    def st_Try(self, s: A.Try) -> list[I.Stmt]:
        catches = []
        for c in s.catches:
            err = c.error_cls.name if c.error_cls is not None else "Error"
            var = self.local_for(c.sym) if c.sym is not None else None
            catches.append(I.Catch(err, var, self.block(c.body)))
        return [I.Try(self.block(s.body), catches, self.block(s.finally_) if s.finally_ is not None else None)]

    def match(self, sw: A.Switch, result: Optional[I.Local] = None) -> list[I.Stmt]:
        value = self.expr(sw.subject)
        subj = self.temp("s", value.ty)
        out: list[I.Stmt] = list(self.flush())
        out.append(I.Let(subj, value))
        self.subst[id(sw.subject)] = I.Var(value.ty, subj)
        arms = []
        for c in sw.cases:
            saved = self.pre
            self.pre = []
            pattern: Optional[I.Expr] = None
            if c.patterns is not None:
                parts = [self.expr(x) for x in c.conds]
                pattern = parts[0] if parts else I.Lit(T.BOOL, False)
                for p in parts[1:]:
                    pattern = I.Logical(T.BOOL, "or", pattern, p)
            guard = self.expr(c.guard) if c.guard is not None else None
            if self.pre:
                self.problem("a case pattern that needs statements isn't lowered yet", c)
            self.pre = saved
            if result is not None:
                body_stmts = c.body
                lowered = self.block(body_stmts[:-1]) if len(body_stmts) > 1 else I.Block([])
                last = body_stmts[-1] if body_stmts else None
                if isinstance(last, A.ExprStmt):
                    saved = self.pre
                    self.pre = []
                    v = self.store(last.expr)
                    lowered.stmts += self.pre + [I.Assign(I.Var(result.ty, result), v)]  # type: ignore[arg-type]
                    self.pre = saved
                body = lowered
            else:
                body = self.block(c.body)
            arms.append(I.Arm(pattern, guard, body))
        self.subst.pop(id(sw.subject), None)
        out.append(I.Match(subj, I.Var(value.ty, subj), arms))
        return out

    def st_Switch(self, s: A.Switch) -> list[I.Stmt]:
        return self.match(s)

    def st_WithStmt(self, s: A.WithStmt) -> list[I.Stmt]:
        return [I.With(self.expr(s.subject), self.block(s.body))]

    def st_GroupStmt(self, s: A.GroupStmt) -> list[I.Stmt]:
        """Task variables declared directly in a group are used after it (concurrency.fx3 2b). Block-scoped
        targets would lose them, so they are declared before the group and set inside it."""
        limit = self.expr(s.limit) if s.limit is not None else None
        body = self.block(s.body)
        before: list[I.Stmt] = []
        for k, st in enumerate(body.stmts):
            if isinstance(st, I.Let) and st.local.ty.kind == "task":
                before.append(I.Let(st.local, None, line=st.line))
                body.stmts[k] = I.Assign(I.Var(st.local.ty, st.local), st.init, line=st.line) if st.init is not None \
                    else I.Block([])
        return before + [I.Group(limit, body, list(getattr(s, "fails", []) or []))]

    def st_SelectStmt(self, s: A.SelectStmt) -> list[I.Stmt]:
        cases = []
        for c in s.cases:
            ex = c.expr
            sc = I.SelectCase(c.kind or "default")
            if c.kind == "after" and isinstance(ex, A.Call):
                sc.value = self.expr(ex.args[0].value)
            elif c.kind in ("recv", "send") and isinstance(ex, A.Call) and isinstance(ex.func, A.Member):
                sc.channel = self.expr(ex.func.obj)
                if c.kind == "send":
                    sc.value = self.store(ex.args[0].value)
                if c.sym is not None:
                    sc.bind = self.local_for(c.sym)
                sc.skip_closed = bool(getattr(c, "skip_closed", False))
            sc.body = self.block(c.body)
            cases.append(sc)
        return [I.Select(cases)]

    def st_Block(self, s: A.Block) -> list[I.Stmt]:
        return [self.block(s)]

    def st_ImportStmt(self, s) -> list[I.Stmt]:
        return []

    def st_FuncDecl(self, s) -> list[I.Stmt]:
        self.problem("functions inside functions aren't part of fx3", s)
        return []

    def st_ClassDecl(self, s) -> list[I.Stmt]:
        return []

    st_EnumDecl = st_ClassDecl
    st_InterfaceDecl = st_ClassDecl

    # ---- expressions ------------------------------------------------------------------
    def expr(self, e: A.Expr) -> I.Expr:
        if id(e) in self.subst:
            return self.subst[id(e)]
        m = getattr(self, "ex_" + type(e).__name__, None)
        if m is None:
            self.problem(f"can't lower {type(e).__name__} yet", e)
            return I.Lit(self.ty(e), None)
        return m(e)

    def declared_ty(self, e: A.Expr) -> Optional[Ty]:
        if isinstance(e, A.Name) and getattr(e, "sym", None) is not None:
            return e.sym.ty
        if isinstance(e, A.Member) and getattr(e, "field_info", None) is not None:
            return e.field_info.ty
        return None

    def place_raw(self, e: A.Expr, ty: Ty) -> I.Expr:
        """A variable / field as itself (the holder of an atomic / locked value, not what's inside)."""
        if isinstance(e, A.Name):
            return self.name_ref(e, ty)
        assert isinstance(e, A.Member)
        return I.Field(ty, self.expr(e.obj), e.name, getattr(e.field_info, "owner", ""))

    def name_ref(self, e: A.Name, ty: Ty) -> I.Expr:
        sym = e.sym
        imported = getattr(sym, "import_from", None)
        if imported:
            return I.GlobalRef(ty, imported, getattr(sym, "import_name", e.id))
        if getattr(sym, "module_level", False) and sym.kind == "var":
            return I.GlobalRef(ty, self.module, sym.name)
        loc = self.local_for(sym)
        return I.Var(loc.ty, loc)

    def _shared_and_narrowed(self, raw: I.Expr, declared: Ty, read: Ty) -> I.Expr:
        if declared.kind in ("atomic", "locked") and read.kind != declared.kind:
            return I.SharedGet(read, raw)
        if declared.kind == "maybe" and read.kind not in ("maybe", "none", "unknown"):
            return I.Unwrap(read, raw)           # narrowed: checked not to be none
        return raw

    def ex_Name(self, e: A.Name) -> I.Expr:
        variant = getattr(e, "variant", None)
        if variant is not None:
            return I.VariantMake(self.ty(e), variant[0], variant[1])
        sym = e.sym
        if sym is None:
            self.problem(f"unresolved name `{e.id}`", e)
            return I.Lit(self.ty(e), None)
        if sym.kind == "class":
            return I.TypeRef(self.ty(e), sym.cls.name if sym.cls is not None else e.id)
        if sym.kind == "func":
            fi = sym.func
            return I.FuncRef(self.ty(e), getattr(sym, "import_from", None) or self.module, e.id,
                             fi.owner or "" if fi is not None else "")
        if sym.kind == "stdconst":
            return I.StaticField(self.ty(e), sym.std_owner, sym.std_name)
        raw = self.name_ref(e, sym.ty)
        return self._shared_and_narrowed(raw, raw.ty, self.ty(e))

    def ex_This(self, e) -> I.Expr:
        return I.This(self.ty(e))

    def ex_Super(self, e) -> I.Expr:
        return I.This(self.ty(e))

    def ex_IntLit(self, e: A.IntLit) -> I.Expr:
        return I.Lit(self.ty(e), e.value)

    def ex_FloatLit(self, e: A.FloatLit) -> I.Expr:
        return I.Lit(self.ty(e), float(e.value))

    def ex_DurationLit(self, e: A.DurationLit) -> I.Expr:
        return I.Lit(T.DURATION, float(e.seconds) if hasattr(e, "seconds") else float(getattr(e, "value", 0)))

    def ex_StrLit(self, e: A.StrLit) -> I.Expr:
        return I.Lit(self.ty(e), e.value)

    def ex_CharLit(self, e: A.CharLit) -> I.Expr:
        return I.Lit(T.CHAR, e.value)

    def ex_BoolLit(self, e: A.BoolLit) -> I.Expr:
        return I.Lit(T.BOOL, e.value)

    def ex_NoneLit(self, e) -> I.Expr:
        return I.Lit(self.ty(e), None)

    def ex_FString(self, e: A.FString) -> I.Expr:
        parts = []
        for p in e.parts:
            if p.expr is None:
                parts.append((None, p.text, ""))
            else:
                parts.append((self.expr(p.expr), p.text if p.debug else "", p.spec))
        return I.Format(T.STRING, parts)

    def ex_ListLit(self, e: A.ListLit) -> I.Expr:
        return I.ListMake(self.ty(e), [self.store(i) for i in e.items])  # type: ignore[misc]

    def ex_SetLit(self, e: A.SetLit) -> I.Expr:
        return I.SetMake(self.ty(e), [self.expr(i) for i in e.items])

    def ex_MapLit(self, e: A.MapLit) -> I.Expr:
        return I.MapMake(self.ty(e), [(self.expr(k), self.store(v)) for k, v in e.pairs])  # type: ignore[misc]

    def ex_EmptyBraces(self, e: A.EmptyBraces) -> I.Expr:
        return I.MapMake(self.ty(e), []) if e.as_map else I.SetMake(self.ty(e), [])

    def ex_TupleLit(self, e: A.TupleLit) -> I.Expr:
        return I.TupleMake(self.ty(e), [self.store(i) for i in e.items])  # type: ignore[misc]

    def ex_Comprehension(self, e: A.Comprehension) -> I.Expr:
        """[expr for x in xs if cond] -> a new list filled by a loop (statements before the expression)."""
        res = self.temp("list", self.ty(e))
        self.pre.append(I.Let(res, I.ListMake(self.ty(e), [])))
        var = self.local_for(e.var_sym, "loop")
        saved = self.pre
        self.pre = []
        item = self.expr(e.expr)
        cond = self.expr(e.cond) if e.cond is not None else None
        inner = self.pre
        self.pre = saved
        add: list[I.Stmt] = inner + [I.ExprStmt(I.Intrinsic(T.VOID, "seq.add", I.Var(res.ty, res), {"value": item}))]
        body = I.Block([I.If(cond, I.Block(add))] if cond is not None else add)
        if isinstance(e.iterable, A.RangeExpr):
            r = self.range_value(e.iterable)
            self.pre.append(I.ForRange(var, r.start, r.end, r.inclusive, r.step, body))
        else:
            it = self.expr(e.iterable)
            kind = {"map": "map", "set": "set", "string": "string"}.get(it.ty.kind, "list")
            self.pre.append(I.ForEach([var], it, kind, body))
        return I.Var(res.ty, res)

    def bin_op(self, op: str, lt: Ty, rt: Ty) -> str:
        if op == "*" and (lt.kind == "string" or T.is_seq(lt)) and T.is_int(rt):
            return "repeat"                 # "ab" * 3, [0] * 5
        if lt.kind == "set" and op in _SET_BIN:
            return _SET_BIN[op]
        if op == "+" and lt.kind in ("string", "char") and rt.kind in ("string", "char"):
            return "concat"
        if op == "+" and T.is_seq(lt) and T.is_seq(rt):
            return "concat"
        return _BIN.get(op, op)

    def ex_Unary(self, e: A.Unary) -> I.Expr:
        return I.Unary(self.ty(e), _UN.get(e.op, e.op), self.expr(e.operand))

    def ex_Binary(self, e: A.Binary) -> I.Expr:
        left, right = self.expr(e.left), self.expr(e.right)
        return I.Binary(self.ty(e), self.bin_op(e.op, left.ty, right.ty), left, right)

    def ex_Compare(self, e: A.Compare) -> I.Expr:
        vals = [self.expr(o) for o in e.operands]
        out: Optional[I.Expr] = None
        for k, op in enumerate(e.ops):
            c = I.Compare(T.BOOL, _CMP.get(op, op), vals[k], vals[k + 1])
            out = c if out is None else I.Logical(T.BOOL, "and", out, c)
        assert out is not None
        return out

    def ex_Logical(self, e: A.Logical) -> I.Expr:
        return I.Logical(T.BOOL, e.op, self.expr(e.left), self.expr(e.right))

    def ex_IfExpr(self, e: A.IfExpr) -> I.Expr:
        return I.Cond(self.ty(e), self.expr(e.cond), self.expr(e.then), self.expr(e.else_))

    def ex_RangeExpr(self, e: A.RangeExpr) -> I.Expr:
        if e.ty is not None and T.is_seq(e.ty):
            return I.Intrinsic(e.ty, "range.toList", None, {"range": self.range_value(e)})
        return self.range_value(e)

    def ex_Coalesce(self, e: A.Coalesce) -> I.Expr:
        left = self.expr(e.left)
        default = self.store(e.right)
        assert default is not None
        if isinstance(e.left, A.Call) and getattr(e.left, "fails", None):
            return I.OrElse(self.ty(e), left, default)
        return I.Coalesce(self.ty(e), left, default)

    def ex_Propagate(self, e: A.Propagate) -> I.Expr:
        return I.Propagate(self.ty(e), self.expr(e.expr))

    def ex_Cast(self, e: A.Cast) -> I.Expr:
        return I.Cast(self.ty(e), self.expr(e.expr), e.mode)

    def ex_TypeTest(self, e: A.TypeTest) -> I.Expr:
        return I.Intrinsic(T.BOOL, "isType", self.expr(e.expr), {"type": I.TypeRef(T.ANY, e.target.name)})

    def ex_SwitchExpr(self, e: A.SwitchExpr) -> I.Expr:
        res = self.temp("v", self.ty(e))
        self.pre.append(I.Let(res, None))
        self.pre += self.match(e.switch, res)
        return I.Var(res.ty, res)

    def ex_VariantTest(self, e: A.VariantTest) -> I.Expr:
        binds = [(self.local_for(sym), k) for sym, k in e.binds]
        return I.VariantTest(T.BOOL, self.expr(e.subject), e.enum, e.variant, binds)

    def lambda_of(self, params: list[I.Local], body: I.Block, node, ty: Ty) -> I.Lambda:
        """Captures: locals of the enclosing function that the body uses. A loop variable is captured by value
        (each step its own: constructs.fx3 1d); one the body changes lives in a shared cell."""
        own = {id(p) for p in params}
        declared: set[int] = set()
        used: list[I.Local] = []
        assigned: set[int] = set()
        for n in walk(body):
            if isinstance(n, I.Let):
                declared.add(id(n.local))
            elif isinstance(n, (I.ForRange,)) and n.var is not None:
                declared.add(id(n.var))
            elif isinstance(n, I.ForEach):
                declared.update(id(v) for v in n.vars if v is not None)
            elif isinstance(n, I.Unpack):
                declared.update(id(v) for v in n.targets if v is not None)
            elif isinstance(n, I.Catch) and n.var is not None:
                declared.add(id(n.var))
            elif isinstance(n, I.Lambda):
                declared.update(id(p) for p in n.params)
            elif isinstance(n, I.Assign) and isinstance(n.target, I.Var):
                assigned.add(id(n.target.local))
            if isinstance(n, I.Var) and n.local not in used:
                used.append(n.local)
        caps = []
        for loc in used:
            if id(loc) in own or id(loc) in declared:
                continue
            if loc.mode == "loop":
                mode = "value"
            elif id(loc) in assigned:
                mode = "cell"
                loc.cell = True
            else:
                mode = "read"
            caps.append((loc, mode))
        return I.Lambda(ty, params, body, caps)

    def ex_Lambda(self, e: A.Lambda) -> I.Expr:
        params = [self.local_for(p.sym, "param") for p in e.params]
        saved = self.pre
        self.pre = []
        if isinstance(e.body, A.Block):
            body = self.block(e.body)
        else:
            v = self.store(e.body)
            ret = e.info.return_ty if getattr(e, "info", None) is not None else T.VOID
            body = I.Block(self.pre + [I.Return(v) if ret != T.VOID else I.ExprStmt(v)])  # type: ignore[arg-type]
        self.pre = saved
        return self.lambda_of(params, body, e, self.ty(e))

    def ex_ImplicitFn(self, e: A.ImplicitFn) -> I.Expr:
        params = []
        for name in e.params:
            sym = e.syms.get(name)
            params.append(self.local_for(sym, "param") if sym is not None else self._new_local(name, T.UNKNOWN))
        saved = self.pre
        self.pre = []
        v = self.expr(e.body)
        body = I.Block(self.pre + [I.Return(v)])
        self.pre = saved
        fty = Ty("func", (Ty("multi", tuple(p.ty for p in params)), v.ty))
        return self.lambda_of(params, body, e, fty)

    def ex_Member(self, e: A.Member) -> I.Expr:
        variant = getattr(e, "variant", None)
        if variant is not None:
            return I.VariantMake(self.ty(e), variant[0], variant[1])
        prop = getattr(e, "_property", None)
        if prop is not None:
            return I.Call(self.ty(e), prop.name, [], owner=prop.owner or "", recv=self.expr(e.obj), kind="method",
                          std=self._std_owner(prop.owner))
        ot = e.obj.ty
        if ot is not None and ot.kind == "tuple" and e.name.isdigit():
            return I.Index(self.ty(e), self.expr(e.obj), I.Lit(T.INT, int(e.name)))
        fi = getattr(e, "field_info", None)
        if ot is not None and ot.kind == "classref":
            return I.StaticField(self.ty(e), ot.name, e.name)
        if e.safe:
            return I.SafeField(self.ty(e), self.expr(e.obj), e.name)
        if fi is not None:
            raw = I.Field(fi.ty, self.expr(e.obj), e.name, fi.owner)
            return self._shared_and_narrowed(raw, fi.ty, self.ty(e))
        if ot is not None and ot.kind in ("result", "maybe") and e.name in ("value", "error"):
            return I.Intrinsic(self.ty(e), f"{ot.kind}.{e.name}", self.expr(e.obj))
        self.problem(f"can't lower the field `{e.name}` of {ot}", e)
        return I.Lit(self.ty(e), None)

    def ex_Index(self, e: A.Index) -> I.Expr:
        obj = self.expr(e.obj)
        if isinstance(e.index, A.RangeExpr):
            r = e.index
            return I.Slice(self.ty(e), obj, self.expr(r.start) if r.start is not None else None,
                           self.expr(r.end) if r.end is not None else None, r.inclusive,
                           self.expr(r.step) if r.step is not None else None)
        sym = getattr(e.obj, "sym", None) if isinstance(e.obj, A.Name) else None
        d = getattr(sym, "annots", {}).get("default") if sym is not None else None
        if d is not None and d.args and obj.ty.kind == "map":
            return I.Intrinsic(self.ty(e), "map.getOr", obj, {"key": self.expr(e.index), "default": self.expr(d.args[0])})
        return I.Index(self.ty(e), obj, self.expr(e.index))

    # ---- calls ----------------------------------------------------------------------------
    def _std_owner(self, owner: Optional[str]) -> bool:
        ci = self.ck.classes.get(owner or "")
        return ci is not None and (ci.annots == {"std"} or (ci.builtin and not ci.is_error))

    def call_args(self, e: A.Call, params) -> list[Optional[I.Expr]]:
        out: list[Optional[I.Expr]] = []
        for p in params:
            if getattr(p, "variadic", False):
                vals = [self.expr(v) for v in getattr(e, "_variadic", [])]
                out.append(I.ListMake(p.ty, vals))
            elif p.name in e.bound:
                v = e.bound[p.name]
                out.append(I.Copy(self.ty(v), self.expr(v)) if getattr(p, "copy", False) else self.expr(v))
            else:
                out.append(None)
        return out

    def ex_Call(self, e: A.Call) -> I.Expr:
        k = e.kind
        ty = self.ty(e)
        fails = list(getattr(e, "fails", None) or [])
        if getattr(e, "async_call", False) and not getattr(e, "_direct", False):
            e._direct = True  # type: ignore[attr-defined]
            try:
                return I.Spawn(ty, self.expr(e))
            finally:
                e._direct = False  # type: ignore[attr-defined]
        if k == "builtin":
            args = {a.name: self.expr(a.value) for a in e.args if a.name is not None}
            extra = [self.expr(a.value) for a in e.args if a.name is None]
            return I.Intrinsic(ty, str(e.target), None, args, extra)
        if k == "cast":
            return self.expr(e.target)
        if k in ("func", "static"):
            fi = e.target
            f = e.func
            module = getattr(getattr(f, "sym", None), "import_from", None) or self.module if isinstance(f, A.Name) \
                else self.module
            return I.Call(ty, fi.name, self.call_args(e, fi.params), module=module, owner=fi.owner or "",
                          fails=list(fi.fails) or fails, kind=k, std=fi.builtin and self._std_owner(fi.owner))
        if k == "value":
            return I.Call(ty, "", [self.expr(a.value) for a in e.args], recv=self.expr(e.func), kind="value")
        if k in ("method", "super"):
            fi = e.target
            assert isinstance(e.func, A.Member)
            recv = self.expr(e.func.obj)
            return I.Call(ty, fi.name, self.call_args(e, fi.params), owner=fi.owner or "", recv=recv,
                          fails=list(fi.fails) or fails, kind=k, std=self._std_owner(fi.owner))
        if k == "ctor":
            ci, chosen = e.target
            explicit = [c for c in ci.ctors if c.decl is not None]
            name = chosen.decl.name if chosen is not None and chosen.decl is not None and len(explicit) > 1 else ""
            return I.New(ty, ci.name, self.call_args(e, chosen.params) if chosen is not None else [], name)
        if k == "bank":
            return self.bank(e)
        if k == "variant":
            ci, v = e.target
            vals = [self.store(e.bound[f.name]) for f in v.fields if f.name in e.bound]
            return I.VariantMake(ty, ci.name, v.name, [x for x in vals if x is not None])
        if k == "enum_all":
            return I.Intrinsic(ty, "enum.all", None, {"enum": I.TypeRef(T.ANY, e.target.name)})
        if k == "enum_from":
            return I.Intrinsic(ty, "enum.from", None, {"enum": I.TypeRef(T.ANY, e.target.name),
                                                       "value": self.expr(e.args[0].value)})
        if k == "enum_builtin":
            assert isinstance(e.func, A.Member)
            return I.Intrinsic(ty, f"enum.{e.target}", self.expr(e.func.obj))
        if k == "spawn":
            inner = e.args[0].value
            return self.expr(inner) if getattr(inner, "async_call", False) else I.Spawn(ty, self.expr(inner))
        if k in ("await", "run"):
            inner = e.args[0].value
            if getattr(inner, "async_call", False):
                inner._direct = True  # type: ignore[attr-defined]     # await f(x): run it here
                try:
                    return self.expr(inner)
                finally:
                    inner._direct = False  # type: ignore[attr-defined]
            v = self.expr(inner)
            return I.Await(ty, v, fails) if v.ty.kind == "task" else v
        if k in ("all", "any"):
            return I.Intrinsic(ty, f"task.{k}", None, extra=[self.expr(a.value) for a in e.args])
        if k == "chan":
            assert isinstance(e.func, A.Member)
            args = {"value": self.store(e.args[0].value)} if e.args else {}
            return I.Intrinsic(ty, f"channel.{e.target}", self.expr(e.func.obj), args)  # type: ignore[arg-type]
        if k == "task_method":
            assert isinstance(e.func, A.Member)
            return I.Intrinsic(ty, f"task.{e.target}", self.expr(e.func.obj))
        if k == "service_get":
            return I.ServiceGet(ty, e.target.name)
        self.problem(f"can't lower this call (kind {k!r}) yet", e)
        return I.Lit(ty, None)

    def bank(self, e: A.Call) -> I.Expr:
        assert isinstance(e.func, A.Member)
        recv_t: Ty = getattr(e, "recv_ty", None) or self.ty(e.func.obj)
        inner_t = recv_t.args[0] if recv_t.kind == "maybe" else recv_t
        kind = T.receiver_kind(inner_t) or inner_t.kind
        args: dict[str, I.Expr] = {}
        for name, v in e.bound.items():
            if isinstance(v, A.RangeExpr):
                args[name] = self.range_value(v)
            elif name in _STORED_ARGS:
                args[name] = self.store(v)   # type: ignore[assignment]
            else:
                args[name] = self.expr(v)
        recv = self.expr(e.func.obj)
        op = f"{kind}.{e.target}"
        if e.func.safe:                      # x?.size(): none when x is none
            tmp = self.temp("r", recv.ty)
            self.pre.append(I.Let(tmp, recv))
            call = I.Intrinsic(self.ty(e).args[0] if self.ty(e).kind == "maybe" else self.ty(e), op,
                               I.Unwrap(inner_t, I.Var(recv.ty, tmp)), args)
            return I.Cond(self.ty(e), I.Compare(T.BOOL, "ne", I.Var(recv.ty, tmp), I.Lit(T.NONE, None)), call,
                          I.Lit(T.NONE, None))
        return I.Intrinsic(self.ty(e), op, recv, args)


def walk(node):
    """Every IR node under `node` (statements, expressions, blocks, arms, catches, select cases)."""
    stack = [node]
    seen: set[int] = set()
    while stack:
        n = stack.pop()
        if id(n) in seen:
            continue
        seen.add(id(n))
        yield n
        children: list = []
        for v in vars(n).values():
            for it in (v if isinstance(v, (list, tuple)) else [v]):
                if isinstance(it, tuple):
                    children += [x for x in it if _is_ir(x)]
                elif isinstance(it, dict):
                    children += [x for x in it.values() if _is_ir(x)]
                elif _is_ir(it):
                    children.append(it)
            if isinstance(v, dict):
                children += [x for x in v.values() if _is_ir(x)]
        stack += reversed(children)          # visit in source order (captures, listings are deterministic)


def _is_ir(x) -> bool:
    return isinstance(x, (I.Expr, I.Stmt, I.Arm, I.Catch, I.SelectCase))


def lower(result) -> list[I.Module]:
    """The IR of a compiled project (driver.compile_source result, check_only is enough): dependencies first."""
    project = result.project
    if project is None:
        return []
    mods = project.order
    out = []
    problems: list[str] = []
    for m in mods:
        lw = Lowerer(m.checker, m.name if m is not project.entry else "", m.path)
        out.append(lw.lower_program(m.program))
        problems += lw.problems
    if problems:
        raise LowerError("\n".join(problems))
    return out
