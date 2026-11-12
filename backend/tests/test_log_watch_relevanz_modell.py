"""Relevanz-Modell: Labels unabhängig von den Regeln, Merkmale, Kennzahlen, Modus mit Grund, Audit, Wirkung im Raster."""
import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.services.log_watch import raster
from app.services.log_watch import relevanz_modell as rm
from app.services.log_watch.definition import KRITERIEN, PARAMETER, STANDARD, STANDARD_OBJEKTE, Definition
from app.services.log_watch.regelwerk import AKTIV, STANDARD_FILTER, standard_regeln

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _ohne_modell():
    rm.setze(None)
    yield
    rm.setze(None)


# ── Labels und Merkmale ─────────────────────────────────────────────────────────

def test_merkmale_ohne_label_und_nachtraegliches():
    m = rm.merkmale("graylog", ["host:a", "befund:ki:1", "signal:modell-rauschen", "incident:x", "muster:m",
                                "ohne-doppelpunkt"], level="3", kriterien=["R2", "R7"], stunde=5)
    assert m == sorted(["quelle:graylog", "host:a", "muster:m", "level:3", "kriterium:R2", "stunde:05"])


def test_positivliste_ki_tags_und_nachtraegliches_nie_merkmal():
    tags = ["host:a", "serie:x", "ursache:y", "thema:oom", "signal:neu", "rauschen:pam"]
    assert rm.merkmale("graylog", tags) == ["host:a", "quelle:graylog", "rauschen:pam", "signal:neu", "thema:oom"]
    assert rm.merkmale("graylog", tags, kriterien=["R2"], mit_regel=False) == ["host:a", "quelle:graylog", "thema:oom"]
    spaeter = rm.nachtraeglich([{"wer": "ki", "was": "+thema:oom +zyklus:~24h"}, {"was": "verknüpft mit befund:1"}])
    assert spaeter == {"thema:oom", "zyklus:~24h"}
    assert "thema:oom" not in rm.merkmale("graylog", tags, nachtraeglich=spaeter)
    b = rm.beobachtung("1", {"quelle": "graylog", "ai_relevanz": 1, "hashtags": tags, "zeit": "2026-11-01T00:00:00Z"})
    assert b.verworfen_arten == ["serie", "ursache"]


def test_seltenes_sammelt_sich_je_art():
    assert rm.abbilde(["host:neu", "muster:m"], {"host:_selten": 0.4, "muster:m": 1.0}) == ["host:_selten", "muster:m"]
    assert rm.abbilde(["host:neu"], {"muster:m": 1.0}) == []


def test_label_mensch_vor_ki_und_regel_nie():
    assert rm.label({"herkunft": "mensch", "relevanz": 1, "ai_relevanz": 3}) == (1, "gold")
    assert rm.label({"herkunft": "mensch", "relevanz": 2}) == (0, "gold")
    assert rm.label({"herkunft": "regel", "ai_relevanz": 1}) == (1, "silber")
    assert rm.label({"herkunft": "regel", "ai_relevanz": 3}) == (0, "silber")
    # nur das Urteil der Regeln → kein Label (sonst lernte das Modell die Regeln nach)
    assert rm.label({"herkunft": "regel", "relevanz": 3, "urteil": "relevant"}) is None


def test_beobachtung_ohne_ki_befunde_und_ohne_menschen_kriterien():
    assert rm.beobachtung("1", {"quelle": "ki", "ai_relevanz": 3}) is None
    b = rm.beobachtung("2", {"quelle": "graylog", "herkunft": "mensch", "relevanz": 0, "kriterien": ["N4"],
                             "hashtags": ["muster:m"], "zeit": "2026-11-01T03:00:00+00:00"})
    assert b.y == 1 and b.herkunft == "gold"
    assert not any(x.startswith("kriterium:") for x in b.merkmale)      # Kriterien des Menschen wären ein Leck
    b = rm.beobachtung("3", {"quelle": "graylog", "ai_relevanz": 2, "kriterien": ["R2"], "hashtags": [],
                             "zeit": "2026-11-01T03:00:00Z"})
    assert "kriterium:R2" in b.merkmale and "stunde:03" in b.merkmale and b.y == 0


# ── Kennzahlen ──────────────────────────────────────────────────────────────────

