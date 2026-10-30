"""AST for fx3.

The parser builds this tree. The checker then fills in `ty` (the type of
every expression) and `sym` (what each name refers to), and replaces aliases
with canonical names. The checked tree is the IR handed to backends: it is
language-neutral, fully typed and canonical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .diagnostics import Span


@dataclass(eq=False)
class Node:
    span: Span


# ---------------------------------------------------------------------------
# Types and annotations as written in source
# ---------------------------------------------------------------------------

@dataclass(eq=False)
class Annotation(Node):
    name: str                       # canonical after checking
    args: list["Expr"] = field(default_factory=list)
    written: str = ""               # original spelling


@dataclass(eq=False)
class TypeExpr(Node):
    name: str                       # as written, e.g. "Vec", "i32", "Point"
    args: list["TypeExpr"] = field(default_factory=list)
    annots: list[Annotation] = field(default_factory=list)   # inner annotations: list<int @typesafe>
    optional: bool = False          # int?


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------

@dataclass(eq=False)
class Expr(Node):
    ty: Any = field(default=None, init=False, repr=False)


@dataclass(eq=False)
class Name(Expr):
    id: str
    sym: Any = field(default=None, init=False, repr=False)


@dataclass(eq=False)
class IntLit(Expr):
    value: int
    suffix: Optional[str] = None


@dataclass(eq=False)
class FloatLit(Expr):
    value: float


@dataclass(eq=False)
class DurationLit(Expr):
    seconds: float


@dataclass(eq=False)
class StrLit(Expr):
    value: str


@dataclass(eq=False)
class CharLit(Expr):
    value: str


@dataclass(eq=False)
class BoolLit(Expr):
    value: bool


@dataclass(eq=False)
class NoneLit(Expr):
    pass


@dataclass(eq=False)
class FStringPart:
    expr: Optional[Expr] = None     # None = literal text
    text: str = ""
    spec: str = ""
    debug: bool = False             # f"{x=}"


@dataclass(eq=False)
class FString(Expr):
    parts: list[FStringPart]


@dataclass(eq=False)
class ListLit(Expr):
    items: list[Expr]


@dataclass(eq=False)
class MapLit(Expr):
    pairs: list[tuple[Expr, Expr]]


@dataclass(eq=False)
class SetLit(Expr):
    items: list[Expr]


@dataclass(eq=False)
class EmptyBraces(Expr):
    """`{}`: a map or a set, depending on the expected type."""
    as_map: bool = True             # decided by the checker


@dataclass(eq=False)
class TupleLit(Expr):
    items: list[Expr]


@dataclass(eq=False)
class Comprehension(Expr):
    expr: Expr
    var: str
    iterable: Expr
    cond: Optional[Expr] = None
    var_sym: Any = field(default=None, init=False, repr=False)


@dataclass(eq=False)
class Unary(Expr):
    op: str                         # "-", "~", "not"
    operand: Expr


@dataclass(eq=False)
class Binary(Expr):
    op: str                         # + - * / ~/ % ** & | ^ << >>
    left: Expr
    right: Expr


@dataclass(eq=False)
class Compare(Expr):
    """Chained comparison: a < b <= c. ops[i] sits between operands[i] and [i+1]."""
    ops: list[str]                  # == != < <= > >= === in "not in"
    operands: list[Expr]


@dataclass(eq=False)
class Logical(Expr):
    op: str                         # "and" | "or"
    left: Expr
    right: Expr


@dataclass(eq=False)
class RangeExpr(Expr):
    start: Optional[Expr]
    end: Optional[Expr]
    inclusive: bool = False
    step: Optional[Expr] = None


@dataclass(eq=False)
class Arg:
    name: Optional[str]
    value: Expr
    span: Span = None  # type: ignore[assignment]


@dataclass(eq=False)
class Call(Expr):
    func: Expr
    args: list[Arg]
    # filled by the checker:
    kind: str = field(default="", init=False)          # "func" | "method" | "ctor" | "builtin" | "bank" | "cast"
    target: Any = field(default=None, init=False, repr=False)   # FuncInfo / ClassInfo / bank method name
    bound: dict = field(default_factory=dict, init=False, repr=False)  # canonical arg name -> Expr
    handled: bool = field(default=False, init=False)


@dataclass(eq=False)
class Member(Expr):
    obj: Expr
    name: str
    safe: bool = False              # ?.
    field_info: Any = field(default=None, init=False, repr=False)


@dataclass(eq=False)
class Index(Expr):
    obj: Expr
    index: Expr


@dataclass(eq=False)
class Cast(Expr):
    expr: Expr
    target: TypeExpr
    mode: str = "as"                # "as" | "as?" | "as!"


@dataclass(eq=False)
class Propagate(Expr):
    """Postfix `?`: pass a failure up to the caller."""
    expr: Expr


@dataclass(eq=False)
class Coalesce(Expr):
    left: Expr
    right: Expr


@dataclass(eq=False)
class IfExpr(Expr):
    cond: Expr
    then: Expr
    else_: Expr


@dataclass(eq=False)
class TypeTest(Expr):
    expr: Expr
    target: TypeExpr
    negate: bool = False


@dataclass(eq=False)
class SwitchExpr(Expr):
    switch: "Switch"


@dataclass(eq=False)
class Param(Node):
    type: Optional[TypeExpr]
    name: str
    annots: list[Annotation] = field(default_factory=list)
    default: Optional[Expr] = None
    variadic: bool = False
    sym: Any = field(default=None, init=False, repr=False)


@dataclass(eq=False)
class Lambda(Expr):
    params: list[Param]
    body: "Expr | Block"
    info: Any = field(default=None, init=False, repr=False)


@dataclass(eq=False)
class This(Expr):
    pass


@dataclass(eq=False)
class Super(Expr):
    pass


@dataclass(eq=False)
class ImplicitFn(Expr):
    """A bank-method argument that uses implicit variables (currVal, index, ...).

    Created by the checker. `params` lists the implicit names, in the order
    the backend must bind them.
    """
    body: Expr
    params: list[str]
    syms: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------

@dataclass(eq=False)
class Stmt(Node):
    pass


@dataclass(eq=False)
class Block(Stmt):
    stmts: list[Stmt]


@dataclass(eq=False)
class ImportStmt(Stmt):
    module: str
    names: list[tuple[str, Optional[str]]] = field(default_factory=list)   # (name, alias)
    all: bool = False
    alias: Optional[str] = None


@dataclass(eq=False)
class DeclItem:
    name: str
    init: Optional[Expr]
    span: Span
    sym: Any = field(default=None, repr=False)


@dataclass(eq=False)
class VarDecl(Stmt):
    type: TypeExpr
    annots: list[Annotation]
    items: list[DeclItem]


@dataclass(eq=False)
class UnpackTarget:
    type: Optional[TypeExpr]
    name: str                       # "_" = ignore
    rest: bool = False
    span: Span = None  # type: ignore[assignment]
    sym: Any = field(default=None, repr=False)


@dataclass(eq=False)
class UnpackDecl(Stmt):
    common_type: Optional[TypeExpr]
    targets: list[UnpackTarget]
    value: Expr


@dataclass(eq=False)
class Assign(Stmt):
    target: Expr
    op: str                         # "=" "+=" ...
    value: Expr


@dataclass(eq=False)
class IncDec(Stmt):
    target: Expr
    op: str                         # "++" | "--"


@dataclass(eq=False)
class ExprStmt(Stmt):
    expr: Expr


@dataclass(eq=False)
class BindCond(Expr):
    """`if (auto v = maybe_value)`: true when present, binding v."""
    type: Optional[TypeExpr]
    name: str
    value: Expr
    sym: Any = field(default=None, init=False, repr=False)


@dataclass(eq=False)
class If(Stmt):
    cond: Expr
    then: Block
    elifs: list[tuple[Expr, Block]] = field(default_factory=list)
    else_: Optional[Block] = None


@dataclass(eq=False)
class RepeatTimes(Stmt):
    count: Expr
    body: Block


@dataclass(eq=False)
class RepeatWhile(Stmt):
    cond: Expr
    body: Block
    at_least_once: bool = False


@dataclass(eq=False)
class RepeatForever(Stmt):
    body: Block


@dataclass(eq=False)
class RepeatEach(Stmt):
    targets: list[UnpackTarget]     # one target, or several for [a, b] in ...
    iterable: Expr
    body: Block
    common_type: Optional[TypeExpr] = None


@dataclass(eq=False)
class RepeatC(Stmt):
    init: Optional[Stmt]
    cond: Optional[Expr]
    step: Optional[Stmt]
    body: Block


@dataclass(eq=False)
class Case:
    patterns: Optional[list[Expr]]  # None = default
    body: list[Stmt]
    span: Span = None  # type: ignore[assignment]
    conds: list = field(default_factory=list)   # filled by checker: one bool Expr per pattern


@dataclass(eq=False)
class Switch(Stmt):
    subject: Expr
    cases: list[Case]
    is_value: bool = False
    subject_sym: Any = field(default=None, init=False, repr=False)


@dataclass(eq=False)
class Catch:
    type_name: Optional[str]
    var: Optional[str]
    body: Block
    span: Span = None  # type: ignore[assignment]
    sym: Any = field(default=None, repr=False)
    error_cls: Any = field(default=None, repr=False)


@dataclass(eq=False)
class Try(Stmt):
    body: Block
    catches: list[Catch]
    finally_: Optional[Block] = None


@dataclass(eq=False)
class Defer(Stmt):
    body: Block


@dataclass(eq=False)
class Return(Stmt):
    values: list[Expr]


@dataclass(eq=False)
class Fail(Stmt):
    error: Expr


@dataclass(eq=False)
class Stop(Stmt):
    label: Optional[str] = None


@dataclass(eq=False)
class Skip(Stmt):
    label: Optional[str] = None


@dataclass(eq=False)
class ActionBody(Node):
    text: str


@dataclass(eq=False)
class Signature:
    inputs: list[TypeExpr]
    outputs: list[TypeExpr]


@dataclass(eq=False)
class FuncDecl(Stmt):
    name: str
    sig: Optional[Signature]
    annots: list[Annotation]
    params: list[Param]
    body: "Block | Expr | ActionBody"
    info: Any = field(default=None, init=False, repr=False)


@dataclass(eq=False)
class ClassDecl(Stmt):
    name: str
    annots: list[Annotation]
    parent: Optional[str]
    implements: list[str]
    fields: list[VarDecl]
    methods: list[FuncDecl]
    info: Any = field(default=None, init=False, repr=False)


@dataclass(eq=False)
class Program(Node):
    stmts: list[Stmt]
    file: str = "<input>"
