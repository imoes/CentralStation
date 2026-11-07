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
