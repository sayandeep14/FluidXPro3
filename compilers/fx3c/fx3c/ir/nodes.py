"""The fx3 IR (ROADMAP E-33 Phase 3, plans/E-33-targets.md section 5).

Typed, desugared and target-neutral: what every backend (Go, Java, C++, Rust, and later Python) reads instead
of the checked syntax tree. Everything a backend would otherwise have to work out is written down here:

- every expression has its type (fxtypes.Ty);
- names are resolved to a Local, a Global, a function or a class; locals have unique names per function;
- collections stored from a variable, field or index are wrapped in Copy (value semantics, memory.fx3);
- a maybe value used as its inner type (after a `none` check) is wrapped in Unwrap;
- failures are explicit: every call lists what it can fail with; `?` is Propagate, `??` on a failing call is
  OrElse, `??` on a maybe value is Coalesce;
- closures list what they capture and how; a variable that a closure changes is marked `cell`;
- method-bank calls and built-ins are Intrinsics from a fixed catalogue (`vector.add`, `print`, ...);
- switch is Match; loops are ForRange / ForEach (with the kind of iterable) / While / CFor / Loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..fxtypes import Ty

# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class Local:
    """A parameter or local variable. `name` is unique in its function."""
    name: str
    ty: Ty
    mode: str = "local"           # local | param | loop
    ref: bool = False             # an @ref parameter: changes reach the caller
    cell: bool = False            # a closure changes it: keep it in a shared cell (Rust Rc<RefCell>, Java holder)
    const: bool = False


@dataclass(eq=False)
class Global:
    """A module-level variable or constant."""
    name: str
    ty: Ty
    const: bool = False
    init: Optional[Expr] = None
    module: str = ""


@dataclass(eq=False)
class Func:
    name: str
    params: list[Local]
    ret: Ty                       # VOID, one type, or multi(...)
    fails: list[str]
    body: Optional[Block]         # None: abstract / interface requirement / @extern
    owner: str = ""               # class name for methods
    static: bool = False
    flags: set[str] = field(default_factory=set)    # async entry test pure memo property ctor transactional provides
    defaults: dict[str, Expr] = field(default_factory=dict)      # parameter -> default value
    type_params: list = field(default_factory=list)               # [("T", ("comparable",))]
    examples: list = field(default_factory=list)                  # [([Expr inputs], Expr output)]
    extern: dict[str, str] = field(default_factory=dict)          # @extern: target -> body text
    native: dict[str, str] = field(default_factory=dict)          # @native: target -> text
    tx: Optional[tuple[str, str]] = None                          # @transactional: ("param"|"field", name)
    style: str = ""                                               # constructor style name
    module: str = ""
    line: int = 0


@dataclass(eq=False)
class FieldDecl:
    name: str
    ty: Ty
    default: Optional[Expr] = None
    private: bool = True
    static: bool = False
    const: bool = False
    checks: list = field(default_factory=list)     # [(kind, [Expr args])]: classes.fx3 7c
    native: dict[str, str] = field(default_factory=dict)


@dataclass(eq=False)
class ClassDecl:
    name: str
    parent: str = ""
    interfaces: list[str] = field(default_factory=list)
    fields: list[FieldDecl] = field(default_factory=list)
    methods: list[Func] = field(default_factory=list)
    ctors: list[Func] = field(default_factory=list)              # explicit constructors (empty: the generated one)
    flags: set[str] = field(default_factory=set)                 # value service error abstract open interface
    derive: set[str] = field(default_factory=set)
    type_params: list = field(default_factory=list)
    injected: dict[str, str] = field(default_factory=dict)       # @service: field -> type name
    native: dict[str, str] = field(default_factory=dict)
    module: str = ""
    line: int = 0


@dataclass(eq=False)
class Variant:
    name: str
    index: int
    fields: list[tuple[str, Ty]] = field(default_factory=list)
    raw: Any = None


@dataclass(eq=False)
class EnumDecl:
    name: str
    variants: list[Variant]
    raw: Optional[Ty] = None
    methods: list[Func] = field(default_factory=list)
    ordered: bool = False
    module: str = ""
    line: int = 0


@dataclass(eq=False)
class Module:
    name: str                     # module name (project.py), "" for a single-file program
    file: str
    imports: list[tuple[str, list[str]]] = field(default_factory=list)   # (module, names)
    std: list[str] = field(default_factory=list)                          # standard modules used
    globals: list[Global] = field(default_factory=list)
    funcs: list[Func] = field(default_factory=list)
    classes: list[ClassDecl] = field(default_factory=list)
    enums: list[EnumDecl] = field(default_factory=list)
    init: list[Stmt] = field(default_factory=list)       # top-level statements, in order
    entry: str = ""               # the @entry function
    tests: list[str] = field(default_factory=list)       # @test functions


# ---------------------------------------------------------------------------
# Expressions (every one has .ty)
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class Expr:
    ty: Ty


@dataclass(eq=False)
class Lit(Expr):
    value: Any                    # int float str bool None; char is a 1-char str with ty char; duration: float seconds


@dataclass(eq=False)
class Var(Expr):
    local: Local


@dataclass(eq=False)
class GlobalRef(Expr):
    module: str
    name: str


@dataclass(eq=False)
class FuncRef(Expr):
    """A function used as a value."""
    module: str
    name: str
    owner: str = ""


@dataclass(eq=False)
class TypeRef(Expr):
    """A class or enum named as a value (only as the receiver of static access)."""
    name: str


@dataclass(eq=False)
class This(Expr):
    pass


@dataclass(eq=False)
class Field(Expr):
    obj: Expr
    name: str
    owner: str = ""


@dataclass(eq=False)
class StaticField(Expr):
    cls: str
    name: str


@dataclass(eq=False)
class Index(Expr):
    obj: Expr
    index: Expr                   # int for sequences and strings, the key for maps


@dataclass(eq=False)
class Slice(Expr):
    obj: Expr
    start: Optional[Expr]
    end: Optional[Expr]
    inclusive: bool = False
    step: Optional[Expr] = None


@dataclass(eq=False)
class Unary(Expr):
    op: str                       # neg  not  bitnot
    operand: Expr


@dataclass(eq=False)
class Binary(Expr):
    """Arithmetic on ints is overflow-checked and `floordiv` / `mod` are floor division (targets.fx3 2a)."""
    op: str                       # add sub mul div floordiv mod pow  bitand bitor bitxor shl shr  concat repeat
    left: Expr                    # set ops: union intersect difference symdiff (on sets)
    right: Expr


@dataclass(eq=False)
class Compare(Expr):
    op: str                       # eq ne lt le gt ge same in notin
    left: Expr
    right: Expr


@dataclass(eq=False)
class Logical(Expr):
    op: str                       # and or
    left: Expr
    right: Expr


@dataclass(eq=False)
class Cond(Expr):
    cond: Expr
    then: Expr
    else_: Expr


@dataclass(eq=False)
class Range(Expr):
    """A range value (repeat(x in range) is ForRange, not this)."""
    start: Expr
    end: Expr
    inclusive: bool = False
    step: Optional[Expr] = None


@dataclass(eq=False)
class Copy(Expr):
    """Value semantics: a collection stored from a variable, field or index gets its own copy."""
    value: Expr


@dataclass(eq=False)
class Unwrap(Expr):
    """A maybe value used as its inner type where the checker proved it isn't none."""
    value: Expr


