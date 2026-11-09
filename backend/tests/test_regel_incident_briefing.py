"""Konsolen-Briefing eines Regel-Incidents enthält den Inhalt seiner Mail (Nutzer 09.11.2026)."""
from app.services.error_clusters.briefing import regel_prompt_section as _regel_prompt_section, triage_lines as _triage_lines

CTX = {  # gekürzt aus incidents.causal_context von af1464ec (09.11.2026)
    "type": "error_cluster", "regel": "I-HOST", "regel_name": "Host mit mehreren dringenden Meldungen",
    "regel_filter": "relevanz>=3 !signal:ausbreitung", "hosts": ["vpp0262.example.com"], "nachrichten": 6,
    "quellen": 1, "fenster_min": 30, "entity_kind": "Host",
    "reasons": ["Regel I-HOST „Host mit mehreren dringenden Meldungen“: relevanz>=3 !signal:ausbreitung — 6 nachrichten"],
    "examples": ["Used: 100.00% - 104 KiB of 104 KiB", "Used: 100.00% - 4.54 GiB of 4.54 GiB"],
    "triage": {"titel": "Disk-Full auf vpp0262.example.com (100% Auslastung)", "zusammenfassung": "Mehrere FS voll.",
               "vermutete_ursache": "Ein Prozess hat den Platz aufgebraucht.", "konfidenz": "hoch",
               "betroffene_komponente": "Dateisystem / Storage", "pruefschritte": ["Belegung prüfen", "Prozesse prüfen"],
               "verwandte_signale": ["NTP-Ausfall"]},
}


def test_regel_briefing_hat_alles_aus_der_mail():
    t = "\n".join(_regel_prompt_section(CTX, "high", "2026-11-09 09:10"))
    for teil in ("Host mit mehreren dringenden Meldungen — vpp0262.example.com", "## Warum gemeldet",
                 "6 relevante Meldungen · 1 Hosts · 1 Quellen in 30 min — Filter `relevanz>=3 !signal:ausbreitung`",
                 "## Betroffen (1 Host)", "- vpp0262.example.com", "## Belege (Beispielzeilen)", "4.54 GiB of 4.54 GiB",
                 "**Disk-Full auf vpp0262.example.com (100% Auslastung)**", "Mehrere FS voll.",
                 "Vermutete Ursache: Ein Prozess hat den Platz aufgebraucht. (Konfidenz: hoch)",
                 "Betroffene Komponente: Dateisystem / Storage", "1. Belegung prüfen", "2. Prozesse prüfen",
                 "Verwandte Signale:", "- NTP-Ausfall"):
        assert teil in t, teil


def test_ohne_triage_kein_leerer_block_und_viele_hosts_gekuerzt():
    c = {**CTX, "triage": None, "hosts": [f"h{i}.example.com" for i in range(25)]}
    t = "\n".join(_regel_prompt_section(c, "medium", "2026-11-09 09:10"))
    assert "KI-Einschätzung" not in t and "(+20)" in t and "… und 5 weitere" in t
    assert _triage_lines({}) == []
