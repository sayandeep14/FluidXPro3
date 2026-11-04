"""fx3c command line.

    fx3c check  file.fx3              check only (fast; for editors)
    fx3c build  file.fx3 [-o DIR]     generate the target code
    fx3c run    file.fx3 [-- args]    build, then run
    fx3c test   file.fx3              run @test functions and @example cases
    fx3c show   file.fx3              print the generated code
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__
from .backends import BACKENDS, TARGETS, BackendError, canonical_target, get_backend
from .driver import compile_source, write_output
from .secrets import load_secrets


def _compile(args, check_only: bool = False):
    path = Path(args.file)
    if not path.exists():
        print(f"fx3c: no such file: {path}", file=sys.stderr)
        raise SystemExit(2)
    if path.suffix != ".fx3":
        print(f"fx3c: warning: {path.name} doesn't end in .fx3", file=sys.stderr)
    source = path.read_text(encoding="utf-8")
    try:
        get_backend(args.target)
    except BackendError as e:
        print(f"fx3c: {e}", file=sys.stderr)
        raise SystemExit(2) from None
    res = compile_source(source, str(path), target=args.target, check_only=check_only,
                         overflow_checks=not args.no_overflow_checks, root=args.root)
    report = res.report(warnings=not args.quiet)
    if report:
        print(report, file=sys.stderr)
    if not res.ok:
        n = len(res.diags.errors)
        print(f"\nfx3c: {n} error{'s' if n != 1 else ''}; nothing was generated", file=sys.stderr)
        raise SystemExit(1)
    return res, path


def native_uses(res) -> list[str]:
    """Every @native / @extern in the project (targets.fx3 section 6): reviewed like @unsafe."""
    from . import nodes as A
    mods = [res.project.entry, *res.project.modules.values()] if res.project is not None else []
    out: list[tuple[str, int, str]] = []        # (file, line, what): sorted as data, not by parsing text

    def targets(annots) -> list[str]:
        return [k for a in annots for k in (getattr(a, "native", None) or {})]
    for m in mods:
        file = m.path
        decls = [s for s in m.program.stmts if isinstance(s, (A.FuncDecl, A.ClassDecl))]
        decls += [fd for s in m.program.stmts if isinstance(s, A.ClassDecl) for fd in s.methods]
        for d in decls:
            if isinstance(d, A.FuncDecl) and isinstance(d.body, A.NativeBody):
                out.append((str(file), d.span.line, f"@extern {d.name}: {', '.join(d.body.bodies)}"))
            kind = "class" if isinstance(d, A.ClassDecl) else "function"
            if targets(d.annots):
                out.append((str(file), d.span.line, f"@native on {kind} {d.name}: {', '.join(targets(d.annots))}"))
            if isinstance(d, A.ClassDecl):
                for f in d.fields:
                    if targets(f.annots):
                        out.append((str(file), f.span.line,
                                    f"@native on a field of {d.name}: {', '.join(targets(f.annots))}"))
    return [f"{file}:{line}  {what}" for file, line, what in sorted(out)]


def cmd_check(args) -> int:
    res, path = _compile(args, check_only=True)
    if getattr(args, "native", False):
        found = native_uses(res)
        for line in found:
            print(line)
        print(f"fx3c: {len(found)} target-specific piece(s) of code (@native / @extern)", file=sys.stderr)
    w = len(res.diags.warnings)
    print(f"fx3c: {path} is OK" + (f" ({w} warning{'s' if w != 1 else ''})" if w else ""), file=sys.stderr)
    return 0


def cmd_build(args) -> int:
    res, path = _compile(args)
    out_dir = Path(args.out) if args.out else path.parent / "build" / args.target
    files = write_output(res.output, out_dir)
    for f in files:
        print(f"fx3c: wrote {f}", file=sys.stderr)
    return 0


def _run(args, extra: list[str]) -> int:
    res, path = _compile(args)
    load_secrets(path.parent)          # e.g. ANTHROPIC_API_KEY for programs that use Ai
    with tempfile.TemporaryDirectory(prefix="fx3c-") as tmp:
        out_dir = Path(args.out) if args.out else Path(tmp)
        write_output(res.output, out_dir)
        cmd = [sys.executable, res.output.entry_file, *extra]
        return subprocess.call(cmd, cwd=out_dir)


def cmd_run(args) -> int:
    return _run(args, args.args)


def cmd_test(args) -> int:
    return _run(args, ["--fx3-test"])


def cmd_show(args) -> int:
    res, _ = _compile(args)
    print(res.output.files[res.output.entry_file], end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="fx3c", description="Compile FluidXPro3 .fx3 files to a target language.")
    p.add_argument("--version", action="version", version=f"fx3c {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("file", help="the .fx3 file")
        sp.add_argument("--target", default="python", type=canonical_target, metavar="LANGUAGE",
                        help=f"target language: {', '.join(TARGETS)} (available now: {', '.join(sorted(BACKENDS))})")
        sp.add_argument("--no-overflow-checks", action="store_true",
                        help="don't check 64-bit integer overflow (faster, less safe)")
        sp.add_argument("-q", "--quiet", action="store_true", help="hide warnings")
        sp.add_argument("--root", help="the project folder that module names start from (default: the file's folder)")

    sp = sub.add_parser("check", help="check a file without generating code")
    common(sp)
    sp.add_argument("--native", action="store_true",
                    help="list every @native / @extern (target-specific code, reviewed like @unsafe)")
    sp.set_defaults(fn=cmd_check)

    sp = sub.add_parser("build", help="generate target code")
    common(sp)
    sp.add_argument("-o", "--out", help="output directory (default: build/<target> next to the file)")
    sp.set_defaults(fn=cmd_build)

    sp = sub.add_parser("run", help="build and run")
    common(sp)
    sp.add_argument("-o", "--out", help="keep the generated files in this directory")
    sp.add_argument("args", nargs=argparse.REMAINDER, help="arguments for the program (after --)")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("test", help="run @test functions and @example cases")
    common(sp)
    sp.add_argument("-o", "--out", help="keep the generated files in this directory")
    sp.set_defaults(fn=cmd_test)

    sp = sub.add_parser("show", help="print the generated code")
    common(sp)
    sp.set_defaults(fn=cmd_show)

    args = p.parse_args(argv)
    if getattr(args, "args", None) and args.args[:1] == ["--"]:
        args.args = args.args[1:]
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
