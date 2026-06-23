"""IT-AIKB Wissensdatenbank connector.

Provides RAG lookups against the internal Confluence KB via the IT-AIKB API.
Two search modes:
  - OpenSearch (fast, returns excerpts): POST /search/opensearch
  - RAG/Deepsearch (LLM answer): POST /search

Auth: Bearer token (format: aikb_…) stored as credentials key "api_token".
Fallback: username + password → fresh JWT per call via POST /auth/login/internal.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector

log = logging.getLogger(__name__)


class AIKBConnector(BaseConnector):
    """Credentials keys: api_token  (preferred)  OR  username + password."""

    async def _bearer(self, client: httpx.AsyncClient) -> str:
        """Return a valid Bearer token — static api_token or fresh JWT."""
        token = self.credentials.get("api_token", "").strip()
        if token:
            return token
        # Fallback: authenticate with username / password
        r = await client.post(
            f"{self.base_url}/auth/login/internal",
            json={
                "username": self.credentials.get("username", ""),
                "password": self.credentials.get("password", ""),
            },
        )
        r.raise_for_status()
        return r.json().get("token", "")

    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def test_connection(self) -> ConnectorTestResult:
        try:
            async with self._client(timeout=10.0) as client:
                token = await self._bearer(client)
                r = await client.get(
                    f"{self.base_url}/auth/me",
                    headers=self._headers(token),
                )
                r.raise_for_status()
                data = r.json()
                user = data.get("display_name") or data.get("username", "")
                role = data.get("role", "")
                return ConnectorTestResult(
                    success=True,
                    message=f"IT-AIKB verbunden als {user} ({role})",
                )
        except Exception as exc:
            return ConnectorTestResult(success=False, message=str(exc))

    async def search_opensearch(
        self,
        query: str,
        space_keys: list[str] | None = None,
        size: int = 5,
    ) -> list[dict]:
        """OpenSearch hits with content_snippet — no extra LLM call.

        Returns list of normalised dicts: title, content, source_url, space_key.
        """
        payload: dict[str, Any] = {
            "query": query,
            "space_keys": space_keys or [],
        }
        try:
            async with self._client(timeout=20.0) as client:
                token = await self._bearer(client)
                r = await client.post(
                    f"{self.base_url}/search/opensearch",
                    json=payload,
                    headers=self._headers(token),
                )
                r.raise_for_status()
                data = r.json()
                hits = data.get("results") or []
                return [self._normalise_hit(h) for h in hits[:size]]
        except Exception as exc:
            log.warning("AIKBConnector.search_opensearch failed: %s", exc)
            return []

    async def search_rag(
        self,
        query: str,
        deepsearch: bool = False,
        space_keys: list[str] | None = None,
    ) -> dict:
        """LLM-powered answer from KB with source citations.

        Returns dict: answer (str), results (list of normalised hits).
        """
        payload: dict[str, Any] = {
            "query": query,
            "space_keys": space_keys or [],
            "include_attachments": False,
        }
        if deepsearch:
            payload["deepsearch_mode"] = True
        try:
            async with self._client(timeout=90.0) as client:
                token = await self._bearer(client)
                r = await client.post(
                    f"{self.base_url}/search",
                    json=payload,
                    headers=self._headers(token),
                )
                r.raise_for_status()
                data = r.json()
                answer = data.get("answer") or ""
                raw_results = data.get("results") or data.get("references") or []
                results = [self._normalise_hit(h) for h in raw_results[:20]]
                return {"answer": answer, "results": results}
        except Exception as exc:
            log.warning("AIKBConnector.search_rag failed: %s", exc)
            return {"answer": "", "results": []}

    async def update_page_dependencies(
        self,
        page_title: str,
        space_key: str,
        service_dependencies: list[str],
        dependency_of: list[str],
    ) -> dict:
        """Schreibt strukturierte Service-Abhängigkeiten via it-aikb REST-API zurück.

        Ruft POST /admin/update-page-dependencies auf — it-aikb schreibt direkt
        in den confluence-pages OpenSearch-Index (service_dependencies, dependency_of).
        Gibt {"updated": bool, "doc_id": str, "message": str} zurück.
        """
        payload = {
            "page_title": page_title,
            "space_key": space_key,
            "service_dependencies": service_dependencies,
            "dependency_of": dependency_of,
        }
        try:
            async with self._client(timeout=30.0) as client:
                token = await self._bearer(client)
                r = await client.post(
                    f"{self.base_url}/admin/update-page-dependencies",
                    json=payload,
                    headers=self._headers(token),
                )
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            log.warning("AIKBConnector.update_page_dependencies failed: %s", exc)
            return {"updated": False, "doc_id": "", "message": str(exc)}

    @staticmethod
    def _normalise_hit(h: dict) -> dict:
        return {
            "title": h.get("title") or h.get("page_title") or "",
            "content": (
                h.get("content_snippet") or h.get("text") or h.get("content") or h.get("body") or ""
            )[:4000],
            "source_url": h.get("source_url") or h.get("url") or h.get("link") or "",
            "space_key": h.get("space_key") or h.get("space") or "",
        }
