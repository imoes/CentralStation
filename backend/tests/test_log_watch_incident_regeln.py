"""Incident-Regeln im Schattenbetrieb: Kandidaten je Gruppe ab Mindestwerten, Vergleich mit den
Incidents der alten Schwellen je Art (Nutzer 07.11.: Incidents nach der Logik des Regelwerks)."""
from app.services.log_watch import incident_regeln as ir
from app.services.log_watch import regelwerk as rw
from app.services.log_watch.definition import STANDARD


def _regel(rid):
    return next(r for r in rw.standard_regeln(STANDARD.objekte) if r.id == rid)


def test_kandidaten_ab_mindestwerten():
    flotte = _regel("I-FLOTTE")                      # mindestens 5 Hosts
    b = [{"key": "dienst:Filesystem /", "doc_count": 9, "hosts": {"value": 6}, "quellen": {"value": 1},
          "host_liste": {"buckets": [{"key": "a"}, {"key": "b"}]}},
         {"key": "dienst:CPU", "doc_count": 40, "hosts": {"value": 2}, "quellen": {"value": 1}}]
    k = ir.kandidaten_aus(flotte, b)
    assert [(x["schluessel"], x["art"], x["hosts"]) for x in k] == [("dienst:Filesystem /", "fleet", 6)]
    host = _regel("I-HOST")                          # mindestens 2 Quellen, Schlüssel bekommt host:
    k = ir.kandidaten_aus(host, [{"key": "cue0148", "doc_count": 3, "hosts": {"value": 1}, "quellen": {"value": 2}}])
    assert k[0]["schluessel"] == "host:cue0148" and k[0]["art"] == "host"


def test_schluessel_der_alten_incidents_in_derselben_form():
    assert ir.schluessel_alt("log_cluster", "a", {"template_key": "k1", "patterns": [{"template_key": "k2"}]}) \
        == ["muster:k1", "muster:k2"]
    assert ir.schluessel_alt("fleet", "a", {"service": "Filesystem /"}) == ["dienst:Filesystem /"]
    assert ir.schluessel_alt("host", "a", None, kanon=lambda h: h + ".example.com") == ["host:a.example.com"]
    assert ir.schluessel_alt("fleet", "a", {}) == []


def test_vergleich_je_art():
    kand = [{"art": "log_cluster", "schluessel": "muster:k1"}, {"art": "log_cluster", "schluessel": "muster:k3"},
            {"art": "host", "schluessel": "host:a"}]
    v = ir.vergleiche(kand, {"log_cluster": {"muster:k1", "muster:k2"}, "fleet": {"dienst:x"}})
    assert v["log_cluster"]["beide"] == 1 and v["log_cluster"]["nur_neu"] == 1 and v["log_cluster"]["nur_alt"] == 1
    assert v["host"]["nur_neu"] == 1 and v["fleet"]["nur_alt"] == 1


def test_lesbarer_betreff_ohne_platzhalter():
    assert ir.lesbar("Nov <NUM> <TS> <NUM>] [proxy_http:error] [pid <NUM>:tid <HEX>] [client <IP>] AH01114: "
                     "HTTP: failed to make connection") == \
        "Nov …] [proxy_http:error] [pid …:tid …] [client …] AH01114: HTTP: failed to make connection"
    from app.services.error_clusters import notify
    s = notify.subject({"containers": [], "count15": 10}, "Fehlerausbruch: Nov <NUM> <TS> foo <IP> bar")
    assert "<NUM>" not in s and "<IP>" not in s


def test_regel_incident_kontext_findet_seinen_schluessel_wieder():
    """Der Kontext eines Regel-Incidents ergibt über schluessel_alt/filter_von denselben Schlüssel —
    sonst würde jeder Lauf einen neuen Incident eröffnen."""
    from app.services.log_watch import incident_objekt as io
    for rid, schl, art in (("I-FLOTTE", "dienst:Filesystem /", "fleet"), ("I-AUSBRUCH", "muster:k1", "log_cluster"),
                           ("I-HOST", "host:cue0148.example.com", "host")):
        r = _regel(rid)
        k = {"regel": rid, "art": art, "schluessel": schl, "nachrichten": 9, "hosts": 6, "quellen": 2,
             "host_liste": ["cue0148.example.com"]}
        ctx = ir.kontext(k, r, ["Beispiel"])
        assert schl in ir.schluessel_alt(art, ctx["hosts"][0], ctx)
        assert io.filter_von(art, ctx["hosts"][0], ctx) is not None
        assert ctx["reasons"][0].startswith(f"Regel {rid}")
    assert ir.titel(_regel("I-FLOTTE"), {"schluessel": "dienst:Filesystem /", "hosts": 6, "nachrichten": 9}) \
        == "Flotten-Ausfall eines Dienstes: Filesystem / · 6 Hosts · 9 Meldungen"


