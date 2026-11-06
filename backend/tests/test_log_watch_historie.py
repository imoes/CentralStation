"""Historie der Log-Überwachung: Dokumentaufbau, Mapping, Embedding-Antwort, Graylog-Belege."""
from collections import Counter
from datetime import datetime, timezone

import pytest

from app.services.error_clusters.collector import BELEGE_JE_HOST, Line, TemplateTally
from app.services.log_watch import historie as h
from app.services.log_watch.embed import DIM, EmbeddingFehler, vektoren_aus_antwort
from app.services.log_watch.raster import Treffer

ZEIT = datetime(2026, 11, 6, 17, 42, 3, tzinfo=timezone.utc)


def _t(urteil="relevant"):
    return Treffer("graylog", "muster:k1", "docker0218.example.com", 2 if urteil == "relevant" else 1,
                   ["Thema „Speicher“"], ["quelle:graylog", "host:docker0218.example.com", "thema:oom"],
                   12, "OOM", urteil=urteil, kriterien=["R5"] if urteil == "relevant" else ["N1"])


def test_dokument_felder_und_stabile_id():
    doc_id, d = h.dokument(_t(), "abc-123", ZEIT, "Out of memory: Killed process 1",
                           {"level": 3, "host_groups": ["a", "b"], "x": None, "o": {"y": [1, 2]}})
    assert doc_id == "graylog:abc-123"
    assert d["relevanz"] == 2 and d["raster_v"] == h.RASTER_V
    assert d["urteil"] == "relevant" and d["herkunft"] == "regel"        # Selektor gesetzt
    assert d["kriterien"] == ["R5"] and d["definition_v"] == 1           # Urteil zitiert Regel
    assert d["hashtags"][2] == "thema:oom" and d["gruppe_anzahl"] == 12
    assert d["roh"] == {"level": 3, "host_groups": "a, b", "o": {"y": "1, 2"}}   # None fällt weg
    assert d["zeit"].startswith("2026-11-06T17:42:03")
    assert set(d) <= set(h.MAPPING["properties"])          # passt zum strikten Mapping


def test_unter_schwelle_gehoert_nicht_in_die_historie():
    with pytest.raises(ValueError):
        h.dokument(_t("nicht_relevant"), "x", ZEIT, "t", {})


def test_selektor_urteil_ist_erschoepfend():
    assert h.URTEILE == ("relevant", "nicht_relevant")
    assert h.MAPPING["properties"]["urteil"]["type"] == "keyword"
    assert "relevanz_herkunft" not in h.MAPPING["properties"]       # ein Name je Ding


def test_mapping_und_index():
    assert h.MAPPING["properties"]["vektor"]["dimension"] == DIM == 1024
    assert h.VORLAGE_BODY["template"]["settings"]["index.knn"] is True
    assert h.index_name(ZEIT) == "cs-logs-2026.11.06"


def test_embedding_antwort_geprueft():
    ok = {"data": [{"index": 1, "embedding": [0.0] * DIM}, {"index": 0, "embedding": [1.0] * DIM}]}
    v = vektoren_aus_antwort(ok, 2)
    assert v[0][0] == 1.0 and v[1][0] == 0.0                # nach index sortiert
    with pytest.raises(EmbeddingFehler):
        vektoren_aus_antwort(ok, 3)
    with pytest.raises(EmbeddingFehler):
        vektoren_aus_antwort({"data": [{"index": 0, "embedding": [0.1] * 384}]}, 1)


def test_tally_merkt_ids_je_host_bis_zum_kontingent():
    tt = TemplateTally("x")
    for i in range(BELEGE_JE_HOST + 3):
        tt.add(Line(ts=f"t{i}", source="h1", container="", message="m", id=f"id{i}"))
    tt.add(Line(ts="t", source="h2", container="", message="m", id=""))   # ohne ID: nicht gemerkt
    assert [i for i, _ in tt.belege["h1"]] == [f"id{i}" for i in range(BELEGE_JE_HOST)]
    assert tt.belege["h2"] == [] and tt.hosts == Counter({"h1": BELEGE_JE_HOST + 3, "h2": 1})


def test_firewall_zeilen_zaehlen_im_raster_nicht_als_graylog():
    tt = TemplateTally("x")
    tt.add(Line(ts="t", source="nfw062.example.com", container="", message="m", id="f1", art="firewall"))
    tt.add(Line(ts="t", source="dora", container="", message="m", id="g1"))
    assert tt.count == 2                                   # Fehler-Cluster zählt weiter alles
    assert dict(tt.hosts) == {"dora": 1} and "nfw062.example.com" not in tt.belege


def test_vergleichsfelder_der_filtersprache_stehen_im_mapping_und_im_dokument():
    from app.services.log_watch.filter import FELDER
    assert all(f in h.MAPPING["properties"] for f in FELDER)      # Suche sieht, was Regeln prüfen
    t = _t()
    t.felder = {"level": 9}
    assert h.dokument(t, "x", ZEIT, "t", {})[1]["level"] == 9
