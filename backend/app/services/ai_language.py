"""Response-language control for LLM calls (internal repo: German default).

The AI answers in the language configured via the global setting ``app.language``
(``de`` default, ``en`` switchable). ``language_instruction()`` is appended to
system prompts so the model honours the chosen language.

The public fork ships an English-default, per-user variant of this module; the
interface (with_language / get_response_language / get_response_language_for_user)
is identical so call sites are portable between both repos.
"""
from __future__ import annotations

from typing import Any

DEFAULT_LANG = "de"

LANG_INSTRUCTION: dict[str, str] = {
    "de": (
        "WICHTIG: Antworte auf Deutsch. Alle Textfelder "
        "(title, description, action, rationale, summary, comment usw.) "
        "MÜSSEN auf Deutsch sein — auch wenn Kontext oder Quellen in einer anderen Sprache vorliegen."
    ),
    "en": (
        "IMPORTANT: Respond in English. All output text fields "
        "(title, description, action, rationale, summary, comment, etc.) "
        "must be written in English, even when the context or sources are in another language."
    ),
}
LANG_NAME: dict[str, str] = {"de": "German", "en": "English"}


def normalize_lang(lang: str | None) -> str:
    code = (lang or "").strip().lower()[:2]
    return code if code in LANG_INSTRUCTION else DEFAULT_LANG


async def get_response_language(db: Any) -> str:
    return await get_response_language_for_user(db, None)


async def get_response_language_for_user(db: Any, user_id: Any) -> str:
    """Resolve the response language from the global app.language setting.

    (The internal repo has no per-user ui_language column; user_id is accepted
    for interface parity with the public fork and ignored here.)
    """
    try:
        from app.services.settings import get_setting
        return normalize_lang(await get_setting(db, "app.language"))
    except Exception:
        return DEFAULT_LANG


def language_instruction(lang: str | None) -> str:
    return LANG_INSTRUCTION[normalize_lang(lang)]


def language_name(lang: str | None) -> str:
    return LANG_NAME[normalize_lang(lang)]


def with_language(system_prompt: str, lang: str | None) -> str:
    return f"{system_prompt}\n\n{language_instruction(lang)}"
