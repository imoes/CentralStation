"""Standardmodell für Codex mit ChatGPT-Konto — EINE Stelle für Konsole (config.toml),
Modellauswahl und System-LLM im Codex-Modus.

Am 02.11.2026 lehnte der Codex-Dienst gpt-5.5 (bis dahin an fünf Stellen fest eingetragen)
und gpt-5.4 ab ("not supported when using Codex with a ChatGPT account"); angeboten werden
gpt-6.x und gpt-5.6 (…/backend-api/codex/models).
"""
CODEX_DEFAULT_MODEL = "gpt-6.1-sol"
