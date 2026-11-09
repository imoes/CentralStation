"""alert_feedback: Rückmeldungen zu Alarmen einzeln speichern (Nutzer 09.11.2026)."""
import asyncio
import pathlib
from datetime import datetime, timezone

import pytest

from app.services import alert_feedback as fb
from app.services.alert_score_learner import _make_hash


def test_zeile_traegt_muster_wie_das_score_lernen_und_alarmzeit():
    item = {"id": "a1", "source": "checkmk", "title": "CPU load", "created_at": "2026-11-09T08:00:00Z"}
    z = fb.zeile("quittiert", item, nutzer="u1")
    assert z["muster"] == _make_hash(item)[0] and z["alert_id"] == "a1" and z["quelle"] == "checkmk"
    assert z["alarm_zeit"] == datetime(2026, 11, 9, 8, tzinfo=timezone.utc) and z["nutzer"] == "u1"


def test_unbekannte_art_und_fehlende_id_werden_abgelehnt():
    with pytest.raises(ValueError, match="erlaubt"):
        fb.zeile("gefaellt_mir", {"id": "a"})
    with pytest.raises(ValueError, match="nicht zuordenbar"):
        fb.zeile("quittiert", {"source": "x"})


def test_arten_sind_dieselben_wie_im_db_constraint():
    import ast
    baum = ast.parse(pathlib.Path("alembic/versions/0064_alert_feedback.py").read_text())
    arten = next(ast.literal_eval(n.value) for n in baum.body
                 if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "ARTEN")
    assert arten == fb.ARTEN


class _Zeile:
    def __init__(self, **kw): self.__dict__.update(kw)


@pytest.fixture(autouse=True)
def _modell(monkeypatch):
    import sys
    monkeypatch.setattr(sys.modules["app.models.workflow"], "AlertFeedback", _Zeile, raising=False)


class _Db:
    def __init__(self, fehler=False):
        self.rows, self.commits, self.rollbacks, self.fehler = [], 0, 0, fehler
    def add(self, r): self.rows.append(r)
    async def commit(self):
        if self.fehler:
            raise RuntimeError("db weg")
        self.commits += 1
    async def rollback(self): self.rollbacks += 1


def test_erfasse_speichert_und_fehler_brechen_nicht_ab(caplog):
    db = _Db()
    assert asyncio.run(fb.erfasse(db, "ausgeblendet", {"id": "x", "title": "t"}, details={"q": 1})) is True
    assert db.commits == 1 and db.rows[0].art == "ausgeblendet" and db.rows[0].details == {"q": 1}
    kaputt = _Db(fehler=True)
    assert asyncio.run(fb.erfasse(kaputt, "quittiert", {"id": "x"})) is False
    assert kaputt.rollbacks == 1 and "nicht gespeichert" in caplog.text


def test_ohne_commit_bleibt_es_in_der_transaktion_des_aufrufers():
    db = _Db()
    assert asyncio.run(fb.erfasse(db, "quittiert", {"id": "x"}, commit=False)) and db.commits == 0


def test_alle_vier_stellen_erfassen():
    feed = pathlib.Path("app/api/feed.py").read_text()
    for art in ("quittiert", "ki_analyse_angefordert", "ausgeblendet"):
        assert f'"{art}"' in feed, art
    assert '"quittiert"' in pathlib.Path("app/api/alerts.py").read_text()
    assert '"ticket"' in pathlib.Path("app/services/ai_agent/graph.py").read_text()
