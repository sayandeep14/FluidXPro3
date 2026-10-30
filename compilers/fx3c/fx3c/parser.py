"""Parser: tokens -> AST (fixed shapes from Syntax/*.fx3)."""

from __future__ import annotations

from . import nodes as A
from .aliases import (
    ACT_WORDS, FAIL_WORDS, FALSE_WORDS, FUNCTION_CTOR_WORDS, FUNCTION_WORDS, NONE_WORDS,
    REPEAT_WORDS, SKIP_WORDS, STOP_WORDS, SWITCH_WORDS, TRUE_WORDS, is_builtin_type_word,
)
from .diagnostics import CompileError, Diagnostic, Span
from .stdlib import ALL_TYPE_NAMES, STD_ERRORS
from .lexer import (ANNOT, CHAR, DURATION, EOF, FLOAT, FSTRING, IDENT, INT, OP, PERCENT, REGEX,
                    STRING, Token, tokenize)

ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "~/=", "%=", "**=", "&=", "|=", "^=", "<<=", ">>=", "??="}
COMPARE_OPS = {"==", "!=", "<", "<=", ">", ">=", "==="}

# Built-in error classes count as type names (errors.fx3 section 3)
BUILTIN_CLASS_NAMES = {"Error", "ValueError", "ParseError", "IOError", "FileNotFound",
                       "PermissionDenied", "NetworkError", "TimeoutError", "ConnectionError",
                       "FormatError"} | ALL_TYPE_NAMES | set(STD_ERRORS)


