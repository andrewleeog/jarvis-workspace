"""Regression checks for the orchestrator's effective system prompts."""

import re

from plotspace.routers import orchestrator


SPANISH_PROMPT_MARKERS = re.compile(
    r"\b(sos|traduc[ií]s|agentes|respond[eé]|espa[nñ]ol|"
    r"proceso mental|manejo de errores|preguntas|terminales)\b",
    re.IGNORECASE,
)


def _assert_english_prompt(prompt: str) -> None:
    assert prompt.startswith("You are JARVIS"), prompt[:120]
    match = SPANISH_PROMPT_MARKERS.search(prompt)
    assert match is None, f"Spanish marker remains in system prompt: {match.group(0)!r}"
    spanish_glyph = re.search(r"[áéíóúñ¿¡]", prompt, re.IGNORECASE)
    assert spanish_glyph is None, (
        f"Spanish-specific character remains in system prompt: {spanish_glyph.group(0)!r}"
    )


def test_api_orchestrator_system_prompt_is_english():
    _assert_english_prompt(orchestrator.SYSTEM_PROMPT)


def test_cli_orchestrator_system_prompt_is_english():
    _assert_english_prompt(orchestrator._system_prompt_cli())


def test_language_rule_defaults_to_english():
    assert "default English" in orchestrator.SYSTEM_PROMPT
    assert "default español" not in orchestrator._system_prompt_cli()
