from app.services.codex_models import extract_codex_model_ids


def test_extract_codex_model_ids_from_chatgpt_backend_payload():
    payload = {
        "models": [
            {"slug": "gpt-6-astra"},
            {"slug": "gpt-5.5"},
            {"slug": "gpt-5.4-mini"},
            {"slug": "text-embedding-3-large"},
            {"id": "codex-auto-review"},
            {"slug": "gpt-5.5"},
            {"slug": ""},
        ]
    }

    assert extract_codex_model_ids(payload) == [
        "gpt-6-astra",
        "gpt-5.5",
        "gpt-5.4-mini",
        "codex-auto-review",
    ]
