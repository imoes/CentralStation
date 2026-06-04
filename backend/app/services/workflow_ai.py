"""ITIL Workflow AI Service.

Provides AI assistance for all user-facing ITIL operations:
- Priority matrix (impact × urgency → P1-P4)
- AI comment drafting (progress update, handoff, escalation)
- AI resolution / closing message generation
- KEDB matching (known error database search via it-aikb)
- Root cause analysis suggestions (5-Why, Fishbone)
- SLA deadline calculation
- Auto-categorization from ticket title/description
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.services.ai_language import with_language
from app.services.llm_client import generate_text

log = logging.getLogger(__name__)

# ── ITIL Priority Matrix ────────────────────────────────────────────────────
PRIORITY_MATRIX = {
    ("high",   "high"):   ("P1", 15,   4 * 60),
    ("high",   "medium"): ("P2", 60,   8 * 60),
    ("high",   "low"):    ("P3", 240,  2 * 24 * 60),
    ("medium", "high"):   ("P2", 60,   8 * 60),
    ("medium", "medium"): ("P3", 240,  2 * 24 * 60),
    ("medium", "low"):    ("P4", 1440, 5 * 24 * 60),
    ("low",    "high"):   ("P3", 240,  2 * 24 * 60),
    ("low",    "medium"): ("P4", 1440, 5 * 24 * 60),
    ("low",    "low"):    ("P4", 1440, 5 * 24 * 60),
}

SLA_LABELS = {
    "P1": {"label": "Kritisch", "response_min": 15,   "resolution_min": 240},
    "P2": {"label": "Hoch",     "response_min": 60,   "resolution_min": 480},
    "P3": {"label": "Mittel",   "response_min": 240,  "resolution_min": 2880},
    "P4": {"label": "Niedrig",  "response_min": 1440, "resolution_min": 7200},
}

CLOSURE_CODES = [
    "solved_permanently",
    "solved_workaround",
    "no_fault_found",
    "duplicate",
    "user_error",
    "cancelled",
]

ITIL_CATEGORIES = [
    "Hardware", "Software", "Netzwerk", "Sicherheit",
    "E-Mail / Kommunikation", "Berechtigungen / Zugang",
    "Backup / Storage", "Monitoring / Alerting",
    "Server / Virtualisierung", "Datenbank", "Sonstiges",
]


def calculate_priority(impact: str, urgency: str) -> dict:
    key = (impact.lower(), urgency.lower())
    priority, response_min, resolution_min = PRIORITY_MATRIX.get(key, ("P4", 1440, 7200))
    return {
        "priority": priority,
        "response_minutes": response_min,
        "resolution_minutes": resolution_min,
        "sla_label": SLA_LABELS.get(priority, {}).get("label", ""),
    }


# ── AI Prompts ──────────────────────────────────────────────────────────────
COMMENT_SYSTEM = """Du bist ein erfahrener IT-Administrator und schreibst einen professionellen
Fortschrittskommentar für ein Jira-Ticket auf Deutsch.

WICHTIG: Lies den gesamten bisherigen Ticket-Verlauf (Beschreibung + alle Kommentare) und
beziehe Dich auf den AKTUELLEN Stand. Wiederhole keine Punkte, die bereits erledigt oder
beantwortet sind. Berücksichtige besonders den neuesten Kommentar — er spiegelt den
aktuellen Sachstand wider und darf nicht ignoriert werden.

Falls Wissensdatenbank-Einträge mitgeliefert werden, nutze diese als zusätzlichen Kontext.

Schreibe einen präzisen, sachlichen Kommentar der:
- den aktuellen Bearbeitungsstand dokumentiert (basierend auf dem neuesten Kommentar)
- durchgeführte Schritte beschreibt
- nächste Schritte nennt (falls bekannt)
- ggf. auf wen gewartet wird (Pending-Informationen)

Ton: professionell, sachlich, präzise. Kein Fülltext. Max 200 Wörter.
Antworte NUR mit dem Kommentartext, kein zusätzlicher Text."""

RESOLUTION_SYSTEM = """Du bist ein erfahrener IT-Administrator und schreibst eine professionelle
Lösungsdokumentation für ein Jira-Ticket auf Deutsch.

