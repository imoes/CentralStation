"""Bildformat-Erkennung an den Magic Bytes.

Eigenes Modul, weil die Regel nichts mit HTTP zu tun hat und ohne die halbe
Anwendung testbar sein soll: ein Import von `computer_proxy` zieht Datenbank und
Abhängigkeiten nach und macht aus einem Zweizeilen-Test einen Integrationstest.

Der vom Browser gemeldete Content-Type wird bewusst NICHT verwendet. Er ist
Clientdaten — eine als `image/png` deklarierte PHP-Datei bliebe sonst eine
PHP-Datei. Aus dem erkannten Format kommt auch die Dateiendung, damit Name und
Inhalt nicht auseinanderfallen können.
"""
from __future__ import annotations

#: (Signatur, MIME-Typ, Dateiendung) — Reihenfolge egal, die Signaturen sind eindeutig.
IMAGE_MAGIC: list[tuple[bytes, str, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
]


def sniff_image(data: bytes) -> tuple[str, str] | None:
    """(mime, Endung) anhand der Magic Bytes, oder None wenn es kein Bild ist."""
    for magic, mime, ext in IMAGE_MAGIC:
        if data.startswith(magic):
            return mime, ext
    # WebP: "RIFF" + 4 Byte Dateigröße + "WEBP". Die vier Größenbytes sind beliebig,
    # deshalb reicht ein startswith("RIFF") hier nicht — RIFF trägt auch WAVE und AVI.
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None
