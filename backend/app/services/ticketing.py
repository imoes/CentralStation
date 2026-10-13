"""Shared ticket-creation logic for Jira and Jira Service Desk.

Centralises what used to live inline in app/api/feed.py so both the News Feed
and the Computer Console create tickets through the exact same path:

  - resolve_jira_connector()  → pick the user's jira / jira_sd connector
  - ai_ticket_draft()         → LLM formulates {summary, description, priority}
  - create_jira_issue()       → create the issue, return {ok, jira_key, url}

Both connector types use the same JiraConnector class — they only differ by
base_url / project / priority names of the target instance.
"""
from __future__ import annotations

import json as _json
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# Severity → German Jira priority names (matches the IMIT/IMSP instances).
SEVERITY_PRIORITY_MAP = {
    "critical": "Kritisch",
    "high": "Hoch",
    "medium": "Normal",
    "low": "Niedrig",
    "info": "Niedrig",
}
DEFAULT_PRIORITIES = ["Kritisch", "Hoch", "Normal", "Niedrig"]


async def resolve_jira_connector(db: AsyncSession, user_id, connector_type: str = "jira"):
    """Return the user's Jira connector of the given type (personal preferred, else global).

    connector_type: "jira" (regular Jira, e.g. IMSP) or "jira_sd" (ServiceDesk, e.g. IMIT).
    """
    from app.models.connector import ConnectorConfig

    r = await db.execute(
        select(ConnectorConfig)
        .where(
            ConnectorConfig.type == connector_type,
            ConnectorConfig.enabled.is_(True),
            ((ConnectorConfig.owner_user_id == user_id) | ConnectorConfig.owner_user_id.is_(None)),
        )
        .order_by(ConnectorConfig.owner_user_id.is_(None), ConnectorConfig.updated_at.desc())
        .limit(1)
    )
    return r.scalar_one_or_none()


async def ai_ticket_draft(
    db: AsyncSession,
    user,
    context: str,
    severity_hint: str = "",
) -> dict:
    """LLM formulates a professional ticket from a free-form context string.

    `context` may be a monitoring-alert summary OR a Computer-Console transcript.
    Returns {summary, description, priority}. Falls back to a template when the
    LLM is unavailable or returns unparseable output.
    """
    severity = (severity_hint or "").strip().lower()
    priority = SEVERITY_PRIORITY_MAP.get(severity, "Normal")

    summary = ""
    description = ""
    try:
        from app.services.settings import get_active_llm_config
        from app.services.ai_language import with_language, get_response_language_for_user
        from app.services.llm_client import generate_text
        from app.services.dashboard.generative_designer import _strip_thinking

        llm_cfg = await get_active_llm_config(db)
        if llm_cfg.is_configured:
            lang = await get_response_language_for_user(db, user.id)
            system_prompt = with_language(
                "You are an IT operations engineer creating a Jira ticket. "
                "Produce a concise, professional ticket from the provided context "
                "(which may be a monitoring alert or a support-chat transcript). "
                "Return STRICT JSON only: "
                '{"summary": "<one line, max 120 chars>", "description": "<structured sections using '
                "Jira wiki markup (*bold*, h3. headings, ---- separators): "
                "h3. Problem | what failed, symptoms, affected service. "
                "h3. Betroffenes System | host/service/component. "
                "h3. Ursache | root cause if identified, else 'Wird untersucht'. "
                "h3. Belege | PFLICHT — kopiere ALLE vollständigen Log-Zeilen, Fehlermeldungen, "
                "Stack-Traces, Metriken und Zeitstempel aus dem Kontext WORTGENAU und VOLLSTÄNDIG. "
                "Kürze, paraphrasiere oder lasse KEINE einzige Log-Zeile weg — jede Log-Zeile "
                "muss exakt so erscheinen wie im Kontext. Formatiere mehrzeilige Log-Ausgaben mit "
                "{{{...}}} (Jira-Codeblock). Lasse diese Sektion NIEMALS leer wenn Belege existieren. "
                "h3. Lösungsvorschlag | concrete fix or next steps proposed in the conversation — "
                "this section is REQUIRED and must reflect what the assistant actually recommended. "
                "h3. Durchgeführte Schritte | what was already tried or investigated. "
                "No invented facts. Every claim in Ursache and Lösungsvorschlag must be backed by "
                "something in Belege — no speculation without data.>\"}. "
                "The 'log source' field names the collector (Graylog/CheckMK), NOT the failing system. "
                # Stilregel für alles, was in Tickets landet (Menschen, oft Externe, lesen es):
                "Write like a colleague documenting the case. NEVER mention how information "
                "was obtained or the state of the tooling: no SSH, tools, connectors, commands "
                "used for diagnosis, read-only mode, check containers, recipes, 'AI analysis', "
                "'KI-Einschätzung', confidence values or automatic detection internals. State "
                "unclear points factually ('no log entries on the host') or leave them out.",
                lang,
            )
            raw = await generate_text(
                llm_cfg,
                [{"role": "system", "content": system_prompt},
                 {"role": "user", "content": context}],
            )
            clean = _strip_thinking(raw)
            lo, hi = clean.find("{"), clean.rfind("}")
            if lo >= 0 and hi > lo:
                try:
                    data = _json.loads(clean[lo:hi + 1])
                    summary = (data.get("summary") or "").strip()
                    description = (data.get("description") or "").strip()
                except Exception:
                    log.warning("ai_ticket_draft: AI returned unparseable JSON, using fallback")
    except Exception as e:
        log.warning("ai_ticket_draft: AI prefill failed, using template fallback: %s", e)

    if not summary:
        first_line = (context or "").strip().splitlines()[0] if context.strip() else "Incident"
        summary = first_line[:120]
    if not description:
        description = (context or "").strip()[:2000]

    return {"summary": summary, "description": description, "priority": priority}