def test_regel_mail_nennt_regel_und_netzplan():
    from app.services.error_clusters import notify
    k = {"regel": "I-FLOTTE", "art": "fleet", "schluessel": "dienst:Filesystem /", "nachrichten": 9, "hosts": 6,
         "quellen": 1, "host_liste": ["a", "b"]}
    ctx = ir.kontext(k, _regel("I-FLOTTE"), ["Filesystem / CRIT 97 %"])
    html = notify.render_html(ctx, None, "https://hal.example.com/feed?incident=abc", pending=False)
    assert "Regel I-FLOTTE" in html and "/topology?modus=fehler&amp;filter=incident:abc" in html
    assert notify.subject(ctx, ir.titel(_regel("I-FLOTTE"), k)).startswith("[CentralStation] Incident: Flotten-Ausfall")


def test_kritisch_zaehlt_fuenf_minuten_fenster_wie_alt():
    """error_clusters/critical eröffnet bei ≥ 3 Fünf-Minuten-Fenstern in 6 h — die Regel zählt dasselbe,
    nicht Zeilen (gemessen 07.11.: 28 Zeilen auf jump012 hätten sonst schon gereicht)."""
    r = _regel("I-KRITISCH")
    assert r.mindestens == {"fenster": 3}
    b = [{"key": "jump012", "doc_count": 28, "hosts": {"value": 1}, "quellen": {"value": 1}, "fenster": {"value": 2}},
         {"key": "mon02", "doc_count": 9, "hosts": {"value": 1}, "quellen": {"value": 1}, "fenster": {"value": 3}}]
    assert [k["schluessel"] for k in ir.kandidaten_aus(r, b)] == ["host:mon02"]


def test_oom_zaehler_im_json_ist_kein_oom():
    """Gemessen 07.11. (jump012): memory.events als JSON-Zähler mit Wert 0 wurde Thema oom (R5)."""
    from app.services.log_watch.vokabular import themen
    zaehler = ('[info]: Request content: {"key":"oom","value":"0"},{"key":"max","value":"0"},'
               '{"key":"oom_kill","value":"0"},{"key":"oom_group_kill","value":"0"}')
    assert "oom" not in [t.id for t in themen(zaehler)]
    for echt in ("kernel: Out of memory: Killed process 4711 (java)", "systemd: oom-kill:constraint=CONSTRAINT_MEMCG",
                 "Memory cgroup out of memory: Killed process", "invoked oom_kill by process 12"):
        assert "oom" in [t.id for t in themen(echt)], echt


def test_umschalten_nur_die_gewaehlten_arten():
    daten = rw.als_daten(rw.standard_regeln(STANDARD.objekte))
    neu = ir.umgeschaltet(daten, {"critical", "log_cluster"})
    modus = {d["id"]: d["modus"] for d in neu if d["aktion"] == rw.INCIDENT}
    assert modus == {"I-AUSBRUCH": "aktiv", "I-FLOTTE": "test", "I-KRITISCH": "aktiv", "I-HOST": "test"}
    # Urteilsregeln bleiben unverändert
    assert [d for d in neu if d["aktion"] != rw.INCIDENT] == [d for d in daten if d["aktion"] != rw.INCIDENT]


def test_bremsen_aus_dem_backtest():
    """Mindestalter/Wiederkehr kommen aus der Statistik der Fehler-Cluster; Host: erste dringende Meldung
    ≥ 15 min alt, letzte ≤ 15 min; Gruppierung über Jaccard der Hosts."""
    import asyncio
    from datetime import datetime, timezone
    jetzt = datetime(2026, 11, 7, 13, 0, tzinfo=timezone.utc)
    host = _regel("I-HOST")
    jung = {"schluessel": "host:a", "erste": "2026-11-07T12:55:00+00:00", "letzte": "2026-11-07T12:59:00+00:00"}
    alt_aktiv = {"schluessel": "host:a", "erste": "2026-11-07T12:30:00+00:00", "letzte": "2026-11-07T12:58:00+00:00"}
    ruhig = {"schluessel": "host:a", "erste": "2026-11-07T12:00:00+00:00", "letzte": "2026-11-07T12:20:00+00:00"}
    assert "jünger" in asyncio.run(ir.freigabe(host, jung, jetzt))
    assert asyncio.run(ir.freigabe(host, alt_aktiv, jetzt)) is None
    assert "nicht mehr aktiv" in asyncio.run(ir.freigabe(host, ruhig, jetzt))
    assert ir.jaccard({"a", "b"}, {"b", "c"}) == 1 / 3 and ir.jaccard(set(), {"a"}) == 0.0