def test_auc():
    assert rm.auc([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1]) == 1.0
    assert rm.auc([0.9, 0.8, 0.2, 0.1], [0, 0, 1, 1]) == 0.0
    assert rm.auc([0.5, 0.5], [0, 1]) == 0.5
    assert rm.auc([0.1, 0.2], [0, 0]) is None


def test_schwelle_laesst_hoechstens_ein_prozent_relevante_darueber():
    p = [i / 1000 for i in range(1000)]
    s = rm.schwelle_fuer(p)
    assert sum(1 for x in p if x >= s) / len(p) <= 0.01


def _daten(n: int, gold_anteil: float = 0.0, seed: int = 1) -> list[rm.Beobachtung]:
    """muster:laerm ist meist Rauschen, muster:ernst nie; Hosts zufällig — ein lernbares, ehrliches Problem."""
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        laerm = rnd.random() < 0.3
        y = 1 if (laerm and rnd.random() < 0.95) else 0
        herkunft = "gold" if rnd.random() < gold_anteil else "silber"
        tags = [f"muster:{'laerm' if laerm else 'ernst'}", f"app:a{rnd.randint(0, 3)}"]
        out.append(rm.Beobachtung(str(i), (T0 + timedelta(minutes=i)).isoformat(), y, herkunft,
                                  rm.merkmale("graylog", tags, kriterien=["R2"]),
                                  "muster:laerm" if laerm else "muster:ernst", "relevant", "R2", "x",
                                  merkmale_ohne_regel=rm.merkmale("graylog", tags, mit_regel=False)))
    return out


def test_zu_wenig_daten_mit_grund():
    m = rm.trainiere(_daten(40))
    assert m["modus"] == "zu_wenig_daten" and "nötig je 50" in m["grund"]


def test_nur_silber_ohne_folgedaten_bleibt_test_mit_grund_und_lernt_das_muster():
    m = rm.trainiere(_daten(3000))
    assert m["modus"] == "test" and "automatische Prüfung" in m["grund"]
    assert m["gewichte"]["muster:laerm"] > 0 > m["gewichte"]["muster:ernst"]
    # nur zwei Schlüssel, beide im Training: der gruppierte Holdout ist leer — benannt (None), nicht geschönt
    assert m["kennzahlen"]["auc"] is None and m["kennzahlen"]["holdout_n"] == 0
    assert m["kennzahlen"]["auc_zeitlich"] > 0.9
    assert m["kennzahlen"]["auc_zeitlich_ohne_regel_merkmale"] > 0.9   # hier trägt das Muster, nicht die Regel
    w = m["widersprueche"]
    assert w["regel_relevant_modell_rauschen"] > 0 and w["je_kriterium"].get("R2")
    assert w["schluessel"][0]["schluessel"] == "muster:laerm"


def test_bereit_mit_gold_gegen_gold_geprueft():
    labels = {"gesamt": {"rauschen": 900, "relevant": 2000}, "gold": {"rauschen": 60, "relevant": 70}}
    gut = {"auc_gold": 0.95, "recall_relevant_gold": 0.995}
    assert rm.modus(labels, gut, [])[0] == "bereit"
    assert rm.modus(labels, {**gut, "auc_gold": 0.6}, [])[0] == "test"
    assert rm.modus(labels, {**gut, "recall_relevant_gold": 0.9}, [])[0] == "test"
    m, grund = rm.modus(labels, gut, [{"klasse": "stunde", "merkmal": "stunde:13"}])
    assert m == "test" and "stunde:13" in grund


def test_ohne_gold_automatisch_freigegeben_nur_mit_folge_pruefung():
    """Nutzer 12.11.: „ich muss da nichts mehr machen“ — keine Menschen-Urteile nötig."""
    labels = {"gesamt": {"rauschen": 900, "relevant": 2000}, "gold": {"rauschen": 0, "relevant": 0}}
    gut = {"auc": 0.8, "recall_relevant": 0.995, "folge_rauschen": 0.01, "folge_relevant": 0.05,
           "folge_rauschen_n": 200}
    m, grund = rm.modus(labels, gut, [])
    assert m == "bereit" and grund.startswith("automatisch")
    assert "AUC" in rm.modus(labels, {**gut, "auc": 0.7}, [])[1]                      # strenger als mit Gold
    m, grund = rm.modus(labels, {**gut, "folge_rauschen": 0.08}, [])
    assert m == "test" and "CheckMK-CRIT" in grund
    assert rm.modus(labels, {**gut, "folge_rauschen_n": 5}, [])[0] == "test"
    assert rm.modus(labels, gut, [{"klasse": "host", "merkmal": "host:a"}])[0] == "test"


