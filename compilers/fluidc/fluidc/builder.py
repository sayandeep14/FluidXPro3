"""fluidc build: .fluid -> .fx3, verified by fx3c.

    read blocks -> settings per block -> decide (reuse from lock / keep hand edit / generate)
    -> generate with the model -> check with fx3c (+ run examples/tests) -> feed errors back
    -> questions (ask policy) -> guarantees, custom keys, unsupported blocks -> write .fx3 + fluid.lock
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol
from collections.abc import Callable

from . import guard, prompts
from .assemble import assemble, code_hash, existing_imports, parse_existing, split_imports
from .errors import FluidError, Message, Report
from .llm import ClaudeGenerator, Generator, Usage
from .lock import BlockEntry, Lock, block_key, stale_blocks, toolchain_now, value_hash
from .reader import Document, guarantee_lines, read
from .settings import CliSettings, _flatten, effective, load_config, suspicious_keys
from .verify import Problem, check, sandbox

CONTEXT_ONLY = {"app", "target", "ask", "with", "words", "uses"}
ASK_IMPACTS_MID = {"data", "money", "security", "external", "irreversible"}


class Asker(Protocol):
    def __call__(self, question: str, options: list[str], default: str) -> str: ...


@dataclass
class BuildOptions:
    fluid_path: Path
    config_path: Optional[Path] = None
    out_dir: Optional[Path] = None
    cli: CliSettings = field(default_factory=CliSettings)
    asker: Optional[Asker] = None          # None = not interactive
    accept_defaults: bool = False           # --yes
    allow_unsupported: bool = False
    refresh: bool = False                   # regenerate blocks made with another model / prompt version
    check_only: bool = False                # verify everything, but write neither the .fx3 nor fluid.lock
    generator_factory: Optional[Callable[[dict], Generator]] = None


@dataclass
class BuildResult:
    ok: bool
    report: Report
    sources: dict[str, str]
    fx3_path: Optional[Path] = None
    lock_path: Optional[Path] = None
    generated: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    kept_edits: list[str] = field(default_factory=list)
    usage: Optional[Usage] = None
    fx3_report: str = ""
    verification: dict = field(default_factory=dict)   # how the code was verified (also in fluid.lock)


@dataclass
class VerifyEnv:
    """How generated tests are run: sandbox level, folders to hide, timeout."""
    sandbox: str = "auto"
    hide: list[str] = field(default_factory=list)
    timeout: float = 120
    capabilities: str = "error"            # verification.capabilities: off | warn | error
    secrets: dict[str, str] = field(default_factory=dict)
    known_text: str = ""                   # .fluid + fluid.config: where addresses must come from


def lock_path_for(fluid_path: Path) -> Path:
    return fluid_path.parent / "fluid.lock"


def _lk(doc: Document, bid: str) -> str:
    return f"{Path(doc.file).name}:{bid}"


def _default_generator(settings: dict) -> Generator:
    llm = settings.get("llm", {})
    return ClaudeGenerator(model=llm.get("model", "claude-opus-5-5"), effort=llm.get("effort", "high"))


def _diff(a: dict, b: dict) -> dict:
    fa, fb = _flatten(a), _flatten(b)
    return {k: v for k, v in fa.items() if fb.get(k) != v}


def setup(opts: BuildOptions, report: Report):
    src = opts.fluid_path.read_text(encoding="utf-8")
    doc = read(src, opts.fluid_path.name)
    cfg_path = opts.config_path
    if cfg_path is None:
        guess = opts.fluid_path.parent / "fluid.config"
        cfg_path = guess if guess.exists() else None
    cfg = load_config(cfg_path, required=opts.config_path is not None)
    return src, doc, cfg


def build(opts: BuildOptions) -> BuildResult:
    report = Report()
    sources: dict[str, str] = {}
    try:
        return _build(opts, report, sources)
    except FluidError as e:
        report.messages += e.messages
        return BuildResult(False, report, sources)
    except sandbox.SandboxUnavailable as e:
        report.error("FL0510", f"can't run the generated tests: {e}",
                     fix='install it, or set verification.sandbox to "process" in fluid.config')
        return BuildResult(False, report, sources)
    except Exception as e:  # noqa: BLE001 - a bug in fluidc itself
        report.error("FL9999", f"internal error in fluidc: {type(e).__name__}: {e}",
                     fix="this is a bug in fluidc; please report it with the .fluid file that caused it")
        return BuildResult(False, report, sources)


def _ask(opts: BuildOptions, question: str, options: list[str], default: str, always: bool = False) -> str | None:
    """Ask the developer. Returns None when we can't ask (non-interactive)."""
    if opts.accept_defaults and not always:
        return default
    if opts.asker is None:
        return None
    return opts.asker(question, options, default)


