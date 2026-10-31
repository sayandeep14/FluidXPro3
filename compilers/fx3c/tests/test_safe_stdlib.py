"""Safe-by-default standard library (ROADMAP E-04): Server limits / auth / CORS / access log, Db transactions
and migrations, SQL built from values (FX0480), secret masking, Http retries and TLS."""

from __future__ import annotations

import http.client
import json
import socket
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from helpers import codes, run_fx3

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "fx3c" / "backends" / "python"


@pytest.fixture(scope="module")
def std():
    sys.path.insert(0, str(RUNTIME_DIR))
    try:
        import fx3rt
        import fx3std
        yield fx3std, fx3rt
    finally:
        sys.path.remove(str(RUNTIME_DIR))


def ok_handler(std_mod):
    return lambda req: std_mod.Response.text("hi " + req.body)


@pytest.fixture
def server(std):
    fx3std, _ = std
    s = fx3std.Server(0)
    s.get("/", ok_handler(fx3std))
    s.post("/", ok_handler(fx3std))
    s.get("/health", lambda req: fx3std.Response.text("ok"))
    yield s
    s.stop()


def request(port, method="GET", path="/", body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body, headers=headers or {})
    r = conn.getresponse()
    out = r.status, r.read().decode(), dict(r.getheaders())
    conn.close()
    return out


# ---- Server ------------------------------------------------------------------------

def test_a_large_body_is_refused_without_reading_it(server):
    server.limits(maxBodyKb=1)
    server.start()
    status, body, _ = request(server.port, "POST", "/", body="x" * 2048)
    assert status == 413 and "too large" in body
    assert request(server.port, "POST", "/", body="small")[:2] == (200, "hi small")


def test_require_header(server, std):
    server.requireHeader("X-Api-Key", "k3y-for-tests", ["/health"])
    server.start()
    assert request(server.port)[0] == 401
    assert request(server.port, headers={"X-Api-Key": "wrong"})[0] == 401
    assert request(server.port, headers={"x-api-key": "k3y-for-tests"})[0] == 200
    assert request(server.port, path="/health")[0] == 200
    assert std[1].mask("key is k3y-for-tests") == "key is ****"


def test_no_cors_unless_allowed(server):
    server.start()
    _, _, headers = request(server.port, headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in headers
    assert request(server.port, "OPTIONS", headers={"Origin": "https://evil.example"})[0] == 403
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_allowed_origins(server):
    server.allowOrigins(["https://app.example"])
    server.start()
    _, _, h = request(server.port, headers={"Origin": "https://app.example"})
    assert h["Access-Control-Allow-Origin"] == "https://app.example"
    status, _, h = request(server.port, "OPTIONS", headers={"Origin": "https://app.example"})
    assert status == 204 and "GET" in h["Access-Control-Allow-Methods"]
    _, _, h = request(server.port, headers={"Origin": "https://other.example"})
    assert "Access-Control-Allow-Origin" not in h


def test_access_log_is_json_without_the_query(server, capfd):
    server.start()
    request(server.port, path="/?token=abc")
    err = capfd.readouterr().err
    line = next(x for x in err.splitlines() if "access " in x)
    entry = json.loads(line.split("access ", 1)[1])
    assert entry["method"] == "GET" and entry["path"] == "/" and entry["status"] == 200
    assert "abc" not in err


def test_a_slow_client_is_cut_off(server):
    server.limits(timeout=0.5)
    server.start()
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=5)
    sock.sendall(b"GET / HTTP/1.1\r\n")             # ... and never finishes the request
    sock.settimeout(5)
    assert sock.recv(100) == b""                     # the server closed the connection
    sock.close()


# ---- Db ----------------------------------------------------------------------------

def test_transactions(std):
    fx3std, fx3rt = std
    db = fx3std.Db.open(":memory:")
    db.exec("create table t (n int)")
    db.transaction(lambda: db.exec("insert into t values (1)"))

    def failing():
        db.exec("insert into t values (2)")
        raise fx3rt.ValueError("no")
    with pytest.raises(fx3rt.DbError, match="rolled back"):
        db.transaction(failing)

    def panicking():
        db.exec("insert into t values (3)")
        raise fx3rt.Panic("bug")
    with pytest.raises(fx3rt.Panic):
        db.transaction(panicking)
    db.begin()
    db.exec("insert into t values (4)")
    db.rollback()
    assert [r["n"].asInt() for r in db.query("select n from t")] == [1]


