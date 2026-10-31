"""The standard library, described once and in fx3's own syntax.

Each module is a built-in class with static members (Math.sqrt, Time.now). A module can
also bring extra types (Http brings HttpResponse; Server brings Request and Response).
Signatures are written like fx3 parameter lists: "(string path, string text) -> int".

The checker turns this into ClassInfo / FuncInfo, so every normal check applies (types,
@fails, named arguments, typo suggestions). Each backend implements the modules in its
own language (Python: backends/python/fx3std.py), with exactly these names.

`handler` in a signature means function[<Request>|<Response>]; `job` means function[<>|<>].
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
        StdType("Http", "an HTTP client. TLS certificates are always verified. GET, PUT and DELETE are retried "
                        "(`retries` times, with backoff) after a connection error, a timeout or a 502/503/504", [
            S("get", "(string url, map<string, string> headers = {}, duration timeout = 30s, int retries = 2) "
                     "-> HttpResponse", NET),
            S("post", '(string url, string body = "", string contentType = "application/json", '
                      "map<string, string> headers = {}, duration timeout = 30s) -> HttpResponse", NET),
            S("put", '(string url, string body = "", string contentType = "application/json", '
                     "map<string, string> headers = {}, duration timeout = 30s, int retries = 2) -> HttpResponse", NET),
            S("delete", "(string url, map<string, string> headers = {}, duration timeout = 30s, int retries = 2) "
                        "-> HttpResponse", NET),
            S("postJson", "(string url, any value, map<string, string> headers = {}, "
                          "duration timeout = 30s) -> HttpResponse", NET),
        ]),
        StdType("HttpResponse", "the answer to an HTTP request", [
            M("json", "() -> Json", FMT), M("ok", "() -> bool", doc="status 200-299"),
        ], fields={"status": "int", "body": "string", "headers": "map<string, string>"}),
    ],

    "Server": [
        StdType("Server", "a small HTTP server: routes -> handler functions. Safe by default: request bodies up to "
                          "1 MB (413 above), 30s socket timeouts, no CORS, one JSON access-log line per request, "
                          "graceful shutdown on Ctrl+C / SIGTERM. Host and port: SERVER_HOST (127.0.0.1), SERVER_PORT", [
            S("create", "(int port = 8080) -> Server", doc="SERVER_PORT in the environment wins"),
            M("limits", "(int maxBodyKb = 1024, duration timeout = 30s, int maxConnections = 64) -> void",
              doc="largest request body, how long a slow client may take, and how many connections at once "
                  "(more get 503; SERVER_MAX_CONNECTIONS in the environment sets the default)"),
            M("allowOrigins", "(vector<string> origins) -> void",
              doc='CORS: browsers on these origins may call the API ("*" = any; avoid with requireHeader)'),
            M("requireHeader", "(string name, string value, vector<string> open = []) -> void",
              doc='every route needs this header with this value (401 otherwise), except the paths in `open`; '
                  'e.g. s.requireHeader("X-Api-Key", Env.require("API_KEY"), ["/health"])'),
            M("accessLog", "(bool on) -> void", doc="one JSON line per request (default on)"),
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

    "Db": [StdType("Db", "a SQLite database (a file, or \":memory:\"). Put values in `params` with `?` "
                         "placeholders; building SQL text from values is a compile error (FX0480)", [
        S("open", "(string path) -> Db", IO),
        M("exec", "(string sql, list params = []) -> int", "DbError", doc="returns the number of changed rows"),
        M("query", "(string sql, list params = []) -> vector<map<string, Json>>", "DbError"),
        M("queryOne", "(string sql, list params = []) -> map<string, Json>?", "DbError"),
        M("lastId", "() -> int"), M("close", "() -> void"),
        M("begin", "() -> void", "DbError", doc="start a transaction; other threads wait until it ends"),
        M("commit", "() -> void", "DbError"), M("rollback", "() -> void"),
        M("transaction", "(job work) -> void", "DbError",
          doc="begin, run work, commit; if work fails or panics, roll back and fail"),
        M("migrate", "(string folder) -> int", "IOError", "DbError",
          doc="apply the folder's *.sql files not applied yet, in name order, each in a transaction; "
              "returns how many were applied"),
    ])],

    "Ai": [StdType("Ai", "ask Claude while the program runs (needs ANTHROPIC_API_KEY)", [
        S("ask", '(string prompt, string system = "", string model = "claude-opus-5-5") -> string', "AiError"),
        S("classify", '(string text, vector<string> options, string instructions = "", '
                      'string model = "claude-opus-5-5") -> string', "AiError"),
    ])],

    "Schedule": [StdType("Schedule", "run jobs again and again: every 10m, or every day at 21:00. Last runs are "
                                     "saved (SCHEDULE_STATE, default .fx3-schedule.json), so a restart neither repeats "
                                     "a run nor runs every missed one", [
        S("every", '(duration interval, job work, string at = "", string name = "") -> void',
          doc='at = "HH:MM" (local time) for intervals of whole days; name: what the log and the saved state call '
              'it (default: job 1, job 2, ...)'),
        S("run", "() -> void", doc="run the jobs when they are due, until Ctrl+C / SIGTERM; a failing job is logged "
                                   "and the others go on"),
        S("runPending", "() -> int", doc="run the jobs that are due now, once; returns how many ran"),
    ])],

    "Process": [
        StdType("Process", "run another program. Never through a shell: the command is a list (program, then its "
                           "arguments), so no argument can become a second command", [
            S("run", '(vector<string> command, string input = "", duration timeout = 60s) -> ProcessResult',
              "IOError", "TimeoutError", doc="IOError: the program wasn't found or can't run"),
        ]),
        StdType("ProcessResult", "how a program ended", [
            M("ok", "() -> bool", doc="exit code 0"),
        ], fields={"code": "int", "out": "string", "err": "string"}),
    ],

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