def incident_link_appended(description: str, url: str) -> str:
    """Den Link zum Incident ans Ende der Beschreibung — einmal, Jira-Wiki-Syntax."""
    if url in description:
        return description
    return f"{description}\n\n----\nVorfall in CentralStation: [{url}]".strip()


def incident_tickets(inc) -> list[dict]:
    """Aus einem Incident erstellte Tickets (causal_context, type "ticket"), älteste zuerst."""
    return [e for e in (inc.causal_context or []) if isinstance(e, dict) and e.get("type") == "ticket"]


def pick_assignee(candidates: list[dict], query: str) -> tuple[str | None, str]:
    """Bearbeiter aus den zuweisbaren Personen wählen: (Login, Fehlergrund).

    Exakter Treffer auf Login, E-Mail oder Anzeigename (ohne Groß/Klein) gewinnt; sonst
    gilt nur ein EINZIGER Kandidat. Mehrere ohne exakten Treffer → kein Raten, sondern
    die Kandidaten im Grund — "Scholtysik" darf nicht still beim falschen Joachim landen.
    """
    q = (query or "").strip().casefold()
    if not q:
        return None, ""
    for c in candidates:
        if q in {(c.get("name") or "").casefold(), (c.get("email") or "").casefold(),
                 (c.get("display_name") or "").casefold()}:
            return c["name"], ""
    if len(candidates) == 1:
        return candidates[0]["name"], ""
    if not candidates:
        return None, f"Kein zuweisbarer Bearbeiter für „{query}“ in diesem Projekt."
    names = ", ".join(f"{c['display_name']} ({c['name']})" for c in candidates[:8])
    return None, f"„{query}“ ist mehrdeutig: {names}. Bitte den Login angeben."


