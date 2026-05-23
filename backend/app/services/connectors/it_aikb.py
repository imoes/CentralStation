"""it-aikb RAG system connector.

Auth: Bearer Token — Authorization: Bearer aikb_xxx
Endpoints:
  POST /search              — standard RRF hybrid search (with LLM answer)
  POST /search/opensearch   — raw OpenSearch hits, no LLM (fast, for KB lookup)
  POST /search/stream       — agentic DeepSearch via SSE
"""
import json
import logging

import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector

log = logging.getLogger(__name__)


class ITAikbConnector(BaseConnector):
    def _headers(self) -> dict:
        token = self.credentials.get("token", "")
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    async def test_connection(self) -> ConnectorTestResult:
        try:
            async with self._client() as client:
                r = await client.get(f"{self.base_url}/health", headers=self._headers())
                r.raise_for_status()
            return ConnectorTestResult(success=True, message="it-aikb RAG reachable")
        except httpx.HTTPStatusError as e:
            return ConnectorTestResult(success=False, message=f"HTTP {e.response.status_code}")
        except Exception as e:
            return ConnectorTestResult(success=False, message=str(e))

    async def search_opensearch(
        self,
        query: str,
        space_keys: list[str] | None = None,
        top_k: int = 5,
    ) -> list[dict]:
        """Raw OpenSearch hits without LLM — fast, for direct KB page lookups."""
        payload: dict = {"query": query, "space_keys": space_keys or []}
        async with self._client(timeout=15.0) as client:
            r = await client.post(
                f"{self.base_url}/search/opensearch",
                headers=self._headers(),
                json=payload,
            )
            r.raise_for_status()
        return r.json().get("results", [])[:top_k]

    async def search(self, query: str, space_keys: list[str] | None = None) -> list[dict]:
        """Standard RRF hybrid search with LLM answer — for knowledge questions."""
        payload: dict = {"query": query, "space_keys": space_keys or [], "deepsearch_mode": False}
        async with self._client(timeout=45.0) as client:
            r = await client.post(
                f"{self.base_url}/search",
                headers=self._headers(),
                json=payload,
            )
            r.raise_for_status()
        data = r.json()
        # Return source chunks; include the LLM answer as a synthetic result if present
        results = list(data.get("results", []))
        answer = data.get("answer", "")
        if answer and not results:
            results = [{"title": "KI-Antwort", "content": answer, "source_url": ""}]
        return results

    async def deepsearch(self, query: str) -> list[dict]:
        """Agentic DeepSearch via SSE — waits for the final sources event."""
        results: list[dict] = []
        payload = {"query": query, "deepsearch_mode": True}
        async with self._client(timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/search/stream",
                headers={**self._headers(), "Accept": "text/event-stream"},
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload_str = line[5:].strip()
                    if payload_str in ("", "[DONE]"):
                        continue
                    try:
                        event = json.loads(payload_str)
                        # "sources" event carries the final document list
                        if event.get("type") == "sources":
                            results.extend(event.get("results", []))
                    except json.JSONDecodeError:
                        pass
        return results
