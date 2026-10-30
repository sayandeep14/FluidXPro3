"""The English condition mini-grammar (collections.fx3 section 7, operators.fx3 section 12).

Turns text such as "currVal is greater than 10 and index is even" into an
ordinary fx3 expression. Fully deterministic: no LLM. Returns None when the
text is outside the grammar, so the caller can report it (or, in a later
version, hand it to the LLM resolver).
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from . import nodes as A
from .diagnostics import Span

_TOKEN = re.compile(r'"[^"]*"|\'[^\']*\'|-?\d+(?:\.\d+)?|>=|<=|==|!=|>|<|[A-Za-z_][A-Za-z0-9_]*|\S')

# (phrase as a list of words) -> operator. Longest phrases first.
_COMPARE_PHRASES: list[tuple[list[str], str]] = sorted([
    (["greater", "than", "or", "equal", "to"], ">="),
    (["less", "than", "or", "equal", "to"], "<="),
    (["no", "less", "than"], ">="),
    (["no", "more", "than"], "<="),
    (["greater", "than"], ">"), (["more", "than"], ">"), (["bigger", "than"], ">"),
    (["larger", "than"], ">"), (["above"], ">"), (["over"], ">"), (["after"], ">"),
    (["less", "than"], "<"), (["fewer", "than"], "<"), (["smaller", "than"], "<"),
    (["below"], "<"), (["under"], "<"), (["before"], "<"),
    (["at", "least"], ">="), (["at", "most"], "<="),
    (["equal", "to"], "=="), (["equals"], "=="), (["same", "as"], "=="),
    ([">="], ">="), (["<="], "<="), (["=="], "=="), (["!="], "!="), ([">"], ">"), (["<"], "<"),
], key=lambda p: -len(p[0]))

_PROPERTIES = {"even", "odd", "positive", "negative", "zero", "empty", "none", "nothing",
               "missing", "true", "false", "yes", "no"}


class _Parser:
    def __init__(self, text: str, span: Span, resolve: Callable[[str], Optional[A.Expr]],
                 default_subject: Optional[A.Expr]):
        self.words = _TOKEN.findall(text)
        self.i = 0
        self.span = span
        self.resolve = resolve
        self.default_subject = default_subject
        self.last_subject: Optional[A.Expr] = None

    def peek(self, off: int = 0) -> str:
        j = self.i + off
        return self.words[j] if j < len(self.words) else ""

    def lw(self, off: int = 0) -> str:
        return self.peek(off).lower()

    def eat(self, *words: str) -> bool:
        if self.lw() in words:
            self.i += 1
            return True
        return False

    def done(self) -> bool:
        return self.i >= len(self.words)

    # condition := clause (("and" | "or" | "but") clause)*
    def parse(self) -> Optional[A.Expr]:
        left = self.parse_and()
        while left is not None and self.lw() == "or":
            self.i += 1
            right = self.parse_and()
            if right is None:
                return None
            left = A.Logical(self.span, "or", left, right)
        return left

    def parse_and(self) -> Optional[A.Expr]:
        left = self.parse_clause()
        while left is not None and self.lw() in ("and", "but"):
            self.i += 1
            right = self.parse_clause()
            if right is None:
                return None
            left = A.Logical(self.span, "and", left, right)
        return left

    def parse_clause(self) -> Optional[A.Expr]:
        negate = self.eat("not")
        subject = None
        # A clause may leave out the subject: "... and less than 20"
        w = self.lw()
        starts_with_predicate = w in ("is", "are", "isn't") or self._match_compare() is not None \
            or w in _PROPERTIES or w in ("between", "divisible", "contains", "has", "starts", "ends")
        if not starts_with_predicate:
            subject = self.parse_subject()
            if subject is None:
                return None
        else:
            subject = self.last_subject or self.default_subject
            if subject is None:
                return None
        self.last_subject = subject
        pred = self.parse_predicate(subject)
        if pred is None:
            return None
        return A.Unary(self.span, "not", pred) if negate else pred

    def parse_subject(self) -> Optional[A.Expr]:
        word = self.peek()
        if not word:
            return None
        e = self.resolve(word)
        if e is None:
            return None
        self.i += 1
        # "the value" / "currVal's length" are not supported: keep it simple
        return e

    def _match_compare(self) -> Optional[tuple[str, int]]:
        for phrase, op in _COMPARE_PHRASES:
            if [self.lw(k) for k in range(len(phrase))] == phrase:
                return op, len(phrase)
        return None

    def parse_predicate(self, subject: A.Expr) -> Optional[A.Expr]:
        negate = False
        if self.eat("isn't"):
            negate = True
        elif self.eat("is", "are"):
            negate = self.eat("not")
        else:
            negate = self.eat("not")
        expr = self._predicate_body(subject)
        if expr is None:
            return None
        return A.Unary(self.span, "not", expr) if negate else expr

    def _predicate_body(self, s: A.Expr) -> Optional[A.Expr]:
        sp = self.span
        m = self._match_compare()
        if m is not None:
            op, n = m
            self.i += n
            v = self.parse_value()
            return None if v is None else A.Compare(sp, [op], [s, v])
        w = self.lw()
        if w == "between":
            self.i += 1
            lo = self.parse_value()
            if lo is None or not self.eat("and"):
                return None
            hi = self.parse_value()
            if hi is None:
                return None
            return A.Compare(sp, ["<=", "<="], [lo, s, hi])
        if w == "even" or w == "odd":
            self.i += 1
            mod = A.Binary(sp, "%", s, A.IntLit(sp, 2))
            return A.Compare(sp, ["=="], [mod, A.IntLit(sp, 0 if w == "even" else 1)])
        if w in ("positive", "negative", "zero"):
            self.i += 1
            op = {"positive": ">", "negative": "<", "zero": "=="}[w]
            return A.Compare(sp, [op], [s, A.IntLit(sp, 0)])
        if w == "empty":
            self.i += 1
            call = A.Call(sp, A.Member(sp, s, "isEmpty"), [])
            return call
        if w in ("none", "nothing", "missing"):
            self.i += 1
            return A.Compare(sp, ["=="], [s, A.NoneLit(sp)])
        if w == "divisible" and self.lw(1) == "by":
            self.i += 2
            v = self.parse_value()
            if v is None:
                return None
            return A.Compare(sp, ["=="], [A.Binary(sp, "%", s, v), A.IntLit(sp, 0)])
        if w in ("contains", "has"):
            self.i += 1
            v = self.parse_value()
            return None if v is None else A.Call(sp, A.Member(sp, s, "contains"), [A.Arg(None, v, sp)])
        if w in ("starts", "ends") and self.lw(1) == "with":
            self.i += 2
            v = self.parse_value()
            name = "startsWith" if w == "starts" else "endsWith"
            return None if v is None else A.Call(sp, A.Member(sp, s, name), [A.Arg(None, v, sp)])
        if w in ("in",):
            return None
        # "x is 5" / "x is true" / "status is done"
        v = self.parse_value()
        return None if v is None else A.Compare(sp, ["=="], [s, v])

    def parse_value(self) -> Optional[A.Expr]:
        sp = self.span
        w = self.peek()
        if not w:
            return None
        self.i += 1
        if (w[0] == '"' and w[-1] == '"') or (w[0] == "'" and w[-1] == "'"):
            return A.StrLit(sp, w[1:-1])
        if re.fullmatch(r"-?\d+", w):
            return A.IntLit(sp, int(w))
        if re.fullmatch(r"-?\d+\.\d+", w):
            return A.FloatLit(sp, float(w))
        lw = w.lower()
        if lw in ("true", "yes"):
            return A.BoolLit(sp, True)
        if lw in ("false", "no"):
            return A.BoolLit(sp, False)
        if lw in ("none", "nothing"):
            return A.NoneLit(sp)
        e = self.resolve(w)
        if e is not None:
            return e
        if re.fullmatch(r"[A-Za-z_]\w*", w):
            return A.StrLit(sp, w)          # a bare word: "status is done"
        return None


def parse_condition(text: str, span: Span, resolve: Callable[[str], Optional[A.Expr]],
                    default_subject: Optional[A.Expr] = None) -> Optional[A.Expr]:
    """Returns an fx3 expression, or None if the text is outside the grammar."""
    p = _Parser(text, span, resolve, default_subject)
    if p.done():
        return None
    expr = p.parse()
    if expr is None or not p.done():
        return None
    return expr
