"""`fluid new <name>`: a project folder that builds out of the box."""

from __future__ import annotations

import shutil
from pathlib import Path

MAIN_FLUID = """\
app {title}
    A first FluidXPro3 program. Change the words, then run `fluid run main.fluid`.

target python

settings
    greeting is "Hello"

to greet (name) -> text
    The greeting, a space, the name and "!".

    example
        "Ada" -> "Hello Ada!"

on start
    Print the greeting for "world".
"""

CONFIG = """\
// fluid.config: how main.fluid becomes .fx3 and then Python.
// JSON with comments; every key is optional. See fluid.config.schema.json.
{
  "$schema": "./fluid.config.schema.json",
  "project": { "entry": "main.fluid", "fx3Out": "build/fx3", "targetOut": "build/python" },
  "target": { "language": "python" },
  "ask": "mid",
  "verification": {
    "examples": "error",
    "sandbox": "auto"       // generated tests run with no network, no secrets, no files outside their folder
  },
  "llm": { "model": "claude-opus-5-5", "effort": "high" }
}
"""

SECRETS = """\
# secrets.env: values your program and fluidc read. Never commit this file (.gitignore has it).
# fluidc needs an API key to turn .fluid into .fx3: https://console.anthropic.com
ANTHROPIC_API_KEY=
"""

GITIGNORE = """\
secrets.env
*.secrets.env
build/
__pycache__/
*.db
"""

README = """\
# {title}

Built with FluidXPro3. `main.fluid` is the source; `build/` is generated.

```sh
fluid run main.fluid      # build (calls Claude for new or changed blocks) and run
fluid test main.fluid     # run the examples and tests, in the sandbox
fluid review main.fluid   # what the model assumed and decided
```

Commit `fluid.lock`: with it, builds are reproducible and need no API calls.
"""


def create(folder: Path) -> list[Path]:
    if folder.exists() and any(folder.iterdir()):
        raise FileExistsError(f"{folder} already exists and isn't empty")
    folder.mkdir(parents=True, exist_ok=True)
    title = folder.name.replace("-", " ").replace("_", " ").strip().title() or "My App"
    files = {
        "main.fluid": MAIN_FLUID.format(title=title),
        "fluid.config": CONFIG,
        "secrets.env": SECRETS,
        ".gitignore": GITIGNORE,
        "README.md": README.format(title=title),
    }
    written = []
    for name, text in files.items():
        p = folder / name
        p.write_text(text, encoding="utf-8")
        written.append(p)
    from fluidc.settings import PACKAGED_SCHEMA
    shutil.copy(PACKAGED_SCHEMA, folder / "fluid.config.schema.json")
    written.append(folder / "fluid.config.schema.json")
    return written