def test_folge_crit_am_selben_host_binnen_einer_stunde():
    docs = [{"quelle": "checkmk", "host": "a", "zeit": "2026-11-12T10:30:00Z", "hashtags": ["zustand:crit"]},
            {"quelle": "checkmk", "host": "b", "zeit": "2026-11-12T13:00:00Z", "hashtags": ["zustand:warn"]}]
    crit = rm.crit_zeiten(docs)
    assert rm.folge(crit, {"quelle": "graylog", "host": "a", "zeit": "2026-11-12T10:00:00Z"}) == 1
    assert rm.folge(crit, {"quelle": "graylog", "host": "a", "zeit": "2026-11-12T09:00:00Z"}) == 0     # 90 min
    assert rm.folge(crit, {"quelle": "graylog", "host": "a", "zeit": "2026-11-12T10:31:00Z"}) == 0     # danach
    assert rm.folge(crit, {"quelle": "graylog", "host": "b", "zeit": "2026-11-12T12:30:00Z"}) == 0     # nur WARN
    assert rm.folge(crit, {"quelle": "checkmk", "host": "a", "zeit": "2026-11-12T10:00:00Z"}) is None


def test_audit_host_stunde_dominant():
    g = {"host:a": 3.0, "stunde:13": 2.0, "muster:x": 1.0}
    pos = [["muster:x", "host:a"]] * 30
    klassen = {b["klasse"] for b in rm.audit(g, [], pos)}
    assert klassen == {"host", "stunde", "dominant"}
    assert rm.audit({"muster:x": 1.0}, [], [["muster:x"]] * 5) == []      # zu wenige für „dominant“


def test_audit_klassen_erschoepfend():
    assert set(rm.AUDIT_KLASSEN) == {"dominant", "host", "stunde", "widerspricht"}
    assert rm.MODI == ("zu_wenig_daten", "test", "bereit")


# ── Wirkung im Raster: ein Signal, entscheiden tut das Regelwerk ─────────────────

MODELL = {"modus": "test", "grund": "Test", "gewichte": {"muster:laerm": 5.0}, "bias": -2.0, "schwelle": 0.5}
TAGS = ["quelle:graylog", "host:h", "signal:neu", "muster:laerm"]


def _urteil(d, tags=TAGS):
    t = raster._urteil("graylog", "muster:laerm", "h", True, list(tags), {}, ["neu"], 3, "x", d)
    return raster.mit_modell([t], d, datetime(2026, 11, 12, 9, tzinfo=timezone.utc))[0]


def _mit_aktiver_n1mod():
    regeln = [replace(r, modus=AKTIV) if r.id == "N1-MOD" else r for r in standard_regeln(dict(STANDARD_OBJEKTE))]
    return Definition(16, [], dict(PARAMETER), regeln=regeln)


def test_ohne_modell_kein_signal():
    t = _urteil(STANDARD)
    assert rm.SIGNAL not in t.hashtags and t.regel == "R2"


def test_modell_setzt_signal_mit_tragenden_gewichten_testregel_zaehlt_nur():
    rm.setze(MODELL)
    t = _urteil(STANDARD)
    assert rm.SIGNAL in t.hashtags and t.regel == "R2" and t.urteil == "relevant"
    assert "N1-MOD" in t.test_treffer
    assert any("muster:laerm (+5.00)" in g for g in t.gruende)


def test_aktive_regel_wirkt_in_produktion_auch_im_modus_test_und_bleibt_auffindbar():
    """Nutzer 12.11.: „kein schattenbetrieb wir testen das in produktion“."""
    from app.services.log_watch import historie
    rm.setze(MODELL)
    t = _urteil(_mit_aktiver_n1mod())
    assert t.regel == "N1-MOD" and t.urteil == "nicht_relevant"
    assert raster.SIG_ENTSCHIED in t.hashtags and t.modell_entschied
    assert raster.Kontingent().nimm(t, datetime(2026, 11, 12, tzinfo=timezone.utc)) == 3     # bleibt in der Historie
    doc_id, doc = historie.dokument(t, "q1", datetime(2026, 11, 12, tzinfo=timezone.utc), "x", {})
    assert doc["urteil"] == "nicht_relevant" and raster.SIG_ENTSCHIED in doc["hashtags"]