class Parser:
    def __init__(self, tokens: list[Token], file: str):
        self.toks = tokens
        self.i = 0
        self.file = file
        self.class_names: set[str] = set(BUILTIN_CLASS_NAMES)
        self._prescan_classes()
        self.semi_optional_before: str | None = None

    # ---- token helpers ------------------------------------------------------
    @property
    def tok(self) -> Token:
        return self.toks[self.i]

    def peek(self, off: int = 1) -> Token:
        j = min(self.i + off, len(self.toks) - 1)
        return self.toks[j]

    def advance(self) -> Token:
        t = self.toks[self.i]
        if self.i < len(self.toks) - 1:
            self.i += 1
        return t

    def error(self, msg: str, tok: Token | None = None, fix: str | None = None, code: str = "FX0201"):
        t = tok or self.tok
        raise CompileError(Diagnostic("error", code, msg, t.span, fix))

    def at_op(self, *ops: str) -> bool:
        return self.tok.kind == OP and self.tok.value in ops

    def at_word(self, *words: str) -> bool:
        return self.tok.kind == IDENT and self.tok.value in words

    def accept_op(self, op: str) -> Token | None:
        if self.at_op(op):
            return self.advance()
        return None

    def expect_op(self, op: str, what: str | None = None) -> Token:
        if self.at_op(op):
            return self.advance()
        # `>>` closing two generic lists at once
        if op == ">" and self.at_op(">>"):
            t = self.tok
            half = Token(OP, ">", Span(t.span.file, t.span.line, t.span.col + 1), ">")
            self.toks[self.i] = Token(OP, ">", t.span, ">")
            self.toks.insert(self.i + 1, half)
            return self.advance()
        if op == ">" and self.at_op(">="):
            self.error("unexpected `>=` in a type")
        got = self.describe(self.tok)
        self.error(f"expected `{op}`{' ' + what if what else ''}, found {got}",
                   fix=f"add `{op}`" if op in (";", ")", "]", "}") else None)

    def expect_ident(self, what: str = "a name") -> Token:
        if self.tok.kind == IDENT:
            return self.advance()
        self.error(f"expected {what}, found {self.describe(self.tok)}")

    def expect_semi(self) -> None:
        if self.semi_optional_before and self.at_op(self.semi_optional_before):
            return
        if self.at_op(";"):
            self.advance()
            return
        prev = self.toks[self.i - 1] if self.i > 0 else self.tok
        raise CompileError(Diagnostic(
            "error", "FX0202", "missing `;` at the end of the statement",
            Span(prev.span.file, prev.span.line, prev.span.col + max(1, prev.span.length), 1),
            "add `;`"))

    @staticmethod
    def describe(t: Token) -> str:
        if t.kind == EOF:
            return "the end of the file"
        if t.kind == IDENT:
            return f"`{t.value}`"
        if t.kind == OP:
            return f"`{t.value}`"
        if t.kind == ANNOT:
            return f"`@{t.value}`"
        return f"`{t.text}`"

    def sp(self) -> Span:
        return self.tok.span

    # ---- pre-scan ------------------------------------------------------------
    def _prescan_classes(self) -> None:
        toks = self.toks
        for j, t in enumerate(toks):
            if t.kind == IDENT and t.value == "class":
                k = j + 1
                while k < len(toks) and toks[k].kind == ANNOT:
                    k += 1
                    if k < len(toks) and toks[k].kind == OP and toks[k].value == "(":
                        depth = 0
                        while k < len(toks):
                            if toks[k].is_op("("):
                                depth += 1
                            elif toks[k].is_op(")"):
                                depth -= 1
                                if depth == 0:
                                    k += 1
                                    break
                            k += 1
                if k < len(toks) and toks[k].kind == IDENT:
                    self.class_names.add(toks[k].value)

    def is_type_word(self, t: Token) -> bool:
        return t.kind == IDENT and (is_builtin_type_word(t.value) or t.value in self.class_names)

    # ---- program ---------------------------------------------------------------
    def parse_program(self) -> A.Program:
        stmts: list[A.Stmt] = []
        start = self.sp()
        while self.tok.kind != EOF:
            stmts.append(self.parse_statement(top=True))
        return A.Program(start, stmts, self.file)

    # ---- statements --------------------------------------------------------------
    def parse_block(self) -> A.Block:
        start = self.expect_op("{", "to start a block").span
        stmts: list[A.Stmt] = []
        saved = self.semi_optional_before
        self.semi_optional_before = None
        while not self.at_op("}"):
            if self.tok.kind == EOF:
                self.error("block is never closed", fix="add `}`")
            stmts.append(self.parse_statement())
        self.advance()
        self.semi_optional_before = saved
        return A.Block(start, stmts)

    def parse_statement(self, top: bool = False) -> A.Stmt:
        t = self.tok
        if t.kind == OP and t.value == "{":
            return self.parse_block()
        if t.kind == OP and t.value == ";":
            self.error("empty statement (a stray `;`)", fix="remove the extra `;`")
        if t.kind == ANNOT:
            if t.value == "unsafe":
                self.error("@unsafe blocks are not supported yet", code="FX1001")
            self.error(f"annotation `@{t.value}` can't start a statement",
                       fix="annotations go after the type (or the keyword), e.g. `int @const x = 1;`")
        if t.kind == IDENT:
            w = t.value
            if w in ("from", "import"):
                return self.parse_import()
            if w in FUNCTION_WORDS and (self.peek().is_op("[") or self.peek().kind == ANNOT or
                                        (self.peek().kind == IDENT and self.peek(2).is_op("=", ";"))):
                return self.parse_function()
            if w == "class":
                return self.parse_class()
            if w == "if" and self.peek().is_op("("):
                return self.parse_if()
            if (w in REPEAT_WORDS and (self.peek().is_op("(") or self.peek().kind == ANNOT)) or \
                    (w == "for" and self.peek().is_op("(")):
                return self.parse_repeat(is_for=(w == "for"))
            if w in SWITCH_WORDS and self.peek().is_op("("):
                sw = self.parse_switch(is_value=False)
                return sw
            if w == "try" and self.peek().is_op("{"):
                return self.parse_try()
            if w == "defer" and self.peek().is_op("{"):
                self.advance()
                return A.Defer(t.span, self.parse_block())
            if w == "return":
                return self.parse_return()
            if w in FAIL_WORDS and self.peek().kind == IDENT:
                self.advance()
                err = self.parse_expr()
                self.expect_semi()
                return A.Fail(t.span, err)
            if w in STOP_WORDS and (self.peek().is_op(";", ")") or (self.peek().kind == IDENT and self.peek(2).is_op(";"))):
                self.advance()
                label = self.advance().value if self.tok.kind == IDENT else None
                self.expect_semi()
                return A.Stop(t.span, label)
            if w in SKIP_WORDS and (self.peek().is_op(";", ")") or (self.peek().kind == IDENT and self.peek(2).is_op(";"))):
                self.advance()
                label = self.advance().value if self.tok.kind == IDENT else None
                self.expect_semi()
                return A.Skip(t.span, label)
            if w == "type" and self.peek().kind == IDENT and self.peek(2).is_op("="):
                self.error("type aliases (`type X = ...;`) are not supported yet", code="FX1001")
            if w in ("enum", "interface", "looper", "checker", "pipeline") and self.peek().kind in (IDENT, ANNOT, OP):
                self.error(f"`{w}` is not supported yet in this version of fx3c", code="FX1001")
        decl = self.try_parse_decl()
        if decl is not None:
            return decl
        if t.kind == OP and t.value == "[" and self._looks_like_bare_unpack():
            return self.parse_unpack(None)
        return self.parse_simple_statement()

    def parse_simple_statement(self, need_semi: bool = True) -> A.Stmt:
        """Assignment, ++/--, or a bare expression."""
        start = self.sp()
        expr = self.parse_expr()
        if self.tok.kind == OP and self.tok.value in ASSIGN_OPS:
            op = self.advance().value
            value = self.parse_expr()
            stmt: A.Stmt = A.Assign(start, expr, op, value)
        elif self.at_op("++", "--"):
            op = self.advance().value
            stmt = A.IncDec(start, expr, op)
        else:
            stmt = A.ExprStmt(start, expr)
        if need_semi:
            self.expect_semi()
        return stmt

    # ---- declarations ------------------------------------------------------------
    def try_parse_decl(self, need_semi: bool = True) -> A.Stmt | None:
        if not self.is_type_word(self.tok):
            # `Strng name = ...;`: an unknown type name. Parse it as a declaration so the
            # checker can suggest the right type.
            nxt, nxt2 = self.peek(), self.peek(2)
            if not (self.tok.kind == IDENT and ((nxt.kind == IDENT and nxt2.is_op("=", ";", ","))
                                                 or nxt.kind == ANNOT)):
                return None
        save = self.i
        try:
            ty = self.parse_type()
        except CompileError:
            self.i = save
            return None
        if not (self.tok.kind in (IDENT, ANNOT) or self.at_op("[")):
            self.i = save
            return None
        if self.tok.kind == IDENT and self.tok.value in ("in",):
            self.i = save
            return None
        annots = self.parse_annotations()
        if self.at_op("["):
            if annots:
                self.error("annotations can't be used on an unpacking declaration")
            return self.parse_unpack(ty, need_semi)
        if self.tok.kind != IDENT:
            self.i = save
            return None
        items: list[A.DeclItem] = []
        while True:
            name_tok = self.expect_ident("a variable name")
            init = None
            if self.accept_op("="):
                init = self.parse_expr()
            elif self.at_op("("):
                self.error("constructor-style arguments are not used in fx3",
                           fix=f"use annotations, e.g. `vector<int> @size(10) @init(0) {name_tok.value};`")
            items.append(A.DeclItem(name_tok.value, init, name_tok.span))
            if not self.accept_op(","):
                break
        if need_semi:
            self.expect_semi()
        return A.VarDecl(ty.span, ty, annots, items)

    def _looks_like_bare_unpack(self) -> bool:
        # [string who, int age] = ...
        j = self.i + 1
        return self.is_type_word(self.toks[j]) and self.toks[j + 1].kind in (IDENT,)

    def parse_unpack(self, common: A.TypeExpr | None, need_semi: bool = True) -> A.UnpackDecl:
        start = self.expect_op("[").span
        targets: list[A.UnpackTarget] = []
        while not self.at_op("]"):
            rest = bool(self.accept_op("..."))
            ty = None
            if self.is_type_word(self.tok) and self.peek().kind == IDENT:
                ty = self.parse_type()
            nt = self.expect_ident("a name to unpack into")
            targets.append(A.UnpackTarget(ty, nt.value, rest, nt.span))
            if not self.accept_op(","):
                break
        self.expect_op("]")
        self.expect_op("=", "after the names to unpack")
        value = self.parse_expr()
        if need_semi:
            self.expect_semi()
        return A.UnpackDecl(start, common, targets, value)

    def parse_type(self) -> A.TypeExpr:
        t = self.expect_ident("a type")
        name = t.value
        if name == "std" and self.at_op("::"):
            self.advance()
            name = self.expect_ident("a type").value
        args: list[A.TypeExpr] = []
        if self.at_op("<"):
            self.advance()
            while True:
                arg = self.parse_type()
                arg.annots = self.parse_annotations()
                args.append(arg)
                if not self.accept_op(","):
                    break
            self.expect_op(">", "to close the type arguments")
        optional = False
        if self.at_op("?") and (self.peek().kind in (IDENT, ANNOT, EOF) or self.peek().is_op("[", ">", ">>", ",", ")")):
            self.advance()
            optional = True
        return A.TypeExpr(t.span, name, args, [], optional)

    def parse_annotations(self) -> list[A.Annotation]:
        out: list[A.Annotation] = []
        while self.tok.kind == ANNOT:
            t = self.advance()
            args: list[A.Expr] = []
            if self.at_op("(") and not self._paren_is_header():
                self.advance()
                while not self.at_op(")"):
                    e = self.parse_expr()
                    if self.accept_op("->"):
                        out_e = self.parse_expr()
                        e = A.TupleLit(e.span, [e, out_e])
                    args.append(e)
                    if not self.accept_op(","):
                        break
                self.expect_op(")")
            out.append(A.Annotation(t.span, t.value, args, t.value))
        return out

    def _paren_is_header(self) -> bool:
        # `repeat @atleastonce (cond)`: the `(` belongs to repeat, not to the annotation.
        prev = self.toks[self.i - 1]
        return prev.kind == ANNOT and prev.value in ("atleastonce", "dowhile", "parallel")

    def parse_import(self) -> A.ImportStmt:
        start = self.sp()
        if self.at_word("import"):
            self.advance()
            mod = self.parse_dotted()
            alias = None
            if self.at_word("as"):
                self.advance()
                alias = self.expect_ident().value
            self.expect_semi()
            return A.ImportStmt(start, mod, [], False, alias)
        self.advance()  # from
        mod = self.parse_dotted()
        if not self.at_word("import"):
            self.error("expected `import` after the module name")
        self.advance()
        names: list[tuple[str, str | None]] = []
        all_ = False
        if self.tok.kind == ANNOT and self.tok.value == "all":
            self.advance()
            all_ = True
        else:
            while True:
                n = self.expect_ident().value
                alias = None
                if self.at_word("as"):
                    self.advance()
                    alias = self.expect_ident().value
                names.append((n, alias))
                if not self.accept_op(","):
                    break
        self.expect_semi()
        return A.ImportStmt(start, mod, names, all_)

    def parse_dotted(self) -> str:
        parts = []
        while self.at_op(".", "/"):
            parts.append(self.advance().value)
        parts.append(self.expect_ident("a module name").value)
        while self.at_op("."):
            self.advance()
            parts.append("." + self.expect_ident().value)
        return "".join(parts)

    # ---- functions & classes -------------------------------------------------
    def parse_signature(self) -> A.Signature:
        self.expect_op("[")

        def types_list() -> list[A.TypeExpr]:
            self.expect_op("<")
            out: list[A.TypeExpr] = []
            while not self.at_op(">"):
                if self.at_op(">>"):
                    break
                ty = self.parse_type()
                ty.annots = self.parse_annotations()
                if self.accept_op("..."):
                    ty.annots.append(A.Annotation(ty.span, "variadic", [], "..."))
                out.append(ty)
                if not self.accept_op(","):
                    break
            self.expect_op(">")
            return out

        ins = types_list()
        self.expect_op("|", "between input and output types, e.g. function[<int>|<int>]")
        outs = types_list()
        self.expect_op("]")
        return A.Signature(ins, outs)

    def parse_function(self) -> A.FuncDecl:
        start = self.advance().span     # function / fn / def ...
        sig = self.parse_signature() if self.at_op("[") else None
        annots = self.parse_annotations()
        name = self.expect_ident("the function name")
        if self.at_op(";"):
            self.advance()
            return A.FuncDecl(start, name.value, sig, annots, [], None)   # abstract
        self.expect_op("=", "after the function name")
        if not (self.tok.kind == IDENT and self.tok.value in FUNCTION_CTOR_WORDS):
            self.error("expected `Function(...)` after `=`", fix=f"write `{name.value} = Function(params) {{ ... }}`")
        self.advance()
        params = self.parse_params()
        body = self.parse_function_body()
        return A.FuncDecl(start, name.value, sig, annots, params, body)

    def parse_params(self) -> list[A.Param]:
        self.expect_op("(")
        params: list[A.Param] = []
        while not self.at_op(")"):
            start = self.sp()
            ty = None
            if self.is_type_word(self.tok) and (self.peek().kind in (IDENT, ANNOT) or self.peek().is_op("<", "?", "...")):
                ty = self.parse_type()
            annots = self.parse_annotations()
            variadic = bool(self.accept_op("..."))
            n = self.expect_ident("a parameter name")
            default = None
            if self.accept_op("="):
                default = self.parse_expr()
            params.append(A.Param(start, ty, n.value, annots, default, variadic))
            if not self.accept_op(","):
                break
        self.expect_op(")", "to close the parameter list")
        return params

    def parse_function_body(self) -> "A.Block | A.Expr | A.ActionBody":
        if self.accept_op("=>"):
            e = self.parse_expr()
            self.expect_semi()
            return e
        if self.at_op("{") and self.peek().is_word("Action") and self.peek(2).is_op(":"):
            start = self.advance().span
            self.advance()
            self.advance()
            if self.tok.kind != STRING:
                self.error('expected the intent as text after `Action:`, e.g. Action: "return the median"')
            text = self.advance().value
            self.accept_op(";")
            self.expect_op("}")
            return A.ActionBody(start, str(text))
        return self.parse_block()

    def parse_class(self) -> A.ClassDecl:
        start = self.advance().span
        annots = self.parse_annotations()
        name = self.expect_ident("the class name")
        self.expect_op("=")
        if not self.at_word("Class"):
            self.error("expected `Class(...)` after `=`", fix=f"write `class {name.value} = Class() {{ ... }}`")
        self.advance()
        self.expect_op("(")
        parent = None
        implements: list[str] = []
        while not self.at_op(")"):
            if self.at_word("implements") and self.peek().is_op("="):
                self.advance()
                self.advance()
                self.expect_op("[")
                while not self.at_op("]"):
                    implements.append(self.expect_ident().value)
                    if not self.accept_op(","):
                        break
                self.expect_op("]")
            else:
                parent = self.expect_ident("the parent class").value
            if not self.accept_op(","):
                break
        self.expect_op(")")
        self.expect_op("{")
        fields: list[A.VarDecl] = []
        methods: list[A.FuncDecl] = []
        while not self.at_op("}"):
            if self.tok.kind == EOF:
                self.error("class body is never closed", fix="add `}`")
            if self.tok.kind == IDENT and self.tok.value in FUNCTION_WORDS:
                methods.append(self.parse_function())
                continue
            d = self.try_parse_decl()
            if not isinstance(d, A.VarDecl):
                self.error("a class body can only contain fields and functions")
            fields.append(d)
        self.advance()
        return A.ClassDecl(start, name.value, annots, parent, implements, fields, methods)

    # ---- control flow --------------------------------------------------------
    def parse_cond(self) -> A.Expr:
        """Inside `if ( ... )`: an expression, or a bind `auto v = maybe`."""
        if self.is_type_word(self.tok) and self.peek().kind == IDENT and self.peek(2).is_op("="):
            start = self.sp()
            ty = self.parse_type()
            name = self.expect_ident().value
            self.expect_op("=")
            value = self.parse_expr()
            return A.BindCond(start, ty, name, value)
        return self.parse_expr()

    def parse_if(self) -> A.If:
        start = self.advance().span
        self.expect_op("(")
        cond = self.parse_cond()
        self.expect_op(")", "to close the condition")
        if not self.at_op("{"):
            self.error("an if body must be a block in { }", fix="wrap the body in { ... }")
        then = self.parse_block()
        elifs: list[tuple[A.Expr, A.Block]] = []
        else_ = None
        while self.at_word("else"):
            self.advance()
            if self.at_word("if"):
                self.advance()
                self.expect_op("(")
                c = self.parse_cond()
                self.expect_op(")")
                elifs.append((c, self.parse_block()))
            else:
                else_ = self.parse_block()
                break
        return A.If(start, cond, then, elifs, else_)

    def parse_repeat(self, is_for: bool) -> A.Stmt:
        start = self.advance().span
        annots = self.parse_annotations()
        for a in annots:
            if a.name in ("label",):
                self.error("loop labels (`@label`) are not supported yet", self.toks[self.i - 1], code="FX1001")
        at_least_once = any(a.name in ("atleastonce", "dowhile") for a in annots)
        if any(a.name == "parallel" for a in annots):
            self.error("`repeat @parallel` is not supported yet", code="FX1001")
        self.expect_op("(")
        # forever: repeat()
        if self.at_op(")"):
            self.advance()
            return A.RepeatForever(start, self.parse_block())
        # C style: repeat(@none; ...) or for (init; cond; step)
        if self.tok.kind == ANNOT and self.tok.value == "none":
            self.advance()
            self.expect_op(";")
            return self._parse_c_loop(start)
        if is_for and self._header_has_semicolon():
            return self._parse_c_loop(start)
        if self._header_has_semicolon():
            self.error("loopers in repeat(...; ...) are not supported yet", code="FX1001",
                       fix="use repeat(@none; init; condition; step) for a C-style loop")
        # each: repeat([int i, auto x] in ...) / repeat(int i in ...) / repeat(x in ...)
        if self.at_op("["):
            save = self.i
            self.advance()
            targets: list[A.UnpackTarget] = []
            ok = True
            while not self.at_op("]"):
                ty = None
                if self.is_type_word(self.tok) and self.peek().kind == IDENT:
                    ty = self.parse_type()
                if self.tok.kind != IDENT:
                    ok = False
                    break
                nt = self.advance()
                targets.append(A.UnpackTarget(ty, nt.value, False, nt.span))
                if not self.accept_op(","):
                    break
            if ok and self.accept_op("]") and self.at_word("in"):
                self.advance()
                it = self.parse_expr()
                self.expect_op(")")
                return A.RepeatEach(start, targets, it, self.parse_block())
            self.i = save
        if self.is_type_word(self.tok):
            save = self.i
            ty = self.parse_type()
            if self.at_op("["):
                self.advance()
                targets = []
                while not self.at_op("]"):
                    nt = self.expect_ident()
                    targets.append(A.UnpackTarget(None, nt.value, False, nt.span))
                    if not self.accept_op(","):
                        break
                self.expect_op("]")
                if not self.at_word("in"):
                    self.error("expected `in` after the loop variables")
                self.advance()
                it = self.parse_expr()
                self.expect_op(")")
                return A.RepeatEach(start, targets, it, self.parse_block(), common_type=ty)
            if self.tok.kind == IDENT and self.peek().is_word("in"):
                nt = self.advance()
                self.advance()
                it = self.parse_expr()
                self.expect_op(")")
                return A.RepeatEach(start, [A.UnpackTarget(ty, nt.value, False, nt.span)], it, self.parse_block())
            self.i = save
        if self.tok.kind == IDENT and self.peek().is_word("in"):
            nt = self.advance()
            self.advance()
            it = self.parse_expr()
            self.expect_op(")")
            return A.RepeatEach(start, [A.UnpackTarget(None, nt.value, False, nt.span)], it, self.parse_block())
        expr = self.parse_expr()
        self.expect_op(")", "to close the repeat header")
        body = self.parse_block()
        if at_least_once:
            return A.RepeatWhile(start, expr, body, at_least_once=True)
        # a number -> times, a bool -> while: the checker decides from the type
        return A.RepeatTimes(start, expr, body)

    def _header_has_semicolon(self) -> bool:
        depth = 0
        j = self.i
        while j < len(self.toks):
            t = self.toks[j]
            if t.kind == OP:
                if t.value in ("(", "[", "{"):
                    depth += 1
                elif t.value in (")", "]", "}"):
                    if depth == 0:
                        return False
                    depth -= 1
                elif t.value == ";" and depth == 0:
                    return True
            if t.kind == EOF:
                return False
            j += 1
        return False

    def _parse_c_loop(self, start: Span) -> A.RepeatC:
        init: A.Stmt | None = None
        if not self.at_op(";"):
            init = self.try_parse_decl(need_semi=False) or self.parse_simple_statement(need_semi=False)
        self.expect_op(";")
        cond = None if self.at_op(";") else self.parse_expr()
        self.expect_op(";")
        step = None if self.at_op(")") else self.parse_simple_statement(need_semi=False)
        self.expect_op(")")
        return A.RepeatC(start, init, cond, step, self.parse_block())

    def parse_switch(self, is_value: bool) -> A.Switch:
        start = self.advance().span
        self.expect_op("(")
        if self.tok.kind == ANNOT and self.tok.value == "none":
            self.advance()
            self.expect_op(";")
        elif self._header_has_semicolon():
            self.error("checkers in switch(...; ...) are not supported yet", code="FX1001",
                       fix="use the inline form: switch(@none; value) { case(...).act(...); }")
        subject = self.parse_expr()
        self.expect_op(")")
        self.expect_op("{")
        cases: list[A.Case] = []
        while not self.at_op("}"):
            cstart = self.sp()
            if self.at_word("case"):
                self.advance()
                self.expect_op("(")
                pats: list[A.Expr] | None = []
                while not self.at_op(")"):
                    pats.append(self.parse_expr())
                    if not self.accept_op(","):
                        break
                self.expect_op(")")
            elif self.at_word("default"):
                self.advance()
                pats = None
            else:
                self.error("expected `case(...)` or `default` inside switch")
            self.expect_op(".", "before act")
            if not (self.tok.kind == IDENT and self.tok.value in ACT_WORDS):
                self.error("expected `.act(...)` after the case")
            self.advance()
            body = self.parse_act_body()
            self.expect_semi()
            cases.append(A.Case(pats, body, cstart))
        self.advance()
        return A.Switch(start, subject, cases, is_value)

    def parse_act_body(self) -> list[A.Stmt]:
        """`.act( stmt; stmt; )`: statements, the last `;` optional."""
        self.expect_op("(")
        saved = self.semi_optional_before
        self.semi_optional_before = ")"
        stmts: list[A.Stmt] = []
        while not self.at_op(")"):
            if self.tok.kind == EOF:
                self.error("act(...) is never closed", fix="add `)`")
            stmts.append(self.parse_statement())
        self.advance()
        self.semi_optional_before = saved
        return stmts

    def parse_try(self) -> A.Try:
        start = self.advance().span
        body = self.parse_block()
        catches: list[A.Catch] = []
        while self.at_word("catch"):
            cs = self.advance().span
            tname = var = None
            if self.accept_op("("):
                if self.tok.kind == IDENT and self.peek().kind == IDENT:
                    tname = self.advance().value
                    var = self.advance().value
                elif self.tok.kind == IDENT:
                    w = self.advance().value
                    if w in self.class_names:
                        tname = w
                    else:
                        var = w
                self.expect_op(")")
            catches.append(A.Catch(tname, var, self.parse_block(), cs))
        fin = None
        if self.at_word("finally"):
            self.advance()
            fin = self.parse_block()
        if not catches and fin is None:
            self.error("`try` needs at least one `catch` or a `finally`")
        return A.Try(start, body, catches, fin)

    def parse_return(self) -> A.Return:
        start = self.advance().span
        values: list[A.Expr] = []
        if not (self.at_op(";") or (self.semi_optional_before and self.at_op(self.semi_optional_before))):
            values.append(self.parse_expr())
            while self.accept_op(","):
                values.append(self.parse_expr())
        self.expect_semi()
        return A.Return(start, values)

    # ---- expressions ----------------------------------------------------------
    def parse_expr(self) -> A.Expr:
        return self.parse_ternary()

    def parse_ternary(self) -> A.Expr:
        cond = self.parse_or()
        if self.at_op("?"):
            start = self.advance().span
            a = self.parse_ternary()
            self.expect_op(":", "in `condition ? a : b`")
            b = self.parse_ternary()
            return A.IfExpr(start, cond, a, b)
        return cond

    def parse_or(self) -> A.Expr:
        left = self.parse_and()
        while self.at_word("or") or self.at_op("||"):
            t = self.advance()
            left = A.Logical(t.span, "or", left, self.parse_and())
        return left

    def parse_and(self) -> A.Expr:
        left = self.parse_compare()
        while self.at_word("and") or self.at_op("&&"):
            t = self.advance()
            left = A.Logical(t.span, "and", left, self.parse_compare())
        return left

    def _compare_op(self) -> str | None:
        t = self.tok
        if t.kind == OP and t.value in COMPARE_OPS:
            return t.value
        if t.kind == IDENT:
            if t.value == "in":
                return "in"
            if t.value == "not" and self.peek().is_word("in"):
                return "not in"
            if t.value == "is":
                return "is"
        return None

    def parse_compare(self) -> A.Expr:
        first = self.parse_coalesce()
        ops: list[str] = []
        operands = [first]
        while (op := self._compare_op()) is not None:
            t = self.advance()
            if op == "not in":
                self.advance()
            if op == "is":
                negate = False
                if self.at_word("not"):
                    self.advance()
                    negate = True
                if self.at_word(*NONE_WORDS):
                    nt = self.advance()
                    ops.append("!=" if negate else "==")
                    operands.append(A.NoneLit(nt.span))
                    continue
                if self.is_type_word(self.tok):
                    target = self.parse_type()
                    expr = operands[-1] if len(operands) == 1 else A.Compare(t.span, ops, operands)
                    return A.TypeTest(t.span, expr, target, negate)
                self.error("after `is`, write a type (x is int) or none; use == to compare values",
                           fix="use `==`")
            ops.append(op)
            operands.append(self.parse_coalesce())
        if not ops:
            return first
        return A.Compare(first.span, ops, operands)

    def parse_coalesce(self) -> A.Expr:
        left = self.parse_range()
        while self.at_op("??"):
            t = self.advance()
            left = A.Coalesce(t.span, left, self.parse_range())
        return left

    def _starts_expr(self, t: Token) -> bool:
        if t.kind in (INT, FLOAT, PERCENT, DURATION, STRING, FSTRING, CHAR, IDENT):
            return not (t.kind == IDENT and t.value in ("step", "and", "or", "in", "else"))
        return t.kind == OP and t.value in ("(", "[", "{", "-", "~", "!")

    def parse_range(self) -> A.Expr:
        if self.at_op("..", "..="):
            t = self.advance()
            end = self.parse_bitor()
            return A.RangeExpr(t.span, None, end, t.value == "..=")
        left = self.parse_bitor()
        if self.at_op("..", "..="):
            t = self.advance()
            inclusive = t.value == "..="
            end = self.parse_bitor() if self._starts_expr(self.tok) else None
            step = None
            if self.at_word("step"):
                self.advance()
                step = self.parse_bitor()
            return A.RangeExpr(t.span, left, end, inclusive, step)
        return left

    def _binary_level(self, ops: tuple[str, ...], next_fn) -> A.Expr:
        left = next_fn()
        while self.tok.kind == OP and self.tok.value in ops:
            t = self.advance()
            left = A.Binary(t.span, t.value, left, next_fn())
        return left

    def parse_bitor(self) -> A.Expr:
        return self._binary_level(("|",), self.parse_bitxor)

    def parse_bitxor(self) -> A.Expr:
        return self._binary_level(("^",), self.parse_bitand)

    def parse_bitand(self) -> A.Expr:
        return self._binary_level(("&",), self.parse_shift)

    def parse_shift(self) -> A.Expr:
        return self._binary_level(("<<", ">>"), self.parse_additive)

    def parse_additive(self) -> A.Expr:
        return self._binary_level(("+", "-"), self.parse_mult)

    def parse_mult(self) -> A.Expr:
        return self._binary_level(("*", "/", "~/", "%"), self.parse_unary)

    def parse_unary(self) -> A.Expr:
        if self.at_op("-", "~", "!") or self.at_word("not"):
            t = self.advance()
            op = "not" if t.value in ("!", "not") else t.value
            operand = self.parse_unary()
            if op == "-" and isinstance(operand, A.IntLit):
                operand.value = -operand.value
                operand.span = t.span
                return operand
            if op == "-" and isinstance(operand, A.FloatLit):
                operand.value = -operand.value
                operand.span = t.span
                return operand
            return A.Unary(t.span, op, operand)
        return self.parse_power()

    def parse_power(self) -> A.Expr:
        base = self.parse_postfix()
        if self.at_op("**"):
            t = self.advance()
            return A.Binary(t.span, "**", base, self.parse_unary())
        return base

    def _q_is_ternary(self) -> bool:
        """Is the `?` at self.i the start of `cond ? a : b` (rather than postfix `?`)?"""
        depth = 0
        j = self.i + 1
        while j < len(self.toks):
            t = self.toks[j]
            if t.kind == EOF:
                return False
            if t.kind == OP:
                if t.value in ("(", "[", "{"):
                    depth += 1
                elif t.value in (")", "]", "}"):
                    if depth == 0:
                        return False
                    depth -= 1
                elif depth == 0 and t.value in (";", ","):
                    return False
                elif depth == 0 and t.value == ":":
                    return True
            j += 1
        return False

    def parse_postfix(self) -> A.Expr:
        e = self.parse_primary()
        while True:
            if self.at_op("("):
                e = self.parse_call(e)
            elif self.at_op("["):
                t = self.advance()
                idx = self.parse_expr()
                self.expect_op("]")
                e = A.Index(t.span, e, idx)
            elif self.at_op("."):
                t = self.advance()
                if self.tok.kind == INT:
                    e = A.Member(t.span, e, str(self.advance().value))
                else:
                    e = A.Member(t.span, e, self.expect_ident("a field or method name").value)
            elif self.at_op("?."):
                t = self.advance()
                e = A.Member(t.span, e, self.expect_ident().value, safe=True)
            elif self.at_op("?") and not self._q_is_ternary():
                t = self.advance()
                e = A.Propagate(t.span, e)
            elif self.at_word("as"):
                t = self.advance()
                mode = "as"
                if self.at_op("?"):
                    self.advance()
                    mode = "as?"
                elif self.at_op("!"):
                    self.advance()
                    mode = "as!"
                e = A.Cast(t.span, e, self.parse_type(), mode)
            else:
                return e

    def parse_call(self, func: A.Expr) -> A.Call:
        t = self.expect_op("(")
        args: list[A.Arg] = []
        while not self.at_op(")"):
            astart = self.sp()
            name = None
            if self.tok.kind == IDENT and self.peek().is_op("="):
                name = self.advance().value
                self.advance()
            value = self.parse_expr()
            args.append(A.Arg(name, value, astart))
            if not self.accept_op(","):
                break
        self.expect_op(")", "to close the call")
        return A.Call(t.span, func, args)

    def parse_primary(self) -> A.Expr:
        t = self.tok
        k = t.kind
        if k == INT:
            self.advance()
            if isinstance(t.value, tuple):
                return A.IntLit(t.span, t.value[0], t.value[1])
            return A.IntLit(t.span, t.value)
        if k == FLOAT:
            self.advance()
            v = t.value[0] if isinstance(t.value, tuple) else t.value
            return A.FloatLit(t.span, float(v))
        if k == PERCENT:
            self.advance()
            return A.FloatLit(t.span, float(t.value))
        if k == DURATION:
            self.advance()
            return A.DurationLit(t.span, float(t.value))
        if k == STRING:
            self.advance()
            return A.StrLit(t.span, str(t.value))
        if k == CHAR:
            self.advance()
            return A.CharLit(t.span, str(t.value))
        if k == FSTRING:
            self.advance()
            return self.parse_fstring(t)
        if k == REGEX:
            self.error("regular expressions (re\"...\") are not supported yet", t, code="FX1001")
        if k == IDENT:
            w = t.value
            if w in TRUE_WORDS:
                self.advance()
                return A.BoolLit(t.span, True)
            if w in FALSE_WORDS:
                self.advance()
                return A.BoolLit(t.span, False)
            if w in NONE_WORDS:
                self.advance()
                return A.NoneLit(t.span)
            if w == "this":
                self.advance()
                return A.This(t.span)
            if w == "super":
                self.advance()
                return A.Super(t.span)
            if w == "if" and self.peek().is_op("("):
                self.advance()
                self.expect_op("(")
                cond = self.parse_expr()
                self.expect_op(")")
                a = self.parse_expr()
                if not self.at_word("else"):
                    self.error("an if-expression needs an `else`", fix='write `if (c) a else b`')
                self.advance()
                b = self.parse_expr()
                return A.IfExpr(t.span, cond, a, b)
            if w in SWITCH_WORDS and self.peek().is_op("("):
                sw = self.parse_switch(is_value=True)
                return A.SwitchExpr(sw.span, sw)
            if w in FUNCTION_CTOR_WORDS and self.peek().is_op("("):
                self.advance()
                params = self.parse_params()
                if self.accept_op("=>"):
                    body: A.Expr | A.Block = self.parse_expr()
                else:
                    body = self.parse_block()
                return A.Lambda(t.span, params, body)
            if w == "Action":
                self.error("Action(...) values need the LLM resolver, which is not available in fx3c yet",
                           t, code="FX0905")
            if w == "new" and self.peek().kind == IDENT and self.peek().value in self.class_names:
                self.advance()   # `new Point(2, 3)` is accepted and means `Point(2, 3)`
                return self.parse_primary()
            self.advance()
            return A.Name(t.span, w)
        if k == OP:
            if t.value == "(":
                self.advance()
                if self.accept_op(")"):
                    return A.TupleLit(t.span, [])
                first = self.parse_expr()
                if self.at_op(","):
                    items = [first]
                    while self.accept_op(","):
                        if self.at_op(")"):
                            break
                        items.append(self.parse_expr())
                    self.expect_op(")")
                    return A.TupleLit(t.span, items)
                self.expect_op(")")
                return first
            if t.value == "[":
                self.advance()
                if self.accept_op("]"):
                    return A.ListLit(t.span, [])
                first = self.parse_expr()
                if self.at_word("for"):
                    self.advance()
                    var = self.expect_ident("the loop variable").value
                    if not self.at_word("in"):
                        self.error("expected `in` in the list comprehension")
                    self.advance()
                    it = self.parse_expr()
                    cond = None
                    if self.at_word("if"):
                        self.advance()
                        cond = self.parse_expr()
                    self.expect_op("]")
                    return A.Comprehension(t.span, first, var, it, cond)
                items = [first]
                while self.accept_op(","):
                    if self.at_op("]"):
                        break
                    items.append(self.parse_expr())
                self.expect_op("]", "to close the list")
                return A.ListLit(t.span, items)
            if t.value == "{":
                self.advance()
                if self.accept_op("}"):
                    return A.EmptyBraces(t.span)
                first = self.parse_expr()
                if self.accept_op(":"):
                    pairs = [(first, self.parse_expr())]
                    while self.accept_op(","):
                        if self.at_op("}"):
                            break
                        k_ = self.parse_expr()
                        self.expect_op(":", "between key and value")
                        pairs.append((k_, self.parse_expr()))
                    self.expect_op("}", "to close the map")
                    return A.MapLit(t.span, pairs)
                items = [first]
                while self.accept_op(","):
                    if self.at_op("}"):
                        break
                    items.append(self.parse_expr())
                self.expect_op("}", "to close the set")
                return A.SetLit(t.span, items)
        self.error(f"expected a value, found {self.describe(t)}")

    def parse_fstring(self, tok: Token) -> A.FString:
        text = str(tok.value)
        parts: list[A.FStringPart] = []
        buf: list[str] = []
        i = 0
        while i < len(text):
            c = text[i]
            if c == "\\" and i + 1 < len(text) and text[i + 1] in "{}":
                buf.append(text[i + 1])
                i += 2
                continue
            if c == "{":
                if buf:
                    parts.append(A.FStringPart(text="".join(buf)))
                    buf = []
                depth = 1
                j = i + 1
                in_str: str | None = None
                while j < len(text) and depth:
                    ch = text[j]
                    if in_str:
                        if ch == in_str:
                            in_str = None
                    elif ch in "\"'":
                        in_str = ch
                    elif ch in "{([":
                        depth += 1
                    elif ch in "})]":
                        depth -= 1
                    j += 1
                if depth:
                    self.error("`{` in f-string is never closed", tok, fix="add `}` (or write \\{ for a literal brace)")
                inner = text[i + 1:j - 1]
                expr_src, spec, debug = self._split_fstring_field(inner)
                sub = Parser(tokenize(expr_src, self.file), self.file)
                sub.class_names = self.class_names
                try:
                    e = sub.parse_expr()
                except CompileError as ce:
                    d = ce.diagnostic
                    raise CompileError(Diagnostic("error", d.code, f"in f-string: {d.message}", tok.span, d.fix))
                if sub.tok.kind != EOF:
                    self.error(f"unexpected `{sub.tok.text}` inside f-string braces", tok)
                parts.append(A.FStringPart(expr=e, spec=spec, debug=debug, text=expr_src.strip()))
                i = j
                continue
            if c == "}":
                self.error("single `}` in f-string", tok, fix="write \\} for a literal brace")
            buf.append(c)
            i += 1
        if buf:
            parts.append(A.FStringPart(text="".join(buf)))
        return A.FString(tok.span, parts)

    @staticmethod
    def _split_fstring_field(inner: str) -> tuple[str, str, bool]:
        depth = 0
        in_str: str | None = None
        for k, ch in enumerate(inner):
            if in_str:
                if ch == in_str:
                    in_str = None
                continue
            if ch in "\"'":
                in_str = ch
            elif ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == ":" and depth == 0 and not inner[k + 1:k + 2] == ":" and not inner[k - 1:k] == ":":
                expr, spec = inner[:k], inner[k + 1:]
                break
        else:
            expr, spec = inner, ""
        debug = False
        if expr.rstrip().endswith("=") and not expr.rstrip().endswith(("==", "!=", "<=", ">=")):
            debug = True
            expr = expr.rstrip()[:-1]
        return expr, spec, debug


def parse(source: str, file: str = "<input>") -> A.Program:
    return Parser(tokenize(source, file), file).parse_program()
