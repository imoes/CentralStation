"""Stufe 0 (Graylog-Verwerfregeln): erkennen, verorten, über Knoten summieren."""
from app.services.log_watch import stufe0 as s0
from app.services.log_watch.cockpit import korpus
from datetime import datetime, timezone

#: gekürzt wie live am 06.11. (Graylog-API)
REGELN = [
    {"id": "r1", "title": "drop_http_success", "modified_at": "2026-08-31",
     "source": 'rule "drop_http_success"\nwhen has_field("http_response_code") AND '
               'to_long($message.http_response_code) < 400\nthen drop_message();\nend'},
    {"id": "r2", "title": "route_to_enriched", "source": 'rule "x" when true then route_to_stream(id:"s");'},
    {"id": "r3", "title": "drop_alt", "source": 'rule "a" when true then drop_message(); end'},
]
PIPES = [{"id": "p1", "title": "[proc]routing", "stages": [{"stage": 6, "rules": ["drop_http_success"]}]}]
VERB = [{"stream_id": "s1", "pipeline_ids": ["p1"]}]


def test_verwerfregeln_mit_ort_feldern_und_wirkungslos_benannt():
    r = {x["titel"]: x for x in s0.verwerf_regeln(PIPES, REGELN, VERB, {"s1": "[proc]enriched"})}
    assert set(r) == {"drop_http_success", "drop_alt"}                     # nur drop_message()
    assert r["drop_http_success"]["orte"] == [{"pipeline": "[proc]routing", "stufe": 6,
                                               "streams": ["[proc]enriched"]}]
    assert r["drop_http_success"]["felder"] == ["http_response_code"] and r["drop_http_success"]["aktiv"]
    assert r["drop_alt"]["orte"] == [] and not r["drop_alt"]["aktiv"]       # in keiner Pipeline


def test_summe_ueber_knoten_und_unvollstaendig_benannt():
    k = {"a", "b", "c"}
    voll = s0.summe([("a", 80.0), ("b", 82.0), ("a", 81.0), ("c", 79.0)], k)
    assert voll == {"pro_stunde": round((81 + 82 + 79) * 3600), "knoten_gesehen": 3,
                    "knoten_erwartet": 3, "vollstaendig": True}
    halb = s0.summe([("a", 80.0)], k)
    assert halb["knoten_gesehen"] == 1 and not halb["vollstaendig"]


def test_cockpit_zeigt_an_der_quelle_verworfen():
    j = datetime(2026, 11, 6, 20, 0, tzinfo=timezone.utc)
    d = korpus([], [], {}, 5, False, j, {"vollstaendig": True, "verworfen_pro_stunde": 1085927,
                                         "regeln": [{"titel": "drop_http_success", "pro_stunde": 927885}]})
    assert d["stufe0"]["zustand"] == "ok" and d["stufe0"]["verworfen_pro_stunde"] == 1085927
    assert korpus([], [], {}, 5, False, j)["stufe0"] is None                # nie gemessen: benannt
    assert korpus([], [], {}, 5, False, j, {"fehler": "x"})["stufe0"]["zustand"] == "fehler"


#: Quelltext live am 06.11. (gekürzt um nichts Wesentliches)
CONTAINER_NOISE = {"titel": "drop_container_noise", "quelltext": '''rule "drop_container_noise"
when
  has_field("container_name")
  AND NOT starts_with(to_string($message.container_name), "cue-integrations.cue.")
  AND NOT (
    contains(to_string($message._msg_lc), "error")
    OR contains(to_string($message._msg_lc), "failed")
    OR contains(to_string($message._msg_lc), "fatal")
    OR contains(to_string($message._msg_lc), "critical")
    OR contains(to_string($message._msg_lc), "panic")
    OR contains(to_string($message._msg_lc), "exception")
    OR (has_field("http_response_code") AND to_long($message.http_response_code) >= 400)
  )
then
  drop_message();
end'''}


def test_jedes_thema_hat_ein_beispiel_das_es_selbst_erkennt():
    from app.services.log_watch.vokabular import THEMEN, themen
    for t in THEMEN:
        assert t.beispiel and t.id in {x.id for x in themen(t.beispiel)}, t.id


def test_widerspruch_stufe0_verwirft_themen_ohne_fehlerwort():
    from app.services.log_watch.vokabular import THEMEN
    w = {x["thema"] for x in s0.widersprueche([CONTAINER_NOISE], THEMEN)}
    # verworfen: typische Zeile ohne error/failed/fatal/critical/panic/exception
    assert {"oom", "disk-full", "timeout", "conn-refused", "dns", "cert", "auth-fail"} <= w
    # behalten: Zeile enthält ein Behalte-Wort, oder Stufe 0 behält per Feld (HTTP ≥ 400)
    assert not ({"kernel-io", "crashloop", "datenbank", "backup", "http-5xx"} & w)
    assert s0.widersprueche([{"titel": "x", "quelltext": "rule x when true then drop_message(); end"}], THEMEN) == []
