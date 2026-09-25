"""Welche Dateien die Konsole annimmt, und mit welcher Begründung sie eine ablehnt.

Die Regel entscheidet über Dateien, die danach im Workspace des Agenten liegen und
von ihm geöffnet werden. Fehler in beide Richtungen fallen nicht sofort auf: zu
streng, und die Excel-Datei des Nutzers wird abgewiesen; zu locker, und eine als
`.xlsx` getarnte Binärdatei landet unbesehen im Workspace.
"""
from __future__ import annotations

import pytest

from app.services.upload_policy import (
    MAX_FILE_BYTES,
    MAX_IMAGE_BYTES,
    Accepted,
    Refused,
    classify,
    safe_filename,
)

ZIP = b"PK\x03\x04" + b"\x00" * 40
OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 40
PDF = b"%PDF-1.7\n..."
PNG = b"\x89PNG\r\n\x1a\n" + b"rest"
CSV = "Datum;Umsatz;Größe\n2026-09-01;1200,50;ü\n".encode("cp1252")


@pytest.mark.parametrize("name,head,kind,label", [
    ("umsatz_q3.xlsx", ZIP, "file", "Tabelle"),
    ("alt.xls", OLE2, "file", "Tabelle"),
    ("export.csv", CSV, "file", "Tabelle"),          # cp1252 aus Excel — muss durch
    ("bericht.docx", ZIP, "file", "Dokument"),
    ("handbuch.pdf", PDF, "file", "PDF"),
    ("syslog.log", b"Sep 25 10:00:01 host sshd[1]: ok\n", "file", "Log"),
    ("config.yaml", b"key: value\n", "file", "Daten"),
    ("deploy.sh", b"#!/bin/sh\necho hi\n", "file", "Skript"),
    ("logs.zip", ZIP, "file", "Archiv"),
    ("logs.tar.gz", b"\x1f\x8b\x08\x00", "file", "Archiv"),
    ("screenshot.png", PNG, "image", "Bild"),
    ("ohne_endung", PNG, "image", "Bild"),           # Bilder erkennt man am Inhalt
])
def test_accepts(name, head, kind, label):
    r = classify(name, head, len(head))
    assert isinstance(r, Accepted), f"{name} abgelehnt: {getattr(r, 'reason', '')}"
    assert r.kind == kind
    assert r.label == label


@pytest.mark.parametrize("name,head,fragment", [
    ("leer.xlsx", b"", "leer"),
    ("getarnt.xlsx", b"MZ\x90\x00 Windows-Programm", "passt nicht"),
    ("getarnt.pdf", b"<html>", "passt nicht"),
    ("binaer.csv", b"a,b\x00\x00\x01", "Binärdaten"),
    ("fake.png", b"das ist Text", "kein gültiges Bild"),
    ("programm.exe", b"MZ\x90\x00", "wird nicht angenommen"),
    ("bibliothek.dll", b"MZ\x90\x00", "wird nicht angenommen"),
    ("ohne_endung", b"irgendwas", "ohne Endung"),
])
def test_refuses_with_a_named_reason(name, head, fragment):
    r = classify(name, head, max(len(head), 1) if head else 0)
    assert isinstance(r, Refused), f"{name} wurde angenommen"
    assert fragment in r.reason


def test_file_size_limit():
    assert isinstance(classify("gross.xlsx", ZIP, MAX_FILE_BYTES + 1), Refused)
    assert isinstance(classify("genau.xlsx", ZIP, MAX_FILE_BYTES), Accepted)


def test_image_size_limit_is_stricter():
    """Bilder gehen für Hermes als data-URL ins LLM — daher die engere Grenze."""
    assert MAX_IMAGE_BYTES < MAX_FILE_BYTES
    assert isinstance(classify("gross.png", PNG, MAX_IMAGE_BYTES + 1), Refused)


def test_extension_is_case_insensitive():
    assert isinstance(classify("UMSATZ.XLSX", ZIP, len(ZIP)), Accepted)


@pytest.mark.parametrize("raw,expected", [
    ("umsatz_q3.xlsx", "umsatz_q3.xlsx"),
    ("../../etc/passwd", "passwd"),                  # nur der letzte Pfadteil
    ("C:\\Users\\x\\bericht.pdf", "bericht.pdf"),    # Windows-Pfad aus dem Browser
    (".bashrc", "bashrc"),                           # keine versteckte Datei erzeugen
    ("Größe & Umsatz (Q3).xlsx", "Größe_Umsatz_Q3_.xlsx"),
    ("Umsatz Q3 2026.xlsx", "Umsatz_Q3_2026.xlsx"),   # kein Leerzeichen im Pfad
    ("", "datei"),
    ("  .  ", "datei"),
])
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected


def test_safe_filename_keeps_extension_when_truncating():
    name = "x" * 200 + ".xlsx"
    out = safe_filename(name, max_len=40)
    assert len(out) <= 40
    assert out.endswith(".xlsx")


def test_safe_filename_never_contains_whitespace():
    """Der Marker trennt Pfade an Leerzeichen — ein Leerzeichen im Namen zerlegt ihn."""
    for raw in ("a b.xlsx", "a\tb.csv", "a\nb.log", "  x  y  .pdf"):
        assert not any(ch.isspace() for ch in safe_filename(raw)), raw
