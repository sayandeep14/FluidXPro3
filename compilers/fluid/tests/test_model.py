"""`fluid model`: show and switch the model that writes the code."""

from __future__ import annotations

from fluid.cli import main as fluid_main
from fluid.doctor import credentials
from fluid.model import current, set_model
from fluidc.settings import load_jsonc

CONFIG = """{
  // how the code is written
  "codegen": { "comments": "minimal" },   /* keep me */
  "llm": {
    "effort": "high",   // think hard
    "model": "claude-opus-5-5"
  }
}
"""


def test_switching_keeps_comments_and_other_keys(tmp_path):
    (tmp_path / "fluid.config").write_text(CONFIG, encoding="utf-8")
    set_model(tmp_path, "gpt-5")
    text = (tmp_path / "fluid.config").read_text(encoding="utf-8")
    assert "// think hard" in text and "/* keep me */" in text and "// how the code is written" in text
    data = load_jsonc(text, "fluid.config")
    assert data["llm"] == {"effort": "high", "model": "gpt-5"} and data["codegen"] == {"comments": "minimal"}
    assert current(tmp_path) == ("openai", "gpt-5", True)


def test_provider_is_written_when_the_name_does_not_say_it(tmp_path):
    (tmp_path / "fluid.config").write_text('{ "codegen": {} }', encoding="utf-8")
    set_model(tmp_path, "my-local-model", provider="openai")
    data = load_jsonc((tmp_path / "fluid.config").read_text(encoding="utf-8"), "fluid.config")
    assert data["llm"] == {"provider": "openai", "model": "my-local-model"}
    # switching back to a Claude model also resets the written provider
    set_model(tmp_path, "claude-sonnet-5-5")
    assert current(tmp_path) == ("anthropic", "claude-sonnet-5-5", True)


def test_a_new_config_is_created(tmp_path, capsys):
    assert fluid_main(["model", "gemini-2.5-pro", "--folder", str(tmp_path)]) == 0
    assert current(tmp_path) == ("google", "gemini-2.5-pro", True)
    assert fluid_main(["model", "--folder", str(tmp_path)]) == 0
    assert "gemini-2.5-pro  (provider google)" in capsys.readouterr().out


def test_doctor_checks_the_key_of_the_provider_in_use(tmp_path, monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "secrets.env").write_text("ANTHROPIC_API_KEY=sk-ant-x\n", encoding="utf-8")
    set_model(tmp_path, "gpt-5")
    text = " ".join(c.render() for c in credentials(tmp_path))
    assert "OpenAI API key" in text and "OPENAI_API_KEY=..." in text and "sk-ant" not in text