@dataclass(eq=False)
class Coalesce(Expr):
    """`x ?? d` on a maybe value."""
    value: Expr
    default: Expr


@dataclass(eq=False)
class SafeField(Expr):
    """`x?.f`: none if x is none."""
    obj: Expr
    name: str


@dataclass(eq=False)
class Call(Expr):
    """A user function or method. args are in parameter order; a parameter left out is None (its default)."""
    target: str                   # function / method name
    args: list[Optional[Expr]]
    module: str = ""              # module of a top-level function ("" = this one)
    owner: str = ""               # class, for methods and static functions
    recv: Optional[Expr] = None   # the object, for methods (This for this.m(), Super for super.m())
    fails: list[str] = field(default_factory=list)
    kind: str = "func"            # func static method super value (calling a function value: recv is it)
    std: bool = False             # a standard-library function or method (fx3std)


@dataclass(eq=False)
class New(Expr):
    """Making an object: the generated constructor (fields in order) or a named one (`ctor`)."""
    cls: str
    args: list[Optional[Expr]]
    ctor: str = ""


@dataclass(eq=False)
class Intrinsic(Expr):
    """A built-in operation every backend implements. args are by parameter name.

    - Method bank: `<kind>.<method>`, kind from fxtypes.receiver_kind (seq, map, set, string, tuple, number,
      ...), method the canonical name in aliases.METHOD_PARAMS: seq.add, map.getOr, string.split, ...
      A function argument (map(currVal * 2)) is a Lambda.
    - Prelude functions by name: print, len, min, max, abs, round, floor, ceil, sum, sorted, range, panic,
      ensure, assert, unreachable, exit, sleep, input, string / str.
    - Made by the lowering: zero (an empty value of the type), seq.filled (size, value), range.toList (range),
      rangeCheck (low, high, name), set.unordered (value), channel.make (capacity), isType (type),
      enum.all / enum.from (enum, value) / enum.name / enum.index / enum.value,
      task.all / task.any (tasks in extra) / task.cancel / task.isDone,
      channel.send (value) / channel.receive / channel.close / channel.isClosed,
      result.value / result.error, maybe.value."""
    op: str
    recv: Optional[Expr]
    args: dict[str, Expr] = field(default_factory=dict)
    extra: list[Expr] = field(default_factory=list)      # positional / variadic values (print(a, b, c))