def pick_issue_type(available: list[str], wanted: str | None,
                    preferred: tuple[str, ...] = ("Aufgabe", "Task", "Serviceanfrage",
                                                 "Projektticket")) -> tuple[str | None, str]:
    """Vorgangstyp, den es im Projekt wirklich gibt: (exakter Jira-Name, Hinweis).

    Verglichen wird ohne Groß/Klein und Leerzeichen am Rand — IMSP führt "Projektticket "
    mit Leerzeichen —, gesendet wird der exakte Name. Fehlt der gewünschte Typ, wird
    der erste bevorzugte genommen und das im Hinweis gesagt, statt still.
    """
    norm = {t.strip().casefold(): t for t in available}
    if wanted and wanted.strip().casefold() in norm:
        return norm[wanted.strip().casefold()], ""
    for p in preferred:
        if p.casefold() in norm:
            hint = (f"Vorgangstyp „{wanted}“ gibt es hier nicht — angelegt als „{norm[p.casefold()].strip()}“."
                    if wanted else "")
            return norm[p.casefold()], hint
    if available:
        hint = f"Vorgangstyp „{wanted}“ gibt es hier nicht — angelegt als „{available[0].strip()}“." if wanted else ""
        return available[0], hint
    return wanted, ""


async def create_jira_issue(
    db: AsyncSession,
    user,
    connector_type: str,
    project: str,
    summary: str,
    description: str,
    priority: str = "Normal",
    issue_type: str = "Serviceanfrage",
    labels: list[str] | None = None,
    assignee: str | None = None,
) -> dict:
    """Create a Jira/ServiceDesk issue in the chosen connector.

    Resolves the requested connector_type; falls back to the other Jira type if
    the requested one isn't configured. Builds the browse URL (create_issue does
    NOT return one). Returns {ok, jira_key, url}.
    """
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector

    conn = await resolve_jira_connector(db, user.id, connector_type)
    if not conn:
        other = "jira" if connector_type == "jira_sd" else "jira_sd"
        conn = await resolve_jira_connector(db, user.id, other)
    if not conn:
        return {"ok": False, "error": "Kein Jira-Connector konfiguriert"}

    creds = decrypt_credentials(conn.encrypted_credentials)
    jira = JiraConnector(base_url=conn.base_url, credentials=creds)

    # Vorgangstyp: nur einen nehmen, den es im Projekt gibt (IMSP kennt z. B. keine
    # "Serviceanfrage", nur "Projektticket ").
    notes: list[str] = []
    try:
        types = await jira.project_issue_types(project)
    except Exception as e:
        log.debug("create_jira_issue: issue types for %s unavailable: %s", project, e)
        types = []
    if types:
        issue_type, hint = pick_issue_type(types, issue_type)
        if hint:
            notes.append(hint)

    # Bearbeiter: nur unter den in diesem Projekt zuweisbaren Personen auflösen.
    assignee_login: str | None = None
    if assignee:
        try:
            candidates = await jira.search_assignable(project, assignee)
        except Exception as e:
            return {"ok": False, "error": f"Bearbeitersuche fehlgeschlagen: {str(e)[:150]}"}
        assignee_login, err = pick_assignee(candidates, assignee)
        if err:
            return {"ok": False, "error": err,
                    "candidates": [{"login": c["name"], "name": c["display_name"]} for c in candidates[:8]]}

    async def _create(prio: str, send_labels: bool = True) -> dict:
        return await jira.create_issue(
            project=project,
            summary=summary[:200],
            description=description,
            issue_type=issue_type,
            priority=prio,
            labels=(labels or ["centralstation"]) if send_labels else None,
            assignee=assignee_login,
        )

    try:
        result = await _create(priority)
    except Exception as e:
        # Priority or labels may not be on the Jira screen → retry without both.
        log.warning("create_jira_issue failed (%s) — retrying without priority/labels", e)
        try:
            result = await _create("", send_labels=False)
        except Exception as e2:
            log.warning("create_jira_issue retry failed: %s", e2)
            return {"ok": False, "error": str(e2)[:200]}

    jira_key = result.get("key", "")
    base = (conn.base_url or "").rstrip("/")
    url = f"{base}/browse/{jira_key}" if jira_key and base else None
    out = {"ok": True, "jira_key": jira_key, "url": url, "issue_type": (issue_type or "").strip()}
    if assignee_login:
        out["assignee"] = assignee_login
    if notes:
        out["note"] = " ".join(notes)
    return out


