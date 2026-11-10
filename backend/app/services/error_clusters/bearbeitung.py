"""Bearbeitungsstand eines Incidents und ob eine Mail dazu rausgehen darf — rein, ohne DB (testbar).

Nutzer 09.11.2026: „Ich bekomme ständig eine Mail-Flut und weiß gar nicht, welcher neu ist und welchen ich bereits
bearbeitet habe.“ Gemessen (24 h bis 09.11. ~13:50 UTC): 113 Incidents mit Mail, 54 von 86 gelösten nach < 1 h zu,
cue0113 9 Incidents, cue0111 8 — derselbe Host/dieselbe Regel als immer neuer Incident. Entscheidung des Nutzers:
„Wiedereröffnen statt neu“ und „Bearbeitet = Ruhe“.

Zustand (erschöpfend, aus causal_context):
- ticket       ein Ticket wurde aus dem Incident erstellt (Eintrag type „ticket“, api/tickets.py)
- übernommen   jemand hat „Übernehmen“ geklickt (Eintrag type „bearbeitung“, art „übernommen“) und nicht wieder
               freigegeben
- unbearbeitet sonst
Mail-Anlässe: neu (erster Bericht) · wieder (derselbe Incident tritt erneut auf) · nachtrag (die KI-Einschätzung
kommt später) · eskalation (etwas Neues: weiterer Host, höhere Schwere).
"""
from __future__ import annotations

from datetime import datetime, timedelta

ZUSTAENDE = ("unbearbeitet", "übernommen", "ticket")
ANLAESSE = ("neu", "wieder", "nachtrag", "eskalation")
#: Ein unbearbeiteter, wieder aufgetretener Incident erinnert höchstens so oft per Mail. Nutzer 09.11.: 1× am Tag —
#: simuliert über die echten Incidents der letzten 24 h: 3 h → 105 Mails, 8 h → 96, 24 h → 71, nie → 50 (statt 113).
ERINNERUNG = timedelta(hours=24)
#: So lange nach dem Schließen lebt derselbe Incident wieder auf, statt einen neuen zu eröffnen
WIEDER_FENSTER = timedelta(hours=24)
SCHWERE = {"low": 0, "medium": 1, "high": 2, "critical": 3}
#: Empfängerwahl, wenn der Incident in keinem CheckMK-Filter (Host-Scope) eines Abonnenten liegt. Nutzer 10.11.2026:
#: „ich möchte nur für meinen CheckMK-Filter E-Mails bekommen; für die nicht abonnierten Systeme soll nur die Analyse
#: gespeichert werden, falls man sie für spätere Suchen braucht.“ (ersetzt „alle Abonnenten“ vom 07.11.)
NUR_GESPEICHERT = "kein Abo-Scope trifft — nur gespeichert, keine Mail"


def _zeit(s) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None


def zustand(causal_context: list | None) -> dict:
    """{zustand, von, zeit, ticket?, grund} — der aktuelle Bearbeitungsstand. Rein."""
    eintraege = [e for e in (causal_context or []) if isinstance(e, dict)]
    tickets = [e for e in eintraege if e.get("type") == "ticket"]
    if tickets:
        t = tickets[-1]
        return {"zustand": "ticket", "von": t.get("von") or t.get("by"), "zeit": t.get("at") or t.get("zeit"),
                "ticket": t.get("key"), "grund": f"Ticket {t.get('key')}"}
    b = [e for e in eintraege if e.get("type") == "bearbeitung"]
    if b and b[-1].get("art") == "übernommen":
        return {"zustand": "übernommen", "von": b[-1].get("von"), "zeit": b[-1].get("zeit"),
                "grund": f"übernommen von {b[-1].get('von')}"}
    return {"zustand": "unbearbeitet", "von": None, "zeit": None, "grund": "noch niemand hat ihn übernommen"}


def bearbeitet(causal_context: list | None) -> bool:
    return zustand(causal_context)["zustand"] != "unbearbeitet"


def letzte_meldung(cluster_ctx: dict | None) -> datetime | None:
    """Zeit der letzten Meldung: zugestellte Mail (mails > 0) oder bewusst nur gespeichert (NUR_GESPEICHERT).
    Ohne das Zweite liefe die KI-Analyse eines nicht abonnierten Incidents bei jedem Wiederauftreten neu. Rein."""
    zeiten = [_zeit(z.get("zeit")) for z in (cluster_ctx or {}).get("zustellungen") or []
              if (z.get("mails") or 0) > 0 or z.get("empfaenger_wahl") == NUR_GESPEICHERT]
    zeiten = [z for z in zeiten if z]
    return max(zeiten) if zeiten else None


def mail_erlaubt(anlass: str, causal_context: list | None, cluster_ctx: dict | None, jetzt: datetime) -> tuple[bool, str]:
    """(darf raus, Grund). Bearbeitet = Ruhe, außer bei Eskalation; Erinnerung höchstens alle ERINNERUNG. Rein."""
    if anlass not in ANLAESSE:
        raise ValueError(f"unbekannter Anlass {anlass!r} — erlaubt: {', '.join(ANLAESSE)}")
    if anlass in ("neu", "eskalation"):
        return True, anlass
    z = zustand(causal_context)
    if z["zustand"] != "unbearbeitet":
        return False, f"bearbeitet ({z['grund']}) — Ruhe bis etwas Neues hinzukommt"
    if anlass == "nachtrag":
        return True, "nachtrag"
    letzte = letzte_meldung(cluster_ctx)
    if letzte and jetzt - letzte < ERINNERUNG:
        return False, (f"wieder aufgetreten, aber letzte Meldung erst {int((jetzt - letzte).total_seconds() // 60)} min her "
                       f"(Erinnerung höchstens alle {int(ERINNERUNG.total_seconds() // 3600)} h)")
    return True, "wieder aufgetreten, noch unbearbeitet"


def eskalation(alt_ctx: dict | None, neue_hosts: list[str], alte_schwere: str, neue_schwere: str) -> str | None:
    """Etwas Neues gegenüber dem bekannten Incident? Benannter Grund oder None. Rein."""
    bekannt = {h.lower() for h in (alt_ctx or {}).get("hosts") or []}
    neu = sorted({h for h in neue_hosts if h.lower() not in bekannt})
    if neu:
        return f"neuer Host: {', '.join(neu[:5])}"
    if SCHWERE.get(neue_schwere, 0) > SCHWERE.get(alte_schwere, 0):
        return f"Schwere gestiegen: {alte_schwere} → {neue_schwere}"
    return None


def wieder_vermerk(cluster_ctx: dict, jetzt: datetime) -> dict:
    """Kontext mit vermerktem Wiederauftreten (höchstens 50 Zeiten) — sichtbar, nicht still. Rein."""
    c = dict(cluster_ctx or {})
    c["wieder"] = ((c.get("wieder") or []) + [jetzt.isoformat()])[-50:]
    return c


def anzahl_24h(cluster_ctx: dict | None, jetzt: datetime) -> int:
    """Wie oft in den letzten 24 h wieder aufgetreten (+1 für das erste Auftreten). Rein."""
    z = [_zeit(t) for t in (cluster_ctx or {}).get("wieder") or []]
    return 1 + sum(1 for t in z if t and jetzt - t <= timedelta(hours=24))
