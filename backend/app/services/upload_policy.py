"""Welche Dateien in der Konsole hochgeladen werden dürfen — und warum eine abgelehnt wird.

Eine reine Funktion ohne Anwendungsimporte, damit sie für sich testbar bleibt.

Zwei Prüfungen, beide nötig:

* Die **Endung** entscheidet, was die Datei sein will. Sie steht auf einer
  ausdrücklichen Liste, damit vorhersehbar ist, was durchgeht — und damit eine
  Ablehnung einen Grund nennen kann statt nur "nicht erlaubt".
* Der **Inhalt** muss dazu passen. Eine `.xlsx` ist ein ZIP-Archiv, eine `.pdf`
  beginnt mit `%PDF-`, eine `.csv` enthält kein NUL-Byte. Der vom Browser gemeldete
  Content-Type wird nicht verwendet — er ist Clientdaten, genau wie die Endung, und
  erst beides zusammen mit dem Inhalt ergibt eine belastbare Aussage.

Bilder laufen über image_sniff und bekommen die Art "image": nur für sie gibt es
Vorschaubilder, Codex' `-i` und Hermes' Bildblöcke. Alles andere ist "file" — der
Agent liest es selbst über den Pfad.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.image_sniff import sniff_image

#: Obergrenze für eine Datei. nginx lässt 25 MB durch (location /api/); multipart
#: trägt etwas Verpackung auf, deshalb liegt die Grenze hier darunter — so kommt der
#: benannte Fehler von uns und nicht ein namenloses HTTP 413 von nginx.
MAX_FILE_BYTES = 20 * 1024 * 1024

#: Bilder bleiben bei 10 MB: sie gehen für Hermes als data-URL in die LLM-Anfrage,
#: und Bild-APIs lehnen größere Eingaben ohnehin ab.
MAX_IMAGE_BYTES = 10 * 1024 * 1024

_ZIP = (b"PK\x03\x04", b"PK\x05\x06")          # auch leere Archive
_OLE2 = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",)  # altes Office (.xls/.doc/.ppt)
_PDF = (b"%PDF-",)
_RTF = (b"{\\rtf",)
_GZIP = (b"\x1f\x8b",)

#: Endung → (Kategorie für die Anzeige, erwartete Signaturen oder "text").
_ALLOWED: dict[str, tuple[str, object]] = {
    # Tabellen
    ".xlsx": ("Tabelle", _ZIP), ".xlsm": ("Tabelle", _ZIP), ".ods": ("Tabelle", _ZIP),
    ".xls": ("Tabelle", _OLE2),
    ".csv": ("Tabelle", "text"), ".tsv": ("Tabelle", "text"),
    # Dokumente
    ".docx": ("Dokument", _ZIP), ".odt": ("Dokument", _ZIP),
    ".pptx": ("Präsentation", _ZIP), ".odp": ("Präsentation", _ZIP),
    ".doc": ("Dokument", _OLE2), ".ppt": ("Präsentation", _OLE2),
    ".pdf": ("PDF", _PDF), ".rtf": ("Dokument", _RTF),
    # Text und Daten
    ".txt": ("Text", "text"), ".log": ("Log", "text"), ".md": ("Text", "text"),
    ".json": ("Daten", "text"), ".yaml": ("Daten", "text"), ".yml": ("Daten", "text"),
    ".xml": ("Daten", "text"), ".toml": ("Daten", "text"), ".ini": ("Konfiguration", "text"),
    ".conf": ("Konfiguration", "text"), ".cfg": ("Konfiguration", "text"),
    ".sql": ("Daten", "text"), ".html": ("Text", "text"), ".htm": ("Text", "text"),
    # Skripte — zum Lesen und Prüfen. Ausführen auf einem Host bleibt Sache des
    # Read-only-Guards; eine hochgeladene Datei ist noch keine Erlaubnis dazu.
    ".sh": ("Skript", "text"), ".py": ("Skript", "text"), ".ps1": ("Skript", "text"),
    # Archive — Log-Pakete kommen oft so. Entpackt wird erst auf Anfrage des Agenten.
    ".zip": ("Archiv", _ZIP), ".gz": ("Archiv", _GZIP), ".tgz": ("Archiv", _GZIP),
}

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

#: Zeichen, die im gespeicherten Dateinamen stehen bleiben. Alles andere wird "_".
#: Bewusst OHNE Leerzeichen: der Nachrichten-Marker trennt mehrere Pfade an
#: Leerzeichen, und der Agent müsste den Pfad in jeder Shell-Zeile quoten — beides
#: bricht bei "Umsatz Q3.xlsx" still.
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-äöüÄÖÜß]+")


@dataclass(frozen=True)
class Accepted:
    kind: str        #: "image" oder "file"
    mime: str
    ext: str         #: mit Punkt, klein geschrieben
    label: str       #: Anzeige, z. B. "Tabelle"


@dataclass(frozen=True)
class Refused:
    reason: str      #: benannter Grund, wird dem Nutzer angezeigt


def safe_filename(name: str, max_len: int = 80) -> str:
    """Anzeigename → Dateiname, der auf der Platte liegen darf.

    Nur der letzte Pfadteil zählt (ein mitgeschicktes "../../etc/passwd" wird zu
    "passwd"), Steuer- und Sonderzeichen werden ersetzt, führende Punkte entfernt,
    damit aus einem Upload keine versteckte Datei wird.
    """
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = _SAFE_NAME.sub("_", base).strip(" ._") or "datei"
    if len(base) > max_len:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: max_len - len(ext) - 1] + "." + ext) if dot and len(ext) <= 8 \
            else base[:max_len]
    return base


def _extension(name: str) -> str:
    lower = (name or "").lower()
    if lower.endswith(".tar.gz"):
        return ".tgz"
    dot = lower.rfind(".")
    return lower[dot:] if dot > lower.rfind("/") and dot != -1 else ""


def _looks_like_text(head: bytes) -> bool:
    """Kein NUL-Byte in den ersten Kilobytes — so erkennt man Binärdaten zuverlässig.

    Die Kodierung wird bewusst nicht geprüft: eine CSV aus Excel ist oft cp1252 und
    nicht UTF-8, und genau solche Dateien sollen hier durchgehen.
    """
    return b"\x00" not in head[:8192]


def classify(name: str, head: bytes, size: int) -> Accepted | Refused:
    """Entscheidet über eine hochgeladene Datei. `head` sind die ersten Bytes."""
    if size <= 0 or not head:
        return Refused("Die Datei ist leer.")

    image = sniff_image(head)
    ext = _extension(name)

    if image:
        mime, img_ext = image
        if size > MAX_IMAGE_BYTES:
            return Refused(f"Bild zu groß ({size // 1024} KB, erlaubt sind "
                           f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB).")
        return Accepted("image", mime, img_ext, "Bild")

    if ext in _IMAGE_EXT:
        # Heißt wie ein Bild, ist aber keins — das ist der Fall, den man nicht
        # stillschweigend als "Datei" durchwinken sollte.
        return Refused(f"Die Datei endet auf {ext}, ist aber kein gültiges Bild.")

    if ext not in _ALLOWED:
        shown = ext or "(ohne Endung)"
        return Refused(
            f"Dateityp {shown} wird nicht angenommen. Erlaubt sind Tabellen, "
            f"Dokumente, PDF, Text- und Datendateien, Skripte und Archive."
        )

    label, expected = _ALLOWED[ext]
    if size > MAX_FILE_BYTES:
        return Refused(f"Datei zu groß ({size // (1024 * 1024)} MB, erlaubt sind "
                       f"{MAX_FILE_BYTES // (1024 * 1024)} MB).")

    if expected == "text":
        if not _looks_like_text(head):
            return Refused(f"Die Datei endet auf {ext}, enthält aber Binärdaten.")
        return Accepted("file", "text/plain", ext, label)

    if not any(head.startswith(sig) for sig in expected):  # type: ignore[union-attr]
        return Refused(f"Der Inhalt passt nicht zur Endung {ext} — die Datei ist "
                       f"beschädigt oder etwas anderes als sie vorgibt.")
    return Accepted("file", "application/octet-stream", ext, label)