def test_erkundung_haelt_etwa_fuenf_prozent_zur_messung_relevant():
    rm.setze(MODELL)
    d = _mit_aktiver_n1mod()
    b = datetime(2026, 11, 12, 9, tzinfo=timezone.utc)
    tr = [raster._urteil("graylog", "muster:laerm", f"h{i}", True, list(TAGS), {}, [], 1, "x", d) for i in range(2000)]
    raster.mit_modell(tr, d, b)
    erk = [t for t in tr if raster.SIG_ERKUNDUNG in t.hashtags]
    assert 0.03 < len(erk) / len(tr) < 0.07
    assert all(t.urteil == "relevant" and "N1-MOD" in t.test_treffer and not t.modell_entschied for t in erk)
    assert all(t.urteil == "nicht_relevant" for t in tr if t not in erk)
    # deterministisch: derselbe Fall fällt immer gleich
    assert raster.erkundung(tr[0], b) == raster.erkundung(tr[0], b)


def test_geblockte_gehen_nie_zur_ki():
    from app.services.log_watch import filter as flt, trichter
    q = str(flt.opensearch(trichter.AUSLOESER))
    assert "signal:modell-entschied" in q and "must_not" in q and "signal:auffaellig-pruefen" in q


def test_aktive_regel_wirkt_bei_bereit_aber_nie_gegen_schaden():
    rm.setze({**MODELL, "modus": "bereit"})
    t = _urteil(_mit_aktiver_n1mod())
    assert t.regel == "N1-MOD" and t.urteil == "nicht_relevant" and t.kriterien[0] == "N1"
    t = _urteil(_mit_aktiver_n1mod(), TAGS + ["signal:schaden"])
    assert t.regel == "R5" and t.urteil == "relevant"


def test_n1mod_steht_nach_schaden_sicherheit_r1_und_vor_r2():
    ids = [x[0] for x in STANDARD_FILTER]
    i = ids.index("N1-MOD")
    assert all(ids.index(r) < i for r in ("R5", "R6", "R6-FW", "R1"))
    assert all(ids.index(r) > i for r in ("R2", "R3", "R4"))
    assert next(r for r in STANDARD.regeln if r.id == "N1-MOD").modus == "test"


def test_zaehle_weist_modell_rauschen_je_urteil_aus():
    rm.setze(MODELL)
    t = _urteil(STANDARD)
    z = raster.zaehle([t], raster.Kontingent(), datetime(2026, 11, 12, tzinfo=timezone.utc))
    assert z["modell_rauschen"] == {"nachrichten": 3, "je_urteil": {"relevant": 3}, "je_kriterium": {"R2": 3},
                                    "entschied": 0, "erkundung": 0}
    assert z["auffaellig"] == {"nachrichten": 0, "je_urteil": {}, "entschied": 0}


def test_kaputtes_modell_haelt_raster_nicht_an():
    rm.setze({"modus": "test", "gewichte": {"muster:laerm": "kaputt"}, "bias": 0, "schwelle": 0.5})
    assert _urteil(STANDARD).regel == "R2"


def test_breite_der_episode_aus_den_relevanten_treffern_eines_fensters():
    assert [rm.breite_stufe(n) for n in (None, 0, 1, 2, 3, 4, 15, 16, 51)] == [None, None, "1", "2-3", "2-3", "4-15",
                                                                             "4-15", "16+", "16+"]
    rm.setze({**MODELL, "gewichte": {"breite:4-15": 5.0}})
    tr = [raster._urteil("graylog", "muster:x", f"h{i}", True, list(TAGS), {}, [], 1, "x", STANDARD) for i in range(5)]
    raster.mit_modell(tr, STANDARD)
    assert all(rm.SIGNAL in t.hashtags for t in tr)              # 5 Hosts → breite:4-15
    einzeln = raster._urteil("graylog", "muster:y", "h", True, list(TAGS), {}, [], 1, "x", STANDARD)
    assert rm.SIGNAL not in raster.mit_modell([einzeln], STANDARD)[0].hashtags


