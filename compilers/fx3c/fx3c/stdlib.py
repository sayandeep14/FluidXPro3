"""The standard library, described once and in fx3's own syntax.

Each module is a built-in class with static members (Math.sqrt, Time.now). A module can
also bring extra types (Http brings HttpResponse; Server brings Request and Response).
Signatures are written like fx3 parameter lists: "(string path, string text) -> int".

The checker turns this into ClassInfo / FuncInfo, so every normal check applies (types,
@fails, named arguments, typo suggestions). Each backend implements the modules in its
own language (Python: backends/python/fx3std.py), with exactly these names.

`handler` in a signature means function[<Request>|<Response>].
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Member:
    name: str
    sig: str
    static: bool = True
    fails: tuple[str, ...] = ()
    infer: str | None = None        # special return-type rule (see checker._std_infer)
    doc: str = ""


@dataclass
class StdType:
    name: str
    doc: str
    members: list[Member] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)       # instance fields (read-only)
    constants: dict[str, str] = field(default_factory=dict)    # static fields
    derive: set[str] = field(default_factory=set)


def S(name: str, sig: str, *fails: str, infer: str | None = None, doc: str = "") -> Member:
    return Member(name, sig, True, tuple(fails), infer, doc)


def M(name: str, sig: str, *fails: str, infer: str | None = None, doc: str = "") -> Member:
    return Member(name, sig, False, tuple(fails), infer, doc)


IO = "IOError"
NET = "NetworkError"
FMT = "FormatError"

MODULES: dict[str, list[StdType]] = {
    "Math": [StdType("Math", "maths functions and constants", [
        S("sqrt", "(float x) -> float"), S("pow", "(float base, float exponent) -> float"),
        S("log", "(float x) -> float"), S("log10", "(float x) -> float"), S("log2", "(float x) -> float"),
        S("exp", "(float x) -> float"),
        S("sin", "(float x) -> float"), S("cos", "(float x) -> float"), S("tan", "(float x) -> float"),
        S("asin", "(float x) -> float"), S("acos", "(float x) -> float"), S("atan", "(float x) -> float"),
        S("atan2", "(float y, float x) -> float"), S("hypot", "(float a, float b) -> float"),
        S("clamp", "(any value, any low, any high) -> any", infer="first_arg"),
        S("lerp", "(float a, float b, float t) -> float"),
        S("gcd", "(int a, int b) -> int"), S("lcm", "(int a, int b) -> int"),
        S("isPrime", "(int n) -> bool"), S("factorial", "(int n) -> int"),
    ], constants={"pi": "float", "e": "float", "tau": "float", "inf": "float"})],

    "Random": [StdType("Random", "random numbers (Random.seed(n) makes them repeatable)", [
        S("int", "(int low, int high) -> int", doc="a whole number from low to high (both included)"),
        S("float", "() -> float", doc="from 0.0 (included) to 1.0 (excluded)"),
        S("chance", "(float probability) -> bool"),
        S("pick", "(any items) -> any", infer="element", doc="a random element; panics if empty"),
        S("shuffle", "(any @ref items) -> void"),
        S("seed", "(int n) -> void"),
    ])],

    "Time": [StdType("Time", "dates and times (local time)", [
        S("now", "() -> Time"), S("today", "() -> Time"),
        S("of", "(int year, int month, int day, int hour = 0, int minute = 0, int second = 0) -> Time",
          "ValueError"),
        S("parse", '(string text, string format = "YYYY-MM-DD") -> Time', "ParseError"),
        S("since", "(Time start) -> duration"),
        M("year", "() -> int"), M("month", "() -> int"), M("day", "() -> int"),
        M("hour", "() -> int"), M("minute", "() -> int"), M("second", "() -> int"),
        M("weekday", "() -> int", doc="1 = Monday ... 7 = Sunday"),
        M("weekdayName", "() -> string"), M("monthName", "() -> string"),
        M("format", '(string pattern = "YYYY-MM-DD HH:mm:ss") -> string',
          doc="YYYY MM DD HH mm ss MMM MMMM ddd dddd"),
        M("plus", "(duration d) -> Time"), M("minus", "(duration d) -> Time"),
        M("diff", "(Time other) -> duration", doc="this time minus the other"),
        M("isBefore", "(Time other) -> bool"), M("isAfter", "(Time other) -> bool"),
        M("date", "() -> Time", doc="the same day at midnight"),
        M("toString", "() -> string"),
    ], derive={"eq", "order", "hash", "print"})],

    "File": [StdType("File", "reading and writing files (paths are text)", [
        S("read", "(string path) -> string", IO), S("readLines", "(string path) -> vector<string>", IO),
        S("write", "(string path, string text) -> void", IO),
        S("writeLines", "(string path, vector<string> lines) -> void", IO),
        S("append", "(string path, string text) -> void", IO),
        S("exists", "(string path) -> bool"), S("delete", "(string path) -> void", IO),
        S("copy", "(string source, string target) -> void", IO),
        S("move", "(string source, string target) -> void", IO),
        S("size", "(string path) -> int", IO),
    ])],

    "Path": [StdType("Path", "file paths and folders", [
        S("join", "(string ...parts) -> string"), S("name", "(string path) -> string"),
        S("stem", "(string path) -> string"), S("ext", "(string path) -> string"),
        S("parent", "(string path) -> string"), S("absolute", "(string path) -> string"),
        S("cwd", "() -> string"), S("isDir", "(string path) -> bool"), S("isFile", "(string path) -> bool"),
        S("listDir", '(string dir = ".") -> vector<string>', IO),
        S("walk", '(string dir = ".") -> vector<string>', IO),
        S("makeDir", "(string path) -> void", IO),
    ])],

    "Csv": [StdType("Csv", "CSV files as lists of rows (each row maps column name -> text)", [
        S("read", "(string path) -> vector<map<string, string>>", IO, FMT),
        S("parse", "(string text) -> vector<map<string, string>>", FMT),
        S("write", "(string path, vector<map<string, string>> rows) -> void", IO),
        S("stringify", "(vector<map<string, string>> rows) -> string"),
    ])],

    "Json": [StdType("Json", "JSON values", [
        S("parse", "(string text) -> Json", FMT),
        S("stringify", "(any value, bool pretty = false) -> string"),
        S("of", "(any value) -> Json"),
        M("get", "(string key) -> Json", doc="a missing key gives a null Json"),
        M("at", "(int index) -> Json"), M("has", "(string key) -> bool"),
        M("keys", "() -> vector<string>"), M("size", "() -> int"),
        M("isNull", "() -> bool"), M("isString", "() -> bool"), M("isNumber", "() -> bool"),
        M("isBool", "() -> bool"), M("isList", "() -> bool"), M("isObject", "() -> bool"),
        M("asString", "() -> string?"), M("asInt", "() -> int?"), M("asFloat", "() -> float?"),
        M("asBool", "() -> bool?"), M("asList", "() -> vector<Json>"),
        M("toString", "() -> string"),
    ], derive={"eq", "print"})],

    "Env": [StdType("Env", "environment variables and program arguments (secrets.env is loaded too)", [
        S("get", "(string name) -> string?"),
        S("require", "(string name) -> string", "ValueError"),
        S("has", "(string name) -> bool"),
        S("args", "() -> vector<string>"), S("platform", "() -> string"),
    ])],

    "Log": [StdType("Log", "log messages to the error output, with time and level", [
        S("debug", "(string message) -> void"), S("info", "(string message) -> void"),
        S("warn", "(string message) -> void"), S("error", "(string message) -> void"),
        S("level", "(string name) -> void", doc="debug | info | warn | error"),
    ])],

    "Http": [
        StdType("Http", "an HTTP client", [
            S("get", "(string url, map<string, string> headers = {}, duration timeout = 30s) -> HttpResponse", NET),
            S("post", '(string url, string body = "", string contentType = "application/json", '
                      "map<string, string> headers = {}, duration timeout = 30s) -> HttpResponse", NET),
            S("put", '(string url, string body = "", string contentType = "application/json", '
                     "map<string, string> headers = {}, duration timeout = 30s) -> HttpResponse", NET),
            S("delete", "(string url, map<string, string> headers = {}, duration timeout = 30s) -> HttpResponse",
              NET),
            S("postJson", "(string url, any value, map<string, string> headers = {}, "
                          "duration timeout = 30s) -> HttpResponse", NET),
        ]),
        StdType("HttpResponse", "the answer to an HTTP request", [
            M("json", "() -> Json", FMT), M("ok", "() -> bool", doc="status 200-299"),
        ], fields={"status": "int", "body": "string", "headers": "map<string, string>"}),
    ],

    "Server": [
        StdType("Server", "a small HTTP server: routes -> handler functions", [
            S("create", "(int port = 8080) -> Server"),
            M("get", "(string path, handler h) -> void", doc='paths can have parameters: "/items/{id}"'),
            M("post", "(string path, handler h) -> void"), M("put", "(string path, handler h) -> void"),
            M("delete", "(string path, handler h) -> void"),
            M("start", "() -> void", doc="start in the background"), M("stop", "() -> void"),
            M("keepRunning", "() -> void", doc="start and serve until the program is stopped"),
        ], fields={"port": "int"}),
        StdType("Request", "an incoming HTTP request", [
            M("json", "() -> Json", FMT),
            M("param", "(string name) -> string?", doc="a {name} from the path, or a query parameter"),
        ], fields={"method": "string", "path": "string", "body": "string",
                   "query": "map<string, string>", "params": "map<string, string>",
                   "headers": "map<string, string>"}),
        StdType("Response", "what a handler returns", [
            S("text", "(string body, int status = 200) -> Response"),
            S("json", "(any value, int status = 200) -> Response"),
            S("html", "(string body, int status = 200) -> Response"),
            S("notFound", '(string message = "not found") -> Response'),
            S("error", "(string message, int status = 500) -> Response"),
            S("redirect", "(string url) -> Response"),
        ], fields={"status": "int", "body": "string", "contentType": "string"}),
    ],

    "Db": [StdType("Db", "a SQLite database (a file, or \":memory:\")", [
        S("open", "(string path) -> Db", IO),
        M("exec", "(string sql, list params = []) -> int", "DbError", doc="returns the number of changed rows"),
        M("query", "(string sql, list params = []) -> vector<map<string, Json>>", "DbError"),
        M("queryOne", "(string sql, list params = []) -> map<string, Json>?", "DbError"),
        M("lastId", "() -> int"), M("close", "() -> void"),
    ])],

    "Ai": [StdType("Ai", "ask Claude while the program runs (needs ANTHROPIC_API_KEY)", [
        S("ask", '(string prompt, string system = "", string model = "claude-opus-5-5") -> string', "AiError"),
        S("classify", '(string text, vector<string> options, string instructions = "", '
                      'string model = "claude-opus-5-5") -> string', "AiError"),
    ])],

    "Email": [StdType("Email", "send email over SMTP (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM)", [
        S("send", "(string to, string subject, string body) -> void", NET),
    ])],
}

# every type name a module provides -> its module
TYPE_MODULE: dict[str, str] = {t.name: mod for mod, types in MODULES.items() for t in types}
ALL_TYPE_NAMES = set(TYPE_MODULE)


def types_used_by(mod: str) -> set[str]:
    """Standard types a module's signatures and fields mention (importing it makes them usable)."""
    import re
    words: set[str] = set()
    for t in MODULES[mod]:
        texts = [m.sig for m in t.members] + list(t.fields.values()) + list(t.constants.values())
        for text in texts:
            words |= set(re.findall(r"[A-Z]\w*", text))
        if any("handler" in m.sig for m in t.members):
            words |= {"Request", "Response"}
    return {w for w in words if w in TYPE_MODULE}

# extra error types used by the standard library
STD_ERRORS = {"DbError": "Error", "AiError": "Error"}
