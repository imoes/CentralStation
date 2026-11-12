"""vi-mcp — das externe, geteilte VI-Gedächtnis (MCP über Streamable HTTP).

Nutzer 08.11.2026: „Wir brauchen einen neuen Konnektor.“ CentralStation ist eine Instanz des Kollektivs: die
Log-Überwachung fragt vor einer Analyse das geteilte Gedächtnis (Hinweise anderer Instanzen, gelernte Suchen),
meldet die Analyse als Handlung (vi_vorher/vi_nachher) und schreibt Gelerntes (bekannte Fehler, Feldbedeutungen)
als Erkenntnisse mit Quelle hinein. Instanz „centralstation“, ohne Reflex-Recht. Siehe ~/skills/VI-KOLLEKTIV.md.

base_url: der MCP-Endpunkt, z. B. https://llm-host03.example.com/vi-mcp/mcp. Credentials: {"token": "…"} (Bearer).
Protokoll: JSON-RPC 2.0 per POST, Antwort JSON oder SSE (data:-Zeilen); Sitzung über Mcp-Session-Id.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector

log = logging.getLogger(__name__)

VI_MCP_CONNECTOR_TYPE = "vi_mcp"
PROTOCOL_VERSION = "2025-03-26"
#: Name dieser Instanz im Kollektiv (Hashtag instanz:… vergibt vi-mcp aus dem Token; hier nur für Filter)
INSTANZ = "centralstation"


class ViMcpFehler(RuntimeError):
    """vi-mcp nicht erreichbar oder Antwort nicht lesbar — benannt, nie still."""


class ViAntwort(str):
    """Antworttext mit dem MCP-Feld isError (result.isError) — Ablehnungen sind daran sprachunabhängig erkennbar
    (vi-mcp ≥ 0.23.0 antwortet englisch; Rat der vi-mcp-Session 10.11.2026). Verhält sich sonst wie str."""
    fehler: bool = False


def antwort_text(result: dict) -> ViAntwort:
    """Text aus einem tools/call-Ergebnis (MCP: content[{type: text, text}]) samt isError."""
    teile = [c.get("text", "") for c in (result or {}).get("content") or [] if c.get("type") == "text"]
    a = ViAntwort("\n".join(teile))
    a.fehler = bool((result or {}).get("isError"))
    return a


def lies_body(body: str) -> dict:
    """JSON-RPC-Antwort aus JSON oder aus einem SSE-Strom (letzte data:-Zeile). Rein — testbar."""
    s = (body or "").strip()
    if s.startswith("{"):
        return json.loads(s)
    zeilen = [z[5:].strip() for z in s.splitlines() if z.startswith("data:")]
    if not zeilen:
        raise ViMcpFehler(f"Antwort weder JSON noch SSE: {s[:120]!r}")
    return json.loads(zeilen[-1])


class ViMcpConnector(BaseConnector):
    """Credentials keys: token (Bearer der Instanz)."""

    def __init__(self, base_url: str | None, credentials: dict):
        super().__init__(base_url, credentials)
        self.session_id: str | None = None
        self._id = 0

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "Authorization": f"Bearer {(self.credentials.get('token') or '').strip()}"}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    async def _rpc(self, client: httpx.AsyncClient, method: str, params: dict) -> dict:
        self._id += 1
        try:
            r = await client.post(self.base_url, headers=self._headers(),
                                  json={"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        except httpx.HTTPError as e:
            raise ViMcpFehler(f"{type(e).__name__}: {e}") from e
        if r.status_code in (401, 403):
            raise ViMcpFehler(f"HTTP {r.status_code}: Token abgelehnt")
        if r.status_code >= 400:
            raise ViMcpFehler(f"HTTP {r.status_code}: {r.text[:160]}")
        self.session_id = r.headers.get("Mcp-Session-Id") or self.session_id
        d = lies_body(r.text)
        if "error" in d:
            raise ViMcpFehler(f"{method}: {d['error'].get('message', d['error'])}"[:300])
        return d.get("result") or {}

    async def _sitzung(self, client: httpx.AsyncClient) -> dict:
        if self.session_id:
            return {}
        return await self._rpc(client, "initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                                      "clientInfo": {"name": f"centralstation-{INSTANZ}", "version": "1"}})

    async def werkzeug(self, name: str, argumente: dict, timeout: float = 20.0) -> str:
        """Ein vi-mcp-Werkzeug aufrufen → Text der Antwort. Fehler als ViMcpFehler."""
        async with self._client(timeout=timeout) as client:
            await self._sitzung(client)
            try:
                res = await self._rpc(client, "tools/call", {"name": name, "arguments": argumente})
            except ViMcpFehler as e:
                # Dienst neu gestartet: alte Sitzung → HTTP 404 („unterbrochen“) → einmal neu anmelden (vi-mcp 0.2.0)
                if str(e).startswith("HTTP 404") or "session" in str(e).lower():
                    self.session_id = None
                    await self._sitzung(client)
                    res = await self._rpc(client, "tools/call", {"name": name, "arguments": argumente})
                else:
                    raise
        return antwort_text(res)

    async def werkzeug_json(self, name: str, argumente: dict, timeout: float = 20.0) -> Any:
        t = await self.werkzeug(name, argumente, timeout)
        try:
            return json.loads(t)
        except (ValueError, TypeError):
            return t

    # ── die vier Wege des Kollektivs ──
    async def vorher(self, tool: str, args: dict, lesend: bool = False, kontext_hashtags: list[str] | None = None) -> Any:
        arg: dict = {"tool": tool, "args": args, "lesend": lesend}
        if kontext_hashtags:
            arg["kontext_hashtags"] = kontext_hashtags       # ab vi-mcp 0.2.0; 0.1.x ignoriert unbekannte Felder nicht
        try:
            return await self.werkzeug_json("vi_vorher", arg)
        except ViMcpFehler as e:
            if kontext_hashtags and "kontext_hashtags" in str(e):
                arg.pop("kontext_hashtags")
                return await self.werkzeug_json("vi_vorher", arg)
            raise

    async def nachher(self, tool: str, args: dict, ok: bool, dauer_ms: int, vorher_id: str | None = None,
                      fehler: str | None = None, ergebnis: str | None = None) -> Any:
        arg: dict = {"tool": tool, "args": args, "ok": ok, "dauer_ms": int(dauer_ms), "lesend": False}
        if vorher_id:
            arg["vorher_id"] = vorher_id
        if fehler:
            arg["fehler"] = fehler[:1000]
        if ok and ergebnis:
            arg["ergebnis"] = ergebnis[:20000]
        return await self.werkzeug_json("vi_nachher", arg)

    async def lerne(self, text: str, hashtags: list[str], quelle: str, wichtigkeit: int = 1,
                    key: str | None = None, ausgang: str | None = None) -> str:
        arg: dict = {"text": text[:1000], "hashtags": hashtags[:20], "quelle": quelle[:500],
                     "wichtigkeit": max(0, min(3, wichtigkeit))}
        if key:
            arg["key"] = key                   # ab vi-mcp 0.23.0: gleicher key aktualisiert statt neu anzulegen
        if ausgang in ("gut", "schlecht"):
            arg["ausgang"] = ausgang           # ab vi-mcp 0.10.0: der Schlaf gleicht neue Erfahrungen dagegen ab
        return await self.werkzeug("vi_lerne", arg)

    async def erinnere(self, filtertext: str, limit: int = 5) -> str:
        return await self.werkzeug("vi_erinnere", {"filter": filtertext, "limit": limit})

    async def rueckmeldung(self, half: list[str], falsch: list[str], vorher_id: str | None = None) -> ViAntwort:
        """vi_feedback (ab vi-mcp 0.49.0): welche der gezeigten Hinweise geholfen bzw. in die Irre geführt haben.
        Bucht auf die letzte Erfahrung dieser Instanz, vor der der Hinweis gezeigt wurde (≤ 60 min) — also NACH
        nachher() rufen. Bewertet die Auswahl des Hinweises, nicht das Wissen (dafür vi_confirm/vi_refute).
        Nutzer 11.11.2026 (über die vi-mcp-Sitzung): „feedback soll gesendet werden“.
        before_id (ab vi-mcp 0.50.1): bucht genau auf die Erfahrung dieses vi_before — sonst trifft es bei parallelen
        Analysen derselben Instanz die jüngste passende, womöglich die eines Nachbarn. 0.50.0 ignoriert das Feld
        (keine Schema-Prüfung: server.py tools/call → tools.call_tool prüft nur Pflichtfelder)."""
        arg: dict = {"helped": list(half)[:20], "wrong": list(falsch)[:20]}
        if vorher_id:
            arg["before_id"] = vorher_id
        return await self.werkzeug("vi_feedback", arg)

    async def geloest(self, name: str, triggers: list[str], steps: list[str], instructions: str, reason: str,
                      evidence: list[str] | None = None) -> ViAntwort:
        """vi_solved: ein Problem gilt als gelöst — als Fähigkeit (skill) ablegen bzw. die gleichnamige weiterentwickeln.
        triggers = Hashtags des Problems (host:, thema:, fehler:), steps = Maßnahmen, instructions = vollständige Lösung
        mit Befehlen, evidence = VI-Objekt-IDs (Erfahrungen) — KEINE fremden IDs. Startet in der VI im Modus test und
        erscheint vor passenden Aktionen (Rat der vi-mcp-Sitzung 12.11.2026)."""
        arg: dict = {"name": name[:200], "triggers": triggers[:20], "steps": steps[:20],
                     "instructions": instructions[:20000], "reason": reason[:1000]}
        if evidence:
            arg["evidence"] = evidence[:20]
        return await self.werkzeug("vi_solved", arg)

    async def test_connection(self) -> ConnectorTestResult:
        try:
            async with self._client(timeout=15.0) as client:
                init = await self._rpc(client, "initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                                              "clientInfo": {"name": "centralstation-test", "version": "1"}})
                res = await self._rpc(client, "tools/call", {"name": "vi_status", "arguments": {}})
            info = init.get("serverInfo") or {}
            status = antwort_text(res)
            return ConnectorTestResult(success=True,
                                       message=f"vi-mcp {info.get('version', '?')} verbunden — {status.splitlines()[0][:160]}",
                                       details={"serverInfo": info, "status": status[:1500]})
        except ViMcpFehler as e:
            return ConnectorTestResult(success=False, message=str(e))
        except Exception as e:  # noqa: BLE001
            return ConnectorTestResult(success=False, message=f"{type(e).__name__}: {e}")


async def load_vi_mcp_connector(db) -> ViMcpConnector | None:
    """Der aktivierte vi-mcp-Konnektor, oder None — EINE Definition von „Kollektiv ist angebunden“."""
    from sqlalchemy import select

    from app.core.security import decrypt_credentials
    from app.models.connector import ConnectorConfig
    row = (await db.execute(select(ConnectorConfig).where(ConnectorConfig.type == VI_MCP_CONNECTOR_TYPE,
                                                           ConnectorConfig.enabled.is_(True)).limit(1))).scalar_one_or_none()
    if row is None:
        return None
    return ViMcpConnector(row.base_url, decrypt_credentials(row.encrypted_credentials))
