"""Rückmeldungen zu Alarmen einzeln erfassen — die Labels für ein gelerntes Scoring (Nutzer 09.11.2026).

Gezählt am 09.11. (Postgres, nur lesend): alert_score_adjustments hielt 1.175 Muster mit 45 positiven, 0 negativen
Deltas; record_quick_ack/record_insight_ignored wurden nie aufgerufen; Tickets und „KI Analyse“-Klicks hingen an
keinem einzelnen Alarm. Ohne Einzelereignisse gibt es weder Training noch Backtest.

Diese Tabelle sammelt nur. Das Scoring (alert_scorer.py, alert_score_learner.py) bleibt unverändert, bis ein
zeitlich getrennter Backtest einen messbaren Vorsprung zeigt.

Arten (erschöpfend, DB-Constraint alert_feedback_art):
- quittiert               Nutzer hat den Alarm quittiert (Feed oder /alerts). Latenz = zeitpunkt − alarm_zeit.
- ki_analyse_angefordert  Nutzer hat „KI Analyse“ geklickt.
- ausgeblendet            Nutzer hat „ignorieren“ gewählt (Ausschluss-Suche angelegt) — das stärkste Rauschen-Signal.
- ticket                  Der Sysadmin-Agent hat ein Ticket angelegt; die Zuordnung zum Alarm ist heuristisch
                          (Host oder Titel im Ticket-Text) und steht in details.zuordnung.
Fehlschläge brechen die eigentliche Aktion nie ab, werden aber geloggt (nicht still).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)

ARTEN = ("quittiert", "ki_analyse_angefordert", "ausgeblendet", "ticket")


def _zeit(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def zeile(art: str, item: dict, nutzer=None, details: dict | None = None) -> dict:
    """Die Werte einer Rückmeldung aus einem Feed-Item/Alarm-Dict. Rein — testbar."""
    if art not in ARTEN:
        raise ValueError(f"unbekannte Rückmeldungsart {art!r} — erlaubt: {', '.join(ARTEN)}")
    from app.services.alert_score_learner import _make_hash
    muster, _ = _make_hash(item)
    alert_id = str(item.get("id") or item.get("external_id") or "")
    if not alert_id:
        raise ValueError("Alarm ohne id/external_id — Rückmeldung nicht zuordenbar")
    return {"alert_id": alert_id[:255], "quelle": str(item.get("source") or "")[:50], "muster": muster, "art": art,
            "nutzer": nutzer, "alarm_zeit": _zeit(item.get("created_at") or item.get("timestamp")),
            "details": details or {}}


async def erfasse(db, art: str, item: dict, nutzer=None, details: dict | None = None, commit: bool = True) -> bool:
    """Rückmeldung speichern. True bei Erfolg; ein Fehler wird geloggt und bricht die Aktion des Nutzers nicht ab."""
    from app.models.workflow import AlertFeedback
    try:
        db.add(AlertFeedback(**zeile(art, item, nutzer, details)))
        if commit:
            await db.commit()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("alert_feedback: %s für %s nicht gespeichert: %s", art, item.get("id") or item.get("external_id"), e)
        if commit:
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001
                pass
        return False
