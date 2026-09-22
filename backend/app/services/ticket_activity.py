"""Pure helpers for Jira ticket snapshots and Computer Console activity diffs."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from app.services.connectors.jira import wiki_to_markdown


SNAPSHOT_VERSION = 1


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _normalised_user_ids(values: Any) -> set[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return set()
    return {_text(value).casefold() for value in values if _text(value)}


def build_ticket_snapshot(detail: dict) -> dict:
    """Return a bounded, JSON-serialisable snapshot without comment bodies."""
    comments: dict[str, dict[str, Any]] = {}
    for comment in detail.get("comments") or []:
        comment_id = _text(comment.get("id"))
        if not comment_id:
            continue
        body = _text(comment.get("body"))
        comments[comment_id] = {
            "created": _text(comment.get("created")),
            "updated": _text(comment.get("updated") or comment.get("created")),
            "body_hash": _sha256(body),
            "author_ids": list(comment.get("author_ids") or []),
        }

    return {
        "version": SNAPSHOT_VERSION,
        "issue_id": _text(detail.get("id") or detail.get("key")),
        "issue_key": _text(detail.get("key")),
        "issue_updated_at": _text(detail.get("updated")),
        "fields": {
            "summary": _text(detail.get("summary")),
            "description_hash": _sha256(_text(detail.get("description"))),
            "status": _text(detail.get("status")),
            "priority": _text(detail.get("priority")),
            "assignee": _text(detail.get("assignee")),
        },
        "comments": comments,
    }


def valid_ticket_snapshot(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("version") != SNAPSHOT_VERSION:
        return False
    if not isinstance(value.get("fields"), dict) or not isinstance(value.get("comments"), dict):
        return False
    return all(
        isinstance(comment_id, str) and isinstance(meta, dict)
        for comment_id, meta in value["comments"].items()
    )


def diff_ticket_activity(
    previous: dict | None,
    current: dict,
    detail: dict,
    *,
    synced_at: datetime | None = None,
    current_user_ids: set[str] | list[str] | None = None,
) -> dict:
    """Compare a persisted snapshot with live Jira detail.

    Legacy sessions have no snapshot. For them, ``synced_at`` is the last known
    local handoff time and prevents the existing ticket history from appearing as
    unread after the migration.
    """
    previous_valid = valid_ticket_snapshot(previous)
    old_comments = previous.get("comments", {}) if previous_valid else {}
    current_comments = current.get("comments", {})
    comments_by_id = {
        _text(comment.get("id")): comment
        for comment in (detail.get("comments") or [])
        if _text(comment.get("id"))
    }
    own_ids = _normalised_user_ids(current_user_ids)

    def is_own_comment(comment_id: str, source: dict[str, dict] | None = None) -> bool:
        comment = comments_by_id.get(comment_id)
        author_ids = (comment or {}).get("author_ids")
        if not author_ids and source:
            author_ids = (source.get(comment_id) or {}).get("author_ids")
        return bool(own_ids & _normalised_user_ids(author_ids))

    raw_new_ids: list[str] = []
    raw_edited_ids: list[str] = []
    raw_deleted_ids: list[str] = []

    if previous_valid:
        raw_new_ids = [comment_id for comment_id in current_comments if comment_id not in old_comments]
        raw_edited_ids = [
            comment_id for comment_id, meta in current_comments.items()
            if comment_id in old_comments and (
                meta.get("updated") != old_comments[comment_id].get("updated")
                or meta.get("body_hash") != old_comments[comment_id].get("body_hash")
            )
        ]
        raw_deleted_ids = [comment_id for comment_id in old_comments if comment_id not in current_comments]
    elif synced_at:
        for comment_id, meta in current_comments.items():
            created = _parse_timestamp(meta.get("created"))
            updated = _parse_timestamp(meta.get("updated"))
            if created and created > synced_at:
                raw_new_ids.append(comment_id)
            elif updated and updated > synced_at:
                raw_edited_ids.append(comment_id)

    # The Jira identity comes from /myself, which is the same account represented
    # by currentUser() in JQL. Its own writes already exist in the console transcript
    # and therefore must not return as unread inbound messages.
    new_ids = [comment_id for comment_id in raw_new_ids if not is_own_comment(comment_id)]
    edited_ids = [comment_id for comment_id in raw_edited_ids if not is_own_comment(comment_id)]
    deleted_ids = [
        comment_id for comment_id in raw_deleted_ids
        if not is_own_comment(comment_id, old_comments)
    ]

    field_labels = {
        "summary": "Titel",
        "description_hash": "Beschreibung",
        "status": "Status",
        "priority": "Priorität",
        "assignee": "Zuweisung",
    }
    field_changes: list[dict[str, str | None]] = []
    if previous_valid:
        old_fields = previous.get("fields") or {}
        for field, label in field_labels.items():
            before = old_fields.get(field)
            after = current["fields"].get(field)
            if before == after:
                continue
            field_changes.append({
                "field": field,
                "label": label,
                "before": None if field == "description_hash" else _text(before),
                "after": None if field == "description_hash" else _text(after),
            })

    new_comments = [comments_by_id[comment_id] for comment_id in new_ids if comment_id in comments_by_id]
    edited_comments = [comments_by_id[comment_id] for comment_id in edited_ids if comment_id in comments_by_id]

    # With no structured baseline, Jira's issue timestamp still tells us that a
    # non-comment field changed since the old console context was created.
    generic_ticket_change = False
    if not previous_valid and synced_at:
        issue_updated = _parse_timestamp(current.get("issue_updated_at"))
        generic_ticket_change = bool(
            issue_updated and issue_updated > synced_at
            and not raw_new_ids and not raw_edited_ids and not raw_deleted_ids
        )

    changed = bool(new_comments or edited_comments or deleted_ids or field_changes or generic_ticket_change)
    return {
        "state": "changed" if changed else "current",
        "comment_change_count": len(new_comments) + len(edited_comments) + len(deleted_ids),
        "new_comments": new_comments,
        "edited_comments": edited_comments,
        "deleted_comment_ids": deleted_ids,
        "field_changes": field_changes,
        "ticket_changed": bool(field_changes or generic_ticket_change),
    }


#: Die Aufgabe, die der Konsole beim Übergeben eines Tickets ins Eingabefeld gelegt
#: wird. Getrennt vom Ticketinhalt, weil beide verschiedenen Zwecken dienen: der Inhalt
#: ist Kontext (hängt an der Nachricht), das hier ist die Bitte an die KI — und die
#: gehört sichtbar ins Eingabefeld, wo der Nutzer sie lesen und ändern kann, bevor er
#: sie abschickt. Vorher steckte sie im Kontext und war damit unsichtbar eingeklappt.
TICKET_TASK_PROMPT = (
    "Analysiere das Ticket und schlage konkrete nächste Schritte vor. Für Recherche "
    "im Bestand nutze die CentralStation-Werkzeuge (Feed, CheckMK, Wissensdatenbank). "
    "Am Ticket selbst kannst du mit `jira_add_comment`, `jira_update_issue` und "
    "`jira_transition_issue` arbeiten — frage vorher nach, bevor du etwas schreibst "
    "oder den Status änderst."
)

#: Stilvorgabe für Ticketkommentare. Bleibt beim Kontext, nicht im Eingabefeld: sie ist
#: Dauerregel für die KI, nichts, was der Nutzer jedes Mal mitschicken möchte.
TICKET_STYLE_NOTE = (
    "Was du ins Ticket schreibst, lesen Menschen — oft auch Externe. Formuliere in "
    "Fließtext wie ein Kollege, der den Vorgang fortschreibt: kurze Absätze, keine "
    "Stichpunktlisten, keine Überschriften, keine Statusmarker. Wie du an die "
    "Information gekommen bist, gehört nicht hinein — also keine Werkzeug- oder "
    "SSH-Erwähnungen und keine Aussagen über deinen eigenen Betriebszustand "
    "(\"Read-only-Analyse\", \"kein Zugriff erlangt\", \"KI-Analyse\"). Konntest du "
    "etwas nicht klären, sag es fachlich oder lass es weg."
)


def build_ticket_context(detail: dict, issue_key: str | None = None) -> str:
    """Nur der Ticketinhalt — Kopfdaten, Beschreibung, Kommentarverlauf, Stilvorgabe.

    Das ist der Teil, der als Kontext an der Nachricht hängt. Die Aufgabe steht in
    TICKET_TASK_PROMPT und landet im Eingabefeld.
    """
    return _ticket_body(detail, issue_key) + "\n\n---\n" + TICKET_STYLE_NOTE


def build_full_ticket_prompt(detail: dict, issue_key: str | None = None) -> str:
    """Ticketinhalt UND Aufgabe in einem Text.

    Weiterhin die Grundlage für den Kontext-Hash und für Aufrufer, die einen einzigen
    fertigen Prompt erwarten — an dieser Zeichenkette hängt die Erkennung, ob ein
    Stand schon übergeben wurde, deshalb bleibt sie unverändert.
    """
    return (_ticket_body(detail, issue_key) + "\n\n---\n" + TICKET_TASK_PROMPT
            + "\n\n" + TICKET_STYLE_NOTE)


def _ticket_body(detail: dict, issue_key: str | None = None) -> str:
    """Kopfdaten, Beschreibung und Kommentarverlauf des Tickets."""
    key = detail.get("key") or issue_key or "?"
    summary = _text(detail.get("summary"))
    description = wiki_to_markdown(_text(detail.get("description")), heading_offset=1)
    lines = [
        f"Bearbeite das Ticket **{key}**: {summary or '(kein Titel)'}",
        "",
        f"- **Status:** {detail.get('status') or '?'}",
        f"- **Priorität:** {detail.get('priority') or '?'}",
        f"- **Zugewiesen an:** {detail.get('assignee') or '(niemand)'}",
        f"- **Aktualisiert:** {_text(detail.get('updated'))[:16]}",
        "",
        "## Beschreibung",
        description or "(keine Beschreibung hinterlegt)",
    ]

    comments = detail.get("comments") or []
    if comments:
        shown = comments[-15:]
        omitted = len(comments) - len(shown)
        lines += ["", f"## Verlauf ({len(comments)} Kommentare"
                      + (f", die {omitted} ältesten ausgelassen" if omitted else "") + ")"]
        for comment in shown:
            body = wiki_to_markdown(_text(comment.get("body")), heading_offset=2)
            lines.append(
                f"\n**{comment.get('author') or '?'}** "
                f"({_text(comment.get('created'))[:16]}):\n{body}"
            )

    return "\n".join(lines)


def build_ticket_activity_prompt(detail: dict, activity: dict) -> str:
    """Build a compact delta prompt; unchanged description/history is omitted."""
    key = detail.get("key") or "?"
    lines = [
        f"Neue Aktivität zum Ticket **{key}** seit dem zuletzt übernommenen Stand:",
        "",
        f"- **Aktueller Status:** {detail.get('status') or '?'}",
        f"- **Priorität:** {detail.get('priority') or '?'}",
        f"- **Zugewiesen an:** {detail.get('assignee') or '(niemand)'}",
        f"- **Aktualisiert:** {_text(detail.get('updated'))[:16]}",
    ]

    for change in activity.get("field_changes") or []:
        label = change.get("label") or change.get("field")
        if change.get("field") == "description_hash":
            lines.append(f"- **{label}:** wurde geändert")
        else:
            lines.append(
                f"- **{label}:** {change.get('before') or '(leer)'} → "
                f"{change.get('after') or '(leer)'}"
            )
    if activity.get("ticket_changed") and not activity.get("field_changes"):
        lines.append("- Weitere Ticketdaten wurden seit dem letzten Kontext geändert.")

    comment_groups = (
        ("Neue Kommentare", activity.get("new_comments") or []),
        ("Bearbeitete Kommentare", activity.get("edited_comments") or []),
    )
    for title, comments in comment_groups:
        if not comments:
            continue
        lines += ["", f"## {title}"]
        for comment in comments:
            body = wiki_to_markdown(_text(comment.get("body")), heading_offset=2)
            lines.append(
                f"\n**{comment.get('author') or '?'}** "
                f"({_text(comment.get('updated') or comment.get('created'))[:16]}):\n{body}"
            )

    deleted = activity.get("deleted_comment_ids") or []
    if deleted:
        lines += ["", f"{len(deleted)} zuvor bekannte(r) Kommentar(e) wurde(n) entfernt."]

    lines += [
        "",
        "Berücksichtige diese Ergänzungen bei der weiteren Bearbeitung. Wiederhole nicht "
        "den bereits bekannten Ticketkontext. Weise knapp auf neuen Handlungsbedarf hin.",
    ]
    return "\n".join(lines)