def _build(opts: BuildOptions, report: Report, sources: dict[str, str]) -> BuildResult:
    src, doc, cfg = setup(opts, report)
    sources[doc.file] = src
    fluid_name = doc.file

    # ---- config sanity ----------------------------------------------------
    for written, known in suspicious_keys(cfg):
        ans = _ask(opts, f"fluid.config has `{written}`. Is it a typo of `{known}`?",
                   ["yes, stop so I can fix it", "no, it is a custom key"], "yes, stop so I can fix it", always=True)
        if ans is None or ans.startswith("yes"):
            raise FluidError(Message("error", "FL0207", f"`{written}` in fluid.config looks like a typo of `{known}`",
                                     cfg.file, fix=f"rename it to `{known}` (or run interactively and confirm "
                                                   "it is a custom key)"))
    project = effective(cfg, doc, None, opts.cli)
    target = project.get("target.language")
    cfg_target = (cfg.data.get("target") or {}).get("language")
    if cfg_target and doc.file_target and cfg_target != doc.file_target:
        raise FluidError(Message("error", "FL0209", f"`target {doc.file_target}` in {fluid_name} doesn't match "
                                 f"target.language = \"{cfg_target}\" in fluid.config", fluid_name))
    if not target:
        raise FluidError(Message("error", "FL0208", "no target language: add `target python` (or cpp) to the "
                                 ".fluid file, or target.language to fluid.config", fluid_name))
    if target != "python":
        report.warning("FL0210", f"fx3c can only generate Python so far; `{target}` code can't be built yet "
                       "(the .fx3 is still generated and checked)")
    check_target = "python"

    block_settings = {b.id: effective(cfg, doc, b, opts.cli) for b in doc.blocks}
    base_dir = Path(cfg.file).parent if cfg.present else opts.fluid_path.parent
    out_dir = opts.out_dir or (base_dir / project.get("project.fx3Out", "build/fx3"))
    fx3_path = out_dir / (opts.fluid_path.stem + ".fx3")
    lock_file = lock_path_for(opts.fluid_path)
    lock = Lock.load(lock_file)
    if lock.migrated_from is not None:
        report.note("FL0404", f"fluid.lock uses format {lock.migrated_from}; it is upgraded when this build "
                    "succeeds (`fluidc lock migrate` does only that)")
    model_now = project.get("llm.model", "claude-opus-5-5")
    prompt_now = prompts.prompt_version()
    stale = stale_blocks(lock, [_lk(doc, b.id) for b in doc.blocks], model_now, prompt_now)
    old_text = fx3_path.read_text(encoding="utf-8") if fx3_path.exists() else ""
    existing = parse_existing(old_text)
    kept_imports = existing_imports(old_text)      # imports a hand edit may rely on
    config_text = Path(cfg.file).read_text(encoding="utf-8") if cfg.present else ""
    venv = VerifyEnv(project.get("verification.sandbox", "auto"),
                     sorted({str(base_dir.resolve()), str(opts.fluid_path.resolve().parent)}),
                     float(project.get("verification.testTimeout", 120)),
                     project.get("verification.capabilities", "error"),
                     guard.secret_values(opts.fluid_path.resolve().parent),
                     src + "\n" + config_text)

    # ---- decide, block by block --------------------------------------------
    codes: dict[str, str] = {}          # what goes into the .fx3
    recorded: dict[str, str] = {}       # the fx3= hash on each marker
    regen: list[str] = []
    fixed: set[str] = set()             # blocks the model must not change (edits, exact)
    entries: dict[str, BlockEntry] = {}
    result = BuildResult(False, report, sources, fx3_path, lock_file)

    for b in doc.blocks:
        if b.keyword in CONTEXT_ONLY:
            continue
        lk = _lk(doc, b.id)
        old = lock.blocks.get(lk)
        answers = dict(old.answers) if old is not None and old.fluid_hash == b.fluid_hash else {}
        key = block_key(b.fluid_hash, block_settings[b.id].fingerprint(), answers)
        region = existing.get(b.id)
        edited = region is not None and region.edited
        if edited and b.exact is not None and code_hash(region.code) == code_hash(b.exact):
            edited = False          # the edit was pinned: the .fluid file now holds the same code
        unchanged = old is not None and old.key == key and not (opts.refresh and lk in stale)
        if edited and not unchanged:
            choice = _ask(opts, f'Block "{b.id}" changed in {fluid_name}, and its fx3 was also edited by hand. '
                                "Which should win?",
                          ["keep my hand edit", "regenerate from the .fluid text"], "keep my hand edit", always=True)
            if choice is None:
                raise FluidError(Message("error", "FL0501", f'block "{b.id}" changed in {fluid_name}, and its fx3 '
                                         "was also edited by hand", fluid_name, b.line_no, b.id,
                                         fix="run interactively to choose, `fluidc pin` the edit, or `fluidc unpin` it"))
            if choice.startswith("keep"):
                unchanged, edited = True, True
            else:
                edited = False
        if edited:
            codes[b.id] = region.code
            recorded[b.id] = region.recorded_hash
            fixed.add(b.id)
            result.kept_edits.append(b.id)
            if old is not None:
                entries[lk] = old
            continue
        if b.exact is not None:
            codes[b.id] = b.exact
            recorded[b.id] = code_hash(b.exact)
            fixed.add(b.id)
            entries[lk] = BlockEntry(key, b.fluid_hash, b.exact, code_hash(b.exact), "exact")
            continue
        if unchanged:
            codes[b.id] = old.fx3
            recorded[b.id] = old.fx3_hash
            entries[lk] = old
            result.reused.append(b.id)
            continue
        regen.append(b.id)
        entries[lk] = BlockEntry(key, b.fluid_hash, "", "", "llm", answers=answers)
        codes[b.id] = ""

    kept_stale = [b.id for b in doc.blocks if b.id in result.reused and _lk(doc, b.id) in stale]
    if kept_stale:
        why = stale[_lk(doc, kept_stale[0])]
        report.warning("FL0403", f"{len(kept_stale)} block(s) were generated with a different toolchain ({why}) "
                       "and are reused unchanged, so the build stays reproducible: "
                       + ", ".join(f'"{x}"' for x in kept_stale),
                       fix="run `fluidc build --refresh` to regenerate them with the current model and prompt")

    # ---- generate ---------------------------------------------------------
    gen: Optional[Generator] = None
    replies: list[dict] = []
    if regen:
        if opts.cli.offline or project.get("llm.offline"):
            raise FluidError(Message("error", "FL0502", "offline build, but these blocks are new or changed: "
                                     + ", ".join(f'"{r}"' for r in regen),
                                     fix="build online once, then commit fluid.lock"))
        # nothing that looks like a secret may be sent to the model
        leaks = guard.scan_sources({fluid_name: src, **({cfg.file: config_text} if config_text else {})},
                                   venv.secrets)
        if leaks:
            for file, line, what in leaks:
                report.error("FL1110", f"{file}:{line} contains {what}; it would be sent to the model",
                             file=file, line=line,
                             fix="move it to secrets.env and name it in a `secrets` block (Env.require reads it)")
            return _fail(result)
        factory = opts.generator_factory or _default_generator
        gen = factory(project.values)
        result.usage = gen.usage
        tags = {b.id: b.tags for b in doc.blocks if b.tags}
        request = prompts.build_request(
            doc=doc, target=target, project_settings=project.values,
            block_settings={bid: _diff(block_settings[bid].values, project.values) for bid in regen},
            generate=regen, existing={bid: c for bid, c in codes.items() if bid not in regen},
            answers={bid: entries[_lk(doc, bid)].answers for bid in regen if entries[_lk(doc, bid)].answers},
            custom=project.custom, instructions=project.get("instructions", []) or [], tags=tags)
        replies.append(gen.start(prompts.SYSTEM_PROMPT, request))
        _merge(replies[-1], codes, regen, fixed, report)
        regen_set = set(regen) | _extra_changed(replies[-1], codes, fixed, doc)
        verdict = _verify_loop(gen, replies, doc, codes, recorded, regen_set, fixed, fluid_name, check_target,
                               project, report, kept_imports=kept_imports, venv=venv)
        if verdict is None:
            return _fail(result)
        # ---- questions ------------------------------------------------------
        changed_answers: dict[str, dict[str, str]] = {}
        for q in _collect(replies, "questions"):
            bid = q.get("block", "")
            if bid not in block_settings:
                continue
            level = block_settings[bid].get("ask", "mid")
            entry = entries.get(_lk(doc, bid))
            if entry is None:
                continue
            entry.questions.append(q)
            if level == "soft" or (level == "mid" and q.get("impact") not in ASK_IMPACTS_MID):
                entry.assumptions.append(f"{q['question']} -> {q['default']} (assumed)")
                continue
            ans = _ask(opts, f'[{bid}] {q["question"]}', q.get("options") or [q["default"]], q["default"])
            if ans is None:
                b = doc.block(bid)
                report.error("FL0601", f'needs an answer: {q["question"]} (options: {", ".join(q.get("options", []))}; '
                             f'default: {q["default"]})', file=fluid_name, line=b.line_no if b else 0, block=bid,
                             fix="run interactively, use --yes to accept the defaults, or --ask=soft")
                continue
            entry.answers[q["question"]] = ans
            if ans != q["default"]:
                changed_answers.setdefault(bid, {})[q["question"]] = ans
        if report.has_errors():
            return _fail(result)
        if changed_answers:
            replies.append(gen.follow_up(prompts.answers_request(changed_answers)))
            _merge(replies[-1], codes, list(regen_set), fixed, report)
            regen_set |= _extra_changed(replies[-1], codes, fixed, doc)
            verdict = _verify_loop(gen, replies, doc, codes, recorded, regen_set, fixed, fluid_name,
                                   check_target, project, report, already_checked=False, kept_imports=kept_imports,
                                   venv=venv)
            if verdict is None:
                return _fail(result)
        for bid in regen_set:
            e = entries.get(_lk(doc, bid))
            if e is None:
                b = doc.block(bid)
                e = entries[_lk(doc, bid)] = BlockEntry("", b.fluid_hash, "", "", "llm")
            e.fx3 = codes.get(bid, "")
            e.fx3_hash = code_hash(split_imports(e.fx3)[1])
            e.model, e.prompt = model_now, prompt_now
            # the key includes the final answers, so the next build reuses this block
            b = doc.block(bid)
            e.key = block_key(b.fluid_hash, block_settings[bid].fingerprint(), e.answers)
            recorded[bid] = e.fx3_hash
        for a in _collect(replies, "assumptions"):
            e = entries.get(_lk(doc, a.get("block", "")))
            if e is not None and a["text"] not in e.assumptions:
                e.assumptions.append(a["text"])
        result.generated = sorted(regen_set, key=lambda x: [b.id for b in doc.blocks].index(x))
    else:
        verdict = _verify_once(doc, codes, recorded, fluid_name, check_target, kept_imports, venv,
                               run_tests=project.get("verification.examples", "error") != "off")
        if not verdict.ok:
            for prob in verdict.problems or [Problem(None, "fx3c rejects it")]:
                report.error("FL0503", f"the .fx3 assembled from fluid.lock and hand edits doesn't pass: {prob.text}",
                             block=prob.block or "")
            result.fx3_report = verdict.report
            return _fail(result)
    result.fx3_report = verdict.report
    result.verification = _verification(verdict, project)
    if verdict.tests_ran and verdict.sandbox == "off":
        report.warning("FL0511", "the generated tests ran without a sandbox (--trust or verification.sandbox = off)")

    # ---- unsupported blocks ----------------------------------------------------
    for u in _collect(replies, "unsupported"):
        e = entries.get(_lk(doc, u.get("block", "")))
        if e is not None:
            e.origin, e.unsupported = "unsupported", u["reason"]
    for b in doc.blocks:
        e = entries.get(_lk(doc, b.id))
        if e is not None and e.origin == "unsupported":
            msg = f'block "{b.id}" needs features fx3c doesn\'t have yet: {e.unsupported}'
            if opts.allow_unsupported:
                report.warning("FL0701", msg + " (left out of the program)", file=fluid_name, line=b.line_no, block=b.id)
            else:
                report.error("FL0701", msg, file=fluid_name, line=b.line_no, block=b.id,
                             fix="remove the block, or build with --allow-unsupported to leave it out for now")

    # ---- guarantees (rules / never) ---------------------------------------------
    reported = _collect(replies, "guarantees")
    for b in doc.blocks:
        if b.keyword not in ("rules", "never"):
            continue
        e = entries.get(_lk(doc, b.id))
        if e is not None and reported:
            mine = [g for g in reported if g.get("block") == b.id]
            if mine:
                e.guarantees = mine
        known = e.guarantees if e is not None else []
        level_default = block_settings[b.id].get(f"verification.{b.keyword}", "warn")
        for line in guarantee_lines(b):
            g = next((x for x in known if _same_line(x.get("line", ""), line)), None)
            status = g["status"] if g else "unverifiable"
            if status == "enforced":
                continue
            level = b.tags.get(line, level_default)
            text = f'can\'t verify this {b.keyword} line: "{line}"' + (f" ({g['how']})" if g and g.get("how") else "")
            ln = _line_of(doc, b, line)
            if level == "error":
                report.error("FL0801", text, file=fluid_name, line=ln, block=b.id,
                             fix="change the tag to [warn], or rephrase it so it can be checked")
            elif level == "warn":
                report.warning("FL0801", text, file=fluid_name, line=ln, block=b.id)

    # ---- custom keys & instructions ------------------------------------------------
    level_ck = project.get("verification.customKeys", "warn")
    wanted = dict(project.custom)
    for k, ins in enumerate(project.get("instructions", []) or []):
        wanted[f"instructions[{k}]"] = ins
    for ck in _collect(replies, "custom_keys"):
        key = ck.get("key", "")
        match = next((w for w in wanted if w == key or w.endswith("." + key) or key.endswith(w)), None)
        if match is not None:
            lock.custom_keys[match] = {"value_hash": value_hash(wanted[match]), **ck}
    for key, value in wanted.items():
        info = lock.custom_keys.get(key)
        if info is None or info.get("value_hash") != value_hash(value):
            if regen:
                report.warning("FL0902", f"the model didn't say how it applied `{key}`", block="")
            continue
        if info["status"] == "conflict":
            report.error("FL0901", f"`{key}` conflicts with another setting: {info['interpretation']}",
                         file=cfg.file, fix="change one of the two settings")
        elif info["status"] == "unclear":
            report.warning("FL0903", f"`{key}` is unclear: {info['interpretation']}", file=cfg.file)
        elif (info.get("check") or "none").strip().lower() in ("none", "") and level_ck != "off":
            m = f"`{key}` is applied but can't be checked automatically ({info['interpretation']})"
            if level_ck == "error":
                report.error("FL0904", m, file=cfg.file)
            else:
                report.note("FL0904", m, file=cfg.file)
    for key in list(lock.custom_keys):
        if key not in wanted:
            del lock.custom_keys[key]

    if report.has_errors():
        return _fail(result)

    if opts.check_only:
        result.ok = True
        return result

    # ---- write ---------------------------------------------------------------
    asm = assemble(doc, codes, {bid: recorded[bid] for bid in result.kept_edits}, fluid_name, kept_imports)
    out_dir.mkdir(parents=True, exist_ok=True)
    fx3_path.write_text(asm.text, encoding="utf-8")
    prefix = f"{fluid_name}:"
    lock.blocks = {k: v for k, v in lock.blocks.items() if not k.startswith(prefix)}
    lock.blocks.update(entries)
    if regen:
        lock.toolchain = toolchain_now(model_now, prompt_now)
    else:
        lock.toolchain = {**lock.toolchain, **{k: v for k, v in toolchain_now("", "").items() if v}}
    lock.verification[fluid_name] = result.verification
    lock.save(lock_file)
    result.ok = True
    return result


