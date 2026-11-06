"""Ein Link je Vorgang: <base>/browse/<KEY>, unabhängig von der Schreibweise der Instanz-URL."""
from app.core.jira_links import browse_url


def test_browse_url_basic():
    assert browse_url("https://servicedesk.example.com", "IMIT-1234") == \
        "https://servicedesk.example.com/browse/IMIT-1234"


def test_trailing_slash_is_not_doubled():
    assert browse_url("https://jira.example.org/", "OPS-7") == "https://jira.example.org/browse/OPS-7"


def test_missing_parts_give_none_not_a_broken_link():
    assert browse_url(None, "OPS-7") is None
    assert browse_url("https://jira.example.org", "") is None
    assert browse_url("", None) is None


def test_key_is_quoted():
    # Ein Schlüssel ist normalerweise harmlos; ein kaputter darf aber nicht aus dem Pfad ausbrechen.
    assert browse_url("https://j.example", "A/../x?y") == "https://j.example/browse/A%2F..%2Fx%3Fy"
