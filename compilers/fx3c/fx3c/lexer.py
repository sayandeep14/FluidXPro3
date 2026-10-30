"""Lexer: turns .fx3 source text into tokens (Syntax/lexical.fx3)."""

from __future__ import annotations

import textwrap
from dataclasses import dataclass

from .diagnostics import CompileError, Diagnostic, Span

# Token kinds
IDENT = "IDENT"
INT = "INT"
FLOAT = "FLOAT"
PERCENT = "PERCENT"      # 50%  -> 0.5
DURATION = "DURATION"    # 500ms, 2s, 5m, 1h, 1d -> seconds
STRING = "STRING"
FSTRING = "FSTRING"
CHAR = "CHAR"
REGEX = "REGEX"
ANNOT = "ANNOT"          # @name
OP = "OP"
EOF = "EOF"

# Longest first.
OPERATORS = [
    "...", "..=", "**=", "~/=", "<<=", ">>=", "===", "??=",
    "==", "!=", "<=", ">=", "&&", "||", "++", "--", "+=", "-=", "*=", "/=", "%=",
    "&=", "|=", "^=", "**", "~/", "<<", ">>", "..", "??", "?.", "=>", "->", "::",
    "+", "-", "*", "/", "%", "=", "<", ">", "!", "&", "|", "^", "~", "?", ":",
    ";", ",", ".", "(", ")", "[", "]", "{", "}",
]

INT_SUFFIXES = {"i8", "i16", "i32", "i64", "i128", "u8", "u16", "u32", "u64", "u128"}
FLOAT_SUFFIXES = {"f", "f32", "f64"}
_DURATION_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
_SIZE_UNITS = {"b": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3}

# Tokens after which a value has just ended. Used to tell `.5` (a float) from
# `person.0` (member access).
_OPERAND_END_KINDS = {IDENT, INT, FLOAT, PERCENT, DURATION, STRING, FSTRING, CHAR, REGEX}
_OPERAND_END_OPS = {")", "]", "}"}


@dataclass
class Token:
    kind: str
    value: object
    span: Span
    text: str = ""          # original spelling

    def is_op(self, *ops: str) -> bool:
        return self.kind == OP and self.value in ops

    def is_word(self, *words: str) -> bool:
        return self.kind == IDENT and self.value in words

    def __repr__(self) -> str:
        return f"Token({self.kind}, {self.value!r} @ {self.span.line}:{self.span.col})"


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "'": "'",
            "{": "\\{", "}": "\\}"}


