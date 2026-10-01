"""Was in der Mail steht und wie die KI-Antwort gelesen wird — am 401-Vorfall."""
from __future__ import annotations

from datetime import datetime, timezone

from app.services.error_clusters.incident import host_matches
from app.services.error_clusters.notify import render_html, subject
from app.services.error_clusters.runner import graylog_link, short_title
from app.services.error_clusters.triage import build_prompt, parse

TEMPLATE = ("ERROR - cue.zipline.content_store.changelog_reader.ChangelogReader - "
            "Failed to read change-log: HTTP401 Unauthorized'")
CTX = {
    "template": TEMPLATE,
    "examples": ["2026-10-01 08:00:26,796 - ERROR - …ChangelogReader - Failed to read change-log: "
                 "HTTP Status '401 Unauthorized'"],
    "rules": ["new", "spread"],
    "reasons": ["neues Muster (14 Tage nicht gesehen)", "24 Container/Quellen, üblich 1"],
    "count15": 14_592, "expected15": 0.6, "total": 85_960,
    "started_at": "2026-10-01T08:00:00+00:00",
    "containers": [{"name": f"cue-zipline-product-{i}", "count": 3_700} for i in range(24)],
    "hosts": ["cueservices.cue.example.com"],
    "graylog_url": "https://graylog.example/search?q=x",
}


def test_short_title_drops_level_and_package():
    assert short_title(TEMPLATE) == ("ChangelogReader – Failed to read change-log: "
                                     "HTTP401 Unauthorized")


def test_subject_names_pattern_spread_and_rate():
    s = subject(CTX, "Fehlerausbruch: ChangelogReader – Failed to read change-log · 24 Container")
    assert s.startswith("[CentralStation] Fehlerausbruch: ChangelogReader")
    assert "24 Container" in s
    assert "9 728 Zeilen/10 min" in s          # 14 592 je 15 min → 9 728 je 10 min


def test_followup_subject_is_marked():
    assert "KI-Einschätzung" in subject(CTX, "Fehlerausbruch: X", followup=True)


def test_mail_without_triage_says_it_follows():
    html = render_html(CTX, None, "https://cs/feed?incident=1", pending=True)
    assert "KI-Einschätzung folgt" in html
    assert "Incident öffnen" in html and "In Graylog ansehen" in html
    assert "keine Aktion ausgeführt" in html
    assert "… und 9 weitere" in html            # 24 Container, 15 gezeigt


def test_mail_with_triage_shows_cause_and_read_only_steps():
    triage = {"titel": "Zipline: Zugang zum Content Store verweigert",
              "zusammenfassung": "24 Zipline-Instanzen erhalten 401.",
              "vermutete_ursache": "Zugangsdaten rotiert", "konfidenz": "mittel",
              "betroffene_komponente": "cue-content-store", "verwandte_signale": ["cue-db 11×"],
              "pruefschritte": ["Logs des Content Store ansehen"]}
    html = render_html(CTX, triage, None, pending=False)
    assert "Zugangsdaten rotiert" in html and "nur lesend" in html
    assert "KI-Einschätzung folgt" not in html


def test_mail_escapes_log_content():
    ctx = {**CTX, "examples": ["<script>alert(1)</script>"]}
    assert "<script>" not in render_html(ctx, None, None, pending=False)


def test_triage_answer_is_parsed_even_with_think_block_and_fence():
    raw = ('<think>überlege…</think>```json\n{"titel": "T", "zusammenfassung": "Z", '
           '"pruefschritte": "nur einer"}\n```')
    out = parse(raw)
    assert out["titel"] == "T" and out["pruefschritte"] == ["nur einer"]
    assert out["verwandte_signale"] == []


def test_unusable_triage_answer_is_none():
    assert parse("") is None
    assert parse("keine Ahnung") is None
    assert parse('{"titel": "ohne Zusammenfassung"}') is None


def test_prompt_carries_the_evidence_not_the_raw_flood():
    p = build_prompt(CTX, [{"container": "cue-db", "faktor": 11.2}], [{"host": "cuedb"}])
    assert "ChangelogReader" in p and "cue-db" in p
    assert '"anzahl_container": 24' in p
    assert p.count("cue-zipline-product-") <= 15 + 1   # höchstens 15 Container im Prompt


def test_graylog_link_searches_the_fixed_part_of_the_pattern():
    t0 = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 1, 8, 40, tzinfo=timezone.utc)
    url = graylog_link("https://graylog.example", TEMPLATE, t0, t1)
    assert url.startswith("https://graylog.example/search?q=")
    assert "ChangelogReader" in url and "rangetype=absolute" in url


def test_scope_matching_for_recipients():
    assert host_matches("cueservices", CTX["hosts"])
    assert not host_matches("cuedb.cue.example.com", CTX["hosts"])


def test_affected_hosts_include_container_hosts():
    """CheckMK kennt die Zipline-Container als Hosts, nicht den Docker-Host cueservices.
    Ohne die Container hätte der 401-Ausbruch niemanden mit Zipline-Filter erreicht."""
    from app.services.error_clusters.runner import affected_hosts
    hosts = affected_hosts({"cueservices.cue.example.com"},
                           {"cue-zipline-product-tz.cue.example.com": 3700, "2c51e08bb9c9": 1})
    assert "cue-zipline-product-tz.cue.example.com" in hosts
    assert "cueservices.cue.example.com" in hosts
    assert "2c51e08bb9c9" not in hosts          # Container-Kurz-IDs sind keine Hosts
    assert host_matches("cue-zipline-product-tz.cue.example.com", hosts)