@dataclass(eq=False)
class Cast(Expr):
    value: Expr
    mode: str = "as"              # as (checked) | as? (maybe) | as! (wrapping)


@dataclass(eq=False)
class Format(Expr):
    """An f-string: text pieces and values with their format spec."""
    parts: list[tuple[Optional[Expr], str, str]]          # (value or None for text, text, spec)


@dataclass(eq=False)
class ListMake(Expr):
    items: list[Expr]


@dataclass(eq=False)
class SetMake(Expr):
    items: list[Expr]
    unordered: bool = False


@dataclass(eq=False)
class MapMake(Expr):
    items: list[tuple[Expr, Expr]]


@dataclass(eq=False)
class TupleMake(Expr):
    items: list[Expr]


@dataclass(eq=False)
class Lambda(Expr):
    params: list[Local]
    body: Block                   # an expression body is lowered to `return expr`
    captures: list[tuple[Local, str]] = field(default_factory=list)   # (local, "read" | "cell" | "value")


@dataclass(eq=False)
class Propagate(Expr):
    """`call?`: a failure goes to the caller (the function's @fails covers it)."""
    value: Expr


@dataclass(eq=False)
class OrElse(Expr):
    """`call ?? default` on a call that can fail."""
    value: Expr
    default: Expr


@dataclass(eq=False)
class Capture(Expr):
    """`result<T> r = call;`: the failure kept as a value."""
    value: Expr


@dataclass(eq=False)
class VariantMake(Expr):
    enum: str
    variant: str
    args: list[Expr] = field(default_factory=list)


@dataclass(eq=False)
class VariantTest(Expr):
    """Is `value` this variant? Binds the named payload fields (locals) when it is."""
    value: Expr
    enum: str
    variant: str
    binds: list[tuple[Local, int]] = field(default_factory=list)


@dataclass(eq=False)
class Spawn(Expr):
    """Start a task: a call (arguments evaluated now, collections copied) or a function value."""
    work: Expr


@dataclass(eq=False)
class Await(Expr):
    task: Expr
    fails: list[str] = field(default_factory=list)


@dataclass(eq=False)
class SharedGet(Expr):
    """The value inside an atomic<T> / locked<T> (a locked one only inside its `with`)."""
    holder: Expr


