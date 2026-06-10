"""IT-AIKB Wissensdatenbank connector.

Provides RAG lookups against the internal Confluence KB via the IT-AIKB API.
Two search modes:
  - OpenSearch (fast, no LLM): POST /search/opensearch — raw Confluence excerpts
  - RAG/Deepsearch (LLM answer): POST /search — full answer with sources

Credentials key: api_token  (Bearer token, format: aikb_...)
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector

log = logging.getLogger(__name__)


class AIKBConnector(BaseConnector):
    """Credentials key: api_token."""

    def _headers(self) -> dict:
        token = self.credentials.get("api_token", "")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def test_connection(self) -> ConnectorTestResult:
        try:
            async with self._client(timeout=10.0) as client:
                r = await client.get(
                    f"{self.base_url}/health",
                    headers=self._headers(),
                )
                r.raise_for_status()
                return ConnectorTestResult(success=True, message="IT-AIKB erreichbar")
        except Exception as exc:
            return ConnectorTestResult(success=False, message=str(exc))

    async def search_opensearch(
        self,
        query: str,
        space_keys: list[str] | None = None,
        size: int = 5,
    ) -> list[dict]:
        """Raw OpenSearch hits — no LLM, fastest path.

        Returns a list of dicts with keys: title, text/body, url, space_key.
        """
        payload: dict[str, Any] = {
            "query": query,
            "space_keys": space_keys or [],
        }
        try:
            async with self._client(timeout=20.0) as client:
                r = await client.post(
                    f"{self.base_url}/search/opensearch",
                    json=payload,
                    headers=self._headers(),
                )
                r.raise_for_status()
                hits = r.json()
                if isinstance(hits, dict):
                    hits = hits.get("results") or hits.get("hits") or []
                return [self._normalise_hit(h) for h in (hits or [])[:size]]
        except Exception as exc:
            log.warning("AIKBConnector.search_opensearch failed: %s", exc)
            return []

    async def search_rag(
        self,
        query: str,
        deepsearch: bool = False,
        space_keys: list[str] | None = None,
    ) -> dict:
        """LLM-powered answer from KB.

        Returns dict with keys: answer (str), results (list of normalised hits).
        """
        payload: dict[str, Any] = {
            "query": query,
            "space_keys": space_keys or [],
            "include_attachments": False,
        }
        if deepsearch:
            payload["deepsearch_mode"] = True
        try:
            async with self._client(timeout=60.0) as client:
                r = await client.post(
                    f"{self.base_url}/search",
                    json=payload,
                    headers=self._headers(),
                )
                r.raise_for_status()
                data = r.json()
                answer = data.get("answer") or ""
                # Prefer references (with URL) over raw sources
                raw_results = data.get("references") or data.get("sources") or []
                results = [self._normalise_hit(h) for h in raw_results[:5]]
                return {"answer": answer, "results": results}
        except Exception as exc:
            log.warning("AIKBConnector.search_rag failed: %s", exc)
            return {"answer": "", "results": []}

    @staticmethod
    def _normalise_hit(h: dict) -> dict:
        """Normalise a KB search hit to a common dict format."""
        return {
            "title": h.get("title") or h.get("page_title") or "",
            "content": (h.get("text") or h.get("content") or h.get("body") or "")[:500],
            "source_url": h.get("url") or h.get("source_url") or h.get("link") or "",
            "space_key": h.get("space_key") or h.get("space") or "",
        }
