"""Konsolen-Briefing-Bausteine für Incidents — rein, ohne DB (testbar). Genutzt von api/feed.incident_briefing
(Konsolen-Übergabe und Ticket-Entwurf); Inhalt wie die Mail (error_clusters.notify.render_html)."""
from __future__ import annotations


def triage_lines(t: dict) -> list[str]:
    """KI-Einschätzung fürs Konsolen-Briefing — dieselben Teile wie in der Mail (error_clusters.notify.render_html):
    Titel, Zusammenfassung, Ursache mit Konfidenz, Komponente, Prüfschritte, verwandte Signale. Rein."""
    if not t:
        return []
    out = ["", "## KI-Einschätzung (automatisch, ungeprüft)"]
    if t.get("titel"):
        out.append(f"**{t['titel']}**")
    if t.get("zusammenfassung"):
        out.append(t["zusammenfassung"])
    if t.get("vermutete_ursache"):
        out.append(f"Vermutete Ursache: {t['vermutete_ursache']}"
                   + (f" (Konfidenz: {t['konfidenz']})" if t.get("konfidenz") else ""))
    if t.get("betroffene_komponente"):
        out.append(f"Betroffene Komponente: {t['betroffene_komponente']}")
    if t.get("pruefschritte"):
        out += ["", "Prüfschritte (nur lesend):"] + [f"{i}. {s}" for i, s in enumerate(t["pruefschritte"], 1)]
    if t.get("verwandte_signale"):
        out += ["", "Verwandte Signale:"] + [f"- {s}" for s in t["verwandte_signale"]]
    return out


def regel_prompt_section(c: dict, severity: str, created_str: str) -> list[str]:
    """Konsolen-Briefing für einen Incident aus dem Regelwerk (log_watch/incident_regeln.py) — derselbe Inhalt wie
    seine Mail: Warum gemeldet, Mengenzeile der Regel, Betroffene, Belegzeilen, KI-Einschätzung. Rein.

    Nutzer 09.11.2026: „wenn ich ein Incident in einer Konsole lade, steht jetzt nicht mehr der ganze Inhalt der
    Mail“ — Regel-Incidents haben die Art „host“ und keine Feed-Meldungen; das Briefing las ihren Kontext nicht."""
    hosts = c.get("hosts") or []
    out = [f"# Untersuchung: {c.get('regel_name') or 'Incident'} — {', '.join(hosts[:5]) or '?'}"
           + (f" (+{len(hosts) - 5})" if len(hosts) > 5 else ""), "",
           f"Automatisch erkannt (Severity: {severity}, seit {created_str} UTC) von Regel {c.get('regel')} "
           f"„{c.get('regel_name')}“. Arbeite read-only, bis die Ursache belegt ist.", "",
           "## Warum gemeldet"] + [f"- {r}" for r in c.get("reasons") or []]
    out.append(f"{c.get('nachrichten')} relevante Meldungen · {len(hosts)} Hosts · {c.get('quellen')} Quellen in "
               f"{c.get('fenster_min')} min — Filter `{c.get('regel_filter')}`")
    if hosts:
        out += ["", f"## Betroffen ({len(hosts)} {c.get('entity_kind') or 'Host'})"] + [f"- {h}" for h in hosts[:20]]
        if len(hosts) > 20:
            out.append(f"- … und {len(hosts) - 20} weitere")
    if c.get("examples"):
        out += ["", "## Belege (Beispielzeilen)"] + [f"- `{str(e)[:400]}`" for e in c["examples"][:10]]
    out += triage_lines(c.get("triage") or {})
    if c.get("graylog_url"):
        out += ["", f"Graylog: {c['graylog_url']}"]
    out.append("")
    return out