@dataclass(eq=False)
class ServiceGet(Expr):
    cls: str


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class Stmt:
    line: int = field(default=0, kw_only=True)   # the .fx3 line (source maps, E-08)


@dataclass(eq=False)
class Block(Stmt):
    stmts: list[Stmt] = field(default_factory=list)


@dataclass(eq=False)
class Let(Stmt):
    local: Local
    init: Optional[Expr] = None


@dataclass(eq=False)
class Assign(Stmt):
    target: Expr                  # Var / GlobalRef / Field / StaticField / Index
    value: Expr


@dataclass(eq=False)
class SharedSet(Stmt):
    """Change an atomic<T> / locked<T>: `op` "=" sets it; an operator updates it in one indivisible step."""
    holder: Expr
    op: str                       # "=" or a Binary op name (add, sub, ...)
    value: Expr


@dataclass(eq=False)
class Unpack(Stmt):
    targets: list[Optional[Local]]    # None for `_`
    value: Expr
    rest: Optional[Local] = None      # [head, ...rest]


@dataclass(eq=False)
class ExprStmt(Stmt):
    expr: Expr


@dataclass(eq=False)
class If(Stmt):
    cond: Expr
    then: Block
    else_: Optional[Block] = None


@dataclass(eq=False)
class While(Stmt):
    cond: Expr
    body: Block
    at_least_once: bool = False
    label: str = ""


@dataclass(eq=False)
class Loop(Stmt):
    body: Block
    label: str = ""


@dataclass(eq=False)
class ForRange(Stmt):
    var: Optional[Local]          # None: repeat(n) with no variable
    start: Expr
    end: Expr
    inclusive: bool
    step: Optional[Expr]
    body: Block
    label: str = ""


@dataclass(eq=False)
class ForEach(Stmt):
    """kind: list (vector / list / array), set, map (key, value), string (chars), channel."""
    vars: list[Optional[Local]]
    iterable: Expr
    kind: str
    body: Block
    label: str = ""


@dataclass(eq=False)
class CFor(Stmt):
    init: list[Stmt]
    cond: Optional[Expr]
    step: list[Stmt]
    body: Block
    label: str = ""


@dataclass(eq=False)
class Break(Stmt):
    label: str = ""


@dataclass(eq=False)
class Continue(Stmt):
    label: str = ""


@dataclass(eq=False)
class Return(Stmt):
    value: Optional[Expr] = None


@dataclass(eq=False)
class Fail(Stmt):
    error: Expr


@dataclass(eq=False)
class Arm:
    pattern: Optional[Expr]       # VariantTest / a bool test against the subject / None for default
    guard: Optional[Expr]
    body: Block


@dataclass(eq=False)
class Match(Stmt):
    """switch: the subject is evaluated once into `subject`; each arm's pattern is a bool expression over it."""
    subject: Local
    value: Expr
    arms: list[Arm]


@dataclass(eq=False)
class Catch:
    error: str                    # error class ("Error" catches every error)
    var: Optional[Local]
    body: Block


@dataclass(eq=False)
class Try(Stmt):
    body: Block
    catches: list[Catch]
    finally_: Optional[Block] = None


@dataclass(eq=False)
class Defer(Stmt):
    body: Block


@dataclass(eq=False)
class With(Stmt):
    """Hold a locked<T>'s lock for the block."""
    holder: Expr
    body: Block


@dataclass(eq=False)
class Group(Stmt):
    limit: Optional[Expr]
    body: Block
    fails: list[str] = field(default_factory=list)


@dataclass(eq=False)
class SelectCase:
    kind: str                     # recv send after default
    channel: Optional[Expr] = None
    value: Optional[Expr] = None  # send: the value; after: the duration
    bind: Optional[Local] = None  # recv: what was received
    skip_closed: bool = False     # recv into T (not T?): a closed channel's case is skipped
    body: Block = field(default_factory=Block)


@dataclass(eq=False)
class Select(Stmt):
    cases: list[SelectCase]