Die Lösungsdokumentation soll enthalten:
1. Kurze Problembeschreibung (1-2 Sätze)
2. Ursache (Root Cause)
3. Durchgeführte Lösung (konkrete Schritte)
4. Lösungstyp: Dauerlösung oder Workaround
5. Empfehlungen zur Prävention (falls sinnvoll)

Ton: professionell, dokumentarisch, für andere Admins verständlich.
Antworte NUR mit der Dokumentation, kein zusätzlicher Text."""

CATEGORIZE_SYSTEM = """Analysiere das folgende IT-Ticket und bestimme:
1. Kategorie (eine aus der Liste)
2. Unterkategorie (frei)
3. Impact (high/medium/low) — Wie viele Benutzer/Systeme sind betroffen?
4. Urgency (high/medium/low) — Wie dringend ist eine Lösung?

Kategorien: Hardware, Software, Netzwerk, Sicherheit, E-Mail / Kommunikation,
Berechtigungen / Zugang, Backup / Storage, Monitoring / Alerting,
Server / Virtualisierung, Datenbank, Sonstiges

Antworte im JSON-Format:
{"category": "...", "subcategory": "...", "impact": "high|medium|low",
 "urgency": "high|medium|low", "reasoning": "..."}"""

SOLUTION_SEARCH_SYSTEM = """Du bist ein IT-Experte. Analysiere das beschriebene IT-Problem und:
1. Schlage konkrete Lösungsschritte vor (ITIL Best Practice)
2. Nenne mögliche Ursachen
3. Erstelle eine präzise deutsche Suchanfrage für die interne Wissensdatenbank

Antworte im JSON-Format:
{
  "possible_causes": ["..."],
  "solution_steps": ["Schritt 1", "Schritt 2", ...],
  "knowledge_query": "...",
  "needs_web_search": true|false
}"""

MAIL_EXTRACT_SYSTEM = """Analysiere diese IT-Support-E-Mail und extrahiere:
- Problem/Anfrage (kurz)
- Betroffener Benutzer / System
- Dringlichkeit
- Ob bereits Ticket vorhanden (falls Ticket-Key erwähnt)

Antworte im JSON-Format:
{
  "summary": "...",
  "affected_system": "...",
  "urgency": "high|medium|low",
  "mentioned_ticket": "PROJ-123 oder null",
  "suggested_title": "..."
}"""

RCA_5WHY_SYSTEM = """Du bist ein ITIL Problem Manager. Führe eine 5-Why-Analyse für das beschriebene IT-Problem durch.

