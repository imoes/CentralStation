import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector


class LLMConnector(BaseConnector):
    """Test the reachability of a custom LLM endpoint (OpenAI-compatible or Anthropic)."""

    async def test_connection(self) -> ConnectorTestResult:
        api_mode = self.credentials.get("api_mode", "chat_completions")
        api_key = self.credentials.get("api_key", "")
        model = self.credentials.get("model", "")
        timeout = float(self.credentials.get("timeout_seconds") or 30)

        if api_mode == "anthropic_messages":
            return await self._test_anthropic(api_key, model, timeout)
        return await self._test_openai(api_key, model, timeout)

    async def _test_openai(self, api_key: str, model: str, timeout: float) -> ConnectorTestResult:
        if not self.base_url:
            return ConnectorTestResult(success=False, message="Base URL fehlt")

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # Try /models first (cheap, no token cost)
        try:
            async with self._client(timeout=timeout) as client:
                r = await client.get(f"{self.base_url}/models", headers=headers)
            if r.status_code in (200, 404):
                # 404 = no /models endpoint (some local servers) but reachable
                return ConnectorTestResult(
                    success=True,
                    message=f"Verbindung OK (HTTP {r.status_code}){f' — Modell: {model}' if model else ''}",
                )
            if r.status_code == 401:
                return ConnectorTestResult(success=False, message="Authentifizierung fehlgeschlagen (API Key falsch?)")
            return ConnectorTestResult(success=False, message=f"HTTP {r.status_code}: {r.text[:200]}")
        except httpx.ConnectError:
            return ConnectorTestResult(success=False, message=f"Verbindung zu {self.base_url} fehlgeschlagen")
        except Exception as exc:
            return ConnectorTestResult(success=False, message=str(exc))

    async def _test_anthropic(self, api_key: str, model: str, timeout: float) -> ConnectorTestResult:
        if not api_key:
            return ConnectorTestResult(success=False, message="API Key fehlt für Anthropic Messages Modus")
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers=headers,
                    json={
                        "model": model or "claude-haiku-4-5-20251001",
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "ping"}],
                    },
                )
            if r.status_code in (200, 400):
                return ConnectorTestResult(success=True, message=f"Anthropic API erreichbar — Modell: {model}")
            if r.status_code == 401:
                return ConnectorTestResult(success=False, message="API Key ungültig (401)")
            return ConnectorTestResult(success=False, message=f"HTTP {r.status_code}: {r.text[:200]}")
        except Exception as exc:
            return ConnectorTestResult(success=False, message=str(exc))
