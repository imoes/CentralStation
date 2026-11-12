"""KI-Fragen beantworten — Ein-Klick-Links aus der Mail (signiert, ohne Login) und Liste für Angemeldete.
Siehe services/ki_fragen.py. Nutzer 12.11.2026: „in der mail und in der konsole“."""
from __future__ import annotations

import html

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from app.api.deps import CurrentUser

router = APIRouter(prefix="/ki-fragen", tags=["ki-fragen"])


def _seite(titel: str, text: str, code: int = 200) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>{html.escape(titel)}</title>"
                        f"<body style='font-family:sans-serif;max-width:40em;margin:3em auto'>"
                        f"<h2>{html.escape(titel)}</h2><p>{html.escape(text)}</p></body>", status_code=code)


@router.get("/{frage_id}/antwort", response_class=HTMLResponse)
async def antwort_per_link(frage_id: str, wahl: str = Query(...), t: str = Query(...)):
    """Ein-Klick-Antwort aus der Mail. Der Link trägt eine Signatur für genau diese Frage und Antwort."""
    from app.core.config import settings
    from app.services import ki_fragen
    if not ki_fragen.token_gueltig(settings.secret_key, frage_id, wahl, t):
        return _seite("Link ungültig", "Dieser Antwort-Link ist nicht gültig (Signatur passt nicht).", 403)
    erg = await ki_fragen.beantworte(frage_id, wahl, "mail-link")
    if erg.get("fehler"):
        return _seite("Nicht übernommen", erg["fehler"], 409)
    return _seite("Danke", f"Antwort „{ki_fragen.WAHL[wahl][0]}“ ist gelernt "
                           f"(Maßnahme/Urteil am Incident {erg.get('frage')}).")


@router.get("")
async def liste(_user: CurrentUser, host: str = Query(""), limit: int = Query(10, ge=1, le=50)):
    """Offene KI-Fragen (für Oberfläche und Konsole)."""
    from app.services import ki_fragen
    return {"offen": await ki_fragen.offene(host, limit)}