def _fail(result: BuildResult) -> BuildResult:
    result.ok = False
    return result


def _collect(replies: list[dict], key: str) -> list[dict]:
    out: list[dict] = []
    for r in replies:
        out += r.get(key, []) or []
    return out


def _same_line(a: str, b: str) -> bool:
    na, nb = " ".join(a.lower().split()).strip(" ."), " ".join(b.lower().split()).strip(" .")
    return na == nb or na.startswith(nb) or nb.startswith(na)


def _line_of(doc: Document, b, text: str) -> int:
    lines = doc.source.splitlines()
    for k in range(b.line_no - 1, b.end_line):
        if text in lines[k]:
            return k + 1
    return b.line_no


def _merge(reply: dict, codes: dict[str, str], allowed: list[str], fixed: set[str], report: Report) -> None:
    for blk in reply.get("blocks", []) or []:
        bid = blk.get("id", "")
        if bid in fixed:
            continue        # hand edits and exact code are never replaced
        if bid in codes:
            codes[bid] = blk.get("fx3", "")


def _extra_changed(reply: dict, codes: dict[str, str], fixed: set[str], doc: Document) -> set[str]:
    """Blocks outside the request that the model changed anyway (allowed for model-made blocks)."""
    return {blk.get("id", "") for blk in reply.get("blocks", []) or []
            if blk.get("id", "") in codes and blk.get("id", "") not in fixed}


