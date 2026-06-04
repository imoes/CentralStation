"""Response-language control for all LLM calls.

The UI is i18n-enabled (English default, German switchable). The AI should
answer in the SAME language the operator uses. The active language is resolved
from the per-user preference ``ui_language`` with the legacy global
``app.language`` setting as fallback, and injected into system prompts via
``language_instruction()``.

Add a new language by extending ``LANG_INSTRUCTION`` — no call site changes.
"""
from __future__ import annotations

from uuid import UUID
from typing import Any

DEFAULT_LANG = "en"

# Per-language instruction appended to system prompts. Keep each line explicit
# so smaller models reliably honour it.
LANG_INSTRUCTION: dict[str, str] = {
    "en": (
        "IMPORTANT: Respond in English. All output text fields "
        "(title, description, action, rationale, summary, comment, etc.) "
        "must be written in English, even when the context or sources are in another language."
    ),
    "de": (
        "WICHTIG: Antworte auf Deutsch. Alle Textfelder "
        "(title, description, action, rationale, summary, comment usw.) "
        "MÜSSEN auf Deutsch sein — auch wenn Kontext oder Quellen in einer anderen Sprache vorliegen."
    ),
}

# Human-readable name used inside HyDE / search prompts.
LANG_NAME: dict[str, str] = {"en": "English", "de": "German"}


def normalize_lang(lang: str | None) -> str:
    code = (lang or "").strip().lower()[:2]
    return code if code in LANG_INSTRUCTION else DEFAULT_LANG


async def get_response_language(db: Any) -> str:
    """Return the configured response language code (e.g. 'en', 'de')."""
    return await get_response_language_for_user(db, None)


async def get_response_language_for_user(db: Any, user_id: UUID | str | None) -> str:
    """Resolve per-user language with a global fallback."""
    try:
        if user_id:
            from sqlalchemy import select
            from app.models.workflow import UserPreference

            result = await db.execute(
                select(UserPreference.ui_language).where(UserPreference.user_id == user_id)
            )
            user_lang = result.scalar_one_or_none()
            if user_lang:
                return normalize_lang(user_lang)

        from app.services.settings import get_setting
        return normalize_lang(await get_setting(db, "app.language"))
    except Exception:
        return DEFAULT_LANG


def language_instruction(lang: str | None) -> str:
    return LANG_INSTRUCTION[normalize_lang(lang)]


def language_name(lang: str | None) -> str:
    return LANG_NAME[normalize_lang(lang)]


def with_language(system_prompt: str, lang: str | None) -> str:
    """Append the language instruction to a system prompt."""
    return f"{system_prompt}\n\n{language_instruction(lang)}"
