"""Neue Felder bleiben nicht still undokumentiert (Nutzer 07.11.): jedes Feld der Historie und jede bekannte
Hashtag-Art hat ihre Bedeutung im Skill, den die KI vor der Analyse liest."""
from app.services.log_watch import felder
from app.services.log_watch.historie import MAPPING
from app.services.log_watch.regelwerk import SIGNALE


def test_jedes_feld_der_historie_ist_dokumentiert():
    fehlt = sorted(set(MAPPING["properties"]) - set(felder.FELDER))
    assert fehlt == [], f"Feld ohne Bedeutung in felder.FELDER: {fehlt}"


def test_jedes_signal_ist_dokumentiert():
    assert sorted(set(SIGNALE) - set(felder.SIGNALE)) == []


def test_skill_enthaelt_felder_arten_signale_kriterien():
    s = felder.skill()
    for name in list(felder.FELDER) + list(felder.HASHTAG_ARTEN) + list(felder.SIGNALE):
        assert f"- {name}" in s, name
    assert "R5" in s and "N2" in s and "disk-full" in s


def test_undokumentierte_arten_werden_benannt():
    assert felder.undokumentiert_aus(["host", "thema", "neuart", "neuart", ""]) == ["neuart"]


def test_ki_liest_den_skill_im_systemprompt():
    from app.services.log_watch import trichter
    assert trichter.SYSTEM_MIT_SKILL.startswith(trichter.SYSTEM) and "SKILL: Felder" in trichter.SYSTEM_MIT_SKILL
