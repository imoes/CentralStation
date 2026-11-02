"""Codex-Modell in der Konsole (02.11.2026): gpt-5.5 stand fest in config.toml und wurde
vom Codex-Dienst abgelehnt ("not supported when using Codex with a ChatGPT account")."""
from __future__ import annotations

from app.services import userenv_manager as um
from app.core.codex_defaults import CODEX_DEFAULT_MODEL


def _model_line(toml: str) -> str:
    return next(l for l in toml.splitlines() if l.startswith("model = "))


def test_selected_model_goes_into_config():
    assert _model_line(um._codex_config_toml({}, "u", model="gpt-5.6-terra")) == 'model = "gpt-5.6-terra"'


def test_without_selection_the_one_default_is_used():
    assert CODEX_DEFAULT_MODEL == "gpt-6.1-sol"
    assert _model_line(um._codex_config_toml({}, "u")) == f'model = "{CODEX_DEFAULT_MODEL}"'


def test_model_cannot_break_out_of_the_toml_line():
    line = _model_line(um._codex_config_toml({}, "u", model='x"\n[mcp_servers.evil]'))
    assert line == f'model = "{CODEX_DEFAULT_MODEL}"'