def test_migrations(std, tmp_path):
    fx3std, fx3rt = std
    (tmp_path / "001_notes.sql").write_text("create table notes (id integer primary key, text text);")
    (tmp_path / "002_seed.sql").write_text("insert into notes (text) values ('a'); insert into notes (text) values ('b');")
    db_path = str(tmp_path / "app.db")
    db = fx3std.Db.open(db_path)
    assert db.migrate(str(tmp_path)) == 2
    assert db.migrate(str(tmp_path)) == 0                       # each file runs once
    (tmp_path / "003_broken.sql").write_text("insert into notes (text) values ('c'); this is not sql;")
    with pytest.raises(fx3rt.DbError, match="003_broken.sql"):
        db.migrate(str(tmp_path))
    assert len(db.query("select * from notes")) == 2           # the broken file left nothing behind
    db.close()
    names = [r[0] for r in sqlite3.connect(db_path).execute("select name from fx3_migrations")]
    assert names == ["001_notes.sql", "002_seed.sql"]


# ---- SQL built from values ---------------------------------------------------------

SQL = '''import Db;
int @const MAX = 10;
function[<Db, string, int>|<>] @fails(DbError) go = Function(Db db, string name, int id) {
    %s
}
'''


@pytest.mark.parametrize("line", [
    'db.query(f"select * from t where name = \'{name}\'")?;',
    'db.exec("delete from t where id = " + string(id))?;',
    'string q = f"select * from t where n = \'{name}\'"; db.query(q)?;',
    'string q = "select * from t where n = "; q += name; db.queryOne(q)?;',
    'string q = name; db.query(q)?;',
])
def test_sql_from_values_is_an_error(line):
    assert "FX0480" in codes(SQL % line)


@pytest.mark.parametrize("line", [
    'db.query("select * from t where id = ?", [id])?;',
    'db.exec(f"create table t (x text check (length(x) <= {MAX}))")?;',
    'string q = "select * from t where id = ?"; db.query(q, [id])?;',
])
def test_sql_from_constants_is_fine(line):
    assert "FX0480" not in codes(SQL % line)


# ---- secrets -----------------------------------------------------------------------

def test_secrets_are_masked_in_print_and_log(tmp_path):
    src = ('import Env;\nimport Log;\nstring k = Env.get("SHOP_API_KEY") ?? "";\n'
           'print("using", k);\nLog.warn(f"key={k}");\nprint(Env.get("PLAIN_NAME") ?? "");\n')
    import os
    old = dict(os.environ)
    os.environ.update({"SHOP_API_KEY": "sk-test-123456", "PLAIN_NAME": "visible-value"})
    try:
        proc = run_fx3(src)
    finally:
        os.environ.clear()
        os.environ.update(old)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["using ****", "visible-value"]
    assert "key=****" in proc.stderr and "sk-test" not in proc.stderr


# ---- Http --------------------------------------------------------------------------

class _Flaky(BaseHTTPRequestHandler):
    hits: dict[str, int] = {}

    def _answer(self):
        n = self.hits[self.command] = self.hits.get(self.command, 0) + 1
        status = 503 if n < 3 else 200
        self.send_response(status)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    do_GET = do_POST = _answer  # noqa: N815

    def log_message(self, *args):
        pass


def test_idempotent_calls_are_retried(std, monkeypatch):
    fx3std, fx3rt = std
    monkeypatch.setattr(fx3std._time, "sleep", lambda s: None)
    _Flaky.hits = {}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Flaky)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    try:
        assert fx3std.Http.get(url).status == 200                 # 503, 503, then 200
        assert fx3std.Http.post(url).status == 503                # POST is never retried
        assert fx3std.Http.get(url, retries=0).status == 200
    finally:
        httpd.shutdown()
    with pytest.raises(fx3rt.Panic, match="timeout"):
        fx3std.Http.get(url, timeout=0.0)


def test_tls_is_always_verified(std):
    import ssl
    fx3std, _ = std
    assert fx3std._TLS.verify_mode == ssl.CERT_REQUIRED and fx3std._TLS.check_hostname


def test_a_transaction_from_fx3_with_a_block_function():
    """Function(...) { ... } as a value (here db.transaction's work) becomes a local def that can change
    the variables around it."""
    src = '''import Db;
int total = 0;
function[<>|<int>] @fails(IOError, DbError) go = Function() {
    Db db = Db.open(":memory:")?;
    db.exec("create table t (n int)")?;
    int added = 0;
    db.transaction(Function() {
        repeat(int i in [1, 2]) {
            try { db.exec("insert into t values (?)", [i]); } catch (DbError e) { panic(e.message); }
            added += 1;
            total += i;
        }
    })?;
    return added;
}
try { print(go(), total); } catch (Error e) { print("err", e.message); }
'''
    proc = run_fx3(src)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "2 3"


def test_too_many_connections_get_503(server):
    server.limits(maxConnections=1, timeout=5.0)
    server.start()
    hog = socket.create_connection(("127.0.0.1", server.port), timeout=5)
    hog.sendall(b"GET / HTTP/1.1\r\n")                  # holds the only slot
    import time
    time.sleep(0.2)
    status, _, headers = request(server.port)
    assert status == 503 and headers.get("Retry-After") == "1"
    hog.close()