def test_gruppierter_holdout_nur_ungesehene_schluessel_und_kopien_zaehlen_einmal():
    daten = _daten(3000)
    # 400 Kopien desselben KI-Urteils an einem Schlüssel im Training
    for i in range(400):
        daten.append(rm.Beobachtung(f"k{i}", (T0 + timedelta(minutes=i)).isoformat(), 1, "silber",
                                    rm.merkmale("graylog", ["muster:kopie"]), "muster:kopie", "relevant", "R2", "x"))
    # spät: neue Schlüssel, je einmal — nur die gehören in den gruppierten Holdout
    for i in range(80):
        y = i % 2
        daten.append(rm.Beobachtung(f"n{i}", (T0 + timedelta(days=5, minutes=i)).isoformat(), y, "silber",
                                    rm.merkmale("graylog", [f"muster:neu{i}", "app:a1" if y else "app:a2"]),
                                    f"muster:neu{i}", "relevant", "R2", "x"))
    train, ho, gr = rm.teile(daten)
    assert gr and not ({b.schluessel for b in gr} & {b.schluessel for b in train})
    m = rm.trainiere(daten)
    assert m["kennzahlen"]["holdout_schluessel"] == len({b.schluessel for b in gr})
    assert "auc_zeitlich" in m["kennzahlen"] and "auc_gruppen" in m["kennzahlen"]


def test_auc_gruppen_zaehlt_je_schluessel_einmal():
    b = [rm.Beobachtung(str(i), "", y, "silber", [], k) for i, (k, y) in enumerate([("a", 1)] * 50 + [("b", 0), ("c", 1)])]
    p = [0.1] * 50 + [0.5, 0.9]
    assert rm.auc(p, [x.y for x in b]) < 0.1 and rm.auc_gruppen(p, b) == 0.5


def test_mit_gold_bereit_nur_wenn_besser_als_die_regeln():
    labels = {"gesamt": {"rauschen": 900, "relevant": 2000}, "gold": {"rauschen": 60, "relevant": 70}}
    m, grund = rm.modus(labels, {"auc_gold": 0.8, "recall_relevant_gold": 0.995, "auc_regel_gold": 0.85}, [])
    assert m == "test" and "nicht besser als die Regeln" in grund


def test_regel_urteil_ueber_eingangs_tags():
    assert rm.regel_urteil(STANDARD.regeln, {"hashtags": ["quelle:graylog", "signal:neu"]}) == 0
    assert rm.regel_urteil(STANDARD.regeln, {"hashtags": ["quelle:graylog"]}) == 1             # Default N0
    # nachträglich (KI) gesetztes signal:neu zählt nicht
    assert rm.regel_urteil(STANDARD.regeln, {"hashtags": ["quelle:graylog", "signal:neu"],
                                              "aenderungen": [{"was": "+signal:neu"}]}) == 1


def test_pruefliste_widerspruch_und_unsicher_je_schluessel_einmal():
    m = {"gewichte": {"muster:laerm": 3.0}, "bias": -1.0, "schwelle": 0.6}
    docs = [(f"a{i}", {"quelle": "graylog", "schluessel": "muster:laerm", "hashtags": ["muster:laerm"],
                       "urteil": "relevant", "zeit": "2026-11-12T08:00:00Z"}) for i in range(3)]
    docs += [("b", {"quelle": "graylog", "schluessel": "muster:ernst", "hashtags": ["muster:ernst"],
                    "urteil": "relevant", "zeit": "2026-11-12T08:00:00Z"}),
             ("c", {"quelle": "graylog", "schluessel": "muster:m", "hashtags": ["muster:laerm"], "herkunft": "mensch",
                    "urteil": "relevant", "zeit": "2026-11-12T08:00:00Z"})]
    w = rm.pruefliste(m, docs, {}, "widerspruch")
    assert w["faelle_gesamt"] == 1 and w["faelle"][0]["schluessel"] == "muster:laerm"      # Mensch-Fall nicht
    u = rm.pruefliste(m, docs, {}, "unsicher")
    assert u["faelle_gesamt"] == 2 and u["faelle"][0]["schluessel"] == "muster:ernst"       # P 0,27 näher an 0,5 als 0,88
    with pytest.raises(ValueError):
        rm.pruefliste(m, docs, {}, "egal")


def test_kriterien_der_definition_unveraendert():
    assert {k.id for k in KRITERIEN} >= {"N1", "R2"}