Format:
{
  "why_1": {"question": "Warum ist X passiert?", "answer": "..."},
  "why_2": {"question": "Warum ...?", "answer": "..."},
  "why_3": {"question": "Warum ...?", "answer": "..."},
  "why_4": {"question": "Warum ...?", "answer": "..."},
  "why_5": {"question": "Warum ...?", "answer": "..."},
  "root_cause": "Kernursache in einem Satz",
  "corrective_action": "Empfohlene Dauerlösung"
}"""


async def _invoke_llm(
    llm_config: Any,
    system: str,
    user_content: str,
    *,
    lang: str | None = None,
) -> str:
    system_prompt = with_language(system, lang) if lang else system
    response = await generate_text(
        llm_config,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.3,
        reasoning_effort="low",
    )
    return response.strip()


async def generate_comment(
    llm_config: Any,
    ticket_title: str,
    ticket_description: str,
    work_notes: list[dict],
    comment_type: str = "progress",  # progress | pending | escalation | handoff
    additional_context: str | None = None,
    db: Any = None,
    lang: str | None = None,
) -> str:
    """Generate an ITIL-compliant ticket comment, enriched with it-aikb DeepSearch."""
    notes_text = "\n".join(
        f"[{n.get('timestamp', '')[:16]}] {n.get('author', 'User')}: {n.get('content', '')}"
        for n in (work_notes or [])[-10:]
    )
    type_hints = {
        "progress":   "Fortschrittsupdate — Was wurde bisher gemacht, was sind die nächsten Schritte",
        "pending":    "Pending-Vermerk — Auf wen/was wird gewartet (Kunde, Vendor, Change, etc.)",
        "escalation": "Eskalation — Warum wird eskaliert, an wen, was wurde bereits versucht",
        "handoff":    "Übergabekommentar — Zusammenfassung für die übernehmende Person",
    }

    # it-aikb DeepSearch for relevant infrastructure/runbook context (timeout: 180s — deepsearch takes ~2min)
    import asyncio
    kb_text = ""
    if db:
        try:
            from sqlalchemy import select as sa_select
            from app.models.connector import ConnectorConfig
            from app.core.security import decrypt_credentials
            conn_result = await db.execute(
                sa_select(ConnectorConfig).where(
                    ConnectorConfig.type == "it_aikb",
                    ConnectorConfig.enabled.is_(True),
                ).limit(1)
            )
            conn = conn_result.scalars().first()
            if conn:
                creds = decrypt_credentials(conn.encrypted_credentials)
                from app.services.connectors.it_aikb import ITAikbConnector
                svc = ITAikbConnector(base_url=conn.base_url, credentials=creds)
                results = await asyncio.wait_for(svc.deepsearch(ticket_title), timeout=180.0)
                if results:
                    snippets = "\n".join(
                        f"- {r.get('title', '')}: {(r.get('content') or r.get('text', ''))[:300]}"
                        for r in results[:5]
                    )
                    kb_text = f"\nWissensdatenbank-Kontext:\n{snippets}"
        except asyncio.TimeoutError:
            log.debug("generate_comment: it-aikb lookup timed out after 180s, skipping")
        except Exception as e:
            log.debug("generate_comment: it-aikb lookup failed: %s", e)

    context_block = f"\nAktuelle Entwicklungen (vom Bearbeiter angegeben):\n{additional_context}" if additional_context and additional_context.strip() else ""
    prompt = f"""Ticket: {ticket_title}
Verlauf (Beschreibung + Kommentare, neueste zuletzt):
{ticket_description}
Kommentartyp: {type_hints.get(comment_type, comment_type)}
Arbeitsnotizen:
{notes_text or '(keine Notizen)'}{kb_text}{context_block}

Schreibe jetzt den Kommentar basierend auf dem aktuellen Stand (letzter Kommentar im Verlauf):"""
    return await _invoke_llm(llm_config, COMMENT_SYSTEM, prompt, lang=lang)


async def generate_resolution(
    llm_config: Any,
    ticket_title: str,
    ticket_description: str,
    work_notes: list[dict],
    root_cause: str | None,
    resolution_type: str = "permanent",
    closure_code: str = "solved_permanently",
    lang: str | None = None,
) -> str:
    """Generate ITIL-compliant resolution/closing documentation."""
    notes_text = "\n".join(
        f"[{n.get('timestamp', '')[:16]}] {n.get('content', '')}"
        for n in (work_notes or [])
    )
    closure_map = {
        "solved_permanently":  "Dauerlösung implementiert",
        "solved_workaround":   "Workaround angewendet (temporäre Lösung)",
        "no_fault_found":      "Kein Fehler reproduzierbar",
        "duplicate":           "Duplikat — zusammengefasst mit anderem Ticket",
        "user_error":          "Benutzerfehler — Benutzer informiert und geschult",
        "cancelled":           "Storniert / nicht mehr relevant",
    }
    prompt = f"""Ticket: {ticket_title}
Problembeschreibung: {ticket_description}
Ursache (Root Cause): {root_cause or '(nicht angegeben)'}
Abschlusstyp: {closure_map.get(closure_code, closure_code)}
Lösungstyp: {'Dauerlösung' if resolution_type == 'permanent_fix' else 'Workaround'}
Durchgeführte Schritte:
{notes_text or '(keine Arbeitsnotizen)'}

