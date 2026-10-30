"""Running generated programs in a sandbox (ROADMAP E-01).

fluidc runs model-written tests to verify them, so those tests must not be able to touch the
developer's machine. There are two layers. The OS layer is used where it exists; the process
layer is always used.

    process  a fresh temporary folder as the working directory and HOME, an environment with no
             secrets, CPU / memory / file-size limits, a wall-clock timeout, and a Python audit
             hook that blocks the following:
               - reading outside the program folder and Python's own files
               - writing outside the program folder
               - network access other than localhost
               - starting processes
               - ctypes
    os       the same, inside the operating system's sandbox:
               - macOS: sandbox-exec
               - Linux: bubblewrap (bwrap), if it is installed
             No network except loopback, the home folder hidden, writes only to the program folder.

Levels are "auto" (os when available, otherwise process), "process", "os" (fail if missing)
and "off" (no sandbox: the developer trusts the code).
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

LEVELS = ("auto", "os", "process", "off")
BLOCK_TAG = "fx3-sandbox: blocked "

# Environment variables passed through. Everything else (API keys, tokens, ...) is dropped.
_KEEP_ENV = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT")


class SandboxUnavailable(Exception):
    pass


@dataclass
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    level: str                       # the level actually used: "os" | "process" | "off"
    timed_out: bool = False
    blocked: list[str] = field(default_factory=list)   # what the sandbox stopped, one line each

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


@functools.lru_cache(maxsize=1)
def os_layer() -> str | None:
    """The OS sandbox this machine has: "sandbox-exec", "bwrap" or None."""
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        try:   # sandbox-exec can't nest inside another sandbox: try it once
            ok = subprocess.run(["sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"],
                                capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        return "sandbox-exec" if ok else None
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        try:
            ok = subprocess.run(["bwrap", "--ro-bind", "/", "/", "--unshare-net", "true"],
                                capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        return "bwrap" if ok else None
    return None


def resolve_level(requested: str) -> str:
    if requested not in LEVELS:
        raise ValueError(f"unknown sandbox level {requested!r}; use one of {', '.join(LEVELS)}")
    if requested == "auto":
        return "os" if os_layer() else "process"
    if requested == "os" and not os_layer():
        raise SandboxUnavailable("no OS sandbox on this machine (macOS needs sandbox-exec; "
                                 "Linux needs bubblewrap: apt install bubblewrap)")
    return requested


def _python_roots() -> list[str]:
    """Folders the sandboxed Python must be able to read: its own install and packages."""
    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}
    roots |= {p for p in sys.path if p and os.path.isdir(p) and ("site-packages" in p or p.endswith("lib-dynload"))}
    exe = os.path.realpath(sys.executable)
    roots.add(os.path.dirname(os.path.dirname(exe)))
    return sorted({os.path.realpath(r) for r in roots if r})


def _macos_profile(root: str, py_roots: list[str], hidden: list[str]) -> str:
    def q(p: str) -> str:
        return json.dumps(os.path.realpath(p))
    lines = [
        "(version 1)",
        "(allow default)",
        "(deny network-outbound)",
        '(allow network-outbound (remote ip "localhost:*"))',
        "(deny network-bind)",
        '(allow network-bind (local ip "localhost:*"))',
    ]
    for h in hidden:                                   # the home folder, the project, ...
        lines.append(f"(deny file-read-data (subpath {q(h)}))")     # contents and listings, not stat()
    lines.append("(allow file-read-data " + " ".join(f"(subpath {q(p)})" for p in [*py_roots, root]) + ")")
    lines.append("(deny file-write*)")
    lines.append(f"(allow file-write* (subpath {q(root)}) (literal \"/dev/null\") (literal \"/dev/tty\")"
                 " (regex #\"^/dev/fd/\") (regex #\"^/dev/std\"))")
    lines.append("(deny process-exec*)")
    lines.append("(allow process-exec* " + " ".join(f"(subpath {q(p)})" for p in py_roots) + ")")
    return "\n".join(lines) + "\n"


def _bwrap_args(root: str, py_roots: list[str], hidden: list[str]) -> list[str]:
    args = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
            "--unshare-net", "--unshare-pid", "--die-with-parent", "--new-session"]
    for h in hidden:
        args += ["--tmpfs", os.path.realpath(h)]
    for p in py_roots:                                  # put Python back if it lives in a hidden folder
        args += ["--ro-bind", p, p]
    args += ["--bind", root, root]
    return args


def run(files: dict[str, str], entry: str, args: list[str] | None = None, *, level: str = "auto",
        timeout: float = 120, memory_mb: int = 2048, file_mb: int = 256, hide: list[str] | None = None,
        env: dict[str, str] | None = None) -> SandboxResult:
    """Write `files` into a fresh folder and run `entry` with the current Python, sandboxed.

    hide: extra folders to hide from the program (the home folder is always hidden).
    env: extra environment variables for the program (never secrets).
    """
    used = resolve_level(level)
    with tempfile.TemporaryDirectory(prefix="fx3-sandbox-") as tmp:
        root = os.path.realpath(tmp)
        work = os.path.join(root, "work")
        os.makedirs(os.path.join(work, "tmp"))
        for name, text in files.items():
            p = Path(work, name)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        if used == "off":
            cmd = [sys.executable, entry, *(args or [])]
            proc_env = {**os.environ, **(env or {})}
            return _exec(cmd, work, proc_env, timeout, used)

        boot = os.path.join(root, "boot.py")
        Path(boot).write_text(_BOOT, encoding="utf-8")
        py_roots = _python_roots()
        limits = {"cpu": int(timeout) + 5, "memory_mb": memory_mb, "file_mb": file_mb,
                  "read_roots": [*py_roots, root, "/dev", "/usr/share/zoneinfo", "/etc/localtime"]}
        cmd = [sys.executable, "-I", "-B", boot, work, entry, json.dumps(limits), *(args or [])]
        proc_env = {k: v for k, v in os.environ.items() if k in _KEEP_ENV}
        proc_env.update({"HOME": work, "USERPROFILE": work, "TMPDIR": os.path.join(work, "tmp"),
                         "TEMP": os.path.join(work, "tmp"), "TMP": os.path.join(work, "tmp"),
                         "PYTHONIOENCODING": "utf-8", "FX3_SANDBOX": used, **(env or {})})
        if used == "os":
            hidden = [str(Path.home()), *(hide or [])]
            hidden = [h for h in hidden if os.path.isdir(h) and not root.startswith(os.path.realpath(h) + os.sep)]
            if os_layer() == "sandbox-exec":
                profile = os.path.join(root, "profile.sb")
                Path(profile).write_text(_macos_profile(root, py_roots, hidden), encoding="utf-8")
                cmd = ["sandbox-exec", "-f", profile, *cmd]
            else:
                cmd = [*_bwrap_args(root, py_roots, hidden), *cmd]
        return _exec(cmd, work, proc_env, timeout, used)


def _exec(cmd: list[str], cwd: str, env: dict[str, str], timeout: float, level: str) -> SandboxResult:
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        return SandboxResult(-1, out, err + f"\nstopped: did not finish within {timeout:g} seconds\n", level,
                             timed_out=True, blocked=_blocked(err))
    return SandboxResult(proc.returncode, proc.stdout, proc.stderr, level, blocked=_blocked(proc.stderr))


def _blocked(stderr: str) -> list[str]:
    seen: list[str] = []
    for line in stderr.splitlines():
        if line.startswith(BLOCK_TAG) and line[len(BLOCK_TAG):] not in seen:
            seen.append(line[len(BLOCK_TAG):])
    return seen


# The bootstrap that runs inside the sandboxed Python, before the program. It must only use the
# standard library and must install the audit hook before any program code runs.
_BOOT = r'''
import json, os, sys

WORK = os.path.realpath(sys.argv[1])
ENTRY = sys.argv[2]
LIMITS = json.loads(sys.argv[3])
ARGS = sys.argv[4:]

try:
    import resource
    def _limit(kind, value):
        try:
            soft, hard = resource.getrlimit(kind)
            value = value if hard == resource.RLIM_INFINITY else min(value, hard)
            resource.setrlimit(kind, (value, hard))
        except (ValueError, OSError):
            pass       # not supported here (e.g. RLIMIT_AS on macOS); the timeout still applies
    _limit(resource.RLIMIT_CPU, LIMITS["cpu"])
    _limit(resource.RLIMIT_FSIZE, LIMITS["file_mb"] * 1024 * 1024)
    if sys.platform.startswith("linux"):
        _limit(resource.RLIMIT_AS, LIMITS["memory_mb"] * 1024 * 1024)
except ImportError:
    pass           # Windows: only the timeout and the audit hook

_case = os.path.normcase
READ_ROOTS = [_case(os.path.realpath(p)) for p in LIMITS["read_roots"]]
WRITE_ROOTS = [_case(WORK)]
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "", "0:0:0:0:0:0:0:1", None}
_stderr = sys.stderr
_busy = [False]


def _inside(path, roots):
    if isinstance(path, int):
        return True                    # an already-open file descriptor
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    path = os.fspath(path)
    if path in ("/dev/null", "nul", "NUL"):
        return True
    full = _case(os.path.realpath(os.path.join(os.getcwd(), path)))
    return any(full == r or full.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def _block(what):
    try:
        _stderr.write("fx3-sandbox: blocked " + what + "\n")
        _stderr.flush()
    except Exception:
        pass
    raise PermissionError("blocked by the fx3 sandbox: " + what)


def _host_ok(host):
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    return host in LOCAL_HOSTS or (isinstance(host, str) and host.startswith("127."))


def _address_ok(address):
    if isinstance(address, tuple) and address:
        return _host_ok(address[0])
    if isinstance(address, (str, bytes)):
        return _inside(address, WRITE_ROOTS)     # a unix socket: only inside the program folder
    return False


_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
_PATH_WRITES = {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.chflags", "os.lchflags",
                "os.truncate", "os.utime", "os.symlink", "os.link", "os.rename", "shutil.rmtree",
                "shutil.copyfile", "shutil.copymode", "shutil.copystat", "shutil.copytree", "shutil.move",
                "tempfile.mkstemp", "tempfile.mkdtemp", "os.mkfifo", "os.mknod", "os.setxattr", "os.removexattr"}
_PATH_READS = {"os.listdir", "os.scandir", "os.chdir", "glob.glob", "os.listxattr", "os.getxattr"}
_NO_PROCESSES = {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork",
                 "os.forkpty", "os.startfile", "pty.spawn", "os.kill", "os.killpg", "webbrowser.open",
                 "sqlite3.enable_load_extension", "sqlite3.load_extension", "ftplib.connect", "nntplib.connect",
                 "poplib.connect", "imaplib.open", "telnetlib.Telnet.open"}
_NO_IMPORTS = {"ctypes", "_ctypes", "cffi"}


def _hook(event, args):
    if _busy[0]:
        return
    _busy[0] = True
    try:
        if event == "open":
            path, mode, flags = (list(args) + [None, None, None])[:3]
            writing = (mode is not None and any(c in str(mode) for c in "wax+")) or \
                      (mode is None and isinstance(flags, int) and flags & _WRITE_FLAGS)
            if writing and not _inside(path, WRITE_ROOTS):
                _block(f"writing {path}")
            if not writing and not _inside(path, READ_ROOTS):
                _block(f"reading {path}")
        elif event in _PATH_WRITES:
            for p in args[:2]:
                if isinstance(p, (str, bytes, os.PathLike)) and not _inside(p, WRITE_ROOTS):
                    _block(f"changing {os.fspath(p)} ({event})")
        elif event in _PATH_READS:
            p = args[0] if args else "."
            if isinstance(p, (str, bytes, os.PathLike)) and not _inside(p, READ_ROOTS):
                _block(f"reading {os.fspath(p)} ({event})")
        elif event == "sqlite3.connect":
            db = args[0] if args else ""
            if db not in (":memory:", "", b":memory:") and not str(db).startswith("file::memory:") \
                    and not _inside(db, WRITE_ROOTS):
                _block(f"opening the database {db}")
        elif event in ("socket.connect", "socket.connect_ex", "socket.sendto", "socket.bind"):
            address = args[1] if len(args) > 1 else None
            if not _address_ok(address):
                _block(f"network access to {address!r} (only localhost is allowed)")
        elif event in ("socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex",
                       "socket.gethostbyaddr"):
            if args and not _host_ok(args[0]):
                _block(f"looking up {args[0]!r} (no network in the sandbox)")
        elif event in ("http.client.connect", "smtplib.connect"):
            if len(args) > 1 and not _host_ok(args[1]):
                _block(f"connecting to {args[1]} (no network in the sandbox)")
        elif event in _NO_PROCESSES or event.startswith("ctypes."):
            _block(f"{event} (no processes or native code in the sandbox)")
        elif event == "import" and args and str(args[0]).split(".")[0] in _NO_IMPORTS:
            _block(f"importing {args[0]}")
    finally:
        _busy[0] = False


sys.path.insert(0, WORK)
os.chdir(WORK)
sys.addaudithook(_hook)
sys.argv = [os.path.join(WORK, ENTRY), *ARGS]

import runpy
runpy.run_path(os.path.join(WORK, ENTRY), run_name="__main__")
'''
