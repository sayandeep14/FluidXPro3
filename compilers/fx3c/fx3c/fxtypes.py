"""Checked types (Syntax/types.fx3)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .aliases import FLOAT_TYPES, INT_BITS, INT_TYPES, SIGNED


@dataclass(frozen=True)
class Ty:
    kind: str                         # see the constructors below
    args: tuple[Ty, ...] = ()
    name: str = ""                    # class name
    strict: bool = True               # list<int> (hint only) -> strict=False

    def __str__(self) -> str:
        k = self.kind
        if k in ("vector", "list", "set", "array"):
            if k == "list" and self.args and self.args[0].kind == "any":
                return "list"
            inner = str(self.args[0]) if self.args else "any"
            return f"{k}<{inner}>"
        if k == "map":
            return f"map<{self.args[0]}, {self.args[1]}>"
        if k == "tuple":
            return "tuple<" + ", ".join(str(a) for a in self.args) + ">"
        if k == "maybe":
            return f"{self.args[0]}?"
        if k == "result":
            return f"result<{self.args[0]}>"
        if k == "class":
            return self.name
        if k == "func":
            return "function"
        if k == "range":
            return f"range<{self.args[0]}>"
        if k == "multi":
            return "(" + ", ".join(str(a) for a in self.args) + ")"
        return k


def prim(kind: str) -> Ty:
    return Ty(kind)


INT = Ty("int")
FLOAT = Ty("float")
BOOL = Ty("bool")
CHAR = Ty("char")
STRING = Ty("string")
NONE = Ty("none")         # type of the literal `none`
VOID = Ty("void")         # "returns nothing"
ANY = Ty("any")
UNKNOWN = Ty("unknown")   # used after an error, to avoid follow-on errors
DURATION = Ty("duration") # seconds (500ms, 2s, 5m, 1h, 1d)


def vector(t: Ty) -> Ty:
    return Ty("vector", (t,))


def list_of(t: Ty = ANY, strict: bool = False) -> Ty:
    return Ty("list", (t,), strict=strict)


def set_of(t: Ty) -> Ty:
    return Ty("set", (t,))


def map_of(k: Ty, v: Ty) -> Ty:
    return Ty("map", (k, v))


def tuple_of(*ts: Ty) -> Ty:
    return Ty("tuple", tuple(ts))


def maybe(t: Ty) -> Ty:
    if t.kind in ("maybe", "any", "unknown"):
        return t
    return Ty("maybe", (t,))


def result_of(t: Ty) -> Ty:
    return Ty("result", (t,))


def range_of(t: Ty) -> Ty:
    return Ty("range", (t,))


def class_ty(name: str) -> Ty:
    return Ty("class", name=name)


def multi(*ts: Ty) -> Ty:
    """Several return values."""
    return Ty("multi", tuple(ts))


# ---------------------------------------------------------------------------
# Questions about types
# ---------------------------------------------------------------------------

def is_int(t: Ty) -> bool:
    return t.kind in INT_TYPES


def is_float(t: Ty) -> bool:
    return t.kind in FLOAT_TYPES


def is_numeric(t: Ty) -> bool:
    return is_int(t) or is_float(t)


def is_seq(t: Ty) -> bool:
    return t.kind in ("vector", "list", "array")


def is_collection(t: Ty) -> bool:
    return t.kind in ("vector", "list", "array", "map", "set", "tuple")


def is_unknown(t: Ty | None) -> bool:
    return t is None or t.kind in ("unknown", "any")


def elem(t: Ty) -> Ty:
    if t.kind in ("vector", "list", "array", "set", "range") and t.args:
        return t.args[0]
    if t.kind == "string":
        return CHAR
    return ANY


def receiver_kind(t: Ty) -> str | None:
    if is_seq(t):
        return "seq"
    if t.kind in ("map", "set", "string", "tuple", "maybe", "result"):
        return t.kind
    if is_numeric(t):
        return "number"
    if t.kind in ("bool", "char", "duration"):
        return t.kind
    return None


def widen_ints(a: Ty, b: Ty) -> Ty | None:
    """Result type of an integer operation, or None if signed/unsigned of the same size mix."""
    if a == b:
        return a
    ab, bb = INT_BITS[a.kind], INT_BITS[b.kind]
    a_signed, b_signed = a.kind in SIGNED, b.kind in SIGNED
    if a_signed == b_signed:
        return a if ab >= bb else b
    # mixed sign: the signed one must be strictly bigger
    s, u = (a, b) if a_signed else (b, a)
    if INT_BITS[s.kind] > INT_BITS[u.kind]:
        return s
    return None


def numeric_result(a: Ty, b: Ty) -> Ty | None:
    if is_float(a) or is_float(b):
        # f32 stays f32 only when the other side is f32 or an integer
        if {a.kind, b.kind} <= {"f32"} or (a.kind == "f32" and is_int(b)) or (b.kind == "f32" and is_int(a)):
            return Ty("f32")
        return FLOAT
    if is_int(a) and is_int(b):
        return widen_ints(a, b)
    return None


def assignable(src: Ty, dst: Ty) -> bool:
    """Can a value of type `src` be stored where `dst` is expected? (automatic widening only)"""
    if dst.kind in ("any", "unknown") or src.kind in ("unknown",):
        return True
    if src == dst:
        return True
    if dst.kind == "list" and not dst.strict:
        # loose list: anything list-like with any elements
        return src.kind in ("list", "vector", "array")
    if src.kind == "any":
        return True
    if dst.kind == "maybe":
        return src.kind == "none" or assignable(src, dst.args[0]) or (
            src.kind == "maybe" and assignable(src.args[0], dst.args[0]))
    if is_int(src) and is_int(dst):
        w = widen_ints(src, dst)
        return w == dst
    if is_int(src) and is_float(dst):
        return True
    if src.kind == "f32" and dst.kind == "float":
        return True
    if src.kind == "char" and dst.kind == "string":
        return True
    if src.kind in ("vector", "array", "list") and dst.kind in ("vector", "array", "list"):
        # element types must match exactly (collections are invariant)
        return src.args[0] == dst.args[0]
    if src.kind == dst.kind and src.kind in ("set", "map", "tuple") and len(src.args) == len(dst.args):
        return all(a == b or b.kind == "any" for a, b in zip(src.args, dst.args))
    if src.kind == "class" and dst.kind == "class":
        return _is_subclass(src.name, dst.name)
    return False


# Set by the checker so assignable() can follow class inheritance.
CLASS_PARENTS: dict[str, str | None] = {}


def _is_subclass(child: str, parent: str) -> bool:
    seen = set()
    c: str | None = child
    while c is not None and c not in seen:
        if c == parent:
            return True
        seen.add(c)
        c = CLASS_PARENTS.get(c)
    return False


@dataclass
class FieldInfo:
    name: str
    ty: Ty
    private: bool = True
    const: bool = False
    static: bool = False
    has_default: bool = False
    owner: str = ""


@dataclass
class FuncInfo:
    name: str
    params: list = field(default_factory=list)       # list[ParamInfo]
    returns: list[Ty] = field(default_factory=list)  # empty = returns nothing
    fails: list[str] = field(default_factory=list)   # error class names
    annots: set[str] = field(default_factory=set)
    decl: object = None                              # FuncDecl
    owner: str | None = None                         # class name for methods
    static: bool = False
    private: bool = False
    is_ctor: bool = False
    style: str | None = None
    inferring: bool = False
    inferred: bool = False
    builtin: bool = False

    @property
    def return_ty(self) -> Ty:
        if not self.returns:
            return VOID
        if len(self.returns) == 1:
            return self.returns[0]
        return multi(*self.returns)


@dataclass
class ParamInfo:
    name: str
    ty: Ty
    has_default: bool = False
    variadic: bool = False
    ref: bool = False


@dataclass
class ClassInfo:
    name: str
    parent: str | None = None
    fields: dict[str, FieldInfo] = field(default_factory=dict)
    methods: dict[str, FuncInfo] = field(default_factory=dict)
    ctors: list[FuncInfo] = field(default_factory=list)
    annots: set[str] = field(default_factory=set)
    derive: set[str] = field(default_factory=set)
    is_error: bool = False
    builtin: bool = False
    decl: object = None

    def is_open(self) -> bool:
        return "open" in self.annots or "abstract" in self.annots
