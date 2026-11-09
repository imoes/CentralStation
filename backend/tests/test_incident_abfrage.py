"""Incidents für die Konsole: Zusammengehörigkeit nur mit benanntem gemeinsamem Merkmal (Nutzer 09.11.2026)."""
from datetime import datetime, timedelta, timezone

from app.services import incident_abfrage as ia

T0 = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
JETZT = T0 + timedelta(hours=3)


def _inc(id_, host, ctx_hosts=(), start=0, ende=None, **kw):
    return {"id": id_, "primary_host": host, "created_at": T0 + timedelta(minutes=start),
            "resolved_at": None if ende is None else T0 + timedelta(minutes=ende),
            "causal_context": [{"hosts": list(ctx_hosts), "regel": "I-HOST"}] if ctx_hosts else [], **kw}


def test_hostvergleich_fqdn_kurzname_und_fremde_domain():
    assert ia.gleicher_host("CUE0109.example.com.", "cue0109.example.com")
    assert ia.gleicher_host("cue0109", "cue0109.example.com")
    assert not ia.gleicher_host("web01.example.com", "web01.example.net")
    assert not ia.gleicher_host("10.0.0.1", "10.0.0.2") and not ia.gleicher_host("", "x")


def test_hosts_aus_haupthost_und_kontext_ohne_doppel():
    assert ia.hosts_von(_inc("a", "A.example.com", ["a.example.com", "b.example.com"])) == ["a.example.com", "b.example.com"]


def test_verwandt_ueber_kontext_host_und_gemeinsame_meldung():
    ziel = _inc("z", "nfw062.example.com", ["mu-img-dc01.example.com"], start=0, ende=60)
    k1 = _inc("k1", "mu-img-dc01.example.com", start=30, ende=90)              # Host im Kontext
    k2 = _inc("k2", "anderer.example.com", start=10)                          # nur gemeinsame Meldung
    k3 = _inc("k3", "fremd.example.com", start=5)                             # gleichzeitig, nichts gemeinsam
    r = ia.verwandte(ziel, [k1, k2, k3, ziel], {"z": {"m1", "m2"}, "k2": {"m2"}, "k1": set()}, JETZT)
    ids = [x["incident"]["id"] for x in r]
    assert ids == ["k2", "k1"] or ids == ["k1", "k2"]
    assert "k3" not in ids and "z" not in ids                               # zeitliche Nähe allein: kein Grund
    g1 = next(x for x in r if x["incident"]["id"] == "k1")
    assert g1["gruende"][0]["art"] == "gleicher_host" and g1["gruende"][0]["hosts"] == ["mu-img-dc01.example.com"]
    assert g1["zeitlich"] == "überlappen sich (Kontext, kein Grund)"
    g2 = next(x for x in r if x["incident"]["id"] == "k2")
    assert g2["gruende"] == [{"art": "gleiche_meldung", "text": "1 gemeinsame Meldung(en)", "external_ids": ["m2"]}]


def test_mehr_gruende_zuerst_und_abstand_benannt():
    ziel = _inc("z", "h1.example.com", start=0, ende=10)
    beide = _inc("b", "h1.example.com", start=100, ende=120)
    nur_host = _inc("n", "h1.example.com", start=20, ende=30)
    r = ia.verwandte(ziel, [nur_host, beide], {"z": {"m"}, "b": {"m"}}, JETZT)
    assert [x["incident"]["id"] for x in r] == ["b", "n"]
    assert r[0]["zeitlich"] == "Abstand 90 min (Kontext, kein Grund)"


def test_statusfilter_erschoepfend_und_ausgabe_iso():
    assert set(ia.STATUS_FILTER["alle"]) == set(ia.STATUS) and ia.STATUS_FILTER["offen"] == ("open", "investigating")
    o = ia.ausgabe(_inc("a", "h.example.com", ["x.example.com"]))
    assert o["created_at"] == T0.isoformat() and o["hosts"] == ["h.example.com", "x.example.com"]
    assert o["kontext"][0]["regel"] == "I-HOST" and "causal_context" not in o


def test_mcp_werkzeuge_vorhanden():
    import pathlib
    q = pathlib.Path("app/api/mcp_server.py").read_text()
    for n in ("list_incidents", "get_incident", "find_related_incidents"):
        assert f"async def {n}(" in q, n


def test_ohne_meldungen_sind_die_kontext_beispiele_die_belege():
    d = _inc("a", "h.example.com")
    d["causal_context"] = [{"hosts": ["h.example.com"], "examples": ["zeile 1", "zeile 2"]}]
    b = ia.belege_aus_kontext(d, 0)
    assert b["belege_aus_kontext"] == ["zeile 1", "zeile 2"] and "Log-Überwachung" in b["hinweis"]
    assert ia.belege_aus_kontext(d, 3) == {}
    assert ia.belege_aus_kontext(_inc("b", "x"), 0)["hinweis"].startswith("weder")
