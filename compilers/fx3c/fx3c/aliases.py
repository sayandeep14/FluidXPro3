"""Alias tables: every accepted spelling -> canonical name.

Built-in vocabulary is matched loosely: case, `_` and `-` are ignored
(Syntax/lexical.fx3 section 4). User-defined names never go through these tables.
"""

from __future__ import annotations

import difflib


def norm(word: str) -> str:
    return word.replace("_", "").replace("-", "").lower()


def _table(spec: dict[str, list[str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for canonical, spellings in spec.items():
        for s in [canonical, *spellings]:
            out[norm(s)] = canonical
    return out


def typo_match(word: str, candidates: list[str], cutoff: float = 0.82) -> str | None:
    """Closest canonical word, for "did you mean ...?". Deterministic."""
    m = difflib.get_close_matches(norm(word), [norm(c) for c in candidates], n=1, cutoff=cutoff)
    if not m:
        return None
    for c in candidates:
        if norm(c) == m[0]:
            return c
    return None


# ---------------------------------------------------------------------------
# Types (variables.fx3 section 2, collections.fx3 section 1)
# ---------------------------------------------------------------------------

SIMPLE_TYPES = _table({
    "int": ["integer", "i64", "int64", "int64_t", "long", "long_long", "whole", "wholenumber",
            "whole_number"],
    "i8": ["int8", "int8_t", "sbyte", "tiny"],
    "i16": ["int16", "int16_t", "short"],
    "i32": ["int32", "int32_t"],
    "i128": ["int128", "huge"],
    "u8": ["uint8", "uint8_t", "byte"],
    "u16": ["uint16", "uint16_t", "ushort"],
    "u32": ["uint", "uint32", "uint32_t", "unsigned", "natural"],
    "u64": ["uint64", "uint64_t", "ulong", "usize", "size_t"],
    "u128": ["uint128"],
    "float": ["f64", "double", "float64", "real", "decimal"],
    "f32": ["float32", "single"],
    "bool": ["boolean", "flag", "yesno", "yes_no", "truth"],
    "char": ["character", "letter", "symbol", "rune"],
    "string": ["str", "text", "word", "words"],
    "auto": ["var", "let", "val", "infer", "thing", "value"],
    "any": [],
    "duration": [],
    "number": ["num"],          # becomes int or float depending on the value
})

COLLECTION_TYPES = _table({
    "list": [],
    "vector": ["vec"],
    "array": ["arr"],
    "set": ["hashset"],
    "map": ["dict", "hashmap", "dictionary"],
    "tuple": ["pair"],
    "maybe": ["option", "optional"],
    "result": [],
})

INT_RANGES = {
    "i8": (-2**7, 2**7 - 1), "i16": (-2**15, 2**15 - 1), "i32": (-2**31, 2**31 - 1),
    "int": (-2**63, 2**63 - 1), "i128": (-2**127, 2**127 - 1),
    "u8": (0, 2**8 - 1), "u16": (0, 2**16 - 1), "u32": (0, 2**32 - 1),
    "u64": (0, 2**64 - 1), "u128": (0, 2**128 - 1),
}
INT_BITS = {"i8": 8, "i16": 16, "i32": 32, "int": 64, "i128": 128,
            "u8": 8, "u16": 16, "u32": 32, "u64": 64, "u128": 128}
SIGNED = {"i8", "i16", "i32", "int", "i128"}
UNSIGNED = {"u8", "u16", "u32", "u64", "u128"}
INT_TYPES = SIGNED | UNSIGNED
FLOAT_TYPES = {"float", "f32"}

TYPE_KEYWORDS = set(SIMPLE_TYPES) | set(COLLECTION_TYPES)   # normalised spellings


def canonical_simple_type(word: str) -> str | None:
    return SIMPLE_TYPES.get(norm(word))


def canonical_collection_type(word: str) -> str | None:
    return COLLECTION_TYPES.get(norm(word))


def is_builtin_type_word(word: str) -> bool:
    return norm(word) in TYPE_KEYWORDS


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------

ANNOTATIONS = _table({
    # simple variables
    "typesafe": ["strict", "typed", "fixedtype", "locktype"],
    "const": ["constant", "final", "fixed", "readonly", "immutable", "frozen", "val"],
    "comptime": ["constexpr", "compiletime"],
    "mut": ["mutable", "changeable"],
    "smart": ["auto_free", "managed", "gc"],
    "temp": ["temporary", "scoped", "local", "short"],
    "static": ["keep"],
    "global": ["everywhere"],
    "threadlocal": ["thread_local", "perthread"],
    "atomic": ["threadsafe", "safe_shared"],
    "optional": ["nullable", "maybe"],
    "unsigned": ["positive_only", "natural"],
    "range": ["between", "limit"],
    "nonzero": ["notzero"],
    "lazy": ["ondemand"],
    "unused": ["ignore"],
    "wrapping": [],
    "saturating": [],
    "unchecked": ["nobounds", "fast"],
    # collections
    "size": [],
    "init": [],
    "maxsize": ["maxlen", "bounded"],
    "capacity": ["reserve", "prealloc"],
    "sorted": ["keepsorted"],
    "unique": ["nodup", "distinct"],
    "ordered": ["insertion_order", "keeporder"],
    "default": ["defaultvalue", "missing"],
    "max": ["largest"],
    "min": ["smallest"],
    # functions and classes
    "pure": ["nosideeffects"],
    "inline": ["alwaysinline"],
    "memo": ["cache", "memoize"],
    "async": ["background"],
    "public": [],
    "private": [],
    "protected": [],
    "entry": ["main"],
    "example": [],
    "deprecated": [],
    "test": [],
    "recursive": ["tailrec"],
    "fails": [],
    "generic": [],
    "constructor": [],
    "destructor": [],
    "style": [],
    "using": [],
    "override": [],
    "abstract": [],
    "open": [],
    "property": [],
    "setter": [],
    "operator": [],
    "error": [],
    "derive": [],
    "value": [],
    "singleton": [],
    "ref": [],
    "move": [],
    "copy": [],
    "atleastonce": ["dowhile"],
    "label": [],
    "parallel": [],
    "unsafe": [],
    "weak": [],
})

ALL_ANNOTATIONS = sorted(set(ANNOTATIONS.values()))


def canonical_annotation(word: str) -> str | None:
    return ANNOTATIONS.get(norm(word))


# ---------------------------------------------------------------------------
# Keywords (constructs.fx3 section 0). Aliases are only recognised in position.
# ---------------------------------------------------------------------------

FUNCTION_WORDS = {"function", "func", "fn", "def", "method", "proc"}
FUNCTION_CTOR_WORDS = {"Function", "Func", "Fn"}
CLASS_WORDS = {"class"}
REPEAT_WORDS = {"repeat", "loop"}
STOP_WORDS = {"stop", "break"}
SKIP_WORDS = {"skip", "continue", "next"}
FAIL_WORDS = {"fail", "throw", "raise"}
SWITCH_WORDS = {"switch", "match", "select"}
ACT_WORDS = {"act", "do", "on"}

TRUE_WORDS = {"true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON"}
FALSE_WORDS = {"false", "False", "FALSE", "no", "No", "NO", "off", "Off", "OFF"}
NONE_WORDS = {"none", "None", "null", "NULL", "nil", "nullptr", "nothing"}

RESERVED = {
    "function", "looper", "checker", "pipeline", "class", "interface", "enum",
    "Function", "Looper", "Checker", "Pipeline", "Class", "Interface", "Enum",
    "if", "else", "repeat", "for", "switch", "case", "default", "stop", "skip", "return",
    "expose", "breakpoint", "branch", "act",
    "try", "catch", "finally", "fail", "defer", "panic", "ensure",
    "async", "await", "spawn", "with", "from", "import", "as",
    "true", "false", "yes", "no", "on", "off", "none", "this", "super",
    "and", "or", "not", "in", "is", "Action", "type", "move",
}

# ---------------------------------------------------------------------------
# Implicit variables usable inside bank-method arguments
# ---------------------------------------------------------------------------

IMPLICIT_VARS = _table({
    "currVal": [], "index": [], "key": [], "prevVal": [], "nextVal": [], "acc": [],
})

# ---------------------------------------------------------------------------
# Argument names (collections.fx3 section 7)
# ---------------------------------------------------------------------------

ARG_NAMES = _table({
    "index": ["at", "i"],
    "key": ["k"],
    "value": ["val", "v"],
    "newVal": ["new", "to", "newValue"],
    "condition": ["where", "if", "cond"],
    "default": ["or"],
    "by": ["sortBy"],
    "desc": ["descending"],
    "start": ["init"],
    "sep": ["separator"],
    "all": [],
    "count": ["n"],
    "old": [],
    "values": [],
    "other": [],
    "msg": ["message"],
    "ch": ["char"],
})


def canonical_arg(word: str) -> str:
    return ARG_NAMES.get(norm(word), word)


# ---------------------------------------------------------------------------
# Method bank. kind: "seq" (list / vector / array), "map", "set", "string",
# "tuple", "maybe", "result", "number"
# ---------------------------------------------------------------------------

# canonical -> (receiver kinds, aliases, "action" | "query", positional params)
_BANK: dict[str, tuple[set[str], list[str], str, list[str]]] = {
    # info
    "size":        ({"seq", "map", "set", "string", "tuple"}, ["len", "length"], "query", []),
    "isEmpty":     ({"seq", "map", "set", "string"}, ["empty"], "query", []),
    # reading
    "get":         ({"seq", "map", "tuple"}, ["at"], "query", ["index"]),
    "getOr":       ({"seq", "map", "maybe", "result"}, ["get_or", "getOrDefault", "valueOr", "unwrapOr"],
                    "query", ["index", "default"]),
    "first":       ({"seq"}, ["front"], "query", []),
    "last":        ({"seq"}, ["back"], "query", []),
    "slice":       ({"seq", "string"}, ["sub", "substring", "substr"], "query", ["index"]),
    "keys":        ({"map"}, [], "query", []),
    "values":      ({"map"}, [], "query", []),
    "items":       ({"map"}, ["entries", "pairs"], "query", []),
    # adding
    "add":         ({"seq", "set"}, ["push", "append", "insert"], "action", ["value", "index"]),
    "addAll":      ({"seq", "set"}, ["extend"], "action", ["values"]),
    "set":         ({"seq", "map"}, ["put"], "action", ["index", "value"]),
    # removing
    "removeAt":    ({"seq"}, ["erase", "delete"], "action", ["index"]),
    "remove":      ({"seq", "set", "map"}, ["discard"], "action", ["value", "all"]),
    "removeWhere": ({"seq", "set", "map"}, ["removeIf"], "action", ["condition"]),
    "pop":         ({"seq"}, [], "action", []),
    "popFront":    ({"seq"}, ["shift"], "action", []),
    "clear":       ({"seq", "map", "set"}, ["reset"], "action", []),
    # changing
    "modifyAt":    ({"seq", "map"}, ["modify", "update", "updateAt"], "action",
                    ["index", "condition", "newVal", "default", "key"]),
    "replace":     ({"seq", "string"}, [], "action", ["old", "newVal"]),
    "fill":        ({"seq"}, [], "action", ["value", "index"]),
    "swap":        ({"seq"}, [], "action", ["index", "other"]),
    "sort":        ({"seq"}, ["order", "sortBy", "orderBy"], "action", ["by", "desc"]),
    "reverse":     ({"seq", "string"}, [], "action", []),
    "dedupe":      ({"seq"}, ["unique", "distinct"], "action", []),
    # searching
    "contains":    ({"seq", "map", "set", "string"}, ["has", "includes", "hasKey", "containsKey"],
                    "query", ["value"]),
    "indexOf":     ({"seq", "string"}, ["findIndex"], "query", ["value"]),
    "find":        ({"seq", "set"}, [], "query", ["condition"]),
    "count":       ({"seq", "set", "map", "string"}, [], "query", ["value"]),
    "any":         ({"seq", "set", "map"}, ["some"], "query", ["condition"]),
    "all":         ({"seq", "set", "map"}, ["every"], "query", ["condition"]),
    "min":         ({"seq", "set"}, [], "query", []),
    "max":         ({"seq", "set"}, [], "query", []),
    "sum":         ({"seq", "set"}, ["total"], "query", []),
    "average":     ({"seq", "set"}, ["avg", "mean"], "query", []),
    # new collection from an old one
    "filter":      ({"seq", "set", "map"}, ["where", "select"], "query", ["condition"]),
    "map":         ({"seq", "set", "map"}, ["transform"], "query", ["newVal"]),
    "reduce":      ({"seq", "set"}, ["fold"], "query", ["newVal", "start"]),
    "sorted":      ({"seq", "set"}, ["sortedBy"], "query", ["by", "desc"]),
    "reversed":    ({"seq", "string"}, [], "query", []),
    "take":        ({"seq"}, ["limit"], "query", ["count"]),
    "skip":        ({"seq"}, ["drop"], "query", ["count"]),
    "join":        ({"seq", "string"}, [], "query", ["sep"]),
    "copy":        ({"seq", "map", "set"}, ["clone"], "query", []),
    "toVector":    ({"seq", "set", "map"}, [], "query", []),
    "toList":      ({"seq", "set", "map"}, [], "query", []),
    "toSet":       ({"seq", "set", "map"}, [], "query", []),
    "enumerate":   ({"seq"}, ["withIndex"], "query", []),
    # sets
    "union":       ({"set"}, [], "query", ["other"]),
    "intersect":   ({"set"}, ["intersection"], "query", ["other"]),
    "difference":  ({"set"}, ["minus"], "query", ["other"]),
    "isSubset":    ({"set"}, ["subsetOf"], "query", ["other"]),
    # strings
    "upper":       ({"string"}, ["toUpperCase", "upcase", "toUpper"], "query", []),
    "lower":       ({"string"}, ["toLowerCase", "downcase", "toLower"], "query", []),
    "capitalize":  ({"string"}, ["title"], "query", []),
    "trim":        ({"string"}, ["strip"], "query", []),
    "split":       ({"string"}, ["explode", "tokenize"], "query", ["sep"]),
    "lines":       ({"string"}, ["splitlines"], "query", []),
    "words":       ({"string"}, [], "query", []),
    "startsWith":  ({"string"}, ["hasPrefix"], "query", ["value"]),
    "endsWith":    ({"string"}, ["hasSuffix"], "query", ["value"]),
    "repeat":      ({"string"}, ["times"], "query", ["count"]),
    "padLeft":     ({"string"}, ["rjust", "padStart"], "query", ["count", "ch"]),
    "padRight":    ({"string"}, ["ljust", "padEnd"], "query", ["count", "ch"]),
    "isNumber":    ({"string"}, ["isNumeric", "isDigit"], "query", []),
    "isAlpha":     ({"string"}, [], "query", []),
    "toInt":       ({"string"}, [], "query", []),
    "toFloat":     ({"string"}, [], "query", []),
    "toBool":      ({"string"}, [], "query", []),
    "chars":       ({"string"}, [], "query", []),
    "equalsIgnoreCase": ({"string"}, [], "query", ["value"]),
    # numbers / misc
    "toString":    ({"number", "string", "bool", "char", "duration"}, [], "query", []),
    "seconds":     ({"duration"}, ["inSeconds"], "query", []),
    "millis":      ({"duration"}, ["milliseconds", "inMillis"], "query", []),
    "minutes":     ({"duration"}, ["inMinutes"], "query", []),
    "hours":       ({"duration"}, ["inHours"], "query", []),
    "isNone":      ({"maybe"}, [], "query", []),
    "isSome":      ({"maybe"}, [], "query", []),
    "unwrap":      ({"maybe", "result"}, [], "query", ["msg"]),
    "isOk":        ({"result"}, [], "query", []),
    "isError":     ({"result"}, [], "query", []),
}

# Aliases that only apply to some receiver kinds.
_KIND_ALIASES: dict[str, dict[str, str]] = {
    "string": {"find": "indexOf", "index": "indexOf", "replaceAll": "replace"},
    "map": {"insert": "set", "put": "set", "delete": "remove", "erase": "remove"},
}

METHOD_KIND = {name: spec[2] for name, spec in _BANK.items()}
METHOD_PARAMS = {name: spec[3] for name, spec in _BANK.items()}


def lookup_method(kind: str, word: str) -> str | None:
    n = norm(word)
    extra = _KIND_ALIASES.get(kind, {})
    for alias, canon in extra.items():
        if norm(alias) == n:
            return canon
    for canon, (kinds, aliases, _, _) in _BANK.items():
        if kind not in kinds:
            continue
        if norm(canon) == n or any(norm(a) == n for a in aliases):
            return canon
    return None


def methods_for(kind: str) -> list[str]:
    return [c for c, (kinds, _, _, _) in _BANK.items() if kind in kinds]


def method_spellings(kind: str) -> dict[str, str]:
    """Every accepted spelling (canonical and aliases) -> canonical, for typo matching."""
    out: dict[str, str] = {}
    for canon, (kinds, aliases, _, _) in _BANK.items():
        if kind in kinds:
            out[canon] = canon
            for a in aliases:
                out[a] = canon
    for alias, canon in _KIND_ALIASES.get(kind, {}).items():
        out[alias] = canon
    return out