Erstelle jetzt die Lösungsdokumentation:"""
    return await _invoke_llm(llm_config, RESOLUTION_SYSTEM, prompt, lang=lang)


async def auto_categorize(
    llm_config: Any,
    title: str,
    description: str,
    lang: str | None = None,
) -> dict:
    """Auto-categorize a ticket and suggest impact/urgency."""
    prompt = f"Ticket Titel: {title}\nBeschreibung: {description}"
    try:
        raw = await _invoke_llm(llm_config, CATEGORIZE_SYSTEM, prompt, lang=lang)
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        return json.loads(raw)
    except Exception as e:
        log.warning("auto_categorize failed: %s", e)
        return {"category": "Sonstiges", "subcategory": "", "impact": "medium", "urgency": "medium"}


async def suggest_solution(
    llm_config: Any,
    db: Any,
    title: str,
    description: str,
    use_rag: bool = True,
    use_web: bool = True,
    lang: str | None = None,
) -> dict:
    """Search for solutions using LLM + RAG + optional SearXNG web search."""
    prompt = f"Problem: {title}\nDetails: {description}"
    try:
        raw = await _invoke_llm(llm_config, SOLUTION_SEARCH_SYSTEM, prompt, lang=lang)
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        plan = json.loads(raw)
    except Exception as e:
        log.warning("suggest_solution planning failed: %s", e)
        plan = {"possible_causes": [], "solution_steps": [], "knowledge_query": title, "needs_web_search": False}

    rag_results = []
    web_results = []

    if use_rag and plan.get("knowledge_query"):
        from sqlalchemy import select
        from app.core.security import decrypt_credentials
        from app.models.connector import ConnectorConfig
        result = await db.execute(
            select(ConnectorConfig).where(
                ConnectorConfig.type == "it_aikb",
                ConnectorConfig.enabled.is_(True),
            )
        )
        conn = result.scalars().first()
        if conn:
            creds = decrypt_credentials(conn.encrypted_credentials)
            from app.services.connectors.it_aikb import ITAikbConnector
            svc = ITAikbConnector(base_url=conn.base_url, credentials=creds)
            try:
                rag_results = await svc.search(plan["knowledge_query"], top_k=5)
            except Exception as e:
                log.warning("suggest_solution RAG failed: %s", e)

    if use_web and plan.get("needs_web_search"):
        from app.services.settings import get_searxng_config
        searxng = await get_searxng_config(db)
        if searxng.is_configured:
            import httpx
            try:
                async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
                    r = await client.get(
                        f"{searxng.base_url}/search",
                        params={"q": plan["knowledge_query"], "format": "json"},
                    )
                    if r.status_code == 200:
                        web_results = [
                            {"title": x.get("title"), "url": x.get("url"), "content": x.get("content", "")[:200]}
                            for x in r.json().get("results", [])[:searxng.results_count]
                        ]
            except Exception as e:
                log.warning("suggest_solution SearXNG failed: %s", e)

    return {
        "possible_causes": plan.get("possible_causes", []),
        "solution_steps": plan.get("solution_steps", []),
        "rag_results": rag_results,
        "web_results": web_results,
    }


async def analyze_mail(
    llm_config: Any,
    subject: str,
    preview: str,
    lang: str | None = None,
) -> dict:
    """Extract structured info from an IT support email."""
    prompt = f"Betreff: {subject}\nNachrichtenvorschau: {preview[:600]}"
    try:
        raw = await _invoke_llm(llm_config, MAIL_EXTRACT_SYSTEM, prompt, lang=lang)
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        return json.loads(raw)
    except Exception as e:
        log.warning("analyze_mail failed: %s", e)
        return {"summary": subject, "urgency": "medium", "mentioned_ticket": None, "suggested_title": subject}


JQL_SYSTEM = """Du bist ein Jira-Experte und wandelst natürlichsprachige Beschreibungen in valide Jira JQL-Queries um.

Antworte im JSON-Format:
{
  "jql": "...",
  "name": "Kurzer Name für die Query (max 50 Zeichen)",
  "explanation": "Was diese Query macht"
}