def test_regel_incident_wird_nicht_vom_mitglieder_abgleich_geschlossen():
    """Gemessen 07.11.: I-HOST-Incidents (ohne Mitglieder) schloss resolve_inactive_host_incidents nach ≤ 15 min."""
    from types import SimpleNamespace
    from app.services.error_clusters.incident import von_regel
    regel = SimpleNamespace(causal_context=[{"type": "error_cluster", "regel": "I-HOST", "schluessel": "host:a"}])
    alt = SimpleNamespace(causal_context=[{"type": "error_cluster", "hosts": ["a"]}])
    assert von_regel(regel) and not von_regel(alt) and not von_regel(SimpleNamespace(causal_context=None))


def test_zustellung_wird_am_incident_vermerkt():
    from datetime import datetime, timezone
    from app.services.error_clusters.notify import zustellung
    t = datetime(2026, 11, 7, 14, 40, tzinfo=timezone.utc)
    z = zustellung(None, 1, ["a@x.de"], "ok", t)
    assert z == [{"zeit": t.isoformat(), "mails": 1, "empfaenger": ["a@x.de"], "triage": "ok"}]
    assert len(zustellung([{}] * 25, 0, [], "none", t)) == 20 and zustellung([{}] * 25, 0, [], "none", t)[-1]["mails"] == 0


def test_abgebrochene_meldung_wird_nachgeholt():
    """Gemessen 07.11.: Incidents von 16:09 ohne Zustellvermerk (Neustart 16:19 brach die Meldung ab)."""
    from datetime import datetime, timedelta, timezone
    from app.services.log_watch.incident_regeln import nachzuholen
    j = datetime(2026, 11, 7, 16, 40, tzinfo=timezone.utc)
    inc = [("a", {"regel": "I-HOST"}, j - timedelta(minutes=31)),                       # fehlt → nachholen
           ("b", {"regel": "I-HOST", "zustellungen": [{"mails": 0}]}, j - timedelta(minutes=31)),  # gemeldet (0 Empfänger)
           ("c", {"regel": "I-HOST"}, j - timedelta(minutes=3)),                         # läuft vielleicht noch
           ("d", {"regel": "I-HOST"}, j - timedelta(hours=3)),                           # zu alt
           ("e", {}, j - timedelta(minutes=31)),                                         # alter Weg, kein Regel-Incident
           ("f", {"regel": "I-HOST"}, j - timedelta(minutes=31))]                        # in Arbeit
    assert nachzuholen(inc, {"f"}, j) == ["a"]


def test_ohne_scope_nur_gespeichert_keine_mail():
    """Nutzer 10.11.: Mails nur für den eigenen CheckMK-Filter; nicht abonnierte Systeme → nur gespeichert, benannt
    (ersetzt „alle Abonnenten“ vom 07.11.)."""
    from app.services.error_clusters.notify import IM_SCOPE, NUR_GESPEICHERT, waehle_empfaenger
    ab = [("a@x.de", ["cue092.example.com"]), ("b@x.de", ["mssql06.example.com"])]
    assert waehle_empfaenger(ab, ["cue092.example.com"]) == (["a@x.de"], IM_SCOPE)
    assert waehle_empfaenger(ab, ["web01.example.com"]) == ([], NUR_GESPEICHERT)
    assert waehle_empfaenger([("c@x.de", [])] + ab, ["web01.example.com"]) == (["c@x.de"], IM_SCOPE)  # leer = alles
    assert waehle_empfaenger([], ["x"]) == ([], NUR_GESPEICHERT)


def test_nur_gespeichert_zaehlt_als_meldung_fuer_die_erinnerung():
    """Ohne Mail darf die KI-Analyse eines nicht abonnierten Incidents nicht bei jedem Wiederauftreten neu laufen:
    die Ablage ohne Mail zählt für die 24-h-Erinnerung wie eine Mail; ein SMTP-Fehlschlag (mails 0, andere Wahl) nicht."""
    from datetime import datetime, timedelta, timezone
    from app.services.error_clusters.bearbeitung import NUR_GESPEICHERT, letzte_meldung, mail_erlaubt
    jetzt = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
    vor_2h = (jetzt - timedelta(hours=2)).isoformat()
    gespeichert = {"zustellungen": [{"zeit": vor_2h, "mails": 0, "empfaenger_wahl": NUR_GESPEICHERT}]}
    assert letzte_meldung(gespeichert) == jetzt - timedelta(hours=2)
    assert mail_erlaubt("wieder", [], gespeichert, jetzt)[0] is False
    assert mail_erlaubt("wieder", [], gespeichert, jetzt + timedelta(hours=23))[0] is True
    fehlschlag = {"zustellungen": [{"zeit": vor_2h, "mails": 0, "empfaenger_wahl": "host-scope"}]}
    assert letzte_meldung(fehlschlag) is None