def _verification(verdict, project) -> dict:
    """What `fluid run` needs to know: did model-written code pass its tests in a sandbox?"""
    examples = project.get("verification.examples", "error")
    info = {"tests": "passed" if verdict.tests_ran and not verdict.tests_failed else
            "failed" if verdict.tests_failed else "skipped" if examples == "off" else "none",
            "sandbox": verdict.sandbox or "-"}
    trusted_only = info["tests"] in ("failed", "skipped") or info["sandbox"] == "off"
    info["status"] = "needs-trust" if trusted_only else "verified"
    return info


def _verify_once(doc, codes, recorded, fluid_name, target, kept_imports=None, venv=None, run_tests=True):
    venv = venv or VerifyEnv()
    asm = assemble(doc, codes, {}, fluid_name, kept_imports)
    return check(asm.text, Path(fluid_name).stem + ".fx3", target, asm.ranges, run_tests=run_tests,
                 sandbox_level=venv.sandbox, hide=venv.hide, timeout=venv.timeout)


def _verify_loop(gen, replies, doc, codes, recorded, regen_set, fixed, fluid_name, target, project, report,
                 already_checked: bool = False, kept_imports=None, venv=None):
    venv = venv or VerifyEnv()
    retries = int(project.get("llm.maxRetries", 3))
    examples_level = project.get("verification.examples", "error")
    for attempt in range(retries + 1):
        missing = [bid for bid in regen_set if bid in codes and not codes[bid].strip()
                   and not any(u.get("block") == bid for u in _collect(replies, "unsupported"))
                   and not any(blk.get("id") == bid for r in replies for blk in r.get("blocks", []) or [])]
        asm = assemble(doc, codes, {}, fluid_name, kept_imports)
        v = check(asm.text, Path(fluid_name).stem + ".fx3", target, asm.ranges,
                  run_tests=examples_level != "off", sandbox_level=venv.sandbox, hide=venv.hide,
                  timeout=venv.timeout)
        problems = list(v.problems)
        if v.tests_failed and examples_level == "warn" and not [p for p in problems if not p.text.startswith("test:")]:
            for p in problems:
                report.warning("FL0505", p.text)
            v.ok = True
            problems = []
        if missing:
            problems += [Problem(bid, f'you did not return block "{bid}"') for bid in missing]
        for f in guard.check_code(doc, codes, set(regen_set) - fixed, venv.secrets, venv.known_text):
            if f.kind == "secret" or venv.capabilities == "error":
                problems.append(Problem(f.block, f.text))
                v.ok = False
            elif venv.capabilities == "warn" and attempt == 0:
                report.warning("FL1101", f.text, block=f.block or "")
        if v.ok and not missing:
            return v
        unfixable = [p for p in problems if p.block is not None and p.block in fixed]
        if unfixable and len(unfixable) == len([p for p in problems if p.block is not None]) and \
                not [p for p in problems if p.block is None]:
            for p in unfixable:
                report.error("FL0504", f"fx3c rejects code that fluidc may not change (a hand edit or exact code): "
                             f"{p.text}", block=p.block or "")
            return None
        if attempt == retries:
            for p in problems:
                report.error("FL0503", f"after {retries} fix attempt(s), fx3c still rejects the code: {p.text}",
                             block=p.block or "")
            return None
        replies.append(gen.follow_up(prompts.fix_request([p.text for p in problems],
                                                         v.test_output if v.tests_failed else None)))
        _merge(replies[-1], codes, list(regen_set), fixed, report)
        regen_set |= _extra_changed(replies[-1], codes, fixed, doc)
    return None
