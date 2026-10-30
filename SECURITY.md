# Security policy

FluidXPro3 turns plain-English `.fluid` files into code with an LLM, verifies that code by running it, and then runs it for real. Security problems in any of those steps matter to us.

## Supported versions

Until 1.0, only the latest release gets security fixes.

| Version | Supported |
|---|---|
| latest 0.x | yes |
| older 0.x | no |

## Reporting a vulnerability

**Please don't open a public issue for a security problem.**

- Use GitHub's private vulnerability reporting ("Report a vulnerability" on the Security tab), or
- email **sayandeepgiri14@gmail.com** with "FluidXPro3 security" in the subject.

Include:
- what an attacker can do
- the smallest `.fluid` / `.fx3` file and command that shows it
- the versions (`fluid --version`) and your OS

We aim to reply within 3 working days and to ship a fix, or a clear plan, within 30 days. We credit reporters in the CHANGELOG unless you'd rather we didn't.

## What counts

These are in scope:
- **Sandbox escapes.** Code run during verification (`fluidc build`, `fluid test`) reaches the network, reads files outside its temporary folder, or sees environment secrets. See `compilers/fluidc/fluidc/sandbox.py`.
- **Prompt injection.** Text in a `.fluid` file, or in a config custom key, makes the generated code do something the file didn't ask for, and fluidc's deterministic checks (`compilers/fluidc/fluidc/guard.py`) don't catch it.
- **Secret leaks.** A value from `secrets.env` ends up in generated code, `fluid.lock`, logs or a model request.
- **Standard library.** The standard library (`fx3std`) is unsafe by default, e.g. SQL injection through an API that looks safe.
- **Bad code that passes.** The compilers accept code that breaks a locked language rule (overflow, bounds, `maybe` narrowing) without a diagnostic.

These are out of scope:
- Programs that do harmful things because their `.fluid` file asked for them in plain words. Read generated code before you run it: `fluid review` shows what the model decided.
- Running with `--trust` or `verification.sandbox = "off"`. Both explicitly turn the sandbox off.

## Handling secrets

- `secrets.env` is git-ignored and never sent to the model.
- fluidc refuses to write generated code that contains the value of any loaded secret.
- If you committed a key by mistake, revoke it first, then clean the history.