def test_alt_duplikate_mailen_nicht_je_lauf_eines():
    """11.11.2026: transfer07 hatte 5 offene Alt-Incidents (Kontext ohne Regel). Je Lauf traf die Schleife ein anderes
    Duplikat mit leerem Kontext → „Etwas Neues: neuer Host: transfer07“ mit 0 Zeilen (13:07 und 13:10 UTC)."""
    from app.services.error_clusters import bearbeitung as bb
    h, schl = "transfer07.example.com", "host:transfer07.example.com"
    alt = [("host", h, {}) for _ in range(5)]
    # Lauf 1: kein Regel-Incident → das erste Alt-Duplikat
    assert ir.waehle_offen(alt, schl) == 0
    # der eigene Host eines Alt-Incidents ist nie „neu“ → keine Eskalation, also keine Mail
    assert bb.eskalation(ir.bekannt_fuer_eskalation({}, h), [h], "high", "high") is None
    # … ein wirklich weiterer Host bleibt eine Eskalation
    assert bb.eskalation(ir.bekannt_fuer_eskalation({}, h), [h, "transfer08.example.com"], "high", "high") \
        == "neuer Host: transfer08.example.com"
    # Übernahme: voller Kontext wie neu, Zustellungen des Alten bleiben
    neu = {"regel": "I-HOST", "schluessel": schl, "hosts": [h], "nachrichten": 7, "examples": ["x"]}
    c = ir.uebernahme({"hosts": [h], "zustellungen": [{"zeit": "t", "mails": 1}]}, neu)
    assert c["regel"] == "I-HOST" and c["schluessel"] == schl and c["nachrichten"] == 7
    assert c["zustellungen"] == [{"zeit": "t", "mails": 1}] and c["hosts"] == [h] and "uebernommen" in c
    assert ir.uebernahme({"regel": "I-HOST", "schluessel": schl}, neu) == {"regel": "I-HOST", "schluessel": schl}
    # Lauf 2: das übernommene steht an Index 3 — der genaue Schlüssel gewinnt vor den übrigen Alt-Duplikaten
    alt2 = alt[:3] + [("host", h, c)] + alt[4:]
    assert ir.waehle_offen(alt2, schl) == 3
    assert ir.waehle_offen(alt2, "host:anderer") is None


def test_uebernahme_behaelt_alte_triage_nicht_sondern_benennt_sie():
    """F5 der externen Analyse: 4 von 5 übernommenen Incidents trugen die leere Triage vom 10.11."""
    neu = {"regel": "I-HOST", "schluessel": "host:a", "hosts": ["a"], "examples": ["x"]}
    alt = {"hosts": ["a"], "triage": {"titel": "Fehlende Daten für Fehleranalyse"}, "zustellungen": [{"zeit": "t"}]}
    c = ir.uebernahme(alt, neu)
    assert "triage" not in c and c["triage_alt"]["titel"] == "Fehlende Daten für Fehleranalyse"
    assert c["zustellungen"] == [{"zeit": "t"}] and "ersetzt" in c["triage_alt"]["grund"]


def test_meldungs_beispiele_neueste_zuerst_ohne_doppel():
    m = [{"zeit": "2026-11-11T10:00", "quelle": "graylog", "host": "h", "titel": "NiFi queue SAP-MUC"},
         {"zeit": "2026-11-11T12:00", "quelle": "checkmk", "host": "h", "titel": "Nifi Process Groups CUEPRINT-MUC"},
         {"zeit": "2026-11-11T09:00", "quelle": "graylog", "host": "h", "titel": "NiFi queue SAP-MUC"},
         {"fehler": "Meldungen nicht ladbar"}]
    assert ir.meldungs_beispiele(m) == ["[checkmk] h: Nifi Process Groups CUEPRINT-MUC", "[graylog] h: NiFi queue SAP-MUC"]
    assert len(ir.meldungs_beispiele([{"zeit": str(i), "titel": f"t{i}"} for i in range(30)], n=10)) == 10
