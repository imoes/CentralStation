from __future__ import annotations

from typing import Any


CODEX_MODEL_EXCLUDE = (
    "embedding",
    "tts",
    "whisper",
    "dall-e",
    "babbage",
    "davinci",
    "ada",
    "curie",
)


def extract_codex_model_ids(payload: dict[str, Any]) -> list[str]:
    """Extract model slugs from chatgpt.com/backend-api/codex/models."""
    raw_models = payload.get("models") or payload.get("data") or []
    models: list[str] = []
    seen: set[str] = set()
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        model_id = (item.get("slug") or item.get("id") or "").strip()
        if (
            model_id
            and model_id not in seen
            and not any(x in model_id for x in CODEX_MODEL_EXCLUDE)
        ):
            models.append(model_id)
            seen.add(model_id)
    return models
