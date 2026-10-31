"""Checker: names, types, canonical forms and the deterministic checks.

After `check()` every expression has `.ty`, every name has `.sym`, every call
knows what it calls, aliases are replaced by canonical names, English
conditions are ordinary expressions, and implicit-variable arguments
(currVal, index, ...) are wrapped in ImplicitFn. That checked tree is the IR
the backends consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import nodes as A
from . import fxtypes as T
from .aliases import (
    ALL_ANNOTATIONS, INT_RANGES, METHOD_KIND, METHOD_PARAMS, RESERVED, canonical_annotation,
    canonical_arg, canonical_collection_type, canonical_simple_type, lookup_method, method_spellings,
    norm, typo_match, IMPLICIT_VARS,
)
from .diagnostics import Diagnostics, Span
from .english import parse_condition
from .fxtypes import ClassInfo, FieldInfo, FuncInfo, ParamInfo, Ty
from . import stdlib as STD

AUTO = Ty("auto")       # "infer from the value"
NUMBER = Ty("number")   # int or float, from the value


@dataclass(eq=False)
class Symbol:
    name: str
    kind: str                      # var | param | func | class | implicit | loopvar | catchvar
    ty: Ty
    span: Span
    const: bool = False
    typesafe: bool = False
    used: bool = False
    annots: dict = field(default_factory=dict)      # canonical name -> Annotation
    module_level: bool = False
    shadows: bool = False
    is_ref: bool = False
    func: Optional[FuncInfo] = None
    cls: Optional[ClassInfo] = None
    decl_depth: int = 0           # block depth inside its function, for type-change rules
    owner_fn: object = None       # FuncInfo or None (module)


class Scope:
    def __init__(self, parent: Optional[Scope], kind: str, fn: object = None):
        self.parent = parent
        self.kind = kind           # module | function | block | implicit | class
        self.syms: dict[str, Symbol] = {}
        self.narrowed: dict[str, Ty] = {}
        self.fn = fn if fn is not None else (parent.fn if parent else None)

    def lookup(self, name: str) -> tuple[Optional[Symbol], Optional[Ty]]:
        s: Optional[Scope] = self
        narrowed: Optional[Ty] = None
        while s is not None:
            if narrowed is None and name in s.narrowed:
                narrowed = s.narrowed[name]
            if name in s.syms:
                sym = s.syms[name]
                return sym, narrowed if narrowed is not None else sym.ty
            s = s.parent
        return None, None


BUILTIN_ERRORS = {
    "Error": None, "ValueError": "Error", "ParseError": "ValueError", "IOError": "Error",
    "FileNotFound": "IOError", "PermissionDenied": "IOError", "NetworkError": "Error",
    "TimeoutError": "NetworkError", "ConnectionError": "NetworkError", "FormatError": "Error",
    **STD.STD_ERRORS,
}

PRELUDE = {"print", "input", "len", "min", "max", "abs", "round", "floor", "ceil", "sum", "sorted",
           "range", "panic", "ensure", "assert", "unreachable", "string", "str", "exit", "crash",
           "abort", "sleep"}

# Annotations that are accepted but not enforced by this version.
_NOT_ENFORCED_VAR = {"static", "global", "temp", "lazy", "atomic", "threadlocal", "smart", "sorted",
                     "unique", "ordered", "maxsize", "capacity", "min", "max", "wrapping",
                     "saturating", "unchecked", "comptime", "nonzero", "unsigned"}
_VAR_ANNOTS = _NOT_ENFORCED_VAR | {"const", "typesafe", "range", "unused", "optional", "size", "init",
                                   "default", "using", "mut", "public", "private", "protected", "weak"}
_FUNC_ANNOTS = {"pure", "inline", "memo", "async", "public", "private", "protected", "entry", "example",
                "deprecated", "test", "recursive", "fails", "constructor", "destructor", "style",
                "override", "abstract", "static", "property", "const", "unsafe", "generic"}
_CLASS_ANNOTS = {"open", "abstract", "error", "derive", "value", "immutable", "const", "singleton",
                 "generic", "public", "private"}


class Checker:
    def __init__(self, program: A.Program, diags: Diagnostics, project: Optional[dict] = None):
        """`project`: the modules this file may import, already checked ({module name: project.Module}).
        Import statements of project modules carry `.target` (their module name), set by project.py."""
        self.prog = program
        self.d = diags
        self.module = Scope(None, "module")
        self.scope = self.module
        self.classes: dict[str, ClassInfo] = {}
        self.functions: dict[str, FuncInfo] = {}
        self.cur_fn: Optional[FuncInfo] = None
        self.cur_class: Optional[ClassInfo] = None
        self.in_ctor = False
        self.loop_depth = 0
        self.try_depth = 0
        self.block_depth = 0
        self.unassigned: set[Symbol] = set()
        self.return_types: Optional[list[list[Ty]]] = None    # while inferring a signature
        self.lambda_depth = 0
        self.entry: Optional[FuncInfo] = None
        self.tests: list[FuncInfo] = []
        self.examples: list[tuple[FuncInfo, list]] = []
        self.pending_style: Optional[str] = None
        self.std_visible: set[str] = set()          # standard-library names brought in by imports
        self.std_modules: set[str] = set()          # modules imported (the backend needs them)
        self._installing_std = False
        self.project = project or {}
        self.foreign_classes: dict[str, str] = {}    # class name -> the project module that defines it
        self.imported_classes: set[str] = set()      # ... of those, the ones this file imports
        T.CLASS_PARENTS.clear()
        self._install_builtins()
        self._install_stdlib()
        self._install_project_classes()

    # ==================================================================
    # Set-up
    # ==================================================================
    def _install_builtins(self) -> None:
        for name, parent in BUILTIN_ERRORS.items():
            ci = ClassInfo(name, parent, is_error=True, builtin=True)
            ci.fields["message"] = FieldInfo("message", T.STRING, private=False, owner=name, has_default=True)
            ctor = FuncInfo(name, [ParamInfo("message", T.STRING, has_default=True)], [], is_ctor=True,
                            owner=name, builtin=True)
            ci.ctors.append(ctor)
            ci.annots.add("open")
            self.classes[name] = ci
            T.CLASS_PARENTS[name] = parent
            self.module.syms[name] = Symbol(name, "class", T.class_ty(name), Span("<builtin>", 0, 0),
                                            cls=ci, module_level=True, used=True)

    def _install_stdlib(self) -> None:
        """Build ClassInfo / FuncInfo for every standard-library module and type (stdlib.py)."""
        from .lexer import tokenize
        from .parser import Parser
        std_types = [t for types in STD.MODULES.values() for t in types]
        for t in std_types:
            ci = ClassInfo(t.name, None, builtin=True)
            ci.annots = {"std"}
            ci.derive = set(t.derive)
            ci.std_module = STD.TYPE_MODULE[t.name]  # type: ignore[attr-defined]
            self.classes[t.name] = ci
        self._installing_std = True
        try:
            for t in std_types:
                ci = self.classes[t.name]
                for fname, tystr in t.fields.items():
                    ci.fields[fname] = FieldInfo(fname, self._std_type(tystr), private=False, const=True,
                                                 owner=t.name)
                for cname, tystr in t.constants.items():
                    ci.fields[cname] = FieldInfo(cname, self._std_type(tystr), private=False, const=True,
                                                 static=True, owner=t.name, has_default=True)
                for m in t.members:
                    params_src, _, ret_src = m.sig.partition("->")
                    p = Parser(tokenize(params_src.strip(), f"<std {t.name}.{m.name}>"), "<std>")
                    p.class_names.add("handler")          # function[<Request>|<Response>]
                    params = p.parse_params()
                    fi = FuncInfo(m.name, owner=t.name, static=m.static, builtin=True)
                    for prm in params:
                        pty = self._std_type_expr(prm.type)
                        is_ref = any(a.name == "ref" for a in prm.annots)
                        fi.params.append(ParamInfo(prm.name, T.vector(pty) if prm.variadic else pty,
                                                   has_default=prm.default is not None, variadic=prm.variadic,
                                                   ref=is_ref))
                    ret = ret_src.strip()
                    fi.returns = [] if ret in ("", "void") else [self._std_type(ret)]
                    fi.fails = list(m.fails)
                    fi.infer = m.infer  # type: ignore[attr-defined]
                    fi._checked = True  # type: ignore[attr-defined]
                    fi.inferred = True
                    ci.methods[m.name] = fi
        finally:
            self._installing_std = False

    def _install_project_classes(self) -> None:
        """Classes of every module checked before this one: values of those types can reach this file
        (a function's result), but only imported classes can be named here."""
        for mod in self.project.values():
            for name, ci in mod.checker.classes.items():
                if ci.builtin or name in mod.checker.foreign_classes:
                    continue
                self.classes[name] = ci
                self.foreign_classes[name] = mod.name
                T.CLASS_PARENTS[name] = ci.parent

    def _std_type(self, text: str) -> Ty:
        from .lexer import tokenize
        from .parser import Parser
        return self._std_type_expr(Parser(tokenize(text, "<std>"), "<std>").parse_type())

    def _std_type_expr(self, te: Optional[A.TypeExpr]) -> Ty:
        if te is None:
            return T.ANY
        if te.name == "handler":
            return Ty("func", (Ty("multi", (T.class_ty("Request"),)), T.class_ty("Response")))
        return self.resolve_type(te, allow_auto=False)

    def import_std(self, s: A.ImportStmt) -> None:
        mod = s.module
        if mod not in STD.MODULES:
            guess = typo_match(mod, list(STD.MODULES) + ["General"])
            self.err("FX0310", f"there is no module `{mod}`", s.span,
                     fix=f"did you mean `{guess}`?" if guess else
                         f"the standard modules are: {', '.join(sorted(STD.MODULES))}")
            return
        self.std_modules.add(mod)
        types = STD.MODULES[mod]
        for t in types:
            self.std_visible.add(t.name)
        # the types its functions use come along too (Db returns Json values, so Json is usable)
        for name in STD.types_used_by(mod):
            self.std_visible.add(name)
            self.std_modules.add(STD.TYPE_MODULE[name])
            if name not in self.module.syms:
                self.module.syms[name] = Symbol(name, "class", T.class_ty(name), s.span, cls=self.classes[name],
                                                module_level=True, used=True)
        module_ci = self.classes[mod]

        def bind(name: str, sym: Symbol) -> None:
            if name in self.module.syms and self.module.syms[name].kind != "class":
                self.err("FX0304", f"`{name}` is already defined", s.span)
                return
            sym.module_level = True
            sym.used = True
            self.module.syms[name] = sym

        if not s.names and not s.all:
            bind(s.alias or mod, Symbol(s.alias or mod, "class", T.class_ty(mod), s.span, cls=module_ci))
            for t in types[1:]:
                bind(t.name, Symbol(t.name, "class", T.class_ty(t.name), s.span, cls=self.classes[t.name]))
            return
        wanted = [(n, a) for n, a in s.names]
        if s.all:
            wanted = [(m, None) for m in module_ci.methods if module_ci.methods[m].static] + \
                     [(f, None) for f, fi in module_ci.fields.items() if fi.static] + \
                     [(t.name, None) for t in types]
        for name, alias in wanted:
            local = alias or name
            if name in self.classes and self.classes[name].annots == {"std"} and \
                    STD.TYPE_MODULE.get(name) == mod:
                bind(local, Symbol(local, "class", T.class_ty(name), s.span, cls=self.classes[name]))
                continue
            fn = module_ci.methods.get(name)
            if fn is not None and fn.static:
                bind(local, Symbol(local, "func", Ty("func"), s.span, func=fn))
                continue
            fld = module_ci.fields.get(name)
            if fld is not None and fld.static:
                sym = Symbol(local, "stdconst", fld.ty, s.span, const=True)
                sym.std_owner = mod  # type: ignore[attr-defined]
                sym.std_name = name  # type: ignore[attr-defined]
                bind(local, sym)
                continue
            names = [m for m, f in module_ci.methods.items() if f.static] + \
                    [f for f, i in module_ci.fields.items() if i.static] + [t.name for t in types]
            guess = typo_match(name, names)
            self.err("FX0311", f"module {mod} has no `{name}`", s.span,
                     fix=f"did you mean `{guess}`?" if guess else None)

    def import_project(self, s: A.ImportStmt) -> None:
        """`from ./helpers import clean;`, `import geo.shapes as g;` (modules.fx3 sections 1-2)."""
        mod = self.project[s.target]  # type: ignore[attr-defined]
        exports = mod.exports()

        def bind(local: str, name: str) -> None:
            sym = exports[name]
            if sym.kind == "class":
                self.imported_classes.add(name)
            if local in self.module.syms:
                self.err("FX0304", f"`{local}` is already defined", s.span,
                         fix=f"import it under another name: `{name} as ...`")
                return
            new = Symbol(local, sym.kind, sym.ty, s.span, const=sym.const, func=sym.func, cls=sym.cls,
                         module_level=True, used=True)
            new.import_from = mod.name  # type: ignore[attr-defined]
            new.import_name = name  # type: ignore[attr-defined]
            self.module.syms[local] = new

        if not s.names and not s.all:
            # `import geo.shapes;` -> geo.shapes.f(...) is not supported yet: name what you use
            self.err("FX0316", f"import names from `{s.module}`, not the whole module", s.span,
                     fix=f"write `from {s.module} import name1, name2;` (or `import @all`)")
            return
        if s.all:
            for name in exports:
                bind(name, name)
            return
        for name, alias in s.names:
            if name in exports:
                bind(alias or name, name)
                continue
            hidden = mod.hidden(name)
            if hidden:
                self.err("FX0317", f"`{name}` in {mod.name} can't be imported: {hidden}", s.span)
                continue
            guess = typo_match(name, list(exports))
            self.err("FX0311", f"module {mod.name} has no `{name}`", s.span,
                     fix=f"did you mean `{guess}`?" if guess else
                         (f"it has: {', '.join(sorted(exports))}" if exports else None))

    def _std_infer(self, fi: FuncInfo, e: A.Call) -> Ty:
        rule = getattr(fi, "infer", None)
        args = list(e.bound.values())
        if rule == "first_arg" and args and args[0].ty is not None:
            return args[0].ty
        if rule == "element" and args and args[0].ty is not None:
            t = args[0].ty
            if T.is_seq(t) or t.kind == "set":
                return T.elem(t)
            if t.kind == "map":
                return t.args[0]
            if not T.is_unknown(t):
                self.err("FX0478", f"{fi.owner}.{fi.name} needs a list, a set or a map, not {t}", e.span)
        return fi.return_ty

    # ==================================================================
    # Entry point
    # ==================================================================
    def check(self) -> None:
        stmts = self.prog.stmts
        # pass 0: imports (they must come first: modules.fx3 section 2)
        seen_other = False
        for s in stmts:
            if isinstance(s, A.ImportStmt):
                if seen_other:
                    self.err("FX0312", "imports go at the top of the file, before anything else", s.span)
                if getattr(s, "target", None) is not None:
                    self.import_project(s)
                elif getattr(s, "missing", False):
                    pass                 # project.py already said so
                elif s.module != "General":
                    self.import_std(s)
            else:
                seen_other = True
        # pass 1: declare classes and functions (they can be used before they are written)
        for s in stmts:
            if isinstance(s, A.ClassDecl):
                self.declare_class(s)
        for s in stmts:
            if isinstance(s, A.ClassDecl):
                self.fill_class(s)
        for s in stmts:
            if isinstance(s, A.FuncDecl):
                self.declare_function(s, owner=None)
        # pass 2: check everything in order
        new: list[A.Stmt] = []
        for s in stmts:
            r = self.check_stmt(s)
            if r is not None:
                new.append(r)
        self.prog.stmts = new
        # function and method bodies are checked after the top-level code, so they
        # can use any top-level variable
        for fi in list(self.functions.values()):
            self.check_function_body(fi)
        for name, ci in self.classes.items():
            if not ci.builtin and name not in self.foreign_classes:
                self.check_class_bodies(ci)
        self._unused_warnings(self.module)

    # ==================================================================
    # Helpers
    # ==================================================================
    def err(self, code: str, msg: str, span: Span, fix: str | None = None) -> None:
        self.d.error(code, msg, span, fix)

    def warn(self, code: str, msg: str, span: Span, fix: str | None = None) -> None:
        self.d.warning(code, msg, span, fix)

    def push(self, kind: str, fn: object = None) -> Scope:
        self.scope = Scope(self.scope, kind, fn)
        return self.scope

    def pop(self) -> None:
        self._unused_warnings(self.scope)
        assert self.scope.parent is not None
        self.scope = self.scope.parent

    def _unused_warnings(self, scope: Scope) -> None:
        for sym in scope.syms.values():
            if sym.kind in ("var",) and not sym.used and not sym.module_level and \
                    "unused" not in sym.annots and not sym.name.startswith("_"):
                self.warn("FX0306", f"`{sym.name}` is never used", sym.span,
                          fix="remove it, or mark it `@unused`")

    def declare(self, name: str, sym: Symbol, span: Span) -> Symbol:
        if name in RESERVED:
            self.err("FX0303", f"`{name}` is a keyword and can't be used as a name", span,
                     fix=f"pick another name, or write `{name}` in backticks")
        if name in self.scope.syms and self.scope.kind != "module" or \
                (self.scope.kind == "module" and name in self.scope.syms and
                 self.scope.syms[name].kind not in ("func", "class")):
            old = self.scope.syms[name]
            if old.kind in ("func", "class"):
                self.err("FX0304", f"`{name}` is already defined as a {old.kind}", span)
            else:
                self.warn("FX0305", f"`{name}` is declared again in the same scope (shadowing)", span)
        # does it hide a live variable of the same function? (backend renames it)
        outer, _ = (self.scope.parent.lookup(name) if self.scope.parent else (None, None))
        if outer is not None and outer.kind in ("var", "param", "loopvar", "catchvar", "implicit") and \
                outer.owner_fn is self.scope.fn:
            sym.shadows = True
        sym.module_level = self.scope.kind == "module"
        sym.owner_fn = self.scope.fn
        sym.decl_depth = self.block_depth
        self.scope.syms[name] = sym
        return sym

    # ---- types --------------------------------------------------------
    def resolve_type(self, te: Optional[A.TypeExpr], allow_auto: bool = True) -> Ty:
        if te is None:
            return AUTO
        ty = self._resolve_type_inner(te, allow_auto)
        if te.optional:
            ty = T.maybe(ty)
        return ty

    def _resolve_type_inner(self, te: A.TypeExpr, allow_auto: bool) -> Ty:
        name = te.name
        simple = canonical_simple_type(name)
        if simple is not None and not te.args:
            if simple == "auto":
                if not allow_auto:
                    self.err("FX0402", "`auto` can't be used here; write the type", te.span)
                    return T.UNKNOWN
                return AUTO
            if simple == "number":
                return NUMBER
            if simple == "any":
                return T.ANY
            if simple == "string":
                return T.STRING
            return Ty(simple)
        coll = canonical_collection_type(name)
        if coll is not None:
            args = [self.resolve_type(a) for a in te.args]
            for a in args:
                if a in (AUTO, NUMBER):
                    self.err("FX0402", "inner types must be written out (not auto)", te.span)
                    return T.UNKNOWN
            strict_inner = [any(canonical_annotation(x.name) == "typesafe" for x in a.annots) for a in te.args]
            if coll == "list":
                if not args:
                    return T.list_of()
                if strict_inner[0]:
                    return T.vector(args[0])            # list<int @typesafe> == vector<int>
                return T.list_of(args[0], strict=False)
            if coll in ("vector", "array"):
                if len(args) != 1:
                    self.err("FX0403", f"{coll} needs exactly one inner type, e.g. {coll}<int>", te.span)
                    return T.UNKNOWN
                return T.vector(args[0])
            if coll == "set":
                if len(args) != 1:
                    self.err("FX0403", "set needs one inner type, e.g. set<int>", te.span)
                    return T.UNKNOWN
                return T.set_of(args[0])
            if coll == "map":
                if len(args) == 0:
                    return T.map_of(T.ANY, T.ANY)
                if len(args) != 2:
                    self.err("FX0403", "map needs a key and a value type, e.g. map<string, int>", te.span)
                    return T.UNKNOWN
                return T.map_of(args[0], args[1])
            if coll == "tuple":
                if norm(name) == "pair" and len(args) != 2:
                    self.err("FX0403", "pair needs exactly two types", te.span)
                return T.tuple_of(*args)
            if coll == "maybe":
                if len(args) != 1:
                    self.err("FX0403", "maybe needs one type, e.g. maybe<int> (or int?)", te.span)
                    return T.UNKNOWN
                return T.maybe(args[0])
            if coll == "result":
                return T.result_of(args[0] if args else T.VOID)
        if name in self.classes:
            ci = self.classes[name]
            if name in self.foreign_classes and name not in self.imported_classes:
                mod = self.foreign_classes[name]
                self.err("FX0315", f"`{name}` comes from module {mod}", te.span,
                         fix=f"add `from {self.project[mod].import_path(self.prog.file)} import {name};` "
                             "at the top of the file")
                self.imported_classes.add(name)      # say it once
            if ci.annots == {"std"} and not self._installing_std and name not in self.std_visible:
                mod = STD.TYPE_MODULE[name]
                self.err("FX0313", f"`{name}` comes from the {mod} module", te.span,
                         fix=f"add `import {mod};` at the top of the file")
            return T.class_ty(name)
        if simple is not None and te.args:
            self.err("FX0403", f"`{name}` doesn't take type arguments", te.span)
            return T.UNKNOWN
        # unknown spelling: typo match against built-in types and classes
        candidates = ["int", "float", "bool", "char", "string", "auto", "any", "list", "vector",
                      "array", "set", "map", "tuple", "i8", "i16", "i32", "u8", "u16", "u32", "u64",
                      "f32", *self.classes.keys()]
        guess = typo_match(name, candidates)
        if guess is not None:
            self.warn("FX0404", f"unknown type `{name}`; using `{guess}`", te.span,
                      fix=f"write `{guess}`")
            te.name = guess
            return self._resolve_type_inner(te, allow_auto)
        self.err("FX0405", f"unknown type `{name}`", te.span,
                 fix="use a built-in type (int, float, string, vector<T>, ...) or define a class")
        return T.UNKNOWN

    def canon_annotations(self, annots: list[A.Annotation], allowed: set[str], where: str) -> dict:
        out: dict[str, A.Annotation] = {}
        for a in annots:
            c = canonical_annotation(a.name)
            if c is None:
                guess = typo_match(a.name, ALL_ANNOTATIONS)
                if guess is not None and guess in allowed:
                    self.warn("FX0501", f"unknown annotation `@{a.name}`; using `@{guess}`", a.span,
                              fix=f"write `@{guess}`")
                    c = guess
                else:
                    self.err("FX0502", f"unknown annotation `@{a.name}`", a.span)
                    continue
            if c not in allowed:
                self.err("FX0503", f"`@{c}` can't be used on {where}", a.span)
                continue
            if c in out and c != "example":
                self.warn("FX0504", f"`@{c}` is written twice", a.span)
            a.name = c
            out[c] = a
        return out

    # ==================================================================
    # Classes
    # ==================================================================
    def declare_class(self, cd: A.ClassDecl) -> None:
        if cd.name in self.foreign_classes:
            self.err("FX0318", f"class `{cd.name}` is also defined in module {self.foreign_classes[cd.name]}",
                     cd.span, fix="class names must be unique in a project: rename one of them")
            return
        if cd.name in self.classes:
            self.err("FX0701", f"class `{cd.name}` is defined twice", cd.span)
            return
        ci = ClassInfo(cd.name, cd.parent, decl=cd)
        cd.info = ci
        self.classes[cd.name] = ci
        T.CLASS_PARENTS[cd.name] = cd.parent
        self.module.syms[cd.name] = Symbol(cd.name, "class", T.class_ty(cd.name), cd.span, cls=ci,
                                           module_level=True, used=True)

    def fill_class(self, cd: A.ClassDecl) -> None:
        ci: ClassInfo = cd.info
        if ci is None or getattr(ci, "_filled", False):
            return
        ci._filled = True  # type: ignore[attr-defined]
        annots = self.canon_annotations(cd.annots, _CLASS_ANNOTS, "a class")
        ci.annots = set(annots)
        if "derive" in annots:
            for a in annots["derive"].args:
                if isinstance(a, A.Name):
                    if a.id not in ("eq", "hash", "print", "order", "copy", "json"):
                        self.err("FX0702", f"`{a.id}` can't be derived", a.span,
                                 fix="use eq, hash, print, order, copy or json")
                    ci.derive.add(a.id)
        if cd.implements:
            self.err("FX1001", "interfaces are not supported yet", cd.span)
        if "error" in annots and cd.parent is None:
            cd.parent = ci.parent = "Error"
            T.CLASS_PARENTS[cd.name] = "Error"
        if ci.parent is not None:
            if ci.parent not in self.classes:
                self.err("FX0703", f"unknown parent class `{ci.parent}`", cd.span)
                ci.parent = None
            else:
                p = self.classes[ci.parent]
                if not p.builtin and p.decl is not None and p is not ci:
                    self.fill_class(p.decl)      # the parent first
                if not (p.is_open() or p.is_error):
                    self.err("FX0704", f"`{ci.parent}` is not @open, so it can't be inherited from", cd.span,
                             fix=f"add @open to class {ci.parent}")
                if p.is_error:
                    ci.is_error = True
        if "error" in annots:
            ci.is_error = True
        immutable = "immutable" in annots or "const" in annots
        for vd in cd.fields:
            fa = self.canon_annotations(vd.annots, _VAR_ANNOTS, "a field")
            fty = self.resolve_type(vd.type, allow_auto=False)
            for item in vd.items:
                if item.name in ci.fields:
                    self.err("FX0705", f"field `{item.name}` is declared twice", item.span)
                ci.fields[item.name] = FieldInfo(
                    item.name, fty, private="public" not in fa, const=("const" in fa or immutable),
                    static=("static" in fa), has_default=item.init is not None or fty.kind == "maybe"
                    or T.is_collection(fty) and fty.kind != "tuple", owner=ci.name)
        for fd in cd.methods:
            fi = self.declare_function(fd, owner=ci)
            if fi is None:
                continue
            if fi.is_ctor:
                ci.ctors.append(fi)
            else:
                if fd.name in ci.methods:
                    self.err("FX0706", f"method `{fd.name}` is defined twice", fd.span)
                ci.methods[fd.name] = fi
        # parent methods: @override rules
        if ci.parent:
            for mname, mi in ci.methods.items():
                pm = self.find_method(self.classes[ci.parent], mname)
                has_override = "override" in mi.annots
                if pm is not None and not has_override:
                    self.err("FX0707", f"`{mname}` replaces the parent's method; mark it @override",
                             mi.decl.span, fix="add @override")
                if pm is None and has_override:
                    self.err("FX0708", f"`{mname}` is marked @override but the parent has no `{mname}`",
                             mi.decl.span)
        abstract_left = [m for m in self._all_methods(ci).values() if m.decl is not None and m.decl.body is None]
        if abstract_left and "abstract" not in ci.annots:
            self.err("FX0709", f"class `{ci.name}` leaves `{abstract_left[0].name}` without a body", cd.span,
                     fix="implement it, or mark the class @abstract")
        # generated constructor: all non-static fields, parent fields first
        if not ci.ctors:
            params = [ParamInfo(f.name, f.ty, has_default=f.has_default)
                      for f in self._all_fields(ci).values() if not f.static]
            ci.ctors.append(FuncInfo(ci.name, params, [], is_ctor=True, owner=ci.name, builtin=True))

    def _all_fields(self, ci: ClassInfo) -> dict[str, FieldInfo]:
        chain: list[ClassInfo] = []
        c: Optional[ClassInfo] = ci
        while c is not None:
            chain.append(c)
            c = self.classes.get(c.parent) if c.parent else None
        out: dict[str, FieldInfo] = {}
        for c in reversed(chain):
            out.update(c.fields)
        return out

    def _all_methods(self, ci: ClassInfo) -> dict[str, FuncInfo]:
        chain: list[ClassInfo] = []
        c: Optional[ClassInfo] = ci
        while c is not None:
            chain.append(c)
            c = self.classes.get(c.parent) if c.parent else None
        out: dict[str, FuncInfo] = {}
        for c in reversed(chain):
            out.update(c.methods)
        return out

    def find_field(self, ci: ClassInfo, name: str) -> Optional[FieldInfo]:
        return self._all_fields(ci).get(name)

    def find_method(self, ci: ClassInfo, name: str) -> Optional[FuncInfo]:
        return self._all_methods(ci).get(name)

    def check_class_bodies(self, ci: ClassInfo) -> None:
        cd: A.ClassDecl = ci.decl
        saved = self.cur_class
        self.cur_class = ci
        # field defaults
        for vd in cd.fields:
            for item in vd.items:
                if item.init is not None:
                    fi = ci.fields[item.name]
                    t = self.expr(item.init, fi.ty)
                    self.require_assignable(t, fi.ty, item.init.span, f"field `{item.name}`")
        for fd in cd.methods:
            if fd.info is not None:
                self.check_function_body(fd.info)
        self.cur_class = saved

    # ==================================================================
    # Functions
    # ==================================================================
    def declare_function(self, fd: A.FuncDecl, owner: Optional[ClassInfo]) -> Optional[FuncInfo]:
        annots = self.canon_annotations(fd.annots, _FUNC_ANNOTS, "a function")
        fi = FuncInfo(fd.name, decl=fd, owner=owner.name if owner else None)
        fi.annots = set(annots)
        fi.static = "static" in annots
        fi.private = "private" in annots
        fi.is_ctor = "constructor" in annots
        fd.info = fi
        if "style" in annots and annots["style"].args:
            a0 = annots["style"].args[0]
            if isinstance(a0, (A.StrLit, A.CharLit)):     # 'c' is a char literal
                fi.style = a0.value
        if "generic" in annots:
            self.err("FX1001", "generic functions are not supported yet", fd.span)
        if "async" in annots:
            self.err("FX1001", "@async functions are not supported yet", fd.span)
        # parameters
        sig_in = fd.sig.inputs if fd.sig else None
        if sig_in is not None and len(sig_in) != len(fd.params) and fd.body is not None:
            self.err("FX0601", f"the signature lists {len(sig_in)} input(s) but `{fd.name}` has "
                     f"{len(fd.params)} parameter(s)", fd.span)
        for k, p in enumerate(fd.params):
            pty: Ty
            if p.type is not None:
                pty = self.resolve_type(p.type, allow_auto=False)
                if sig_in is not None and k < len(sig_in):
                    sty = self.resolve_type(sig_in[k], allow_auto=False)
                    if sty != pty and not T.is_unknown(sty) and not T.is_unknown(pty):
                        self.err("FX0602", f"parameter `{p.name}` is {pty} but the signature says {sty}",
                                 p.span)
            elif sig_in is not None and k < len(sig_in):
                pty = self.resolve_type(sig_in[k], allow_auto=False)
            else:
                self.err("FX0603", f"parameter `{p.name}` needs a type", p.span,
                         fix=f"write e.g. `int {p.name}`, or give the function a signature")
                pty = T.UNKNOWN
            pa = self.canon_annotations(p.annots, {"ref", "const", "move", "copy"}, "a parameter")
            is_ref = "ref" in pa
            if is_ref and not (T.is_collection(pty) or pty.kind == "class"):
                self.err("FX1001", "@ref on simple values (int, string, ...) is not supported yet", p.span)
            if p.variadic:
                pty_store = T.vector(pty)
            else:
                pty_store = pty
            fi.params.append(ParamInfo(p.name, pty_store, has_default=p.default is not None,
                                       variadic=p.variadic, ref=is_ref))
        # outputs
        if fd.sig is not None:
            fi.returns = [self.resolve_type(t, allow_auto=False) for t in fd.sig.outputs]
            if fi.is_ctor and fi.returns:
                self.err("FX0604", "a constructor returns nothing; its signature must end with |<>]", fd.span)
        elif isinstance(fd.body, A.ActionBody):
            self.err("FX0905", "an Action body needs a signature (the fixed contract for the generated code)",
                     fd.span)
        else:
            fi.inferred = False   # inferred later, from the body
        # fails
        if "fails" in annots:
            for a in annots["fails"].args:
                if isinstance(a, A.Name) and a.id in self.classes and self.classes[a.id].is_error:
                    fi.fails.append(a.id)
                else:
                    self.err("FX0605", "@fails(...) takes error class names, e.g. @fails(ParseError)",
                             a.span)
        if "entry" in annots:
            if self.entry is not None:
                self.err("FX0606", "only one function can be @entry", fd.span)
            self.entry = fi
            if fi.params:
                self.err("FX0607", "the @entry function takes no parameters (use Env.args() later)", fd.span)
        if "test" in annots:
            self.tests.append(fi)
        if "example" in annots:
            pass
        if owner is None:
            if fd.name in self.functions or fd.name in self.module.syms:
                self.err("FX0608", f"`{fd.name}` is defined twice", fd.span)
            self.functions[fd.name] = fi
            self.module.syms[fd.name] = Symbol(fd.name, "func", Ty("func"), fd.span, func=fi,
                                               module_level=True, used=True)
        if fd.body is None and owner is None:
            self.err("FX0609", f"function `{fd.name}` has no body", fd.span)
        if fd.body is None and owner is not None and "abstract" not in annots:
            fi.annots.add("abstract")
        return fi

    def ensure_signature(self, fi: FuncInfo, span: Span) -> None:
        """Make sure a function without a written signature has its return types inferred."""
        fd: A.FuncDecl = fi.decl
        if fd is None or fd.sig is not None or fi.inferred:
            return
        if fi.inferring:
            self.err("FX0610", f"`{fi.name}` calls itself, so its return type can't be inferred", span,
                     fix=f"give `{fi.name}` a signature: function[<...>|<...>]")
            fi.returns = [T.UNKNOWN]
            return
        self.check_function_body(fi)

    def check_function_body(self, fi: FuncInfo) -> None:
        fd: A.FuncDecl = fi.decl
        if fd is None or getattr(fi, "_checked", False):
            return
        if fd.body is None:
            fi._checked = True  # type: ignore[attr-defined]
            return
        if isinstance(fd.body, A.ActionBody):
            fi._checked = True  # type: ignore[attr-defined]
            self.err("FX0905", "Action bodies need the LLM resolver, which this version of fx3c doesn't "
                     "have yet", fd.body.span,
                     fix="write the body by hand for now, or wait for the fluid builder")
            return
        fi.inferring = True
        saved = (self.scope, self.cur_fn, self.loop_depth, self.try_depth, self.unassigned,
                 self.return_types, self.in_ctor, self.block_depth, self.cur_class)
        self.cur_class = self.classes[fi.owner] if fi.owner is not None else None
        self.scope = Scope(self.module, "function", fi)
        self.cur_fn = fi
        self.loop_depth = 0
        self.try_depth = 0
        self.block_depth = 0
        self.unassigned = set()
        self.in_ctor = fi.is_ctor
        inferring = fd.sig is None
        self.return_types = [] if inferring else None
        for p, pi in zip(fd.params, fi.params):
            sym = Symbol(p.name, "param", pi.ty, p.span, is_ref=pi.ref, used=True)
            p.sym = self.declare(p.name, sym, p.span)
            if p.default is not None:
                dt = self.expr(p.default, pi.ty)
                self.require_assignable(dt, pi.ty, p.default.span, f"default for `{p.name}`")
        if isinstance(fd.body, A.Block):
            terminated = self.check_block_stmts(fd.body)
            if inferring:
                rts = self.return_types or []
                fi.returns = self._unify_returns(rts, fd.span)
            elif fi.returns and not terminated:
                self.err("FX0611", f"`{fi.name}` must return {self._fmt_returns(fi.returns)} on every path",
                         fd.span, fix="add a return at the end")
        else:
            t = self.expr(fd.body, fi.returns[0] if (fi.returns and len(fi.returns) == 1) else None)
            if inferring:
                fi.returns = [] if t == T.VOID else [t]
            elif len(fi.returns) == 1:
                self.require_assignable(t, fi.returns[0], fd.body.span, "the returned value")
            elif not fi.returns and t != T.VOID:
                pass
        self._unused_warnings(self.scope)
        (self.scope, self.cur_fn, self.loop_depth, self.try_depth, self.unassigned,
         self.return_types, self.in_ctor, self.block_depth, self.cur_class) = saved
        fi.inferring = False
        fi.inferred = True
        fi._checked = True  # type: ignore[attr-defined]
        if "example" in fi.annots:
            self._check_examples(fi)

    def _check_examples(self, fi: FuncInfo) -> None:
        fd: A.FuncDecl = fi.decl
        cases = []
        for a in fd.annots:
            if a.name != "example":
                continue
            for arg in a.args:
                if not (isinstance(arg, A.TupleLit) and len(arg.items) == 2):
                    self.err("FX0612", "write examples as @example(input -> output)", a.span)
                    continue
                inp, out = arg.items
                ins = inp.items if isinstance(inp, A.TupleLit) else [inp]
                if len(ins) != len(fi.params):
                    self.err("FX0613", f"example gives {len(ins)} input(s), `{fi.name}` takes {len(fi.params)}",
                             a.span)
                    continue
                for e, p in zip(ins, fi.params):
                    t = self.expr(e, p.ty)
                    self.require_assignable(t, p.ty, e.span, "the example input")
                if fi.returns:
                    t = self.expr(out, fi.returns[0])
                    self.require_assignable(t, fi.returns[0], out.span, "the example output")
                cases.append((ins, out))
        self.examples.append((fi, cases))

    def _unify_returns(self, rts: list[list[Ty]], span: Span) -> list[Ty]:
        if not rts:
            return []
        first = rts[0]
        for r in rts[1:]:
            if len(r) != len(first):
                self.err("FX0614", "return statements give different numbers of values", span)
                return first
        out = []
        for k in range(len(first)):
            ty = first[k]
            for r in rts[1:]:
                ty = self.unify(ty, r[k], span) or ty
            out.append(ty)
        return out

    @staticmethod
    def _fmt_returns(rs: list[Ty]) -> str:
        return ", ".join(str(r) for r in rs)

    # ==================================================================
    # Statements
    # ==================================================================
    def check_block(self, b: A.Block, scope_kind: str = "block") -> bool:
        self.push(scope_kind)
        self.block_depth += 1
        terminated = self.check_block_stmts(b)
        self.block_depth -= 1
        self.pop()
        return terminated

    def check_block_stmts(self, b: A.Block) -> bool:
        terminated = False
        new: list[A.Stmt] = []
        for s in b.stmts:
            if terminated:
                self.warn("FX0507", "this code can never run (it comes after return / fail / stop)", s.span)
                terminated = False
            r = self.check_stmt(s)
            if r is not None:
                new.append(r)
            if self._terminates(r if r is not None else s):
                terminated = True
        b.stmts = new
        return terminated

    def _terminates(self, s: A.Stmt) -> bool:
        if isinstance(s, (A.Return, A.Fail, A.Stop, A.Skip)):
            return True
        if isinstance(s, A.ExprStmt) and isinstance(s.expr, A.Call) and s.expr.kind == "builtin" and \
                s.expr.target in ("panic", "unreachable", "exit"):
            return True
        if isinstance(s, A.If) and s.else_ is not None:
            return getattr(s, "_all_terminate", False)
        if isinstance(s, A.Block):
            return getattr(s, "_terminates", False)
        if isinstance(s, A.Switch):
            return getattr(s, "_all_terminate", False)
        if isinstance(s, A.RepeatForever):
            return not getattr(s, "_has_stop", True)
        return False

    def check_stmt(self, s: A.Stmt) -> Optional[A.Stmt]:
        m = getattr(self, "st_" + type(s).__name__, None)
        if m is None:
            self.err("FX1001", f"{type(s).__name__} is not supported yet", s.span)
            return s
        r = m(s)
        return s if r is None else r

    def st_ImportStmt(self, s: A.ImportStmt):
        return s          # handled before everything else (see check())

    def st_FuncDecl(self, s: A.FuncDecl):
        if self.scope is not self.module:
            self.err("FX1001", "functions inside functions are not supported yet", s.span)
        return s

    def st_ClassDecl(self, s: A.ClassDecl):
        if self.scope is not self.module:
            self.err("FX1001", "classes must be declared at the top level", s.span)
        return s

    def st_Block(self, s: A.Block):
        s._terminates = self.check_block(s)  # type: ignore[attr-defined]
        return s

    # ---- declarations -------------------------------------------------
    def st_VarDecl(self, s: A.VarDecl):
        annots = self.canon_annotations(s.annots, _VAR_ANNOTS, "a variable")
        s.annots = list(annots.values())
        for name in annots:
            if name in _NOT_ENFORCED_VAR:
                self.warn("FX0505", f"`@{name}` is accepted but not enforced yet by fx3c", annots[name].span)
        declared = self.resolve_type(s.type)
        style = None
        if "using" in annots and annots["using"].args and \
                isinstance(annots["using"].args[0], (A.StrLit, A.CharLit)):
            style = annots["using"].args[0].value
        for item in s.items:
            ty = declared
            if item.init is not None:
                self.pending_style = style
                expected = None if declared in (AUTO, NUMBER) else declared
                if declared.kind == "result":
                    # result<T> x = mayFail(...);  keeps the failure as a value
                    if isinstance(item.init, A.Call):
                        item.init.handled = True
                    expected = declared.args[0]
                vt = self.expr(item.init, expected)
                if declared.kind == "result":
                    if not getattr(item.init, "fails", None):
                        self.err("FX0627", "result<T> holds the outcome of a call that can fail (@fails)",
                                 item.init.span)
                    vt = declared if T.assignable(vt, declared.args[0]) else vt
                self.pending_style = None
                if vt == T.VOID:
                    self.err("FX0406", "this returns nothing, so it can't be stored", item.init.span)
                    vt = T.UNKNOWN
                if vt.kind == "multi":
                    self.err("FX0407", "this returns several values; unpack them with "
                             "`auto [a, b] = ...;`", item.init.span)
                    vt = T.UNKNOWN
                if declared == AUTO:
                    if vt.kind == "none":
                        self.err("FX0408", "can't infer a type from `none`", item.init.span,
                                 fix="write the type, e.g. `int? x = none;`")
                    ty = vt
                elif declared == NUMBER:
                    ty = vt if T.is_numeric(vt) else T.UNKNOWN
                    if not T.is_numeric(vt) and not T.is_unknown(vt):
                        self.err("FX0401", f"`number` needs a number, but this is {vt}", item.init.span)
                else:
                    self.require_assignable(vt, declared, item.init.span, f"`{item.name}`")
                    if vt.kind == "result" and declared.kind != "result":
                        pass
            else:
                if declared in (AUTO, NUMBER):
                    self.err("FX0409", f"`{item.name}` needs a value (or a written type) so its type is known",
                             item.span)
                    ty = T.UNKNOWN
                if "const" in annots:
                    self.err("FX0410", f"constant `{item.name}` needs a value", item.span)
            if "size" in annots:
                if not T.is_seq(ty):
                    self.err("FX0503", "@size only works on list, vector and array", annots["size"].span)
                else:
                    for a in annots["size"].args:
                        self.require_assignable(self.expr(a, T.INT), T.INT, a.span, "@size")
                if "init" in annots:
                    for a in annots["init"].args:
                        self.require_assignable(self.expr(a, T.elem(ty)), T.elem(ty), a.span, "@init")
            elif "init" in annots:
                self.err("FX0503", "@init needs @size (the number of elements to fill)", annots["init"].span)
            if "default" in annots:
                if ty.kind != "map":
                    self.err("FX0503", "@default only works on maps", annots["default"].span)
                else:
                    for a in annots["default"].args:
                        self.require_assignable(self.expr(a, ty.args[1]), ty.args[1], a.span, "@default")
            if "range" in annots:
                a = annots["range"]
                if len(a.args) != 2:
                    self.err("FX0503", "write @range(low, high)", a.span)
                else:
                    for x in a.args:
                        self.expr(x, ty)
                    if item.init is not None:
                        self._check_range_literal(item.init, a, item.name)
            sym = Symbol(item.name, "var", ty, item.span, const="const" in annots,
                         typesafe="typesafe" in annots, annots=dict(annots))
            item.sym = self.declare(item.name, sym, item.span)
            if item.init is None and not self._default_assigned(ty, annots):
                self.unassigned.add(item.sym)
        return s

    def _default_assigned(self, ty: Ty, annots: dict) -> bool:
        return T.is_collection(ty) and ty.kind != "tuple" or ty.kind == "maybe" or "size" in annots

    def _check_range_literal(self, value: A.Expr, ann: A.Annotation, name: str) -> None:
        lo, hi = ann.args
        if isinstance(value, (A.IntLit, A.FloatLit)) and isinstance(lo, (A.IntLit, A.FloatLit)) and \
                isinstance(hi, (A.IntLit, A.FloatLit)):
            if not (lo.value <= value.value <= hi.value):
                self.err("FX0411", f"{value.value} is outside @range({lo.value}, {hi.value}) of `{name}`",
                         value.span)

    def st_UnpackDecl(self, s: A.UnpackDecl):
        common = self.resolve_type(s.common_type) if s.common_type else AUTO
        vt = self.expr(s.value)
        if vt.kind == "multi" or vt.kind == "tuple":
            parts = list(vt.args)
        elif T.is_seq(vt):
            parts = None
        elif T.is_unknown(vt):
            parts = [T.UNKNOWN] * len(s.targets)
        else:
            self.err("FX0412", f"can't unpack {vt}; unpack a tuple, a list or several return values",
                     s.value.span)
            parts = [T.UNKNOWN] * len(s.targets)
        has_rest = any(t.rest for t in s.targets)
        if parts is not None:
            if has_rest:
                self.err("FX0413", "`...rest` only works when unpacking a list", s.span)
            elif len(parts) != len(s.targets):
                self.err("FX0414", f"{len(s.targets)} names but {len(parts)} values to unpack", s.span)
                parts = (parts + [T.UNKNOWN] * len(s.targets))[:len(s.targets)]
        for k, tgt in enumerate(s.targets):
            if parts is None:
                ety = vt if tgt.rest else T.elem(vt)
            else:
                ety = parts[k] if k < len(parts) else T.UNKNOWN
            want = self.resolve_type(tgt.type) if tgt.type else common
            if want not in (AUTO, NUMBER):
                self.require_assignable(ety, want, tgt.span or s.span, f"`{tgt.name}`")
                ety = want
            if tgt.name == "_":
                continue
            tgt.sym = self.declare(tgt.name, Symbol(tgt.name, "var", ety, tgt.span or s.span), tgt.span or s.span)
        return s

    # ---- assignment ----------------------------------------------------
    def st_Assign(self, s: A.Assign):
        tgt = s.target
        if isinstance(tgt, A.Name):
            sym, cur = self.scope.lookup(tgt.id)
            if sym is None:
                self._undefined(tgt)
                self.expr(s.value)
                return s
            tgt.sym = sym
            tgt.ty = cur
            if sym.kind in ("func", "class"):
                self.err("FX0415", f"`{tgt.id}` is a {sym.kind}, not a variable", tgt.span)
                return s
            if sym.kind == "param":
                self.err("FX0416", f"parameter `{tgt.id}` can't be reassigned", tgt.span,
                         fix=f"copy it into a new variable first: `auto my{tgt.id.capitalize()} = {tgt.id};`")
            if sym.kind in ("implicit", "loopvar"):
                self.err("FX0417", f"`{tgt.id}` can't be assigned here", tgt.span)
            if sym.const:
                self.err("FX0418", f"`{tgt.id}` is a constant and can't be changed", tgt.span)
            if s.op == "=":
                vt = self.expr(s.value, cur)
                if vt == T.VOID:
                    self.err("FX0406", "this returns nothing, so it can't be stored", s.value.span)
                elif not T.assignable(vt, cur) and not T.is_unknown(vt):
                    if sym.typesafe or sym.kind != "var":
                        self.err("FX0419", f"`{tgt.id}` is {cur} (@typesafe) and can't hold {vt}", s.value.span)
                    elif self.block_depth != sym.decl_depth:
                        self.err("FX0420", f"`{tgt.id}` is {cur}; its type can only change at the level where "
                                 f"it was declared, not inside a branch or loop", s.value.span)
                    else:
                        sym.ty = vt          # loose variable: now a different type (shadowing)
                        tgt.ty = vt
                        s._retyped = True    # type: ignore[attr-defined]
                self.unassigned.discard(sym)
            else:
                if sym in self.unassigned:
                    self.err("FX0302", f"`{tgt.id}` is used before it has a value", tgt.span)
                self._compound(s, cur)
            return s
        # member / index targets
        tt = self.lvalue(tgt)
        if s.op == "=":
            vt = self.expr(s.value, tt)
            if vt == T.VOID:
                self.err("FX0406", "this returns nothing, so it can't be stored", s.value.span)
            else:
                self.require_assignable(vt, tt, s.value.span, "this")
        else:
            self._compound(s, tt)
        return s

    def _compound(self, s: A.Assign, cur: Ty) -> None:
        op = s.op[:-1]
        if s.op == "??=":
            if cur.kind != "maybe":
                self.err("FX0421", "`??=` only works on values that may be none", s.span)
            vt = self.expr(s.value, cur.args[0] if cur.kind == "maybe" else cur)
            return
        vt = self.expr(s.value, cur if T.is_numeric(cur) else None)
        if op == "/" and T.is_int(cur):
            self.err("FX0422", "`/=` would make a float, but this is an int", s.span,
                     fix="use `~/=` for whole-number division")
            return
        fake = A.Binary(s.span, op, A.Name(s.span, "_"), A.Name(s.span, "_"))
        rt = self.binary_type(fake, cur, vt)
        if rt is not None and not T.assignable(rt, cur) and not T.is_unknown(rt):
            self.err("FX0419", f"`{s.op}` would give {rt}, which doesn't fit {cur}", s.span)
        s._op_ty = rt  # type: ignore[attr-defined]

    def lvalue(self, e: A.Expr) -> Ty:
        if isinstance(e, A.Index):
            ot = self.expr(e.obj)
            self._check_mutable(e.obj, "changed")
            if ot.kind == "string":
                self.err("FX1001", "changing one character of a string is not supported yet", e.span)
                self.expr(e.index)
                return T.CHAR
            if ot.kind == "tuple":
                self.err("FX1001", "changing a tuple item is not supported yet", e.span)
            t = self.expr(e)
            return t
        if isinstance(e, A.Member):
            ot = self.expr(e.obj)
            if ot.kind == "tuple":
                self.err("FX1001", "changing a tuple item is not supported yet", e.span)
            t = self.expr(e)
            fi: Optional[FieldInfo] = e.field_info
            if fi is not None and fi.const and not (self.in_ctor and isinstance(e.obj, A.This)):
                self.err("FX0423", f"field `{fi.name}` is read-only outside the constructor", e.span)
            if fi is None and not T.is_unknown(t):
                self.err("FX0424", "only fields can be assigned to", e.span)
            return t
        if isinstance(e, (A.Name,)):
            return self.expr(e)
        self.err("FX0425", "can't assign to this", e.span)
        self.expr(e)
        return T.UNKNOWN

    def _check_mutable(self, obj: A.Expr, verb: str) -> None:
        if isinstance(obj, A.Name):
            sym, _ = self.scope.lookup(obj.id)
            if sym is None:
                return
            if sym.const:
                self.err("FX0418", f"`{obj.id}` is a constant and can't be {verb}", obj.span)
            elif sym.kind == "param" and not sym.is_ref and T.is_collection(sym.ty):
                self.err("FX0426", f"parameter `{obj.id}` is read-only", obj.span,
                         fix="mark the parameter @ref to let the function change the caller's value")
            elif sym.kind in ("implicit",):
                self.err("FX0417", f"`{obj.id}` can't be {verb} here", obj.span)

    def st_IncDec(self, s: A.IncDec):
        if isinstance(s.target, A.Name):
            sym, cur = self.scope.lookup(s.target.id)
            if sym is not None and sym.const:
                self.err("FX0418", f"`{s.target.id}` is a constant and can't be changed", s.target.span)
            if sym is not None and sym.kind == "param":
                self.err("FX0416", f"parameter `{s.target.id}` can't be changed", s.target.span)
            t = self.expr(s.target)
        else:
            t = self.lvalue(s.target)
        if not T.is_int(t) and not T.is_unknown(t):
            self.err("FX0427", f"`{s.op}` only works on whole numbers, not {t}", s.span)
        return s

    def st_ExprStmt(self, s: A.ExprStmt):
        e = s.expr
        self.expr(e)
        if isinstance(e, A.Call):
            recv = getattr(e, "recv_ty", None)
            is_query = e.kind == "bank" and METHOD_KIND.get(e.target) == "query" or (
                e.kind == "bank" and recv is not None and recv.kind == "string" and
                e.target in ("replace", "reverse"))
            if e.kind == "bank" and is_query and e.target != "isEmpty":
                hint = {"sorted": "sort", "reversed": "reverse"}.get(e.target)
                fix = f"did you mean {hint}()?" if hint else "store it: `auto result = ...;`"
                if recv is not None and recv.kind == "string":
                    fix = "strings don't change in place; write `s = s.upper();` (for example)"
                self.warn("FX0508", f"the result of `{e.target}` is not used", e.span, fix=fix)
            elif e.kind == "bank" and e.target == "isEmpty":
                self.warn("FX0508", "`empty()` only checks; did you mean clear()?", e.span)
        elif isinstance(e, A.Propagate) and isinstance(e.expr, A.Call):
            pass            # `mayFail()?;` as a statement
        elif not isinstance(e, (A.SwitchExpr,)):
            self.warn("FX0508", "this value is computed but never used", e.span)
        return s

    # ---- control flow ------------------------------------------------
    def check_cond(self, e: A.Expr, subject: Optional[A.Expr] = None) -> A.Expr:
        """Check a condition. Turns English text into an expression. Returns the (new) expression."""
        if isinstance(e, A.StrLit):
            parsed = self.english(e, subject)
            if parsed is None:
                bad = A.BoolLit(e.span, False)
                bad.ty = T.BOOL
                return bad
            e = parsed
        t = self.expr(e, T.BOOL)
        if t != T.BOOL and not T.is_unknown(t):
            fix = None
            if T.is_numeric(t):
                fix = "compare it, e.g. `(count != 0)`"
            elif T.is_collection(t) or t.kind == "string":
                fix = "use `not x.isEmpty()`"
            elif t.kind == "maybe":
                fix = "use `x != none`, or `if (auto v = x)`"
            self.err("FX0428", f"a condition must be true/false, but this is {t} (fx3 has no truthiness)",
                     e.span, fix=fix)
        return e

    def english(self, lit: A.StrLit, subject: Optional[A.Expr]) -> Optional[A.Expr]:
        def resolve(word: str) -> Optional[A.Expr]:
            canon = IMPLICIT_VARS.get(norm(word))
            if canon is not None:
                sym, _ = self.scope.lookup(canon)
                if sym is not None:
                    return A.Name(lit.span, canon)
            sym, _ = self.scope.lookup(word)
            if sym is not None and sym.kind not in ("func", "class"):
                return A.Name(lit.span, word)
            return None
        parsed = parse_condition(lit.value, lit.span, resolve, subject)
        if parsed is None:
            self.err("FX0901", f"can't understand the condition \"{lit.value}\"", lit.span,
                     fix="rephrase it (e.g. \"x is greater than 10\"), or write it as an expression; "
                         "the LLM resolver for free-form conditions is not available yet")
        return parsed

    def _narrow_from(self, cond: A.Expr) -> tuple[list[tuple[str, Ty]], list[tuple[str, Ty]]]:
        """Names that are known not-none when the condition is (true, false)."""
        when_true: list[tuple[str, Ty]] = []
        when_false: list[tuple[str, Ty]] = []
        if isinstance(cond, A.Compare) and len(cond.ops) == 1 and cond.ops[0] in ("==", "!="):
            l, r = cond.operands  # noqa: E741
            name = None
            if isinstance(l, A.Name) and isinstance(r, A.NoneLit):
                name = l
            elif isinstance(r, A.Name) and isinstance(l, A.NoneLit):
                name = r
            if name is not None:
                sym, ty = self.scope.lookup(name.id)
                if sym is not None and ty is not None and ty.kind == "maybe":
                    if cond.ops[0] == "!=":
                        when_true.append((name.id, ty.args[0]))
                    else:
                        when_false.append((name.id, ty.args[0]))
        elif isinstance(cond, A.Logical) and cond.op == "and":
            a, _ = self._narrow_from(cond.left)
            b, _ = self._narrow_from(cond.right)
            when_true = a + b
        elif isinstance(cond, A.Logical) and cond.op == "or":
            _, a = self._narrow_from(cond.left)
            _, b = self._narrow_from(cond.right)
            when_false = a + b
        return when_true, when_false

    def _check_branch(self, block: A.Block, narrow: list[tuple[str, Ty]], bind: Optional[A.BindCond] = None) -> bool:
        self.push("block")
        self.block_depth += 1
        for name, ty in narrow:
            self.scope.narrowed[name] = ty
        if bind is not None and bind.sym is not None:
            self.scope.syms[bind.name] = bind.sym
        t = self.check_block_stmts(block)
        self.block_depth -= 1
        self.pop()
        return t

    def _cond_for_if(self, cond: A.Expr) -> A.Expr:
        if isinstance(cond, A.BindCond):
            vt = self.expr(cond.value)
            inner = vt.args[0] if vt.kind == "maybe" else vt
            if vt.kind != "maybe" and not T.is_unknown(vt):
                self.err("FX0429", "`if (auto v = x)` needs a value that may be none", cond.value.span)
            want = self.resolve_type(cond.type) if cond.type else AUTO
            if want not in (AUTO, NUMBER):
                self.require_assignable(inner, want, cond.span, f"`{cond.name}`")
                inner = want
            cond.sym = Symbol(cond.name, "var", inner, cond.span, used=False)
            cond.sym.owner_fn = self.scope.fn
            cond.sym.module_level = self.scope.kind == "module"
            cond.ty = T.BOOL
            return cond
        return self.check_cond(cond)

    def st_If(self, s: A.If):
        before = set(self.unassigned)
        outcomes: list[tuple[set, bool]] = []
        s.cond = self._cond_for_if(s.cond)
        bind = s.cond if isinstance(s.cond, A.BindCond) else None
        wt, wf = self._narrow_from(s.cond) if bind is None else ([], [])
        term = self._check_branch(s.then, wt, bind)
        outcomes.append((set(self.unassigned), term))
        false_narrow = list(wf)
        new_elifs = []
        for c, b in s.elifs:
            self.unassigned = set(before)
            self.push("block")
            for n, t in false_narrow:
                self.scope.narrowed[n] = t
            c2 = self._cond_for_if(c)
            bind2 = c2 if isinstance(c2, A.BindCond) else None
            wt2, wf2 = self._narrow_from(c2) if bind2 is None else ([], [])
            term2 = self._check_branch(b, wt2, bind2)
            self.pop()
            false_narrow += wf2
            outcomes.append((set(self.unassigned), term2))
            new_elifs.append((c2, b))
        s.elifs = new_elifs
        if s.else_ is not None:
            self.unassigned = set(before)
            term3 = self._check_branch(s.else_, false_narrow)
            outcomes.append((set(self.unassigned), term3))
        else:
            outcomes.append((before, False))
        live = [u for u, t in outcomes if not t]
        self.unassigned = set().union(*live) if live else set(before)
        s._all_terminate = all(t for _, t in outcomes)  # type: ignore[attr-defined]
        # `if (x == none) { return; }` -> x is not none afterwards
        if s.else_ is None and not s.elifs and outcomes[0][1]:
            for n, t in wf:
                self.scope.narrowed[n] = t
        return s

    def st_RepeatTimes(self, s: A.RepeatTimes):
        if isinstance(s.count, A.StrLit):
            return self.st_RepeatWhile(A.RepeatWhile(s.span, s.count, s.body))
        t = self.expr(s.count)
        if t == T.BOOL:
            w = A.RepeatWhile(s.span, s.count, s.body)
            return self._loop_body(w, already_checked_cond=True)
        if not T.is_int(t) and not T.is_unknown(t):
            self.err("FX0430", f"repeat(...) needs a number of times or a true/false condition, not {t}",
                     s.count.span)
        self._loop(s.body)
        return s

    def st_RepeatWhile(self, s: A.RepeatWhile):
        return self._loop_body(s, already_checked_cond=False)

    def _loop_body(self, s: A.RepeatWhile, already_checked_cond: bool) -> A.RepeatWhile:
        if not already_checked_cond:
            s.cond = self.check_cond(s.cond)
        if s.at_least_once:
            self.loop_depth += 1
            self.check_block(s.body)
            self.loop_depth -= 1
        else:
            self._loop(s.body)
        return s

    def _loop(self, body: A.Block, setup=None) -> None:
        before = set(self.unassigned)
        self.loop_depth += 1
        self.push("block")
        self.block_depth += 1
        if setup:
            setup()
        self.check_block_stmts(body)
        self.block_depth -= 1
        self.pop()
        self.loop_depth -= 1
        self.unassigned = before

    def st_RepeatForever(self, s: A.RepeatForever):
        self._loop(s.body)
        s._has_stop = self._contains_stop(s.body)  # type: ignore[attr-defined]
        if not s._has_stop:  # type: ignore[attr-defined]
            self.warn("FX0509", "this loop has no `stop;`, so it never ends", s.span)
        return s

    def _contains_stop(self, node) -> bool:
        """Does a loop body contain a `stop` (or return / fail) that leaves this loop?"""
        if isinstance(node, (A.Stop, A.Return, A.Fail)):
            return True
        if isinstance(node, (A.RepeatEach, A.RepeatWhile, A.RepeatTimes, A.RepeatC, A.RepeatForever)):
            return False          # a stop in a nested loop leaves that loop, not this one
        children: list = []
        if isinstance(node, A.Block):
            children = node.stmts
        elif isinstance(node, A.If):
            children = [node.then] + [b for _, b in node.elifs] + ([node.else_] if node.else_ else [])
        elif isinstance(node, A.Switch):
            children = [x for c in node.cases for x in c.body]
        elif isinstance(node, A.Try):
            children = [node.body] + [c.body for c in node.catches] + ([node.finally_] if node.finally_ else [])
        elif isinstance(node, A.ExprStmt) and isinstance(node.expr, A.Call) and node.expr.kind == "builtin" \
                and node.expr.target in ("panic", "exit"):
            return True
        return any(self._contains_stop(c) for c in children)

    def st_RepeatEach(self, s: A.RepeatEach):
        it = self.expr(s.iterable)
        n = len(s.targets)
        if it.kind == "map":
            if n == 2:
                tys = [it.args[0], it.args[1]]
            elif n == 1:
                tys = [it.args[0]]
            else:
                self.err("FX0431", "a map gives two values per step: [key, value]", s.span)
                tys = [T.UNKNOWN] * n
        elif T.is_seq(it) or it.kind in ("set", "range", "string"):
            et = T.elem(it)
            if n == 1:
                tys = [et]
            elif et.kind == "tuple" and len(et.args) == n:
                tys = list(et.args)
            elif T.is_unknown(et):
                tys = [T.UNKNOWN] * n
            else:
                self.err("FX0431", f"each element is {et}, which can't be split into {n} names", s.span)
                tys = [T.UNKNOWN] * n
        elif T.is_unknown(it):
            tys = [T.UNKNOWN] * n
        else:
            self.err("FX0432", f"can't loop over {it}", s.iterable.span)
            tys = [T.UNKNOWN] * n
        common = self.resolve_type(s.common_type) if s.common_type else AUTO

        def setup():
            for tgt, ty in zip(s.targets, tys):
                want = self.resolve_type(tgt.type) if tgt.type else common
                if want not in (AUTO, NUMBER):
                    self.require_assignable(ty, want, tgt.span or s.span, f"`{tgt.name}`")
                    ty = want
                if tgt.name != "_":
                    tgt.sym = self.declare(tgt.name, Symbol(tgt.name, "loopvar", ty, tgt.span or s.span,
                                                            used=True), tgt.span or s.span)
        self._loop(s.body, setup)
        return s

    def st_RepeatC(self, s: A.RepeatC):
        before = set(self.unassigned)
        self.push("block")
        if s.init is not None:
            self.check_stmt(s.init)
            if isinstance(s.init, A.VarDecl):
                for it in s.init.items:
                    if it.sym is not None:
                        it.sym.used = True
        if s.cond is not None:
            s.cond = self.check_cond(s.cond)
        self.loop_depth += 1
        self.push("block")
        self.block_depth += 1
        self.check_block_stmts(s.body)
        self.block_depth -= 1
        self.pop()
        if s.step is not None:
            self.check_stmt(s.step)
        self.loop_depth -= 1
        self.pop()
        self.unassigned = before
        return s

    def st_Stop(self, s: A.Stop):
        if self.loop_depth == 0:
            self.err("FX0510", "`stop;` can only be used inside a loop", s.span)
        return s

    def st_Skip(self, s: A.Skip):
        if self.loop_depth == 0:
            self.err("FX0510", "`skip;` can only be used inside a loop", s.span)
        return s

    def st_Switch(self, s: A.Switch):
        self.check_switch(s)
        return s

    def check_switch(self, s: A.Switch) -> Ty:
        st = self.expr(s.subject)
        defaults = [c for c in s.cases if c.patterns is None]
        if len(defaults) > 1:
            self.err("FX0511", "switch has more than one default", defaults[1].span)
        if defaults and s.cases[-1].patterns is not None:
            self.err("FX0512", "default must be the last case", defaults[0].span)
        covered_bool = set()
        result_ty: Optional[Ty] = None
        before = set(self.unassigned)
        outcomes: list[tuple[set, bool]] = []
        for c in s.cases:
            c.conds = []
            if c.patterns is not None:
                for p in c.patterns:
                    c.conds.append(self._case_cond(p, s.subject, st))
                    if isinstance(p, A.BoolLit):
                        covered_bool.add(p.value)
            self.unassigned = set(before)
            self.push("block")
            self.block_depth += 1
            block = A.Block(c.span or s.span, c.body)
            if s.is_value:
                if not c.body or not isinstance(c.body[-1], A.ExprStmt):
                    self.err("FX0513", "each case of a switch used as a value must end with a value", c.span or s.span)
                    self.check_block_stmts(block)
                else:
                    for x in c.body[:-1]:
                        self.check_stmt(x)
                    last = c.body[-1]
                    t = self.expr(last.expr, result_ty)
                    result_ty = t if result_ty is None else (self.unify(result_ty, t, last.span) or result_ty)
                term = False
            else:
                term = self.check_block_stmts(block)
                c.body = block.stmts
            self.block_depth -= 1
            self.pop()
            outcomes.append((set(self.unassigned), term))
        exhaustive = bool(defaults) or covered_bool == {True, False}
        if not exhaustive:
            self.err("FX0514", "switch needs a `default` (its cases don't cover every value)", s.span,
                     fix="add `default.act(...);` as the last case")
            outcomes.append((before, False))
        live = [u for u, t in outcomes if not t]
        self.unassigned = set().union(*live) if live else set(before)
        s._all_terminate = exhaustive and all(t for _, t in outcomes)  # type: ignore[attr-defined]
        return result_ty or T.VOID

    def _case_cond(self, p: A.Expr, subject: A.Expr, st: Ty) -> A.Expr:
        """Build the bool expression that tests one pattern against the subject."""
        sp = p.span
        if isinstance(p, A.StrLit) and st.kind != "string":
            e = self.english(p, subject)
            if e is None:
                return A.BoolLit(sp, False)
            self.expr(e, T.BOOL)
            return e
        if isinstance(p, A.RangeExpr):
            e = A.Compare(sp, ["in"], [subject, p])
            self.expr(e)
            return e
        e = A.Compare(sp, ["=="], [subject, p])
        self.expr(e)
        return e

    def st_Try(self, s: A.Try):
        before = set(self.unassigned)
        self.try_depth += 1
        self.check_block(s.body)
        self.try_depth -= 1
        after_body = set(self.unassigned)
        seen_catch_all = False
        seen: list[str] = []
        for c in s.catches:
            self.unassigned = set(before)
            if seen_catch_all:
                self.err("FX0615", "this catch can never run: an earlier catch already catches every error",
                         c.span)
            if c.type_name is None or c.type_name == "Error":
                seen_catch_all = True
            cls = None
            if c.type_name is not None:
                cls = self.classes.get(c.type_name)
                if cls is None or not cls.is_error:
                    self.err("FX0616", f"`{c.type_name}` is not an error type", c.span)
                    cls = None
                else:
                    for prev in seen:
                        if T._is_subclass(c.type_name, prev):
                            self.err("FX0615", f"this catch can never run: `{prev}` above already catches "
                                     f"{c.type_name}", c.span)
                    seen.append(c.type_name)
            c.error_cls = cls or self.classes["Error"]
            self.push("block")
            if c.var:
                c.sym = self.declare(c.var, Symbol(c.var, "catchvar", T.class_ty(c.error_cls.name), c.span,
                                                   used=True), c.span)
            self.check_block_stmts(c.body)
            self.pop()
        self.unassigned = after_body | before
        if s.finally_ is not None:
            self.check_block(s.finally_)
        return s

    def st_Defer(self, s: A.Defer):
        self.check_block(s.body)
        return s

    def st_Return(self, s: A.Return):
        fn = self.cur_fn
        if fn is None:
            self.err("FX0617", "`return` can only be used inside a function", s.span)
            for v in s.values:
                self.expr(v)
            return s
        if self.return_types is not None:
            tys = []
            for v in s.values:
                t = self.expr(v)
                tys.append(t)
            self.return_types.append(tys)
            return s
        want = fn.returns
        if len(s.values) != len(want):
            if not want:
                self.err("FX0618", f"`{fn.name}` returns nothing, but a value is returned here", s.span)
            else:
                self.err("FX0618", f"`{fn.name}` returns {len(want)} value(s) ({self._fmt_returns(want)}), "
                         f"but {len(s.values)} are given here", s.span)
            for v in s.values:
                self.expr(v)
            return s
        for v, w in zip(s.values, want):
            t = self.expr(v, w)
            self.require_assignable(t, w, v.span, "the returned value")
        return s

    def st_Fail(self, s: A.Fail):
        t = self.expr(s.error)
        if t.kind != "class" or not self.classes.get(t.name, ClassInfo("")).is_error:
            if not T.is_unknown(t):
                self.err("FX0619", "`fail` needs an error, e.g. `fail ParseError(\"bad input\");`", s.error.span)
            return s
        fn = self.cur_fn
        if self.try_depth > 0:
            return s
        if fn is None or not any(T._is_subclass(t.name, f) for f in fn.fails):
            where = f"`{fn.name}`" if fn else "this code"
            self.err("FX0620", f"{where} fails with {t.name}, but doesn't declare it", s.span,
                     fix=f"add @fails({t.name}) to the function" if fn else "wrap it in try { ... }")
        return s

    # ==================================================================
    # Expressions
    # ==================================================================
    def expr(self, e: A.Expr, expected: Optional[Ty] = None) -> Ty:
        if expected in (AUTO, NUMBER):
            expected = None
        m = getattr(self, "ex_" + type(e).__name__)
        t = m(e, expected)
        e.ty = t
        return t

    def require_assignable(self, src: Ty, dst: Ty, span: Span, what: str) -> None:
        if dst in (AUTO, NUMBER) or T.is_unknown(src) or T.is_unknown(dst):
            return
        if not T.assignable(src, dst):
            fix = None
            if src.kind == "none":
                msg = f"{what} can't be none, because its type is {dst}"
                fix = f"make the type optional: {dst}?"
            elif src.kind == "maybe" and T.assignable(src.args[0], dst):
                msg = f"this may be none, but {what} is {dst}"
                fix = "give a default with `?? value`, or check it first"
            elif T.is_numeric(src) and T.is_numeric(dst):
                msg = f"{src} doesn't automatically become {dst} (that could lose information)"
                fix = f"use `as {dst}` (checked) if it is really what you want"
            elif src.kind == "result":
                msg = "this can fail; handle it before storing the value"
                fix = "use try/catch, `?? default`, or `?`"
            elif src.kind == "list" and dst.kind == "vector":
                msg = f"this list may hold mixed values, but {what} is {dst} (one type only)"
                fix = "use `list` for mixed values"
            else:
                msg = f"{what} is {dst}, but this is {src}"
            self.err("FX0401", msg, span, fix)

    def unify(self, a: Ty, b: Ty, span: Span) -> Optional[Ty]:
        if a == b:
            return a
        if T.is_unknown(a):
            return b
        if T.is_unknown(b):
            return a
        if T.is_numeric(a) and T.is_numeric(b):
            r = T.numeric_result(a, b)
            if r is not None:
                return r
        if a.kind == "none" and b.kind != "none":
            return T.maybe(b)
        if b.kind == "none":
            return T.maybe(a)
        if a.kind == "maybe" and T.assignable(b, a):
            return a
        if b.kind == "maybe" and T.assignable(a, b):
            return b
        if T.assignable(a, b):
            return b
        if T.assignable(b, a):
            return a
        self.err("FX0433", f"these give different types: {a} and {b}", span)
        return None

    def _undefined(self, n: A.Name) -> None:
        names = []
        s: Optional[Scope] = self.scope
        while s is not None:
            names += [k for k, v in s.syms.items()]
            s = s.parent
        if n.id in STD.MODULES or n.id in STD.TYPE_MODULE:
            mod = STD.TYPE_MODULE.get(n.id, n.id)
            self.err("FX0313", f"`{n.id}` comes from the {mod} module", n.span,
                     fix=f"add `import {mod};` at the top of the file")
            return
        guess = typo_match(n.id, names + sorted(PRELUDE) + sorted(STD.MODULES), cutoff=0.75)
        self.err("FX0301", f"`{n.id}` is not defined", n.span,
                 fix=f"did you mean `{guess}`?" if guess else None)

    # ---- literals -----------------------------------------------------
    def ex_IntLit(self, e: A.IntLit, expected):
        if e.suffix:
            ty = Ty(e.suffix if e.suffix != "i64" else "int")
        elif expected is not None and T.is_int(expected):
            ty = expected
        elif expected is not None and (T.is_float(expected) or
                                       (expected.kind == "maybe" and T.is_float(expected.args[0]))):
            return expected if T.is_float(expected) else expected.args[0]
        elif expected is not None and expected.kind == "maybe" and T.is_int(expected.args[0]):
            ty = expected.args[0]
        else:
            ty = T.INT
        lo, hi = INT_RANGES.get(ty.kind, (None, None))
        if lo is not None and not (lo <= e.value <= hi):
            self.err("FX0434", f"{e.value} doesn't fit in {ty} ({lo} to {hi})", e.span)
        return ty

    def ex_DurationLit(self, e, expected):
        return T.DURATION

    def ex_FloatLit(self, e, expected):
        if expected is not None and expected.kind == "f32":
            return expected
        return T.FLOAT

    def ex_StrLit(self, e, expected):
        return T.STRING

    def ex_CharLit(self, e, expected):
        if expected is not None and expected == T.STRING:
            return T.STRING
        return T.CHAR

    def ex_BoolLit(self, e, expected):
        return T.BOOL

    def ex_NoneLit(self, e, expected):
        if expected is not None and expected.kind == "maybe":
            return expected
        return T.NONE

    def ex_FString(self, e: A.FString, expected):
        for p in e.parts:
            if p.expr is not None:
                t = self.expr(p.expr)
                if t == T.VOID:
                    self.err("FX0406", "this returns nothing, so it can't be put in the text", p.expr.span)
        return T.STRING

    def ex_ListLit(self, e: A.ListLit, expected):
        if expected is not None and (T.is_seq(expected)):
            want = expected.args[0]
            for it in e.items:
                t = self.expr(it, want)
                if expected.kind == "list" and not expected.strict:
                    continue
                if not T.assignable(t, want) and not T.is_unknown(t):
                    if expected.kind == "vector":
                        self.err("FX0435", f"{expected} holds one type ({want}), but this is {t}", it.span,
                                 fix="use `list` for mixed values")
                    else:
                        self.require_assignable(t, want, it.span, "this element")
            return expected
        if expected is not None and expected.kind == "set":
            # [1, 2] for a set is the wrong bracket (collections.fx3 section 5)
            self.err("FX0436", "[ ] is for lists; a set uses { }", e.span, fix="write {1, 2, 3}")
            for it in e.items:
                self.expr(it)
            return expected
        if expected is not None and expected.kind in ("map", "tuple"):
            other = "{key: value}" if expected.kind == "map" else "(a, b)"
            self.err("FX0436", f"[ ] is for lists; a {expected.kind} uses {other}", e.span)
            for it in e.items:
                self.expr(it)
            return expected
        if not e.items:
            if expected is None:
                self.err("FX0437", "can't tell the type of an empty list", e.span,
                         fix="write the type, e.g. `vector<int> v = [];`")
                return T.UNKNOWN
            return expected
        tys = [self.expr(it) for it in e.items]
        return self._infer_seq(tys, e.span)

    def _infer_seq(self, tys: list[Ty], span: Span) -> Ty:
        first = tys[0]
        same = True
        for t in tys[1:]:
            if t == first:
                continue
            if T.is_numeric(t) and T.is_numeric(first):
                first = T.numeric_result(first, t) or first
                continue
            if t.kind == "none" or first.kind == "none":
                first = T.maybe(t if first.kind == "none" else first)
                continue
            same = False
        if not same:
            return T.list_of()
        return T.vector(first)

    def ex_SetLit(self, e: A.SetLit, expected):
        if expected is not None and expected.kind == "map":
            self.err("FX0436", "this is a set ({a, b}); a map needs {key: value}", e.span)
        if expected is not None and T.is_seq(expected):
            self.err("FX0436", "{ } is for maps and sets; a list uses [ ]", e.span, fix="write [1, 2, 3]")
        want = expected.args[0] if expected is not None and expected.kind == "set" else None
        tys = [self.expr(it, want) for it in e.items]
        if want is not None:
            for it, t in zip(e.items, tys):
                self.require_assignable(t, want, it.span, "this element")
            return expected
        if expected is not None and expected.kind in ("vector", "list", "array", "map"):
            return expected
        inner = self._infer_seq(tys, e.span)
        return T.set_of(inner.args[0] if inner.kind == "vector" else T.ANY)

    def ex_MapLit(self, e: A.MapLit, expected):
        if expected is not None and T.is_seq(expected):
            self.err("FX0436", "{ } is for maps and sets; a list uses [ ]", e.span)
        kt = vt = None
        if expected is not None and expected.kind == "map":
            kt, vt = expected.args
        keys, vals = [], []
        for k, v in e.pairs:
            keys.append(self.expr(k, kt))
            vals.append(self.expr(v, vt))
        seen = set()
        for k, _ in e.pairs:
            if isinstance(k, (A.StrLit, A.IntLit, A.CharLit, A.BoolLit)):
                if k.value in seen:
                    self.err("FX0438", f"key {k.value!r} appears twice in this map", k.span)
                seen.add(k.value)
        if kt is not None:
            for (k, v), ktt, vtt in zip(e.pairs, keys, vals):
                self.require_assignable(ktt, kt, k.span, "this key")
                self.require_assignable(vtt, vt, v.span, "this value")
            return expected
        ki = self._infer_seq(keys, e.span)
        vi = self._infer_seq(vals, e.span)
        return T.map_of(ki.args[0] if ki.kind == "vector" else T.ANY, vi.args[0] if vi.kind == "vector" else T.ANY)

    def ex_EmptyBraces(self, e: A.EmptyBraces, expected):
        if expected is not None and expected.kind == "set":
            e.as_map = False
            return expected
        if expected is not None and expected.kind == "map":
            return expected
        if expected is not None and T.is_seq(expected):
            self.err("FX0436", "{ } is for maps and sets; an empty list is [ ]", e.span)
            return expected
        self.err("FX0437", "can't tell whether `{}` is a map or a set", e.span,
                 fix="write the type, e.g. `map<string, int> m = {};`")
        return T.UNKNOWN

    def ex_TupleLit(self, e: A.TupleLit, expected):
        if expected is not None and T.is_seq(expected):
            self.err("FX0436", "( ) is a tuple; a list uses [ ]", e.span)
        if expected is not None and expected.kind == "tuple":
            if len(expected.args) != len(e.items):
                self.err("FX0439", f"{expected} has {len(expected.args)} items, but {len(e.items)} are given", e.span)
            tys = []
            for it, w in zip(e.items, expected.args):
                t = self.expr(it, w)
                self.require_assignable(t, w, it.span, "this item")
                tys.append(w)
            for it in e.items[len(expected.args):]:
                self.expr(it)
            return expected
        return T.tuple_of(*[self.expr(it) for it in e.items])

    def ex_Comprehension(self, e: A.Comprehension, expected):
        it = self.expr(e.iterable)
        et = T.elem(it) if it.kind != "map" else it.args[0]
        self.push("implicit")
        e.var_sym = self.declare(e.var, Symbol(e.var, "loopvar", et, e.span, used=True), e.span)
        if e.cond is not None:
            e.cond = self.check_cond(e.cond)
        want = expected.args[0] if expected is not None and T.is_seq(expected) else None
        t = self.expr(e.expr, want)
        self.pop()
        if expected is not None and T.is_seq(expected):
            return expected
        return T.vector(t)

    # ---- names --------------------------------------------------------
    def ex_Name(self, e: A.Name, expected):
        sym, ty = self.scope.lookup(e.id)
        if sym is None:
            canon = IMPLICIT_VARS.get(norm(e.id))
            if canon is not None and canon != e.id:
                sym, ty = self.scope.lookup(canon)
                if sym is not None:
                    e.id = canon
        if sym is None:
            if e.id in PRELUDE:
                self.err("FX0307", f"`{e.id}` is a function; call it with ( )", e.span)
                return T.UNKNOWN
            self._undefined(e)
            return T.UNKNOWN
        e.sym = sym
        sym.used = True
        if sym in self.unassigned:
            self.err("FX0302", f"`{e.id}` is used before it has a value", e.span,
                     fix=f"give `{e.id}` a value first (or a value where it is declared)")
            self.unassigned.discard(sym)
        if sym.kind == "class":
            return Ty("classref", name=sym.cls.name if sym.cls is not None else e.id)
        if sym.kind == "func":
            return Ty("funcref", name=e.id)
        return ty

    def ex_This(self, e, expected):
        if self.cur_class is None or self.cur_fn is None or self.cur_fn.static:
            self.err("FX0710", "`this` can only be used inside a class method", e.span)
            return T.UNKNOWN
        return T.class_ty(self.cur_class.name)

    def ex_Super(self, e, expected):
        if self.cur_class is None or self.cur_class.parent is None:
            self.err("FX0711", "`super` needs a class with a parent", e.span)
            return T.UNKNOWN
        return Ty("super", name=self.cur_class.parent)

    # ---- operators -----------------------------------------------------
    def ex_Unary(self, e: A.Unary, expected):
        if e.op == "not":
            if isinstance(e.operand, A.StrLit):
                e.operand = self.check_cond(e.operand)
                return T.BOOL
            t = self.expr(e.operand, T.BOOL)
            if t != T.BOOL and not T.is_unknown(t):
                self.err("FX0440", f"`not` needs true/false, not {t}", e.span)
            return T.BOOL
        t = self.expr(e.operand, expected)
        if e.op == "-":
            if not T.is_numeric(t) and not T.is_unknown(t) and t.kind != "duration":
                self.err("FX0441", f"can't negate {t}", e.span)
            return t
        if e.op == "~":
            if not T.is_int(t) and not T.is_unknown(t):
                self.err("FX0441", f"`~` needs a whole number, not {t}", e.span)
            return t
        return t

    def ex_Binary(self, e: A.Binary, expected):
        num_expected = expected if expected is not None and T.is_numeric(expected) else None
        lt = self.expr(e.left, num_expected)
        rt = self.expr(e.right, num_expected if e.op not in ("<<", ">>", "**") else None)
        if e.op == "^" and isinstance(e.left, A.IntLit) and isinstance(e.right, A.IntLit):
            self.warn("FX0442", "`^` is XOR, not power", e.span,
                      fix=f"did you mean {e.left.value} ** {e.right.value}?")
        r = self.binary_type(e, lt, rt)
        return r if r is not None else T.UNKNOWN

    def binary_type(self, e: A.Binary, lt: Ty, rt: Ty) -> Optional[Ty]:
        op = e.op
        if T.is_unknown(lt) or T.is_unknown(rt):
            return T.UNKNOWN
        if lt.kind == "maybe" or rt.kind == "maybe":
            self.err("FX0443", "this value may be none; handle that first (?? default, or check it)", e.span)
            return T.UNKNOWN
        if op == "+":
            if lt.kind == "string" and rt.kind in ("string", "char"):
                return T.STRING
            if lt.kind == "char" and rt.kind == "string":
                return T.STRING
            if lt.kind == "string" or rt.kind == "string":
                self.err("FX0444", f"can't add {lt} and {rt}; numbers don't turn into text automatically",
                         e.span, fix='use an f-string: f"age: {age}"')
                return None
            if T.is_seq(lt) and T.is_seq(rt):
                if lt.kind == "list" or rt.kind == "list":
                    return T.list_of()
                if lt.args[0] != rt.args[0]:
                    self.err("FX0445", f"can't join {lt} and {rt}", e.span)
                return lt
        if op == "*":
            if lt.kind == "string" and T.is_int(rt):
                return T.STRING
            if T.is_seq(lt) and T.is_int(rt):
                return lt
        if lt.kind == "set" and rt.kind == "set" and op in ("|", "&", "-", "^"):
            return lt
        if lt.kind == "duration" or rt.kind == "duration":
            # durations: d + d, d - d, d * n, n * d, d / n -> duration; d / d -> float
            if lt.kind == rt.kind == "duration" and op in ("+", "-"):
                return T.DURATION
            if lt.kind == rt.kind == "duration" and op == "/":
                return T.FLOAT
            if op in ("*",) and (T.is_numeric(lt) or T.is_numeric(rt)):
                return T.DURATION
            if op == "/" and lt.kind == "duration" and T.is_numeric(rt):
                return T.DURATION
            self.err("FX0446", f"`{op}` can't be used on {lt} and {rt}", e.span,
                     fix="durations can be added, subtracted, and multiplied or divided by a number")
            return None
        if op in ("&", "|", "^", "<<", ">>"):
            if T.is_int(lt) and T.is_int(rt):
                return lt if op in ("<<", ">>") else (T.widen_ints(lt, rt) or lt)
            self.err("FX0446", f"`{op}` needs whole numbers (or two sets), not {lt} and {rt}", e.span)
            return None
        if not (T.is_numeric(lt) and T.is_numeric(rt)):
            self.err("FX0446", f"`{op}` can't be used on {lt} and {rt}", e.span)
            return None
        if op == "/":
            if lt.kind == "f32" and rt.kind in ("f32",) + tuple(INT_RANGES):
                return Ty("f32")
            return T.FLOAT
        res = T.numeric_result(lt, rt)
        if res is None:
            self.err("FX0447", f"can't mix {lt} and {rt} (signed and unsigned of the same size)", e.span,
                     fix="cast one side with `as`")
            return None
        if op == "**" and T.is_int(res) and isinstance(e.right, A.IntLit) and e.right.value < 0:
            return T.FLOAT
        return res

    def ex_Compare(self, e: A.Compare, expected):
        tys = []
        for k, o in enumerate(e.operands):
            if isinstance(o, A.NoneLit):
                tys.append(T.NONE)
                o.ty = T.NONE
                continue
            other = None
            if k > 0 and tys[-1] is not None and T.is_numeric(tys[-1]):
                other = tys[-1]
            tys.append(self.expr(o, other))
        for k, op in enumerate(e.ops):
            a, b = tys[k], tys[k + 1]
            sp = e.operands[k + 1].span
            if T.is_unknown(a) or T.is_unknown(b):
                continue
            if op in ("in", "not in"):
                self._check_in(a, b, sp)
                continue
            if op == "===":
                if a.kind != "class" or b.kind != "class":
                    self.err("FX0448", "`===` checks for the same object; it only works on class objects", sp,
                             fix="use `==` to compare values")
                continue
            if a.kind == "none" or b.kind == "none":
                other = b if a.kind == "none" else a
                if op not in ("==", "!="):
                    self.err("FX0449", "none can only be compared with == or !=", sp)
                elif other.kind not in ("maybe", "none"):
                    self.err("FX0450", f"this is {other}, which can never be none", sp)
                continue
            if op in ("<", "<=", ">", ">="):
                if (T.is_numeric(a) and T.is_numeric(b)) or (a == b and a.kind in ("string", "char", "duration")):
                    if T.is_numeric(a) and T.numeric_result(a, b) is None:
                        self.err("FX0447", f"can't compare {a} with {b} (signed and unsigned)", sp)
                    continue
                if a.kind == "class" and a == b and "order" in self.classes.get(a.name, ClassInfo("")).derive:
                    continue          # @derive(order)
                if a.kind == "maybe" or b.kind == "maybe":
                    self.err("FX0443", "this value may be none; handle that first", sp)
                    continue
                self.err("FX0451", f"can't order {a} and {b}", sp)
                continue
            # == / !=
            if T.is_numeric(a) and T.is_numeric(b):
                continue
            if a.kind == "class" and b.kind == "class":
                ci = self.classes.get(a.name)
                if ci is not None and "eq" not in ci.derive and not ci.is_error:
                    self.err("FX0712", f"`==` needs @derive(eq) on class {a.name}", sp,
                             fix=f"add @derive(eq) to class {a.name}, or use === for the same object")
                continue
            base_a = a.args[0] if a.kind == "maybe" else a
            base_b = b.args[0] if b.kind == "maybe" else b
            if base_a == base_b or T.assignable(base_a, base_b) or T.assignable(base_b, base_a):
                continue
            if {a.kind, b.kind} <= {"char", "string"}:
                continue
            self.err("FX0452", f"can't compare {a} with {b} (different types)", sp)
        return T.BOOL

    def _check_in(self, item: Ty, coll: Ty, span: Span) -> None:
        if coll.kind == "map":
            want = coll.args[0]
        elif T.is_seq(coll) or coll.kind in ("set", "range"):
            want = T.elem(coll)
        elif coll.kind == "string":
            if item.kind not in ("string", "char"):
                self.err("FX0453", f"can't look for {item} in a string", span)
            return
        else:
            self.err("FX0453", f"`in` needs a collection, a string or a range, not {coll}", span)
            return
        if not (T.is_unknown(want) or T.assignable(item, want) or T.assignable(want, item) or
                (T.is_numeric(item) and T.is_numeric(want))):
            self.err("FX0453", f"can't look for {item} in {coll}", span)

    def ex_Logical(self, e: A.Logical, expected):
        if isinstance(e.left, A.StrLit):
            e.left = self.check_cond(e.left)
        else:
            lt = self.expr(e.left, T.BOOL)
            if lt != T.BOOL and not T.is_unknown(lt):
                self.err("FX0428", f"`{e.op}` needs true/false on both sides, but this is {lt}", e.left.span)
        wt, wf = self._narrow_from(e.left)
        narrow = wt if e.op == "and" else wf
        self.push("block")
        for n, t in narrow:
            self.scope.narrowed[n] = t
        if isinstance(e.right, A.StrLit):
            e.right = self.check_cond(e.right)
        else:
            rt = self.expr(e.right, T.BOOL)
            if rt != T.BOOL and not T.is_unknown(rt):
                self.err("FX0428", f"`{e.op}` needs true/false on both sides, but this is {rt}", e.right.span)
        self.scope = self.scope.parent  # no unused warnings for this helper scope
        return T.BOOL

    def ex_RangeExpr(self, e: A.RangeExpr, expected):
        as_list = expected is not None and T.is_seq(expected) and T.is_int(T.elem(expected))
        for part in (e.start, e.end, e.step):
            if part is not None:
                t = self.expr(part, T.INT)
                if not T.is_int(t) and not T.is_unknown(t):
                    self.err("FX0454", f"ranges need whole numbers, not {t}", part.span)
        if e.step is not None and isinstance(e.step, A.IntLit) and e.step.value == 0:
            self.err("FX0455", "a range step can't be 0", e.step.span)
        if as_list:
            return expected           # vector<int> nums = 1..=10;
        return T.range_of(T.INT)

    def ex_Coalesce(self, e: A.Coalesce, expected):
        if isinstance(e.left, A.Call):
            e.left.handled = True
        lt = self.expr(e.left)
        if isinstance(e.left, A.Call) and getattr(e.left, "fails", None):
            inner = lt
        elif lt.kind == "maybe":
            inner = lt.args[0]
        elif lt.kind == "result":
            inner = lt.args[0]
        elif T.is_unknown(lt):
            inner = T.UNKNOWN
        else:
            self.err("FX0456", f"`??` needs a value that may be none (or a call that can fail), not {lt}",
                     e.left.span)
            inner = lt
        rt = self.expr(e.right, inner)
        if isinstance(e.right, A.Call) and e.right.kind == "builtin" and e.right.target in ("panic", "unreachable"):
            return inner
        if rt.kind == "maybe":
            return rt if T.assignable(inner, rt) else inner
        self.require_assignable(rt, inner, e.right.span, "the default")
        return inner

    def ex_IfExpr(self, e: A.IfExpr, expected):
        e.cond = self.check_cond(e.cond)
        wt, wf = self._narrow_from(e.cond)
        self.push("block")
        for n, t in wt:
            self.scope.narrowed[n] = t
        a = self.expr(e.then, expected)
        self.scope = self.scope.parent
        self.push("block")
        for n, t in wf:
            self.scope.narrowed[n] = t
        b = self.expr(e.else_, expected)
        self.scope = self.scope.parent
        return self.unify(a, b, e.span) or T.UNKNOWN

    def ex_TypeTest(self, e: A.TypeTest, expected):
        self.expr(e.expr)
        self.resolve_type(e.target, allow_auto=False)
        return T.BOOL

    def ex_Cast(self, e: A.Cast, expected):
        src = self.expr(e.expr)
        dst = self.resolve_type(e.target, allow_auto=False)
        ok = (T.is_numeric(src) and T.is_numeric(dst)) or src == dst or T.is_unknown(src) or \
            (src.kind == "char" and T.is_int(dst)) or (T.is_int(src) and dst.kind == "char") or \
            (src.kind == "class" and dst.kind == "class") or (src.kind == "any")
        if not ok:
            fix = None
            if src.kind == "string" and T.is_numeric(dst):
                fix = 'use "...".toInt() / .toFloat(): parsing text can fail, so it gives a maybe value'
            elif dst.kind == "string":
                fix = "use .toString() or an f-string"
            self.err("FX0457", f"can't convert {src} to {dst}", e.span, fix)
        if e.mode == "as?":
            return T.maybe(dst)
        return dst

    def ex_Propagate(self, e: A.Propagate, expected):
        inner = e.expr
        if isinstance(inner, A.Call):
            inner.handled = True
        t = self.expr(inner, expected)
        fails = getattr(inner, "fails", None) if isinstance(inner, A.Call) else None
        if not fails:
            if t.kind == "result":
                fails = ["Error"]
                t = t.args[0]
            elif t.kind == "maybe":
                self.err("FX0621", "postfix `?` passes failures up; for a value that may be none use ?? or a check",
                         e.span)
                return t.args[0]
            elif not T.is_unknown(t):
                self.err("FX0622", "postfix `?` only works on a call that can fail (@fails)", e.span)
                return t
        fn = self.cur_fn
        if fn is None or not fn.fails:
            self.err("FX0623", "`?` passes the failure to the caller, so it can only be used inside an "
                     "@fails function", e.span, fix="add @fails(...) to the function, or use try/catch")
        else:
            for f in fails or []:
                if not any(T._is_subclass(f, ff) for ff in fn.fails):
                    self.err("FX0624", f"`{fn.name}` doesn't declare @fails({f}), so `?` can't pass it on",
                             e.span, fix=f"add {f} to @fails(...)")
        return t

    def ex_SwitchExpr(self, e: A.SwitchExpr, expected):
        return self.check_switch(e.switch)

    def ex_Lambda(self, e: A.Lambda, expected):
        fi = FuncInfo("<function>")
        e.info = fi
        saved = (self.scope, self.cur_fn, self.return_types, self.loop_depth, self.block_depth)
        self.push("function", fi)
        self.cur_fn = fi
        self.loop_depth = 0
        self.block_depth = 0
        for p in e.params:
            if p.type is None:
                self.err("FX0603", f"parameter `{p.name}` needs a type", p.span)
                pty = T.UNKNOWN
            else:
                pty = self.resolve_type(p.type, allow_auto=False)
            fi.params.append(ParamInfo(p.name, pty, has_default=p.default is not None))
            p.sym = self.declare(p.name, Symbol(p.name, "param", pty, p.span, used=True), p.span)
        if isinstance(e.body, A.Block):
            self.return_types = []
            self.check_block_stmts(e.body)
            fi.returns = self._unify_returns(self.return_types, e.span)
        else:
            t = self.expr(e.body)
            fi.returns = [] if t == T.VOID else [t]
        self.scope = saved[0]
        (self.cur_fn, self.return_types, self.loop_depth, self.block_depth) = saved[1:]
        return Ty("func", (Ty("multi", tuple(p.ty for p in fi.params)), fi.return_ty))

    def ex_BindCond(self, e, expected):
        self.err("FX0458", "`auto v = x` only works directly inside if ( )", e.span)
        return T.BOOL

    def ex_ImplicitFn(self, e: A.ImplicitFn, expected):
        return e.ty or T.UNKNOWN

    # ---- member / index -------------------------------------------------
    def ex_Member(self, e: A.Member, expected):
        ot = self.expr(e.obj)
        if T.is_unknown(ot):
            return T.UNKNOWN
        if e.safe:
            if ot.kind != "maybe":
                self.err("FX0459", "`?.` is for values that may be none; use `.` here", e.span)
            else:
                ot = ot.args[0]
        r = self._member_type(e, ot)
        if e.safe and ot.kind != "maybe" and not T.is_unknown(r):
            return T.maybe(r)
        return r

    def _member_type(self, e: A.Member, ot: Ty) -> Ty:
        name = e.name
        if ot.kind == "tuple":
            if name.isdigit():
                k = int(name)
                if k >= len(ot.args):
                    self.err("FX0460", f"this tuple has {len(ot.args)} items; .{k} doesn't exist", e.span)
                    return T.UNKNOWN
                return ot.args[k]
            self.err("FX0460", "tuple items are read with .0, .1, ...", e.span)
            return T.UNKNOWN
        if ot.kind == "result":
            if name == "value":
                return ot.args[0]
            if name == "error":
                return T.class_ty("Error")
        if ot.kind == "maybe" and name in ("value",):
            return ot.args[0]
        if ot.kind == "classref":
            ci = self.classes.get(ot.name)
            if ci is not None:
                f = self.find_field(ci, name)
                if f is not None:
                    if not f.static:
                        self.err("FX0713", f"`{name}` belongs to each object, not to the class", e.span)
                    self._privacy(f.private, f.owner, name, e.span)
                    e.field_info = f
                    return f.ty
            self.err("FX0714", f"class {ot.name} has no static field `{name}`", e.span)
            return T.UNKNOWN
        if ot.kind == "class":
            ci = self.classes.get(ot.name)
            if ci is None:
                return T.UNKNOWN
            f = self.find_field(ci, name)
            if f is not None:
                self._privacy(f.private, f.owner, name, e.span)
                e.field_info = f
                return f.ty
            m = self.find_method(ci, name)
            if m is not None and "property" in m.annots:
                self.ensure_signature(m, e.span)
                e.field_info = None
                e._property = m  # type: ignore[attr-defined]
                return m.return_ty
            if m is not None:
                self.err("FX0715", f"`{name}` is a method; call it with ( )", e.span)
                return T.UNKNOWN
            names = list(self._all_fields(ci)) + list(self._all_methods(ci))
            guess = typo_match(name, names)
            self.err("FX0716", f"{ot.name} has no field `{name}`", e.span,
                     fix=f"did you mean `{guess}`?" if guess else None)
            return T.UNKNOWN
        if ot.kind == "maybe":
            self.err("FX0443", f"this may be none; use `?.{name}` or check it first", e.span)
            return T.UNKNOWN
        self.err("FX0716", f"{ot} has no field `{name}`", e.span,
                 fix="call a method with ( ), e.g. .size()" if T.is_collection(ot) or ot.kind == "string" else None)
        return T.UNKNOWN

    def _privacy(self, private: bool, owner: str, name: str, span: Span) -> None:
        if not private:
            return
        cc = self.cur_class
        if cc is None or not T._is_subclass(cc.name, owner):
            self.err("FX0717", f"`{name}` is private to class {owner}", span,
                     fix="mark it @public, or add a method that returns it")

    def ex_Index(self, e: A.Index, expected):
        ot = self.expr(e.obj)
        it = self.expr(e.index, T.INT if (T.is_seq(ot) or ot.kind == "string") else
                       (ot.args[0] if ot.kind == "map" else None))
        if T.is_unknown(ot):
            return T.UNKNOWN
        if T.is_seq(ot) or ot.kind == "string":
            if it.kind == "range":
                return ot
            if not T.is_int(it) and not T.is_unknown(it):
                self.err("FX0461", f"positions are whole numbers, not {it}", e.index.span)
            return T.CHAR if ot.kind == "string" else ot.args[0]
        if ot.kind == "map":
            self.require_assignable(it, ot.args[0], e.index.span, "the key")
            return ot.args[1]
        if ot.kind == "tuple":
            if isinstance(e.index, A.IntLit) and 0 <= e.index.value < len(ot.args):
                return ot.args[e.index.value]
            self.err("FX0460", "tuple items need a fixed position, e.g. t[0] or t.0", e.index.span)
            return T.UNKNOWN
        if ot.kind == "maybe":
            self.err("FX0443", "this may be none; check it first", e.span)
            return T.UNKNOWN
        self.err("FX0462", f"{ot} can't be indexed with [ ]", e.span)
        return T.UNKNOWN

    # ==================================================================
    # Calls
    # ==================================================================
    def ex_Call(self, e: A.Call, expected):
        f = e.func
        if isinstance(f, A.Member):
            return self._method_call(e, f, expected)
        if isinstance(f, A.Name):
            sym, ty = self.scope.lookup(f.id)
            if sym is None and f.id in PRELUDE:
                return self._builtin_call(e, f.id, expected)
            if sym is None:
                simple = canonical_simple_type(f.id)
                if simple is not None and simple not in ("auto", "any", "number"):
                    # T(x) == x as T
                    if len(e.args) != 1:
                        self.err("FX0463", f"{f.id}(x) takes one value", e.span)
                        return T.UNKNOWN
                    cast = A.Cast(e.span, e.args[0].value, A.TypeExpr(f.span, f.id), "as")
                    t = self.expr(cast)
                    e.kind = "cast"
                    e.target = cast
                    return t
                self._undefined(f)
                for a in e.args:
                    self.expr(a.value)
                return T.UNKNOWN
            f.sym = sym
            sym.used = True
            if sym.kind == "func":
                fi = sym.func
                self.ensure_signature(fi, e.span)
                e.kind = "static" if fi.owner is not None else "func"
                e.target = fi
                self._bind_args(e, fi.params, fi.name)
                self._fails_check(e, fi)
                f.ty = Ty("funcref", name=fi.name)
                return self._std_infer(fi, e) if getattr(fi, "infer", None) else fi.return_ty
            if sym.kind == "class":
                return self._ctor_call(e, sym.cls, None)
            if ty is not None and ty.kind == "func":
                params_t, ret = ty.args
                e.kind = "value"
                ptys = list(params_t.args)
                if len(e.args) != len(ptys):
                    self.err("FX0464", f"`{f.id}` takes {len(ptys)} value(s), {len(e.args)} given", e.span)
                for a, pt in zip(e.args, ptys):
                    self.require_assignable(self.expr(a.value, pt), pt, a.value.span, "this argument")
                f.ty = ty
                return ret
            self.err("FX0465", f"`{f.id}` is not a function", f.span)
            for a in e.args:
                self.expr(a.value)
            return T.UNKNOWN
        self.err("FX0465", "this can't be called", e.span)
        return T.UNKNOWN

    def _fails_check(self, e: A.Call, fi: FuncInfo) -> None:
        if not fi.fails:
            return
        e.fails = list(fi.fails)  # type: ignore[attr-defined]
        if e.handled or self.try_depth > 0 or getattr(self, "_result_ctx", False):
            return
        self.err("FX0625", f"`{fi.name}` can fail with {', '.join(fi.fails)}, and that isn't handled here",
                 e.span, fix="wrap it in try/catch, add `?? default`, or add `?` inside an @fails function")

    def _bind_args(self, e: A.Call, params: list[ParamInfo], fname: str) -> None:
        """Match call arguments to parameters (positional, then named). Checks types."""
        bound: dict[str, A.Expr] = {}
        pos = [a for a in e.args if a.name is None]
        named = [a for a in e.args if a.name is not None]
        seen_named = False
        for a in e.args:
            if a.name is not None:
                seen_named = True
            elif seen_named:
                self.err("FX0466", "positional arguments must come before named ones", a.value.span)
        pi = 0
        variadic_vals: list[A.Expr] = []
        for a in pos:
            if pi < len(params) and params[pi].variadic:
                variadic_vals.append(a.value)
                continue
            if pi >= len(params):
                self.err("FX0464", f"`{fname}` takes {len(params)} argument(s), but more are given", a.value.span)
                self.expr(a.value)
                continue
            bound[params[pi].name] = a.value
            pi += 1
        for a in named:
            match = next((p for p in params if p.name == a.name), None)
            if match is None:
                guess = typo_match(a.name, [p.name for p in params])
                self.err("FX0467", f"`{fname}` has no parameter `{a.name}`", a.span or a.value.span,
                         fix=f"did you mean `{guess}`?" if guess else None)
                self.expr(a.value)
                continue
            if match.name in bound:
                self.err("FX0468", f"`{a.name}` is given twice", a.value.span)
            bound[match.name] = a.value
        for p in params:
            if p.variadic:
                continue
            if p.name not in bound and not p.has_default:
                self.err("FX0469", f"`{fname}` needs `{p.name}`", e.span)
        for p in params:
            if p.name in bound and p.ty.kind == "func":
                self._check_function_arg(bound[p.name], p, fname)
                continue
            if p.name in bound:
                v = bound[p.name]
                t = self.expr(v, p.ty)
                self.require_assignable(t, p.ty, v.span, f"`{p.name}`")
                if p.ref and not isinstance(v, (A.Name, A.Member, A.Index)):
                    self.err("FX0470", f"`{p.name}` is @ref, so it needs a variable, not a value", v.span)
            if p.variadic:
                for v in variadic_vals:
                    et = T.elem(p.ty)
                    self.require_assignable(self.expr(v, et), et, v.span, f"`{p.name}`")
        e.bound = bound
        e._variadic = variadic_vals  # type: ignore[attr-defined]

    def _check_function_arg(self, v: A.Expr, p: ParamInfo, fname: str) -> None:
        """A parameter of function type (e.g. a Server handler) given a function by name or a Function(...)."""
        want_params, want_ret = p.ty.args
        want = f"function[<{', '.join(str(a) for a in want_params.args)}>|<{want_ret}>]"
        t = self.expr(v)
        if t.kind == "funcref":
            fi = self.functions.get(t.name)
            if fi is None:
                return
            self.ensure_signature(fi, v.span)
            ok = len(fi.params) == len(want_params.args) and \
                all(T.assignable(w, pi.ty) for w, pi in zip(want_params.args, fi.params)) and \
                T.assignable(fi.return_ty, want_ret)
            if not ok:
                self.err("FX0479", f"`{fi.name}` must be a {want} to be used here", v.span)
            v.ty = p.ty
            return
        if t.kind == "func":
            if t != p.ty:
                self.err("FX0479", f"this function must be a {want}", v.span)
            return
        if not T.is_unknown(t):
            self.err("FX0479", f"`{p.name}` of {fname} needs a function ({want}), not {t}", v.span,
                     fix="pass the name of a function, without ( )")

    def _ctor_call(self, e: A.Call, ci: ClassInfo, by_name: Optional[FuncInfo]) -> Ty:
        if ci.annots == {"std"}:
            makers = [m for m, f in ci.methods.items() if f.static and f.returns == [T.class_ty(ci.name)]]
            self.err("FX0314", f"{ci.name} values can't be made with {ci.name}(...)", e.span,
                     fix=(f"use {ci.name}.{makers[0]}(...)" if makers else
                          f"{ci.name} is a module: call its functions, e.g. {ci.name}.{next(iter(ci.methods), '...')}(...)"))
            return T.class_ty(ci.name)
        if "abstract" in ci.annots:
            self.err("FX0718", f"class {ci.name} is @abstract and can't be created directly", e.span)
        style = self.pending_style
        self.pending_style = None
        cands = ci.ctors
        if by_name is not None:
            chosen = by_name
        elif style is not None:
            chosen = next((c for c in cands if c.style == style), None)
            if chosen is None:
                self.err("FX0719", f"class {ci.name} has no constructor with @style('{style}')", e.span)
                chosen = cands[0] if cands else None
        elif len(cands) == 1:
            chosen = cands[0]
        else:
            fitting = [c for c in cands if self._args_fit(e, c.params)]
            if len(fitting) == 1:
                chosen = fitting[0]
            elif not fitting:
                self.err("FX0720", f"no constructor of {ci.name} takes these arguments", e.span)
                chosen = cands[0]
            else:
                self.err("FX0721", f"more than one constructor of {ci.name} fits; pick one with @using('...')",
                         e.span)
                chosen = fitting[0]
        e.kind = "ctor"
        e.target = (ci, chosen)
        if chosen is not None:
            self._bind_args(e, chosen.params, ci.name)
        return T.class_ty(ci.name)

    def _args_fit(self, e: A.Call, params: list[ParamInfo]) -> bool:
        pos = [a for a in e.args if a.name is None]
        if len(pos) > len(params):
            return False
        required = [p for p in params if not p.has_default]
        if len(pos) < len(required) and not any(a.name for a in e.args):
            return False
        # quick type probe on literals
        for a, p in zip(pos, params):
            v = a.value
            lit_ty = {A.IntLit: T.INT, A.FloatLit: T.FLOAT, A.StrLit: T.STRING, A.BoolLit: T.BOOL}.get(type(v))
            if lit_ty is not None and not T.assignable(lit_ty, p.ty):
                return False
        return True

    def _builtin_call(self, e: A.Call, name: str, expected) -> Ty:
        e.kind = "builtin"
        e.target = name
        if name in ("str",):
            e.target = name = "string"
        if name in ("crash", "abort"):
            e.target = name = "panic"
        if name == "assert":
            e.target = name = "ensure"
        args = e.args
        tys = []
        for a in args:
            if name == "ensure" and a is args[0]:
                a.value = self.check_cond(a.value)
                tys.append(T.BOOL)
                continue
            tys.append(self.expr(a.value))
        pos_n = len([a for a in args if a.name is None])
        if name == "print":
            for a in args:
                if a.name is not None and canonical_arg(a.name) not in ("sep", "end") and a.name != "end":
                    self.err("FX0467", f"print has no option `{a.name}` (only sep and end)", a.value.span)
            for a, t in zip(args, tys):
                if t == T.VOID:
                    self.err("FX0406", "this returns nothing, so it can't be printed", a.value.span)
            if self.cur_fn is not None and "pure" in self.cur_fn.annots:
                self.err("FX0626", "a @pure function can't print", e.span)
            return T.VOID
        if name == "input":
            if pos_n > 1:
                self.err("FX0464", "input takes at most one prompt", e.span)
            return T.STRING
        if name == "len":
            if pos_n != 1:
                self.err("FX0464", "len takes one value", e.span)
            elif not (T.is_collection(tys[0]) or tys[0].kind == "string" or T.is_unknown(tys[0])):
                self.err("FX0471", f"len needs a collection or text, not {tys[0]}", e.span)
            return T.INT
        if name in ("min", "max"):
            if pos_n == 1 and (T.is_seq(tys[0]) or tys[0].kind == "set"):
                return T.maybe(T.elem(tys[0]))
            if pos_n < 2:
                self.err("FX0464", f"{name} takes two or more numbers (or one collection)", e.span)
                return T.UNKNOWN
            r = tys[0]
            for t in tys[1:]:
                r = self.unify(r, t, e.span) or r
            return r
        if name == "abs":
            return tys[0] if tys else T.UNKNOWN
        if name == "round":
            if pos_n >= 2 or any(canonical_arg(a.name or "") == "digits" for a in args):
                return T.FLOAT
            return T.INT
        if name in ("floor", "ceil"):
            return T.INT
        if name == "sum":
            return T.elem(tys[0]) if tys else T.UNKNOWN
        if name == "sorted":
            return tys[0] if tys else T.UNKNOWN
        if name == "range":
            return T.range_of(T.INT)
        if name in ("panic", "unreachable"):
            return T.VOID
        if name == "ensure":
            if not args:
                self.err("FX0464", "ensure(condition, \"message\")", e.span)
            return T.VOID
        if name == "string":
            return T.STRING
        if name == "exit":
            return T.VOID
        if name == "sleep":
            if pos_n != 1 or (tys and tys[0].kind != "duration" and not T.is_unknown(tys[0])):
                self.err("FX0477", "sleep takes a duration, e.g. sleep(500ms) or sleep(2s)", e.span)
            return T.VOID
        return T.UNKNOWN

    # ---- methods ------------------------------------------------------------
    def _method_call(self, e: A.Call, f: A.Member, expected) -> Ty:
        obj = f.obj
        # static access: ClassName.method(...) / ClassName.ctorName(...)
        if isinstance(obj, A.Name):
            sym, _ = self.scope.lookup(obj.id)
            if sym is not None and sym.kind == "class":
                obj.sym = sym
                obj.ty = Ty("classref", name=obj.id)
                ci = sym.cls
                ctor = next((c for c in ci.ctors if c.decl is not None and c.decl.name == f.name), None)
                if ctor is not None:
                    return self._ctor_call(e, ci, ctor)
                m = self.find_method(ci, f.name)
                if m is None:
                    self.err("FX0722", f"class {ci.name} has no method `{f.name}`", f.span)
                    return T.UNKNOWN
                if not m.static:
                    self.err("FX0723", f"`{f.name}` is not @static; call it on an object", f.span)
                self.ensure_signature(m, e.span)
                e.kind = "static"
                e.target = m
                self._bind_args(e, m.params, m.name)
                self._fails_check(e, m)
                return self._std_infer(m, e) if getattr(m, "infer", None) else m.return_ty
        ot = self.expr(obj)
        f.ty = ot
        if T.is_unknown(ot):
            for a in e.args:
                self.expr(a.value)
            return T.UNKNOWN
        if f.safe:
            if ot.kind != "maybe":
                self.err("FX0459", "`?.` is for values that may be none; use `.` here", f.span)
            else:
                inner_t = self._method_on(e, f, ot.args[0], expected)
                return T.maybe(inner_t) if inner_t not in (T.VOID,) else T.VOID
        return self._method_on(e, f, ot, expected)

    def _method_on(self, e: A.Call, f: A.Member, ot: Ty, expected) -> Ty:
        if ot.kind in ("class", "super"):
            ci = self.classes.get(ot.name)
            if ci is None:
                return T.UNKNOWN
            m = self.find_method(ci, f.name)
            if m is None:
                guess = typo_match(f.name, list(self._all_methods(ci)))
                self.err("FX0722", f"{ci.name} has no method `{f.name}`", f.span,
                         fix=f"did you mean `{guess}`?" if guess else None)
                for a in e.args:
                    self.expr(a.value)
                return T.UNKNOWN
            if m.private and (self.cur_class is None or not T._is_subclass(self.cur_class.name, m.owner or "")):
                self.err("FX0717", f"`{f.name}` is private to class {m.owner}", f.span)
            if "property" in m.annots:
                self.err("FX0724", f"`{f.name}` is a property; read it without ( )", f.span)
            self.ensure_signature(m, e.span)
            e.kind = "method" if ot.kind == "class" else "super"
            e.target = m
            self._bind_args(e, m.params, m.name)
            self._fails_check(e, m)
            return m.return_ty
        kind = T.receiver_kind(ot)
        if kind is None:
            self.err("FX0801", f"{ot} has no methods", f.span)
            return T.UNKNOWN
        canon = lookup_method(kind, f.name)
        # context-dependent aliases
        if canon == "count" and not e.args:
            canon = "size"
        if canon is None:
            spellings = method_spellings(kind)
            near = typo_match(f.name, list(spellings))
            guess = spellings[near] if near is not None else None
            if guess is not None:
                self.warn("FX0802", f"`{f.name}` is not a method of {ot}; using `{guess}`", f.span,
                          fix=f"write `{guess}`")
                canon = guess
            else:
                self.err("FX0801", f"`{f.name}` is not a method of {ot}", f.span,
                         fix="see the method bank in Syntax/collections.fx3; resolving unknown methods "
                             "with the LLM is not available yet")
                return T.UNKNOWN      # arguments are not checked: they may use currVal etc.
        if canon == "filter" and norm(f.name) == "select" and e.args:
            # select(bool) -> filter, select(value) -> map
            probe = self._probe_is_bool(e.args[0].value, ot)
            if not probe:
                canon = "map"
        e.kind = "bank"
        e.target = canon
        e.recv_ty = ot  # type: ignore[attr-defined]
        f.name = canon
        if ot.kind == "string" and canon in ("replace", "reverse"):
            pass
        elif METHOD_KIND.get(canon) == "action":
            self._check_mutable(f.obj, "changed")
        return self._bank_call(e, canon, ot, kind, expected)

    def _probe_is_bool(self, v: A.Expr, ot: Ty) -> bool:
        if isinstance(v, (A.Compare, A.Logical, A.BoolLit, A.StrLit)):
            return True
        if isinstance(v, A.Unary) and v.op == "not":
            return True
        return False

    # Which parameters of each bank method may use implicit variables, and how.
    _IMPLICIT_PARAMS = {
        "condition": "cond", "newVal": "value", "by": "key",
    }

    def _bank_call(self, e: A.Call, canon: str, ot: Ty, kind: str, expected) -> Ty:
        params = METHOD_PARAMS[canon]
        bound: dict[str, A.Expr] = {}
        pos = [a for a in e.args if a.name is None]
        named = [a for a in e.args if a.name is not None]
        for k, a in enumerate(pos):
            if k >= len(params):
                self.err("FX0464", f"`{canon}` takes at most {len(params)} argument(s)", a.value.span)
                continue
            bound[params[k]] = a.value
        for a in named:
            cn = canonical_arg(a.name)
            if cn not in params:
                guess = typo_match(a.name, params)
                self.err("FX0467", f"`{canon}` has no option `{a.name}`", a.span or a.value.span,
                         fix=(f"did you mean `{guess}`?" if guess else
                              f"its options are: {', '.join(params) or 'none'}"))
                continue
            if cn in bound:
                self.err("FX0468", f"`{cn}` is given twice", a.value.span)
            bound[cn] = a.value
        e.bound = bound
        el = T.elem(ot) if kind in ("seq", "set", "string") else (ot.args[1] if kind == "map" else T.ANY)
        key_t = ot.args[0] if kind == "map" else T.INT

        def need(*names: str) -> bool:
            ok = True
            for n in names:
                if n not in bound:
                    self.err("FX0469", f"`{canon}` needs `{n}`", e.span)
                    ok = False
            return ok

        def plain(name: str, want: Optional[Ty]) -> Ty:
            v = bound[name]
            t = self.expr(v, want)
            if want is not None:
                self.require_assignable(t, want, v.span, f"`{name}`")
            return t

        def implicit(name: str, want: Optional[Ty], extra: Optional[dict] = None, as_cond: bool = False) -> Ty:
            v = bound[name]
            fn, t = self._implicit_fn(v, kind, el, key_t, want, extra, as_cond)
            bound[name] = fn
            return t

        # --- per method -----------------------------------------------------
        c = canon
        if c == "size" or (c == "count" and not bound):
            return T.INT
        if c == "isEmpty":
            return T.BOOL
        if c in ("get",):
            if kind == "map":
                if "index" in bound and "key" not in bound:
                    bound["key"] = bound.pop("index")
                if need("key"):
                    plain("key", key_t)
                return el
            if kind == "tuple":
                if "index" in bound and isinstance(bound["index"], A.IntLit) and \
                        0 <= bound["index"].value < len(ot.args):
                    self.expr(bound["index"], T.INT)
                    return ot.args[bound["index"].value]
                self.err("FX0460", "tuple.get needs a fixed position, e.g. t.get(0)", e.span)
                return T.UNKNOWN
            if need("index"):
                t = plain("index", None)
                if t.kind == "range":
                    return ot
            return el
        if c == "getOr":
            if kind in ("maybe", "result"):
                v = bound.pop("index", None) or bound.get("default")
                if v is None:
                    self.err("FX0469", "getOr needs a default value", e.span)
                    return ot.args[0]
                bound.clear()
                bound["default"] = v
                plain("default", ot.args[0])
                return ot.args[0]
            if kind == "map" and "index" in bound:
                bound["key"] = bound.pop("index")
            idx = "key" if kind == "map" else "index"
            if need(idx, "default"):
                plain(idx, key_t if kind == "map" else T.INT)
                plain("default", el)
            return el
        if c in ("first", "last", "pop", "popFront"):
            return T.maybe(el)
        if c == "slice":
            if need("index"):
                t = plain("index", None)
                if t.kind != "range" and not T.is_unknown(t):
                    self.err("FX0472", "slice needs a range, e.g. slice(1..3)", e.span)
            return ot
        if c == "keys":
            return T.vector(ot.args[0])
        if c == "values":
            return T.vector(ot.args[1])
        if c == "items":
            return T.vector(T.tuple_of(ot.args[0], ot.args[1]))
        if c == "add":
            if need("value"):
                plain("value", el if not (ot.kind == "list" and not ot.strict) else None)
            if "index" in bound:
                if kind == "set":
                    self.err("FX0473", "a set has no positions", bound["index"].span)
                plain("index", T.INT)
            return T.VOID
        if c == "addAll":
            if need("values"):
                t = plain("values", None)
                if not (T.is_seq(t) or t.kind == "set" or T.is_unknown(t)):
                    self.err("FX0474", f"addAll needs a collection, not {t}", bound["values"].span)
                elif not T.is_unknown(t) and not (ot.kind == "list" and not ot.strict) and \
                        not T.assignable(T.elem(t), el):
                    self.err("FX0401", f"can't add {T.elem(t)} values to {ot}", bound["values"].span)
            return T.VOID
        if c == "set":
            if kind == "map":
                if "index" in bound and "key" not in bound:
                    bound["key"] = bound.pop("index")
                if need("key", "value"):
                    plain("key", key_t)
                    plain("value", el)
            elif need("index", "value"):
                plain("index", T.INT)
                plain("value", el)
            return T.VOID
        if c == "removeAt":
            if need("index"):
                t = plain("index", None)
                if not (T.is_int(t) or t.kind == "range" or T.is_unknown(t)):
                    self.err("FX0461", f"removeAt needs a position or a range, not {t}", bound["index"].span)
            return T.VOID
        if c == "remove":
            if kind == "map":
                if "value" in bound:
                    bound["key"] = bound.pop("value")
                if need("key"):
                    plain("key", key_t)
                return T.VOID
            if need("value"):
                plain("value", el)
            if "all" in bound:
                plain("all", T.BOOL)
            return T.VOID
        if c == "removeWhere":
            if need("condition"):
                implicit("condition", T.BOOL, as_cond=True)
            return T.VOID
        if c == "clear":
            return T.VOID
        if c == "modifyAt":
            if kind == "map":
                if "index" in bound:
                    bound["key"] = bound.pop("index")
                if "key" in bound:
                    plain("key", key_t)
                if "default" in bound:
                    plain("default", el)
            else:
                if "index" in bound:
                    t = plain("index", None)
                    if not (T.is_int(t) or t.kind == "range" or (T.is_seq(t) and T.is_int(T.elem(t)))
                            or T.is_unknown(t)):
                        self.err("FX0461", f"index must be a position, a range or a list of positions, not {t}",
                                 bound["index"].span)
                if "default" in bound:
                    self.err("FX0467", "`default` only applies to maps", bound["default"].span)
            if "condition" in bound:
                implicit("condition", T.BOOL, as_cond=True)
            if need("newVal"):
                implicit("newVal", el)
            return T.VOID
        if c == "replace":
            if kind == "string":
                if need("old", "newVal"):
                    plain("old", T.STRING)
                    plain("newVal", T.STRING)
                return T.STRING
            if need("old", "newVal"):
                plain("old", el)
                plain("newVal", el)
            return T.VOID
        if c == "fill":
            if need("value"):
                plain("value", el)
            if "index" in bound:
                plain("index", None)
            return T.VOID
        if c == "swap":
            if need("index", "other"):
                plain("index", T.INT)
                plain("other", T.INT)
            return T.VOID
        if c in ("sort", "sorted"):
            if "by" in bound:
                implicit("by", None)
            if "desc" in bound:
                plain("desc", T.BOOL)
            if c == "sorted":
                return T.vector(el) if kind == "set" else ot
            return T.VOID
        if c in ("reverse",):
            if kind == "string":
                return T.STRING
            return T.VOID
        if c == "reversed":
            return ot if kind != "string" else T.STRING
        if c == "dedupe":
            return T.VOID
        if c == "contains":
            if kind == "map":
                if need("value"):
                    bound["key"] = bound.pop("value")
                    plain("key", key_t)
                return T.BOOL
            if kind == "string":
                if need("value"):
                    plain("value", T.STRING)
                return T.BOOL
            if need("value"):
                self._value_or_cond(bound, "value", kind, el, key_t)
            return T.BOOL
        if c == "indexOf":
            if kind == "string":
                if need("value"):
                    plain("value", T.STRING)
                return T.maybe(T.INT)
            if need("value"):
                self._value_or_cond(bound, "value", kind, el, key_t)
            return T.maybe(T.INT)
        if c == "find":
            if need("condition"):
                implicit("condition", T.BOOL, as_cond=True)
            return T.maybe(el)
        if c == "count":
            if kind == "string":
                plain("value", T.STRING)
                return T.INT
            self._value_or_cond(bound, "value", kind, el, key_t)
            return T.INT
        if c in ("any", "all"):
            if need("condition"):
                implicit("condition", T.BOOL, as_cond=True)
            return T.BOOL
        if c in ("min", "max"):
            if not (T.is_numeric(el) or el.kind in ("string", "char") or T.is_unknown(el)):
                self.err("FX0475", f"{c} needs numbers or text, not {el}", e.span)
            return T.maybe(el)
        if c == "sum":
            if not (T.is_numeric(el) or T.is_unknown(el)):
                self.err("FX0475", f"sum needs numbers, not {el}", e.span)
            return el
        if c == "average":
            if not (T.is_numeric(el) or T.is_unknown(el)):
                self.err("FX0475", f"average needs numbers, not {el}", e.span)
            return T.FLOAT
        if c == "filter":
            if need("condition"):
                implicit("condition", T.BOOL, as_cond=True)
            return ot
        if c == "map":
            if need("newVal"):
                t = implicit("newVal", None)
                if kind == "map":
                    return T.map_of(ot.args[0], t)
                if kind == "set":
                    return T.set_of(t)
                return T.vector(t) if not T.is_unknown(t) else T.list_of()
            return T.UNKNOWN
        if c == "reduce":
            st = None
            if "start" in bound:
                st = plain("start", None)
            acc_t = st if st is not None else el
            if need("newVal"):
                t = implicit("newVal", acc_t, extra={"acc": acc_t})
            return acc_t
        if c in ("take", "skip"):
            if need("count"):
                plain("count", T.INT)
            return ot
        if c == "join":
            if kind == "string":
                # ", ".join(list)
                if "sep" in bound:
                    t = plain("sep", None)
                    if not (T.is_seq(t) or T.is_unknown(t)):
                        self.err("FX0476", "write sep.join(list), e.g. \", \".join(names)", e.span)
                    bound["values"] = bound.pop("sep")
                return T.STRING
            if "sep" in bound:
                plain("sep", T.STRING)
            return T.STRING
        if c == "copy":
            return ot
        if c == "toVector":
            if kind == "map":
                return T.vector(T.tuple_of(ot.args[0], ot.args[1]))
            if ot.kind == "list" and not ot.strict and ot.args[0].kind == "any":
                return T.vector(T.ANY)
            return T.vector(el)
        if c == "toList":
            return T.list_of()
        if c == "toSet":
            return T.set_of(el if kind != "map" else ot.args[0])
        if c == "enumerate":
            return T.vector(T.tuple_of(T.INT, el))
        if c in ("union", "intersect", "difference"):
            if need("other"):
                plain("other", ot)
            return ot
        if c == "isSubset":
            if need("other"):
                plain("other", ot)
            return T.BOOL
        # strings
        if c in ("upper", "lower", "capitalize", "trim"):
            return T.STRING
        if c == "split":
            if "sep" in bound:
                plain("sep", T.STRING)
            return T.vector(T.STRING)
        if c in ("lines", "words"):
            return T.vector(T.STRING)
        if c in ("startsWith", "endsWith", "equalsIgnoreCase"):
            if need("value"):
                plain("value", T.STRING)
            return T.BOOL
        if c == "repeat":
            if need("count"):
                plain("count", T.INT)
            return T.STRING
        if c in ("padLeft", "padRight"):
            if need("count"):
                plain("count", T.INT)
            if "ch" in bound:
                plain("ch", T.CHAR)
            return T.STRING
        if c in ("isNumber", "isAlpha"):
            return T.BOOL
        if c == "toInt":
            return T.maybe(T.INT)
        if c == "toFloat":
            return T.maybe(T.FLOAT)
        if c == "toBool":
            return T.maybe(T.BOOL)
        if c == "chars":
            return T.vector(T.CHAR)
        if c == "toString":
            return T.STRING
        if c in ("seconds", "millis", "minutes", "hours"):
            return T.FLOAT
        if c in ("isNone", "isSome", "isOk", "isError"):
            return T.BOOL
        if c == "unwrap":
            if "msg" in bound:
                plain("msg", T.STRING)
            return ot.args[0]
        return T.UNKNOWN

    def _value_or_cond(self, bound: dict, name: str, kind: str, el: Ty, key_t: Ty) -> None:
        """contains / indexOf / count take a value OR a condition using currVal."""
        v = bound.get(name)
        if v is None:
            return
        if isinstance(v, A.StrLit) and el.kind != "string":
            fn, _ = self._implicit_fn(v, kind, el, key_t, T.BOOL, None, as_cond=True)
            bound["condition"] = fn
            del bound[name]
            return
        if self._uses_implicit(v):
            fn, _ = self._implicit_fn(v, kind, el, key_t, T.BOOL, None, as_cond=True)
            bound["condition"] = fn
            del bound[name]
            return
        t = self.expr(v, el)
        self.require_assignable(t, el, v.span, "the value to look for")

    def _uses_implicit(self, v: A.Expr) -> bool:
        found = False

        def walk(n) -> None:
            nonlocal found
            if isinstance(n, A.Name) and IMPLICIT_VARS.get(norm(n.id)) is not None:
                sym, _ = self.scope.lookup(n.id)
                if sym is None or sym.kind == "implicit":
                    found = True
            if isinstance(n, A.Node) or isinstance(n, A.Arg):
                for val in vars(n).values():
                    if isinstance(val, (A.Node, A.Arg)):
                        walk(val)
                    elif isinstance(val, list):
                        for x in val:
                            if isinstance(x, (A.Node, A.Arg)):
                                walk(x)
        walk(v)
        return found

    def _implicit_fn(self, v: A.Expr, kind: str, el: Ty, key_t: Ty, want: Optional[Ty],
                     extra: Optional[dict], as_cond: bool) -> tuple[A.ImplicitFn, Ty]:
        """Wrap an argument that may use currVal / index / key / prevVal / nextVal / acc."""
        self.push("implicit")
        syms: dict[str, Symbol] = {}

        def add(name: str, ty: Ty) -> None:
            s = Symbol(name, "implicit", ty, v.span, used=False)
            s.owner_fn = self.scope.fn
            # a user variable with the same name wins (lexical rules)
            outer, _ = self.scope.parent.lookup(name) if self.scope.parent else (None, None)
            if outer is not None and outer.kind != "implicit":
                return
            self.scope.syms[name] = s
            syms[name] = s

        add("currVal", el)
        if kind == "map":
            add("key", key_t)
        else:
            add("index", T.INT)
        if kind == "seq":
            add("prevVal", T.maybe(el))
            add("nextVal", T.maybe(el))
        for k2, t2 in (extra or {}).items():
            add(k2, t2)
        if as_cond:
            body = self.check_cond(v, subject=A.Name(v.span, "currVal") if "currVal" in syms else None)
            t = T.BOOL
        else:
            body = v
            t = self.expr(v, want)
            if want is not None:
                self.require_assignable(t, want, v.span, "the new value")
        self.scope = self.scope.parent  # type: ignore[assignment]
        used = [n for n, s in syms.items() if s.used]
        fn = A.ImplicitFn(v.span, body, used, {n: syms[n] for n in used})
        fn.ty = t
        return fn, t


def check(program: A.Program, diags: Diagnostics) -> Checker:
    c = Checker(program, diags)
    c.check()
    return c
