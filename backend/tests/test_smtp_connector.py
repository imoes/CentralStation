"""SMTP connector: the sender must survive DKIM signing at the relay.

Measured 2026-11-12 on smtp01 (PMG): a mail with ``From: …@localhost`` and no
Date/Message-ID is signed first and rewritten afterwards → dkim=fail at Microsoft;
the same mail with a fully qualified sender, Date and Message-ID → dkim=pass.
"""
import asyncio
from email.utils import parseaddr, parsedate_to_datetime

import pytest

from app.services.connectors.smtp import SMTPConnector


def _conn(**cred):
    return SMTPConnector("mailrelay.example.org", {"port": "25", **cred})


@pytest.mark.parametrize("addr, reason", [
    (None, "no sender address"),
    ("", "no sender address"),
    ("   ", "no sender address"),
    ("centralstation", "not an e-mail address"),
    ("@example.com", "not an e-mail address"),
    ("centralstation@", "not an e-mail address"),
    ("centralstation@localhost", "no fully qualified domain"),
    ("root@mon02", "no fully qualified domain"),
])
def test_unusable_sender_is_refused_with_reason(addr, reason):
    c = _conn(from_email=addr)
    assert reason in c.sender_problem()
    with pytest.raises(ValueError, match=reason):
        c.build_message("someone@example.org", "s", "<p>x</p>")


def test_missing_sender_no_longer_falls_back_to_localhost():
    c = _conn()
    assert c.from_email == ""
    assert c.sender_problem() is not None


def test_qualified_sender_builds_complete_headers():
    c = _conn(from_email="centralstation@example.com", from_name="CentralStation")
    assert c.sender_problem() is None
    msg = c.build_message("someone@example.org", "Betreff äöü", "<p>x</p>")
    assert parseaddr(msg["From"]) == ("CentralStation", "centralstation@example.com")
    assert msg["To"] == "someone@example.org"
    assert parsedate_to_datetime(msg["Date"]) is not None
    assert msg["Message-ID"].startswith("<") and msg["Message-ID"].endswith("@example.com>")


def test_message_ids_are_unique():
    c = _conn(from_email="centralstation@example.com")
    ids = {c.build_message("a@example.org", "s", "x")["Message-ID"] for _ in range(50)}
    assert len(ids) == 50


def test_connection_test_names_the_sender_problem_without_connecting():
    c = _conn(from_email="centralstation@localhost")
    res = asyncio.run(c.test_connection())
    assert res.success is False
    assert "no fully qualified domain" in res.message
