"""jira_add_weblink: Weblink (Remote Link) an ein Ticket, auch instanzübergreifend (Nutzer 09.11.2026:
„Weblink JCUE-6373 → IMIT-2517 … kann ich mit meinen Werkzeugen nicht“)."""
import asyncio
import json

import httpx

from app.services.connectors.jira import JiraConnector


def _conn(monkeypatch, base, handler):
    c = JiraConnector(base_url=base, credentials={"token": "t"})
    real = httpx.AsyncClient
    monkeypatch.setattr(c, "_client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return c


def test_connector_remote_link_vertrag(monkeypatch):
    gesehen = []

    def h(req):
        gesehen.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        if req.method == "GET":
            return httpx.Response(200, json=[{"id": 7, "object": {"url": "https://x/browse/A-1", "title": "A-1: t"}}])
        return httpx.Response(201, json={"id": 8, "self": "https://jira/rest/api/2/issue/B-2/remotelink/8"})
    c = _conn(monkeypatch, "https://jira.example/", h)
    assert c.browse_url("A-1") == "https://jira.example/browse/A-1"
    assert asyncio.run(c.list_remote_links("B-2")) == [{"id": 7, "url": "https://x/browse/A-1", "title": "A-1: t"}]
    assert asyncio.run(c.add_remote_link("B-2", "https://y/browse/C-3", "C-3: x"))["id"] == 8
    assert gesehen[-1] == ("POST", "/rest/api/2/issue/B-2/remotelink",
                           {"object": {"url": "https://y/browse/C-3", "title": "C-3: x"}})


class _Fake:
    def __init__(self, base, vorhanden=()):
        self.base, self.vorhanden, self.angelegt = base, list(vorhanden), []
    def browse_url(self, k): return f"{self.base}/browse/{k}"
    async def get_issue_detail(self, k): return {"summary": "Druckserver hängt"}
    async def list_remote_links(self, k): return self.vorhanden
    async def add_remote_link(self, k, url, title):
        self.angelegt.append((k, url, title)); return {"id": 1, "self": "s"}


def _tool(monkeypatch, quelle, ziel_conn, schreiben=True, **kw):
    from app.api import mcp_server as ms
    async def _jira(key):
        return ({"JCUE-6373": quelle, "IMIT-2517": ziel_conn}.get(key), None if key in ("JCUE-6373", "IMIT-2517")
                else f"Ticket '{key}' auf keiner Jira-Instanz gefunden")
    async def _write(action): return None if schreiben else {"ok": False, "error": "Zustimmung erforderlich"}
    monkeypatch.setattr(ms, "_jira_for_issue", _jira)
    monkeypatch.setattr(ms, "_require_write", _write)
    fn = getattr(ms.jira_add_weblink, "fn", ms.jira_add_weblink)
    return asyncio.run(fn(**kw))


def test_weblink_auf_ticket_einer_anderen_instanz(monkeypatch):
    jira, sd = _Fake("https://jira.example.org"), _Fake("https://servicedesk.example.com")
    r = _tool(monkeypatch, jira, sd, issue_key="JCUE-6373", ziel="IMIT-2517")
    assert r["ok"] and r["vorhanden"] is False and r["url"] == "https://servicedesk.example.com/browse/IMIT-2517"
    assert jira.angelegt == [("JCUE-6373", "https://servicedesk.example.com/browse/IMIT-2517",
                              "IMIT-2517: Druckserver hängt")]


def test_vorhandener_link_wird_nicht_doppelt_angelegt(monkeypatch):
    jira = _Fake("https://jira", vorhanden=[{"id": 5, "url": "https://sd/browse/IMIT-2517/", "title": "alt"}])
    r = _tool(monkeypatch, jira, _Fake("https://sd"), issue_key="JCUE-6373", ziel="IMIT-2517")
    assert r["ok"] and r["vorhanden"] is True and r["id"] == 5 and jira.angelegt == []


def test_genau_eins_von_ziel_und_url_und_schreibfreigabe(monkeypatch):
    j = _Fake("https://jira")
    assert "Genau eins" in _tool(monkeypatch, j, j, issue_key="JCUE-6373")["error"]
    assert "Genau eins" in _tool(monkeypatch, j, j, issue_key="JCUE-6373", ziel="IMIT-2517", url="https://x")["error"]
    r = _tool(monkeypatch, j, j, schreiben=False, issue_key="JCUE-6373", ziel="IMIT-2517")
    assert r["error"] == "Zustimmung erforderlich" and j.angelegt == []
    assert "Ziel:" in _tool(monkeypatch, j, j, issue_key="JCUE-6373", ziel="GIBTS-1")["error"]


def test_freie_url(monkeypatch):
    j = _Fake("https://jira")
    r = _tool(monkeypatch, j, j, issue_key="JCUE-6373", url="https://wiki/x", titel="Runbook")
    assert r["ok"] and j.angelegt == [("JCUE-6373", "https://wiki/x", "Runbook")]


def test_stibo_ticket_wird_beim_hersteller_verlinkt(monkeypatch):
    # IPPCUE/DXSD liegen auf stibodx.atlassian.net — keine eigene Instanz, also kein Nachschlagen
    jira = _Fake("https://jira.example.org")
    r = _tool(monkeypatch, jira, None, issue_key="JCUE-6373", ziel="ippcue-2517")
    assert r["ok"] and r["url"] == "https://stibodx.atlassian.net/browse/IPPCUE-2517"
    assert jira.angelegt == [("JCUE-6373", "https://stibodx.atlassian.net/browse/IPPCUE-2517", "IPPCUE-2517")]
