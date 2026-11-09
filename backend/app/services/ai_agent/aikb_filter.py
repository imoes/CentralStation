"""Filter für die AIKB-Filtersuche aus den Alarmen eines Laufs — ersetzt die LLM-Vorstufe RAG_DECISION_PROMPT.

Nutzer 09.11.2026: rag_lookup soll die AIKB nicht mehr über frei formulierte Anfragen eines Sprachmodells fragen,
sondern über dieselben Hashtags, mit denen CentralStation und AIKB taggen (AIKB: POST /search/filter, Branch
feature/hashtags). Gemessen von der AIKB-Session am 09.11.: kNN fand zu „Tablespace Füllstand auf cue0109“ unter den
Top-10 keinen cue0109-Treffer, BM25 9 — Bezeichner gehören in Tags, nicht in den Vektor.

Ableitung, rein und ohne LLM:
- host: kanonischer Name aus dem Hostverzeichnis (log_watch/hosts.py) — nur zugeordnete Hosts; ein unbekannter Name
  hätte in der AIKB keinen Tag, der Filter träfe nichts.
- thema: aus dem festen Vokabular (log_watch/vokabular.themen) über Titel/Nachricht — nur, wenn kein Host bekannt ist
  (sonst holt `host OR thema` jede Seite zu dem Thema, unabhängig vom Host).
- immer !art:vorlage (Vorlagenseiten ohne Inhalt, siehe vi-mcp 0.20.6 „Was fehlt noch“).
"""
from __future__ import annotations

MAX_HOSTS = 8
MAX_THEMEN = 4


def _host(a: dict) -> str:
    meta = a.get("metadata") or {}
    return str(a.get("host") or a.get("agent") or meta.get("host") or "").strip()


def aikb_filter_aus_alarmen(alerts: list[dict], verz) -> tuple[str | None, str]:
    """(Filtertext oder None, Begründung). Rein — testbar."""
    hosts: list[str] = []
    unbekannt: list[str] = []
    for a in alerts:
        roh = _host(a)
        if not roh:
            continue
        e, k, grund = verz.zuordnen(roh)
        if e is not None:
            if e.name not in hosts:
                hosts.append(e.name)
        elif f"{roh} ({grund})" not in unbekannt:
            unbekannt.append(f"{roh} ({grund})")      # der Name aus dem Alarm, nicht der Vergleichsschlüssel
    if hosts:
        teil = hosts[:MAX_HOSTS]
        f = (f"host:{teil[0]}" if len(teil) == 1 else "host:(" + " OR ".join(teil) + ")") + " !art:vorlage"
        why = f"{len(teil)} Host(s) aus dem Hostverzeichnis" + (f"; nicht zugeordnet: {', '.join(unbekannt[:5])}" if unbekannt else "")
        return f, why
    from app.services.log_watch.vokabular import themen
    ids: list[str] = []
    for a in alerts:
        for t in themen(f"{a.get('title') or ''}\n{a.get('message') or ''}"):
            if t.id not in ids:
                ids.append(t.id)
    if ids:
        teil = ids[:MAX_THEMEN]
        f = (f"thema:{teil[0]}" if len(teil) == 1 else "thema:(" + " OR ".join(teil) + ")") + " !art:vorlage"
        return f, "kein Host zugeordnet; Themen aus dem Vokabular" + (f" (nicht zugeordnet: {', '.join(unbekannt[:5])})" if unbekannt else "")
    return None, "weder ein zugeordneter Host noch ein Thema aus dem Vokabular" + (f" (nicht zugeordnet: {', '.join(unbekannt[:5])})" if unbekannt else "")
