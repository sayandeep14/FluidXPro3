"""Reader: a `.fluid` file -> blocks. Fully deterministic (FLUID_SPEC.md sections 1-2).

Rules:
  - a block keyword (or alias) at column 0 starts a block
  - everything indented below it belongs to that block
  - sub-keywords (example, exact, ask, with) are recognised one level in, only when
    the line holds nothing but the keyword and its arguments
  - `//` starts a comment at the start of a line or after a space (so URLs survive)
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .errors import FluidError, Message
from .keywords import NAMED, match_block_keyword, match_sub_keyword, suggest_block_keyword

TAG_RE = re.compile(r"\s+\[(off|warn|error)\]\s*$")
ASK_LEVELS = {"soft", "mid", "hard"}


@dataclass
class Example:
    lines: list[str]
    line_no: int


@dataclass
class Block:
    keyword: str               # canonical keyword
    written: str               # as written (may be an alias)
    header: str                # text after the keyword on the first line
    line_no: int               # 1-based line of the header
    end_line: int              # last line of the block
    body: list[str]            # body lines, comments removed, indentation kept relative
    raw: str                   # the exact source text of the block
    name: str = ""
    inputs: list[str] = field(default_factory=list)
    output: str = ""
    examples: list[Example] = field(default_factory=list)
    exact: str | None = None   # pinned fx3 code
    exact_lines: tuple[int, int] | None = None
    ask: str | None = None
    with_settings: list[tuple[str, str]] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)   # rules/never line -> off|warn|error
    modifier: str = ""         # rules strict / rules loose
    id: str = ""

    @property
    def fluid_hash(self) -> str:
        return hashlib.sha256(self.raw.encode()).hexdigest()[:12]

    def prose(self) -> str:
        return "\n".join(self.body)


@dataclass
class Document:
    file: str
    source: str
    blocks: list[Block]
    file_ask: str | None = None
    file_target: str | None = None
    file_with: list[tuple[str, str]] = field(default_factory=list)

    def block(self, block_id: str) -> Block | None:
        return next((b for b in self.blocks if b.id == block_id), None)


def strip_comment(line: str) -> str:
    """Remove a `//` comment: at line start, or after whitespace (keeps https://...)."""
    stripped = line.lstrip()
    if stripped.startswith("//"):
        return ""
    m = re.search(r"\s//", line)
    if m:
        # don't cut inside a quoted string
        before = line[:m.start()]
        if before.count('"') % 2 == 0:
            return before.rstrip()
    return line.rstrip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t").replace("\t", "    ")) if line.strip() else 10**6


def parse_signature(header: str) -> tuple[str, list[str], str]:
    """`monthly summary (month) -> Summary`  ->  ("monthly summary", ["month"], "Summary")"""
    output = ""
    if "->" in header:
        header, output = header.split("->", 1)
        output = output.strip()
    inputs: list[str] = []
    m = re.search(r"\((.*)\)\s*$", header.strip())
    if m:
        inside = m.group(1).strip()
        if inside:
            inputs = [p.strip() for p in inside.split(",") if p.strip()]
        header = header.strip()[:m.start()]
    return header.strip(), inputs, output


def _block_id(b: Block) -> str:
    if b.keyword in NAMED and b.name:
        return f"{b.keyword} {b.name}"
    return b.keyword


def read(source: str, file: str = "main.fluid") -> Document:
    lines = source.splitlines()
    errors: list[Message] = []
    starts: list[tuple[int, str, str, str]] = []   # (index, canon, written, rest)
    for i, raw in enumerate(lines):
        if not raw.strip() or raw[0] in " \t":
            continue
        text = strip_comment(raw)
        if not text.strip():
            continue
        m = match_block_keyword(text)
        if m is None:
            first = text.split()[0]
            guess = suggest_block_keyword(first)
            errors.append(Message(
                "error", "FL0101", f"`{first}` is not a block keyword", file, i + 1,
                fix=(f"did you mean `{guess}`?" if guess else
                     "start the line with a keyword (to, thing, rules, ...), or indent it to make it part "
                     "of the block above")))
            continue
        starts.append((i, *m))
    if errors:
        raise FluidError(errors)

    doc = Document(file, source, [])
    for k, (i, canon, written, rest) in enumerate(starts):
        end = (starts[k + 1][0] if k + 1 < len(starts) else len(lines)) - 1
        # blank lines and column-0 comments after a block belong to the gap, not the block
        while end > i and (not lines[end].strip() or (lines[end][0] not in " \t" and not strip_comment(lines[end]))):
            end -= 1
        raw_text = "\n".join(lines[i:end + 1])
        body_raw = [x for x in lines[i + 1:end + 1]
                    if not (x.strip() and x[0] not in " \t" and not strip_comment(x))]
        block = Block(canon, written, strip_comment(rest) if rest else "", i + 1, end + 1, [], raw_text)
        _parse_body(block, body_raw, i + 2, file)
        _parse_header(block, file)
        # top-level `ask` / `target` / `with` configure the whole file
        if canon == "ask":
            lvl = block.header.strip().lower()
            if lvl not in ASK_LEVELS:
                raise FluidError(Message("error", "FL0102", f"`ask {lvl}`: the level must be soft, mid or hard",
                                         file, block.line_no))
            doc.file_ask = lvl
        elif canon == "target":
            doc.file_target = block.header.strip().lower()
        elif canon == "with":
            doc.file_with += parse_with(block.header, file, block.line_no)
        doc.blocks.append(block)
    # ids, made unique
    seen: dict[str, int] = {}
    for b in doc.blocks:
        bid = _block_id(b)
        seen[bid] = seen.get(bid, 0) + 1
        b.id = bid if seen[bid] == 1 else f"{bid} #{seen[bid]}"
    return doc


def _parse_header(b: Block, file: str) -> None:
    h = b.header
    if b.keyword in ("to", "check", "flow", "ai"):
        b.name, b.inputs, b.output = parse_signature(h)
        if not b.output:
            for line in b.body:
                if line.strip().startswith("->"):
                    b.output = line.strip()[2:].strip()
                    break
        if not b.name:
            raise FluidError(Message("error", "FL0103", f"`{b.written}` needs a name, e.g. `to add expense (amount)`",
                                     file, b.line_no))
    elif b.keyword in ("rules", "never"):
        mod = h.strip().lower()
        if mod in ("strict", "loose"):
            b.modifier = mod
        elif mod:
            b.name = h.strip()
    elif b.keyword in NAMED:
        b.name = h.strip()


def parse_with(text: str, file: str, line_no: int) -> list[tuple[str, str]]:
    """`safety paranoid, comments detailed` -> [("safety", "paranoid"), ("comments", "detailed")]"""
    out = []
    for part in [p.strip() for p in text.split(",") if p.strip()]:
        bits = part.split(None, 1)
        if len(bits) != 2:
            raise FluidError(Message("error", "FL0104", f"`with {part}`: write a setting and a value, "
                                     "e.g. `with safety paranoid`", file, line_no))
        out.append((bits[0], bits[1].strip()))
    return out


def _parse_body(b: Block, body: list[str], first_line_no: int, file: str) -> None:
    # common indentation of the body
    ind = min((_indent(x) for x in body if x.strip()), default=0)
    k = 0
    cleaned: list[str] = []
    while k < len(body):
        raw = body[k]
        line_no = first_line_no + k
        if not raw.strip():
            cleaned.append("")
            k += 1
            continue
        if _indent(raw) == ind:
            text = strip_comment(raw).strip()
            sub = match_sub_keyword(text) if text else None
            if sub is not None:
                canon, rest = sub
                if canon == "example" and rest in ("", ":"):
                    k += 1
                    ex_lines = []
                    while k < len(body) and (not body[k].strip() or _indent(body[k]) > ind):
                        t = strip_comment(body[k]).strip()
                        if t:
                            ex_lines.append(t)
                        k += 1
                    b.examples.append(Example(ex_lines, line_no))
                    continue
                if canon == "exact" and rest in ("", ":"):
                    k += 1
                    while k < len(body) and not body[k].strip():
                        k += 1
                    if k >= len(body) or not body[k].strip().startswith("```"):
                        raise FluidError(Message("error", "FL0105", "`exact` must be followed by a ```fx3 code block",
                                                 file, line_no))
                    fence_ind = _indent(body[k])
                    start = k
                    k += 1
                    code: list[str] = []
                    while k < len(body) and body[k].strip() != "```":
                        code.append(body[k][fence_ind:] if len(body[k]) >= fence_ind else body[k].lstrip())
                        k += 1
                    if k >= len(body):
                        raise FluidError(Message("error", "FL0106", "the ``` code block after `exact` is never closed",
                                                 file, first_line_no + start))
                    b.exact = "\n".join(code).rstrip() + "\n"
                    b.exact_lines = (first_line_no + start, first_line_no + k)
                    k += 1
                    continue
                if canon == "ask" and rest.lower() in ASK_LEVELS:
                    b.ask = rest.lower()
                    k += 1
                    continue
                if canon == "with" and rest:
                    b.with_settings += parse_with(rest, file, line_no)
                    k += 1
                    continue
        text = strip_comment(raw)
        if b.keyword in ("rules", "never") and text.strip():
            m = TAG_RE.search(text)
            if m:
                text = text[:m.start()]
                b.tags[text.strip()] = m.group(1)
        if text.strip():
            cleaned.append(text[ind:] if len(text) >= ind else text.strip())
        k += 1
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    b.body = cleaned


def guarantee_lines(b: Block) -> list[str]:
    """The individual lines of a rules / never block."""
    return [x.strip() for x in b.body if x.strip()]
