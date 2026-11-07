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


def test_zaehlregel_aus_vokabular_und_verwerfbedingung():
    import re
    from app.services.log_watch.vokabular import THEMEN
    betroffen = [t for t in THEMEN if t.id in {x["thema"] for x in s0.widersprueche([CONTAINER_NOISE], THEMEN)}]
    rx = re.compile(s0.themen_regex(betroffen))
    assert all(rx.match(t.beispiel.lower()) for t in betroffen)            # jedes Beispiel passt
    assert not rx.match("get /api/items 200 12ms user=4711")                 # Routine nicht
    regel = s0.zaehlregel(CONTAINER_NOISE["quelltext"], betroffen)
    assert regel.startswith('rule "count_topic_rescued"') and "drop_message" not in regel
    assert 'has_field("container_name")' in regel and "set_field" not in regel   # zählt nur


def _mit_themen_zweig(themen):
    """drop_container_noise wie seit 07.11. 13:54 UTC: Behalten-Block plus regex(themen_regex) über _msg_lc."""
    literal = s0.themen_regex(themen).replace("\\", "\\\\").replace('"', '\\"')
    q = CONTAINER_NOISE["quelltext"].replace(
        '\n  )\nthen', f'\n    OR regex("{literal}", to_string($message._msg_lc)).matches == true\n  )\nthen')
    assert q != CONTAINER_NOISE["quelltext"]
    return {"titel": "drop_container_noise", "quelltext": q}


def test_themen_zweig_im_behalten_block_hebt_widersprueche_auf():
    from app.services.log_watch.vokabular import THEMEN
    regel = _mit_themen_zweig(THEMEN)
    assert s0.behalte_ausdruecke(regel["quelltext"]) == [s0.themen_regex(THEMEN)]   # Literal → Wert
    assert s0.widersprueche([regel], THEMEN) == []
    assert s0.themen_ausdruck_stand([regel], THEMEN) == [{"regel": "drop_container_noise", "aktuell": True}]


def test_veralteter_themen_ausdruck_wird_benannt():
    from app.services.log_watch.vokabular import THEMEN
    regel = _mit_themen_zweig(THEMEN[:-1])                       # ein Thema fehlt im eingespielten Ausdruck
    assert s0.themen_ausdruck_stand([regel], THEMEN) == [{"regel": "drop_container_noise", "aktuell": False}]
    assert s0.themen_ausdruck_stand([CONTAINER_NOISE], THEMEN) == []   # ohne Themen-Zweig: nichts zu prüfen


def test_zaehlregel_zaehlt_die_durch_themen_geretteten():
    from app.services.log_watch.vokabular import THEMEN
    regel = s0.zaehlregel(_mit_themen_zweig(THEMEN)["quelltext"], THEMEN)
    # der Themen-Zweig ist aus dem Behalten-Block entfernt und steht genau einmal als UND-Bedingung da
    assert regel.count("regex(") == 1 and "AND regex(" in regel
    assert "set_field" not in regel and "drop_message" not in regel


def test_zaehlregeln_nach_titel():
    regeln = [{"id": "1", "title": "count_topic_rescued"}, {"id": "2", "title": "drop_http_success"}]
    assert s0.zaehl_regeln(regeln) == [{"id": "1", "titel": "count_topic_rescued", "art": "gerettet"}]


def test_stufe0_faellig_nach_abstand_nicht_nach_minute():
    from datetime import datetime, timedelta, timezone
    from app.services.log_watch.lauf import STUFE0_ABSTAND_MIN, faellig
    jetzt = datetime(2026, 11, 7, 14, 37, tzinfo=timezone.utc)          # keine Viertelstunde — trotzdem fällig
    assert faellig(None, jetzt, STUFE0_ABSTAND_MIN)
    assert faellig(jetzt - timedelta(minutes=37), jetzt, STUFE0_ABSTAND_MIN)
    assert not faellig(jetzt - timedelta(minutes=5), jetzt, STUFE0_ABSTAND_MIN)