class Lexer:
    def __init__(self, source: str, file: str = "<input>"):
        self.src = source
        self.file = file
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens: list[Token] = []

    # ---- helpers -----------------------------------------------------------
    def _err(self, msg: str, line: int, col: int, length: int = 1, fix: str | None = None):
        raise CompileError(Diagnostic("error", "FX0101", msg, Span(self.file, line, col, length), fix))

    def _peek(self, off: int = 0) -> str:
        i = self.pos + off
        return self.src[i] if i < len(self.src) else ""

    def _peek_in(self, off: int, chars: str) -> bool:
        """True if the character at `off` is one of `chars` (never true at the end of input)."""
        c = self._peek(off)
        return c != "" and c in chars

    def _advance(self, n: int = 1) -> str:
        s = self.src[self.pos:self.pos + n]
        for ch in s:
            if ch == "\n":
                self.line += 1
                self.col = 1
            else:
                self.col += 1
        self.pos += n
        return s

    def _prev_is_operand_end(self) -> bool:
        if not self.tokens:
            return False
        t = self.tokens[-1]
        return t.kind in _OPERAND_END_KINDS or (t.kind == OP and t.value in _OPERAND_END_OPS)

    def _add(self, kind: str, value: object, line: int, col: int, text: str) -> None:
        self.tokens.append(Token(kind, value, Span(self.file, line, col, max(1, len(text))), text))

    # ---- main loop ---------------------------------------------------------
    def tokenize(self) -> list[Token]:
        while self.pos < len(self.src):
            ch = self._peek()
            if ch in " \t\r\n":
                self._advance()
                continue
            # comments
            if ch == "/" and self._peek(1) == "/":
                self._skip_line()
                continue
            if ch == "#":
                self._skip_line()
                continue
            if ch == "/" and self._peek(1) == "*":
                self._skip_block_comment()
                continue
            line, col = self.line, self.col
            # string prefixes: f"..", r"..", re".."
            if ch in "fr" and self._peek_in(1, "\"'"):
                self._advance()
                self._string(line, col, prefix=ch)
                continue
            if ch == "r" and self._peek(1) == "e" and self._peek_in(2, "\"'"):
                self._advance(2)
                self._string(line, col, prefix="re")
                continue
            if ch in "\"'":
                self._string(line, col, prefix="")
                continue
            if ch.isdigit() or (ch == "." and self._peek(1).isdigit() and not self._prev_is_operand_end()):
                self._number(line, col)
                continue
            if ch == "@":
                self._annotation(line, col)
                continue
            if ch == "`":
                self._backtick_ident(line, col)
                continue
            if ch.isalpha() or ch == "_":
                self._ident(line, col)
                continue
            for op in OPERATORS:
                if self.src.startswith(op, self.pos):
                    self._advance(len(op))
                    self._add(OP, op, line, col, op)
                    break
            else:
                self._err(f"unexpected character {ch!r}", line, col)
        self.tokens.append(Token(EOF, None, Span(self.file, self.line, self.col), ""))
        return self.tokens

    def _skip_line(self) -> None:
        while self.pos < len(self.src) and self._peek() != "\n":
            self._advance()

    def _skip_block_comment(self) -> None:
        line, col = self.line, self.col
        end = self.src.find("*/", self.pos + 2)
        if end < 0:
            self._err("block comment is never closed", line, col, 2, fix="add */")
        self._advance(end + 2 - self.pos)

    def _ident(self, line: int, col: int) -> None:
        start = self.pos
        while self._peek() and (self._peek().isalnum() or self._peek() == "_"):
            self._advance()
        word = self.src[start:self.pos]
        self._add(IDENT, word, line, col, word)

    def _backtick_ident(self, line: int, col: int) -> None:
        self._advance()
        start = self.pos
        while self._peek() and self._peek() != "`":
            if self._peek() == "\n":
                self._err("backtick name is not closed on the same line", line, col)
            self._advance()
        word = self.src[start:self.pos].strip().replace(" ", "_")
        self._advance()
        self._add(IDENT, word, line, col, word)

    def _annotation(self, line: int, col: int) -> None:
        self._advance()
        start = self.pos
        while self._peek() and (self._peek().isalnum() or self._peek() == "_"):
            self._advance()
        name = self.src[start:self.pos]
        if not name:
            self._err("`@` must be followed by an annotation name", line, col)
        self._add(ANNOT, name, line, col, "@" + name)

    def _number(self, line: int, col: int) -> None:
        start = self.pos
        src = self.src
        is_float = False
        if src.startswith(("0x", "0X"), self.pos):
            self._advance(2)
            while self._peek() and (self._peek() in "0123456789abcdefABCDEF_"):
                self._advance()
            digits = src[start + 2:self.pos].replace("_", "")
            value: object = int(digits, 16)
        elif src.startswith(("0b", "0B"), self.pos):
            self._advance(2)
            while self._peek() in ("0", "1", "_"):
                self._advance()
            value = int(src[start + 2:self.pos].replace("_", ""), 2)
        elif src.startswith(("0o", "0O"), self.pos):
            self._advance(2)
            while self._peek() and self._peek() in "01234567_":
                self._advance()
            value = int(src[start + 2:self.pos].replace("_", ""), 8)
        else:
            def digits() -> None:
                while True:
                    c = self._peek()
                    if c.isdigit() or c == "_":
                        self._advance()
                    elif c == "'" and self._peek(1).isdigit() and self.pos > start and src[self.pos - 1].isdigit():
                        self._advance()       # C++ digit separator 1'000'000
                    else:
                        break
            digits()
            # decimal point: only if followed by a digit (so `1..10` stays a range)
            if self._peek() == "." and self._peek(1).isdigit():
                is_float = True
                self._advance()
                digits()
            if self._peek() in ("e", "E") and (self._peek(1).isdigit() or
                                               (self._peek_in(1, "+-") and self._peek(2).isdigit())):
                is_float = True
                self._advance(2 if self._peek_in(1, "+-") else 1)
                digits()
            raw = src[start:self.pos].replace("_", "").replace("'", "")
            value = float(raw) if is_float else int(raw)
        text = src[start:self.pos]
        # suffix (10u8, 2.5f) or percent (50%)
        suffix = None
        if self._peek().isalpha():
            s = self.pos
            while self._peek() and (self._peek().isalnum()):
                self._advance()
            suffix = src[s:self.pos]
            if suffix in FLOAT_SUFFIXES:
                value = float(value)
                is_float = True
            elif suffix in INT_SUFFIXES and not is_float:
                pass
            elif suffix in _DURATION_UNITS:
                secs = float(value) * _DURATION_UNITS[suffix]
                self._add(DURATION, secs, line, col, src[start:self.pos])
                return
            elif suffix in _SIZE_UNITS and not is_float:
                self._add(INT, int(value) * _SIZE_UNITS[suffix], line, col, src[start:self.pos])
                return
            else:
                self._err(f"unknown number suffix `{suffix}`", line, col, self.pos - start)
            text = src[start:self.pos]
        elif self._peek() == "%" and not (self._peek(1).isalnum() or self._peek_in(1, "(_")):
            self._advance()
            self._add(PERCENT, float(value) / 100.0, line, col, src[start:self.pos])
            return
        kind = FLOAT if is_float else INT
        tok_value = (value, suffix) if suffix else value
        self._add(kind, tok_value, line, col, text)

    def _string(self, line: int, col: int, prefix: str) -> None:
        q = self._peek()
        triple = self.src.startswith(q * 3, self.pos)
        delim = q * 3 if triple else q
        self._advance(len(delim))
        raw = prefix in ("r", "re")
        buf: list[str] = []
        depth = 0           # inside {...} of an f-string, quotes belong to the expression
        while True:
            if self.pos >= len(self.src):
                self._err("string is never closed", line, col, fix=f"add {delim}")
            c = self._peek()
            if prefix == "f" and depth > 0 and c in "\"'" and not self.src.startswith(delim * 3, self.pos):
                # a string inside an f-string expression: copy it as it is
                end = self.src.find(c, self.pos + 1)
                if end < 0 or "\n" in self.src[self.pos:end]:
                    self._err("string inside an f-string expression is never closed", self.line, self.col)
                buf.append(self.src[self.pos:end + 1])
                self._advance(end + 1 - self.pos)
                continue
            if prefix == "f" and c == "{":
                depth += 1
            elif prefix == "f" and c == "}" and depth > 0:
                depth -= 1
            if depth == 0 and self.src.startswith(delim, self.pos):
                self._advance(len(delim))
                break
            if c == "\n" and not triple:
                self._err("string is not closed before the end of the line", line, col,
                          fix=f'close it with {delim}, or use """...""" for multi-line text')
            if c == "\\" and not raw:
                nxt = self._peek(1)
                if nxt == "u" and self._peek(2) == "{":
                    end = self.src.find("}", self.pos)
                    code = self.src[self.pos + 3:end]
                    try:
                        buf.append(chr(int(code, 16)))
                    except ValueError:
                        self._err(f"bad unicode escape \\u{{{code}}}", self.line, self.col)
                    self._advance(end + 1 - self.pos)
                    continue
                if nxt in _ESCAPES:
                    # keep \{ \} marked so f-string splitting can tell them apart
                    buf.append(_ESCAPES[nxt] if prefix == "f" or nxt not in "{}" else nxt)
                    self._advance(2)
                    continue
                self._err(f"unknown escape \\{nxt}", self.line, self.col, 2)
            buf.append(c)
            self._advance()
        text = "".join(buf)
        if triple:
            text = textwrap.dedent(text.lstrip("\n").rstrip(" \t"))
            if text.endswith("\n"):
                text = text[:-1]
        if prefix == "f":
            self._add(FSTRING, text, line, col, "f" + delim + text + delim)
        elif prefix == "re":
            self._add(REGEX, text, line, col, "re" + delim + text + delim)
        elif q == "'" and not triple and len(text) == 1:
            self._add(CHAR, text, line, col, "'" + text + "'")
        else:
            self._add(STRING, text, line, col, delim + text + delim)


def tokenize(source: str, file: str = "<input>") -> list[Token]:
    return Lexer(source, file).tokenize()
