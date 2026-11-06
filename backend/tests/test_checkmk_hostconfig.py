"""CheckMK host_config: wirksame (geerbte) Tags und der Netzbereich aus dem Ordner.

Gemessen 06.11.: alle 3 057 Hosts hatten im Hostverzeichnis leere Kritikalität und leeren Ort —
gelesen wurde `attributes.tag_groups`, die API liefert `tag_<gruppe>`, Geerbtes nur wirksam."""
from app.services.connectors.checkmk import host_aus_config


def _item(folder="/kassel/dmz-i", eff=None, attrs=None):
    return {"id": "haproxy01.example.net", "extensions": {
        "folder": folder, "attributes": attrs or {"tag_tg-os": "linux"},
        "effective_attributes": eff if eff is not None else {
            "tag_criticality": "prod", "tag_tg-location": "KAS-0", "tag_networking": "lan",
            "labels": {"hg:proxy": "yes"}}}}


def test_wirksame_tags_auch_geerbt():
    md = host_aus_config(_item())["metadata"]
    assert md["criticality"] == "prod" and md["location"] == "KAS-0" and md["hostgroups"] == ["proxy"]


def test_netzbereich_aus_dem_ordner_nicht_aus_dem_tag():
    md = host_aus_config(_item())["metadata"]
    assert (md["netz"], md["netzbereich"], md["folder"]) == ("dmz", "dmz-i", "/kassel/dmz-i")
    md = host_aus_config(_item("/muenchen/mue-0/internet/web-dmz"))["metadata"]
    assert (md["netz"], md["netzbereich"]) == ("dmz", "web-dmz")
    md = host_aus_config(_item("/muenchen/mue-0/server"))["metadata"]
    assert (md["netz"], md["netzbereich"]) == ("intern", "")
    assert host_aus_config(_item(""))["metadata"]["netz"] == ""        # Ordner unbekannt: benannt leer


def test_alte_api_form_tag_groups():
    md = host_aus_config(_item(eff={}, attrs={"tag_groups": {"criticality": "test"}}))["metadata"]
    assert md["criticality"] == "test"


def test_hashtags_netz_im_raster():
    from app.services.log_watch.hosts import Hostverzeichnis
    from app.services.log_watch.raster import _host_tags
    v = Hostverzeichnis.aus_checkmk([host_aus_config(_item())])
    _, e, tags = _host_tags("haproxy01.example.net", v)
    assert {"kritikalitaet:prod", "ort:KAS-0", "netz:dmz", "netzbereich:dmz-i"} <= set(tags)
