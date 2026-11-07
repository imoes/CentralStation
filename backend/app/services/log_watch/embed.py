"""Embeddings für die Historie: bge-m3 über den OpenAI-kompatiblen llama.cpp-Endpunkt.

Gemessen 06.11.: `bge-m3-Q4_K_M`, 1 024 Dimensionen, Vektoren normiert, mehrere Texte je
Aufruf. Eingebettet wird nur, was in die Historie geht (Relevanz ≥ 2) — also wenig.
Kein `max_tokens`: es wird nichts erzeugt, nur eingebettet; zu lange Texte werden vorher
gekürzt (das Modell schneidet sonst selbst ab, ohne es zu sagen).
"""
from __future__ import annotations

import os

import httpx

URL = os.environ.get("LOG_WATCH_EMBED_URL", "https://llm-host03.example.com/embed/v1/embeddings")
DIM = 1024
#: Texte je Aufruf
STAPEL = 32
#: Zeichen je Text. Nicht das Modell begrenzt (bge-m3: 8 192 Token), sondern der Server: er nimmt je
#: Eingabe höchstens seine Batch-Größe (2 048 Token, llama-embed --batch-size). Gemessen 07.11.:
#: 4 000 Zeichen Logtext = 2 164 Token (1,85 Zeichen/Token) → HTTP 500 für den GANZEN Stapel.
#: 3 000 Zeichen ≈ 1 620 Token. Gespeichert wird der volle Text, gekürzt nur die Eingabe des Vektors.
MAX_ZEICHEN = 3000


class EmbeddingFehler(RuntimeError):
    """Der Endpunkt war nicht erreichbar oder lieferte keine passenden Vektoren."""


def vektoren_aus_antwort(antwort: dict, erwartet: int) -> list[list[float]]:
    daten = sorted(antwort.get("data") or [], key=lambda d: d.get("index", 0))
    if len(daten) != erwartet:
        raise EmbeddingFehler(f"{len(daten)} Vektoren für {erwartet} Texte")
    out = []
    for d in daten:
        v = d.get("embedding")
        if not isinstance(v, list) or len(v) != DIM:
            raise EmbeddingFehler(f"Vektor mit {len(v) if isinstance(v, list) else '?'} statt {DIM} Dimensionen")
        out.append([float(x) for x in v])
    return out


async def einbetten(texte: list[str]) -> list[list[float]]:
    """Ein Vektor je Text, in derselben Reihenfolge. Fehler sind benannt (EmbeddingFehler)."""
    out: list[list[float]] = []
    async with httpx.AsyncClient(timeout=120.0, verify=False) as client:
        for i in range(0, len(texte), STAPEL):
            teil = [(t or " ")[:MAX_ZEICHEN] for t in texte[i:i + STAPEL]]
            try:
                r = await client.post(URL, json={"input": teil})
                r.raise_for_status()
            except httpx.HTTPError as e:
                raise EmbeddingFehler(f"{type(e).__name__}: {e}") from e
            out += vektoren_aus_antwort(r.json(), len(teil))
    return out
