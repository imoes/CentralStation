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
