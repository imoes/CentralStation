"""Eine URL-Regel für OpenAI-kompatible APIs: fehlt der Versionspfad, kommt /v1 dazu.

Anlass (07.10.2026): alle Aufrufe von hal an llm-host03 gingen an
/qwen27b/chat/completions statt /qwen27b/v1/chat/completions — eingetragen war die
Basis-URL ohne /v1 (seit 22.05.), und nichts im Code ergänzte sie. Es funktionierte nur,
weil llama-server beide Routen bedient.
"""
from __future__ import annotations

import pytest

from app.services.llm_client import _build_api_url, openai_base_url


@pytest.mark.parametrize("given,expected", [
    # llama.cpp hinter nginx-Präfix — der Fall von hal
    ("https://llm-host03.example.com/qwen35b", "https://llm-host03.example.com/qwen35b/v1"),
    ("https://llm-host03.example.com/qwen27b/", "https://llm-host03.example.com/qwen27b/v1"),
    ("http://llm-host02.test.example.com:8000", "http://llm-host02.test.example.com:8000/v1"),
    # schon mit Version: unverändert
    ("https://llm-host03.example.com/qwen35b/v1", "https://llm-host03.example.com/qwen35b/v1"),
    ("https://api.llmbase.ai/v1", "https://api.llmbase.ai/v1"),
    ("https://api.openai.com/v1", "https://api.openai.com/v1"),
    ("https://api.perplexity.ai/router/v1", "https://api.perplexity.ai/router/v1"),
    ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1"),
    ("https://generativelanguage.googleapis.com/v1beta/openai",
     "https://generativelanguage.googleapis.com/v1beta/openai"),
    # eingetragener Endpunkt wird abgeschnitten, Version bleibt
    ("https://api.openai.com/v1/chat/completions", "https://api.openai.com/v1"),
    ("https://llm-host03.example.com/qwen35b/chat/completions",
     "https://llm-host03.example.com/qwen35b/v1"),
    # bewusst ohne Version: ChatGPT-Backend (Codex), Azure
    ("https://chatgpt.com/backend-api/codex", "https://chatgpt.com/backend-api/codex"),
    ("https://x.openai.azure.com/openai/deployments/gpt",
     "https://x.openai.azure.com/openai/deployments/gpt"),
    ("", ""),
])
def test_openai_base_url(given: str, expected: str) -> None:
    assert openai_base_url(given) == expected


def test_hal_calls_now_carry_v1() -> None:
    assert (_build_api_url("https://llm-host03.example.com/qwen35b", "chat/completions")
            == "https://llm-host03.example.com/qwen35b/v1/chat/completions")


def test_codex_backend_url_is_unchanged() -> None:
    assert (_build_api_url("https://chatgpt.com/backend-api/codex", "responses")
            == "https://chatgpt.com/backend-api/codex/responses")


def test_a_model_name_with_v_and_digit_is_no_version() -> None:
    """"/qwen35b" beginnt nicht mit v+Ziffer; "/v1x-models" wäre eins — nur ganze Segmente."""
    assert openai_base_url("https://h/llama-v2-7b") == "https://h/llama-v2-7b/v1"
