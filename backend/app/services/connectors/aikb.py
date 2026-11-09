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

#: Obergrenze für eine Tiefensuche. Gemessen: eine einzige Tiefensuche macht rund 120
#: LLM-Aufrufe (plus ~160 Embeddings und ~50 Reranks) und brauchte 279 s — also knapp
#: unter der früheren Grenze von 300 s, und damit bei jeder Schwankung darüber.
#:
#: Die Grenzen der Kette sind gestaffelt, damit immer die INNERE zuerst greift und eine
#: klare Meldung liefert, statt dass weiter außen still abgeschnitten wird:
#:     aikb-Tiefensuche 900 s  <  MCP-Werkzeuggrenze 930 s  <  nginx /api/ 1000 s
#: Die MCP-Grenze steht in userenv_manager.MCP_TOOL_TIMEOUT_S (Hermes, Codex) und in
#: userenv/main.py (Claude, MCP_TOOL_TIMEOUT), nginx in nginx/nginx.conf.
DEEPSEARCH_TIMEOUT_S = 900.0

#: Connector-Typ, unter dem die Wissensdatenbank angebunden ist. Es gibt daneben einen
#: zweiten Typ "it_aikb" — der gehört NICHT dazu: search_knowledge_base fragt genau
#: diesen hier ab, und nur er entscheidet, ob das Werkzeug funktioniert.
AIKB_CONNECTOR_TYPE = "aikb"


async def load_aikb_connector(db):
    """Der aktivierte Wissensdatenbank-Konnektor, oder None.

    EINE Definition von "aikb ist konfiguriert", genutzt vom MCP-Werkzeug
    search_knowledge_base UND von der Agenten-Anleitung. Wären das zwei Abfragen,
    könnte die Anleitung ein Werkzeug versprechen, das dann mit "kein Konnektor"
    verweigert — oder schweigen, obwohl es funktionieren würde.
    """
    from sqlalchemy import select
    from app.models.connector import ConnectorConfig

    result = await db.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.type == AIKB_CONNECTOR_TYPE,
            ConnectorConfig.enabled.is_(True),
        ).limit(1)
    )
    return result.scalar_one_or_none()


async def search_kb(db, query: str, size: int = 5) -> dict:
    """Schnelle Suche (OpenSearch, Textauszüge) — EINE Umsetzung für das MCP-Werkzeug
    search_knowledge_base (ohne Tiefensuche) und den KI-Trichter der Log-Überwachung.
    Ein Ausfall heißt `error`, nie eine leere Wissensdatenbank."""
    from app.core.security import decrypt_credentials

    conn = await load_aikb_connector(db)
    if not conn:
        return {"error": "Kein aktiver IT-AIKB Connector konfiguriert"}
    aikb = AIKBConnector(base_url=conn.base_url, credentials=decrypt_credentials(conn.encrypted_credentials))
    hits = await aikb.search_opensearch(query, size=size)
    if not hits and aikb.last_error:
        # A failed search must not look like an empty knowledge base — the agent
        # would report "nothing documented" as a finding.
        return {"mode": "opensearch", "query": query, "error": f"Wissensdatenbank nicht erreichbar: {aikb.last_error}",
                "results": [], "count": 0}
    return {"mode": "opensearch", "query": query, "count": len(hits),
            "results": [{"title": h.get("title", ""), "space": h.get("space_key", ""),
                         "url": h.get("source_url", ""), "excerpt": h.get("content", "")[:4000]} for h in hits]}


async def aikb_configured(db) -> bool:
    """Ist die Wissensdatenbank für die Agenten nutzbar?"""
    return await load_aikb_connector(db) is not None


class AIKBConnector(BaseConnector):
    """Credentials keys: api_token  (preferred)  OR  username + password."""

    #: Reason the last search failed, or None. The search methods return [] on error
    #: because six callers expect a plain list — without this, a timeout is
    #: indistinguishable from "the knowledge base has nothing on this", which is the
    #: more damaging of the two answers because it reads as a fact.
    last_error: str | None = None

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
        self.last_error = None
        try:
            # 900s. The endpoint needs ~5-18s when healthy, but it stalls for minutes
            # at a time while the rest of the service stays responsive (/health and
            # /auth/me answer in 0.0s throughout), so a short cap turned a slow search
            # into no search. Agents reach this through MCP, whose per-tool timeout is
            # 930s (see DEEPSEARCH_TIMEOUT_S for the whole chain), so they can use it.
            async with self._client(timeout=900.0) as client:
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
            # str() on httpx timeouts is empty — name the type, or the log says nothing.
            self.last_error = f"{type(exc).__name__}: {exc}".rstrip(": ")
            log.warning("AIKBConnector.search_opensearch failed: %s", self.last_error)
            return []

    async def search_filter(
        self,
        filter_text: str,
        text: str | None = None,
        size: int = 10,
        space_keys: list[str] | None = None,
    ) -> dict:
        """Filtersuche über die Hashtags der Doku-Abschnitte (POST /search/filter, it-aikb ≥ feature/hashtags) — ohne LLM.

        Filtersprache wie log_watch/filter.py (art:wert, art:(a OR b), !, OR, Klammern). Ohne `text` eine reine
        terms-Abfrage, mit `text` BM25 + kNN. Rückgabe: {"treffer": [normalisiert], "hinweise": [...], "dauer": {...},
        "fehlt": bool} — `fehlt` = der Endpunkt existiert auf dieser AIKB nicht (404/405), also noch nicht ausgerollt.
        Fehler stehen in last_error, wie bei search_opensearch."""
        payload: dict[str, Any] = {"filter": filter_text, "size": max(1, min(size, 50))}
        if text:
            payload["text"] = text
        if space_keys:
            payload["space_keys"] = space_keys
        self.last_error = None
        try:
            async with self._client(timeout=60.0) as client:
                token = await self._bearer(client)
                r = await client.post(f"{self.base_url}/search/filter", json=payload, headers=self._headers(token))
                if r.status_code in (404, 405):
                    self.last_error = f"/search/filter fehlt auf dieser AIKB (HTTP {r.status_code})"
                    return {"treffer": [], "hinweise": [], "dauer": {}, "fehlt": True}
                if r.status_code in (400, 422):
                    self.last_error = f"Filter abgelehnt (HTTP {r.status_code}): {r.text[:300]}"
                    return {"treffer": [], "hinweise": [], "dauer": {}, "fehlt": False}
                r.raise_for_status()
                data = r.json()
                treffer = [{**self._normalise_hit({**h, "title": h.get("titel") or h.get("title"),
                                                         "content_snippet": h.get("auszug")}),
                            "hashtags": h.get("hashtags") or [], "abschnitt": h.get("abschnitt") or ""}
                           for h in (data.get("treffer") or [])[:size]]
                return {"treffer": treffer, "hinweise": data.get("hinweise") or [], "dauer": data.get("dauer") or {},
                        "anzahl_seiten": data.get("anzahl_seiten"), "fehlt": False}
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}".rstrip(": ")
            log.warning("AIKBConnector.search_filter failed: %s", self.last_error)
            return {"treffer": [], "hinweise": [], "dauer": {}, "fehlt": False}

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
        # Deepsearch is LLM-driven and heavy (measured: ~120 LLM calls, 279s for one
        # query); the plain OpenSearch-backed RAG answer stays fast.
        _timeout = DEEPSEARCH_TIMEOUT_S if deepsearch else 90.0
        try:
            async with self._client(timeout=_timeout) as client:
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