#: Wie Nutzer das Ziel nennen → Connector-Typ. "ServiceDesk" ist NICHT Jira, auch wenn
#: beides Jira-Software ist — wer es sagt, meint die andere Instanz.
TARGET_ALIASES = {
    "jira": "jira",
    "servicedesk": "jira_sd", "service desk": "jira_sd", "service-desk": "jira_sd",
    "sd": "jira_sd", "jsd": "jira_sd", "jira_sd": "jira_sd",
}


def normalize_target(target: str | None) -> tuple[str | None, str]:
    """Genanntes Ziel → ("jira" | "jira_sd" | None, Fehlergrund)."""
    if not target or not target.strip():
        return None, ""
    t = TARGET_ALIASES.get(target.strip().casefold())
    if t is None:
        return None, f"Unbekanntes Ziel „{target}“ — erlaubt: Jira oder ServiceDesk."
    return t, ""


async def projects_on_target(db: AsyncSession, user, target: str) -> list[str]:
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector
    conn = await resolve_jira_connector(db, user.id, target)
    if not conn:
        return []
    jira = JiraConnector(base_url=conn.base_url, credentials=decrypt_credentials(conn.encrypted_credentials))
    return sorted(p["key"] for p in await jira.list_projects())


async def find_connector_for_project(db: AsyncSession, user, project: str,
                                     preferred: str | None = None) -> tuple[str | None, str]:
    """Auf welcher Instanz liegt `project`? ("jira" | "jira_sd", Grund bei None).

    IMSP liegt nur auf Jira, IMIT im ServiceDesk — wer nur den Projektschlüssel nennt,
    soll nicht wissen müssen, wo. `preferred` wird zuerst geprüft.
    """
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector
    # Ein GENANNTES Ziel ist bindend: "ServiceDesk" heißt ServiceDesk — kein stilles
    # Ausweichen auf Jira, nur weil das Projekt dort existiert.
    order = [preferred] if preferred in ("jira", "jira_sd") else ["jira", "jira_sd"]
    for ctype in order:
        conn = await resolve_jira_connector(db, user.id, ctype)
        if not conn:
            continue
        jira = JiraConnector(base_url=conn.base_url, credentials=decrypt_credentials(conn.encrypted_credentials))
        try:
            if await jira.has_project(project):
                return ctype, ""
        except Exception as e:
            log.debug("find_connector_for_project: %s on %s failed: %s", project, ctype, e)
    if preferred in ("jira", "jira_sd"):
        label = "im ServiceDesk" if preferred == "jira_sd" else "in Jira"
        try:
            keys = await projects_on_target(db, user, preferred)
        except Exception:
            keys = []
        return None, (f"Projekt {project} gibt es {label} nicht."
                      + (f" Verfügbar {label}: {', '.join(keys[:40])}." if keys else ""))
    return None, f"Projekt {project} gibt es auf keiner verbundenen Jira-Instanz."


# ── Tickets suchen (Jira + ServiceDesk) ──────────────────────────────────────

INSTANCE_LABEL = {"jira": "Jira", "jira_sd": "ServiceDesk"}
_ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")
_SEARCH_FIELDS = ["summary", "status", "priority", "assignee", "reporter", "issuetype",
                  "created", "updated", "description"]