Wichtige JQL-Syntax:
- assignee = currentUser()  — aktuell eingeloggter Benutzer
- statusCategory != Done  — NIEMALS status != Done, nur statusCategory verwenden (sprachunabhängig)
- statusCategory in (new, indeterminate)  — für offene Tickets
- priority in (Kritisch, Hoch, Normal, Niedrig)  — deutsche Namen, NIEMALS englische (Highest, High, Medium, Low)
- created >= -7d  /  updated >= startOfDay()
- project = "IMIT"  — spezifisches Projekt
- issuetype in (Bug, Task, Story)
- summary ~ "suchbegriff"  — Textsuche (NIEMALS summary = "...")
- ORDER BY updated DESC, priority ASC"""


async def generate_jql(llm_config: Any, description: str, lang: str | None = None) -> dict:
    """Generate a Jira JQL query from a natural language description."""
    prompt = f"Beschreibung: {description}"
    try:
        raw = await _invoke_llm(llm_config, JQL_SYSTEM, prompt, lang=lang)
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        return json.loads(raw)
    except Exception as e:
        log.warning("generate_jql failed: %s", e)
        safe = description.replace('"', '\\"')
        return {
            "jql": f'summary ~ "{safe}" ORDER BY updated DESC',
            "name": description[:50],
            "explanation": "Fallback-Textsuche",
        }


_EXCLUSION_SYSTEM = """Du bist ein OpenSearch/Lucene-Query-Experte.

Analysiere eine Monitoring-Meldung und erstelle eine OpenSearch Lucene-Ausschluss-Query,
die ähnliche Meldungen dauerhaft ausblendet — ohne zu viele andere Meldungen zu blockieren.

Regeln:
- Nutze bevorzugt das `title`-Feld (body ist oft leer bei kurzen Meldungen)
- Wähle charakteristische Phrasen oder Muster aus dem Titel (z.B. "[php-fpm:access]", "cci:ccitext")
- Bei Container-spezifischen Logs: `metadata.container_name:"name"` mit einschließen wenn sinnvoll
- Nicht zu breit (NICHT nur source:graylog oder severity:info)
- Nicht zu eng (kein Timestamp, keine exakten numerischen Werte)
- Antworte im JSON-Format: {"query": "...", "name": "Kurzer Name (max 60 Zeichen)"}
- Antworte NUR mit dem JSON, kein Markdown, keine Erklärung"""


async def generate_exclusion_query(
    llm_config: Any,
    item: dict,
    lang: str | None = None,
) -> dict:
    """Generate an OpenSearch exclusion query for a feed item using the LLM."""
    source = item.get("source", "")
    title = item.get("title", "")
    body = (item.get("body") or "")[:300]
    metadata = item.get("metadata") or {}
    container = metadata.get("container_name", "")
    host = metadata.get("host", "") or metadata.get("agent", "")

    prompt_parts = [f"Source: {source}", f"Title: {title}"]
    if body and body != title:
        prompt_parts.append(f"Body: {body[:200]}")
    if container:
        prompt_parts.append(f"Container: {container}")
    if host:
        prompt_parts.append(f"Host: {host}")

    try:
        raw = await _invoke_llm(llm_config, _EXCLUSION_SYSTEM, "\n".join(prompt_parts), lang=lang)
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        result = json.loads(raw)
        return {"query": result.get("query", ""), "name": result.get("name", title[:60])}
    except Exception as e:
        log.warning("generate_exclusion_query failed: %s", e)
        safe = title.replace('"', '\\"')[:100]
        return {"query": f'title:"{safe}"', "name": f"Ignoriert: {title[:50]}"}


async def run_5why_analysis(
    llm_config: Any,
    title: str,
    description: str,
    work_notes: list[dict] | None = None,
    lang: str | None = None,
) -> dict:
    """ITIL Problem Management: 5-Why root cause analysis."""
    notes_text = "\n".join(n.get("content", "") for n in (work_notes or [])[-5:])
    prompt = f"""Problembeschreibung: {title}
Details: {description}
Arbeitsnotizen: {notes_text or '(keine)'}

Führe eine 5-Why-Analyse durch:"""
    try:
        raw = await _invoke_llm(llm_config, RCA_5WHY_SYSTEM, prompt, lang=lang)
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        return json.loads(raw)
    except Exception as e:
        log.warning("5why analysis failed: %s", e)
        return {"root_cause": "Analyse nicht verfügbar", "error": str(e)}
