"""it-aikb RAG system connector.

Auth: Bearer Token — Authorization: Bearer aikb_xxx
Endpoints:
  GET  /search        — standard RRF hybrid search
  POST /search/stream — agentic DeepSearch (SSE)
"""
import json

import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector


class ITAikbConnector(BaseConnector):
    def _headers(self) -> dict:
        token = self.credentials.get("token", "")
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    async def test_connection(self) -> ConnectorTestResult:
        try:
            async with self._client() as client:
                r = await client.get(
                    f"{self.base_url}/health",
                    headers=self._headers(),
                )
                r.raise_for_status()
            return ConnectorTestResult(success=True, message="it-aikb RAG reachable")
        except httpx.HTTPStatusError as e:
            return ConnectorTestResult(success=False, message=f"HTTP {e.response.status_code}")
        except Exception as e:
            return ConnectorTestResult(success=False, message=str(e))

    async def search(self, query: str, top_k: int = 5) -> list[dict]:
        """Standard RRF hybrid search — for simple, direct questions."""
        async with self._client(timeout=30.0) as client:
            r = await client.get(
                f"{self.base_url}/search",
                headers=self._headers(),
                params={"q": query, "top_k": top_k},
            )
            r.raise_for_status()
        return r.json().get("results", [])

    async def deepsearch(self, query: str) -> list[dict]:
        """Agentic DeepSearch via SSE — collects all events until done."""
        results: list[dict] = []
        async with self._client(timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/search/stream",
                headers={**self._headers(), "Accept": "text/event-stream"},
                json={"query": query},
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload in ("", "[DONE]"):
                        continue
                    try:
                        event = json.loads(payload)
                        if event.get("type") == "result":
                            results.extend(event.get("results", []))
                    except json.JSONDecodeError:
                        pass
        return results
