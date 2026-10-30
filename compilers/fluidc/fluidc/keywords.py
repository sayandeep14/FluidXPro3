"""`.fluid` keywords and their aliases (FLUID_SPEC.md section 2)."""

from __future__ import annotations

import difflib

# canonical -> aliases. Multi-word aliases are allowed ("how to", "must not").
BLOCK_KEYWORDS: dict[str, list[str]] = {
    "app": ["program", "project"],
    "target": ["language", "build for"],
    "ask": ["questions"],
    "with": ["using"],
    "uses": ["use", "needs", "requires", "import"],
    "secrets": ["secret", "env"],
    "settings": ["constants", "defaults"],
    "words": ["glossary", "terms", "definitions"],
    "thing": ["data", "type", "model", "record", "entity"],
    "to": ["function", "fn", "def", "how to"],
    "check": ["decide", "decision", "table", "classify"],
    "flow": ["steps", "process", "workflow", "pipeline"],
    "ai": ["smart", "llm"],
    "web": ["api", "http", "server", "endpoints"],
    "page": ["screen", "view", "ui"],
    "command": ["cmd", "cli"],
    "on": ["upon"],
    "every": ["schedule"],
    "when": ["whenever"],
    "rules": ["rule", "always", "must", "constraints"],
    "never": ["must not", "forbidden", "do not"],
    "test": ["scenario"],
}

SUB_KEYWORDS: dict[str, list[str]] = {
    "example": ["e.g.", "sample"],
    "exact": ["code", "fx3", "verbatim"],
    "ask": ["questions"],
    "with": ["using"],
}

# Keywords whose block is a "named" definition: the text after the keyword is a name.
NAMED = {"to", "check", "flow", "ai", "thing", "test", "page", "command", "app", "when", "every", "on"}


def _spellings(table: dict[str, list[str]]) -> list[tuple[list[str], str]]:
    out = []
    for canon, aliases in table.items():
        for s in [canon, *aliases]:
            out.append((s.lower().split(), canon))
    out.sort(key=lambda x: -len(x[0]))     # longest phrase first
    return out


_BLOCK = _spellings(BLOCK_KEYWORDS)
_SUB = _spellings(SUB_KEYWORDS)


def _match(words: list[str], table) -> tuple[str, int] | None:
    for phrase, canon in table:
        n = len(phrase)
        if [w.lower() for w in words[:n]] == phrase:
            return canon, n
    return None


def match_block_keyword(line: str) -> tuple[str, str, str] | None:
    """`to monthly summary (month)` -> ("to", "to", "monthly summary (month)").

    Returns (canonical, written, rest) or None. Keywords are lowercase (FLUID_SPEC section 1).
    """
    words = line.split()
    if not words or words[0] != words[0].lower():
        return None
    m = _match(words, _BLOCK)
    if m is None:
        return None
    canon, n = m
    written = " ".join(words[:n])
    rest = line.strip()[len(written):].strip()
    return canon, written, rest


def match_sub_keyword(line: str) -> tuple[str, str] | None:
    words = line.split()
    if not words or words[0] != words[0].lower():
        return None
    m = _match(words, _SUB)
    if m is None:
        return None
    canon, n = m
    rest = " ".join(line.split()[n:])
    return canon, rest


def suggest_block_keyword(word: str) -> str | None:
    cands = [s for phrase, _ in _BLOCK for s in [" ".join(phrase)]]
    m = difflib.get_close_matches(word.lower(), cands, n=1, cutoff=0.75)
    if not m:
        return None
    for phrase, canon in _BLOCK:
        if " ".join(phrase) == m[0]:
            return canon
    return None
