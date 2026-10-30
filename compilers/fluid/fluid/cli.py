"""fluid: one command for the whole toolchain (ROADMAP E-06).

    fluid new     <name>               a project folder that builds out of the box
    fluid build   <file> [options]     .fluid -> .fx3 -> Python,  or  .fx3 -> Python
    fluid run     <file> [-- args]     build, then run (model-written code must have passed its tests in the sandbox)
    fluid test    <file>               run the examples and @test functions, in the sandbox
    fluid check   <file>               verify without writing; for .fluid, also: is fluid.lock current? (CI)
    fluid review  <file.fluid>         what the model assumed, answered and decided
    fluid doctor  [folder]             Python, packages, credentials (never shown), sandbox, locks

Options that `fluid build` / `run` don't know are passed on to fluidc (e.g. --ask, --yes, --offline).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__


def _versions() -> str:
    import fluidc
    import fx3c
    return f"fluid {__version__} (fluidc {fluidc.__version__}, fx3c {fx3c.__version__}, " \
           f"language spec {fx3c.SPEC_VERSION})"


def _kind(path: Path) -> str:
    if not path.exists():
        print(f"fluid: no such file: {path}", file=sys.stderr)
        raise SystemExit(2)
    if path.suffix in (".fluid", ".fx3"):
        return path.suffix[1:]
    print(f"fluid: {path.name} is neither a .fluid nor a .fx3 file", file=sys.stderr)
    raise SystemExit(2)


def _paths(fluid_path: Path, config: str | None, out: str | None) -> tuple[Path, Path, dict]:
    """The .fx3 fluidc writes, the target folder, and the project settings."""
    from fluidc.builder import BuildOptions, setup
    from fluidc.errors import Report
    from fluidc.settings import CliSettings, effective
    opts = BuildOptions(fluid_path, Path(config) if config else None)
    _, doc, cfg = setup(opts, Report())
    project = effective(cfg, doc, None, CliSettings())
    base = Path(cfg.file).parent if cfg.present else fluid_path.parent
    fx3_dir = Path(out) if out else base / project.get("project.fx3Out", "build/fx3")
    target = project.get("target.language", "python")
    target_out = base / project.get("project.targetOut", "build/out")
    if target_out.name == "out":
        target_out = target_out.parent / target   # build/out -> build/python
    return fx3_dir / (fluid_path.stem + ".fx3"), target_out, project.values


def _fluidc_args(args, rest: list[str]) -> list[str]:
    extra = list(rest)
    if args.config:
        extra += ["--config", args.config]
    if args.out:
        extra += ["-o", args.out]
    if getattr(args, "trust", False):
        extra.append("--trust")
    return extra


def cmd_build(args, rest) -> int:
    from fx3c.cli import main as fx3c_main
    from fluidc.cli import main as fluidc_main
    path = Path(args.file)
    if _kind(path) == "fx3":
        return fx3c_main(["build", str(path), *rest, *(["-o", args.out] if args.out else [])])
    rc = fluidc_main(["build", str(path), *_fluidc_args(args, rest)])
    if rc != 0:
        return rc
    fx3_file, target_out, _ = _paths(path, args.config, args.out)
    return fx3c_main(["build", str(fx3_file), "-o", str(target_out), "-q"])


def cmd_run(args, rest, program_args: list[str]) -> int:
    from fx3c.cli import main as fx3c_main
    from fluidc.cli import main as fluidc_main
    from fluidc.cli import run_allowed
    path = Path(args.file)
    if _kind(path) == "fx3":
        return fx3c_main(["run", str(path), *rest, "--", *program_args])
    rc = fluidc_main(["build", str(path), *_fluidc_args(args, rest)])
    if rc != 0:
        return rc
    why = run_allowed(path, args.trust)
    if why:
        print(f"fluid: not running {path}: {why}. Read the code (`fluid review {path}`), then run it with --trust",
              file=sys.stderr)
        return 1
    fx3_file, _, _ = _paths(path, args.config, args.out)
    return fx3c_main(["run", str(fx3_file), "-q", "--", *program_args])


def cmd_test(args, rest) -> int:
    from fx3c import sandbox
    from fx3c.driver import compile_source
    from fluidc.cli import main as fluidc_main
    path = Path(args.file)
    level = "off" if args.trust else "auto"
    if _kind(path) == "fluid":
        rc = fluidc_main(["build", str(path), "-q", *_fluidc_args(args, rest)])
        if rc != 0:
            return rc
        fx3_file, _, project = _paths(path, args.config, args.out)
        level = "off" if args.trust else project.get("verification", {}).get("sandbox", "auto")
        hide = [str(path.resolve().parent)]
    else:
        fx3_file, hide = path, [str(path.resolve().parent)]
    res = compile_source(fx3_file.read_text(encoding="utf-8"), str(fx3_file))
    if not res.ok or res.output is None:
        print(res.report(), file=sys.stderr)
        return 1
    try:
        ran = sandbox.run(res.output.files, res.output.entry_file, ["--fx3-test"], level=level, hide=hide)
    except sandbox.SandboxUnavailable as e:
        print(f"fluid: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(ran.stdout)
    sys.stdout.flush()
    sys.stderr.write(ran.stderr)
    where = {"os": "in the OS sandbox", "process": "in the process sandbox", "off": "WITHOUT a sandbox (--trust)"}
    print(f"fluid: tests ran {where[ran.level]}", file=sys.stderr)
    return ran.returncode


def cmd_check(args, rest) -> int:
    from fx3c.cli import main as fx3c_main
    from fluidc.builder import BuildOptions, build
    from fluidc.cli import _print_messages
    from fluidc.settings import CliSettings
    path = Path(args.file)
    if _kind(path) == "fx3":
        return fx3c_main(["check", str(path), *rest])
    opts = BuildOptions(path, Path(args.config) if args.config else None, Path(args.out) if args.out else None,
                        CliSettings(offline=True, trust=args.trust), check_only=True)
    res = build(opts)
    _print_messages(res.report.messages, res.sources)
    if not res.ok:
        if any(m.code == "FL0502" for m in res.report.messages):
            print("fluid: fluid.lock is out of date: build online, then commit fluid.lock", file=sys.stderr)
        return 1
    print(f"fluid: {path} is OK (fluid.lock is current; {len(res.reused)} block(s), "
          f"tests {res.verification.get('tests', '-')}, sandbox {res.verification.get('sandbox', '-')})",
          file=sys.stderr)
    return 0


def cmd_review(args, rest) -> int:
    from fluidc.cli import main as fluidc_main
    return fluidc_main(["review", args.file, *rest])


def cmd_new(args, rest) -> int:
    from .scaffold import create
    folder = Path(args.name)
    try:
        files = create(folder)
    except FileExistsError as e:
        print(f"fluid: {e}", file=sys.stderr)
        return 1
    for f in files:
        print(f"fluid: wrote {f}", file=sys.stderr)
    print(f"\nNext:\n  cd {folder}\n  # put your key in secrets.env (ANTHROPIC_API_KEY=...)\n"
          "  fluid run main.fluid", file=sys.stderr)
    return 0


def cmd_doctor(args, rest) -> int:
    from .doctor import run
    return run(Path(args.folder))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    program_args: list[str] = []
    if "--" in argv:
        k = argv.index("--")
        argv, program_args = argv[:k], argv[k + 1:]

    p = argparse.ArgumentParser(prog="fluid", description="FluidXPro3: .fluid / .fx3 -> Python, in one command.",
                                epilog="Unknown options are passed on to fluidc / fx3c.")
    p.add_argument("--version", action="version", version=_versions())
    sub = p.add_subparsers(dest="command", required=True)

    def file_cmd(name: str, fn, help_: str):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("file", help="a .fluid or .fx3 file")
        sp.add_argument("--config", help="fluid.config to use")
        sp.add_argument("-o", "--out", help="output folder: the .fx3 for a .fluid file, the Python for a .fx3 file")
        sp.add_argument("--trust", action="store_true", help="run without the sandbox (you've read the code)")
        sp.set_defaults(fn=fn)

    file_cmd("build", cmd_build, "build a .fluid or .fx3 file")
    file_cmd("run", cmd_run, "build and run (program arguments after --)")
    file_cmd("test", cmd_test, "run the examples and tests in the sandbox")
    file_cmd("check", cmd_check, "verify without writing anything (CI)")
    file_cmd("review", cmd_review, "show what the model assumed and decided")
    sp = sub.add_parser("new", help="create a project folder")
    sp.add_argument("name", help="the folder to create")
    sp.set_defaults(fn=cmd_new)
    sp = sub.add_parser("doctor", help="check this machine and project")
    sp.add_argument("folder", nargs="?", default=".", help="project folder (default: here)")
    sp.set_defaults(fn=cmd_doctor)

    args, rest = p.parse_known_args(argv)
    if args.command in ("new", "doctor") and rest:
        p.error(f"unknown options: {' '.join(rest)}")
    if args.command not in ("new", "doctor"):     # doctor reports where the key comes from by itself
        from fx3c.secrets import load_secrets
        load_secrets(Path(args.file).resolve().parent)
    if args.command == "run":
        return cmd_run(args, rest, program_args)
    return args.fn(args, rest)


if __name__ == "__main__":
    raise SystemExit(main())
