"""Feed-Vorfilter auf den CheckMK-Filter des Nutzers (Nutzer 10.11.2026: „nicht nur Mails, auch der Feed soll
vorgefiltert werden“) — je Korpus das richtige Hostfeld."""
from app.services import feed_index as fi


def test_historie_filtert_auf_host_keyword_klein():
    f = fi.scope_filter(["Cue0124.example.com", "vpp0266.example.com", "", None], fi.HISTORIE)
    assert f == {"terms": {"host": ["cue0124.example.com", "vpp0266.example.com"]}}


def test_rohmeldungen_filtern_wie_bisher_ueber_metadata():
    f = fi.scope_filter(["cue0124.example.com"], fi.ROH)
    felder = [list(t["terms"])[0] for t in f["bool"]["should"]]
    assert felder == ["metadata.host.keyword", "metadata.agent.keyword", "metadata.host_candidates.keyword"]


def test_ohne_scope_kein_filter():
    assert fi.scope_filter([], fi.HISTORIE) is None and fi.scope_filter(None, fi.ROH) is None