def build_search_jql(query: str, project: str | None = None, open_only: bool = False) -> str:
    """Freitext → JQL. Ein Ticketschlüssel (IMIT-123) wird direkt gesucht."""
    q = (query or "").strip()
    clauses: list[str] = []
    if q and _ISSUE_KEY.match(q.upper()):
        clauses.append(f"issuekey = {q.upper()}")
    elif q:
        esc = q.replace("\\", "\\\\").replace('"', '\\"')
        clauses.append(f'text ~ "{esc}"')
    if project:
        clauses.append(f'project = "{project.upper()}"')
    if open_only:
        clauses.append("statusCategory != Done")
    if not clauses:
        raise ValueError("Suchbegriff, Projekt oder 'nur offene' angeben")
    return " AND ".join(clauses) + " ORDER BY updated DESC"


def snippet(text: str | None, query: str, width: int = 140) -> str:
    """Textstelle um den ersten Suchbegriff — damit man sieht, WARUM ein Ticket passt."""
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t:
        return ""
    words = [w for w in re.split(r"\W+", query or "") if len(w) > 2]
    pos = min((t.lower().find(w.lower()) for w in words if t.lower().find(w.lower()) >= 0), default=-1)
    if pos < 0:
        return t[:width] + ("…" if len(t) > width else "")
    a = max(0, pos - width // 2)
    return ("…" if a else "") + t[a:a + width] + ("…" if a + width < len(t) else "")


async def search_tickets(db: AsyncSession, user_id, query: str, *, target: str | None = None,
                         project: str | None = None, open_only: bool = False,
                         max_results: int = 20) -> dict:
    """In Jira UND ServiceDesk suchen (oder in einem genannten Ziel).

    Ein Projekt wird nur dort gesucht, wo es existiert — sonst lieferte die andere Instanz
    eine JQL-Fehlermeldung ("Wert 'IMIT' existiert nicht"), die wie ein Problem aussieht.
    """
    from app.core.security import decrypt_credentials
    from app.services.connectors.jira import JiraConnector

    wanted, err = normalize_target(target)
    if err:
        return {"ok": False, "error": err}
    jql = build_search_jql(query, project, open_only)
    results: list[dict] = []
    notes: list[str] = []
    searched: list[str] = []
    for ctype in ([wanted] if wanted else ["jira", "jira_sd"]):
        conn = await resolve_jira_connector(db, user_id, ctype)
        label = INSTANCE_LABEL[ctype]
        if not conn:
            if wanted:
                notes.append(f"Kein {label}-Connector konfiguriert")
            continue
        jira = JiraConnector(base_url=conn.base_url, credentials=decrypt_credentials(conn.encrypted_credentials))
        if project and not await jira.has_project(project.upper()):
            notes.append(f"Projekt {project.upper()} gibt es nicht in {label}")
            continue
        searched.append(label)
        try:
            issues = await jira.search_issues(jql, fields=_SEARCH_FIELDS)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{label}: {str(exc)[:160]}")
            continue
        base = conn.base_url.rstrip("/")
        for i in issues:
            f = i.get("fields") or {}
            desc = f.get("description")
            results.append({
                "key": i.get("key"), "summary": f.get("summary"),
                "status": (f.get("status") or {}).get("name"),
                "type": (f.get("issuetype") or {}).get("name"),
                "priority": (f.get("priority") or {}).get("name"),
                "assignee": (f.get("assignee") or {}).get("displayName"),
                "reporter": (f.get("reporter") or {}).get("displayName"),
                "created": f.get("created"), "updated": f.get("updated"),
                "instance": label, "url": f"{base}/browse/{i.get('key')}",
                "match": snippet(desc if isinstance(desc, str) else f.get("summary"), query),
            })
    results.sort(key=lambda r: r.get("updated") or "", reverse=True)
    n = max(1, min(50, max_results))
    return {"ok": bool(searched) or bool(results), "jql": jql, "searched": searched,
            "count": len(results[:n]), "total_found": len(results), "issues": results[:n],
            **({"notes": notes} if notes else {})}
