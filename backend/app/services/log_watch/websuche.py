"""Websuche für Logzeilen, deren Bedeutung die KI nicht sicher kennt (Nutzer 07.11.: „nur bei Logeinträgen, bei
denen die KI sich nicht sicher ist, was sie bedeuten — z. B. Stack Overflow kann solche Informationen
liefern“; Suchmaschine: SearXNG auf llm-host03, wie Hermes sucht).

Was die Suche verlässt, ist bereinigt: SearXNG reicht die Anfrage an externe Suchmaschinen weiter, darum
werden interne Namen entfernt — Hostnamen (cue0148, *.example.com …), IP-Adressen, E-Mail-Adressen, UUIDs,
lange Hex-Kennungen, Benutzerpfade und Werte von password=/token=/key=. Bleibt danach zu wenig übrig, wird
nicht gesucht (Grund benannt). Fehlercodes in Großbuchstaben (ORA-12541, AH01114, T1040) bleiben.

`seite(url)` lädt nur öffentliche HTTP(S)-Seiten — nie interne Adressen (keine Anfrage ins eigene Netz über
die KI). Ergebnisse sind Kontext: im Befund nennen, nie als Beleg zitieren.
"""
from __future__ import annotations

import html
import ipaddress
import re
from urllib.parse import urlparse

#: Standard-Adresse; überschreibbar über die Einstellung `log_watch.websuche_url`
SEARXNG_URL = "http://llm-host03.example.com:8080"
EINSTELLUNG = "log_watch.websuche_url"
TREFFER_MAX = 5
SEITE_MAX = 6000
INTERNE_DOMAINS = ("example.com", "example.org", "example.net", "zo-intern.de", "local", "intern", "lan")

_MUSTER = [
    ("Benutzerpfad", re.compile(r"/(?:home|Users)/[^\s/]+\S*")),
    ("E-Mail", re.compile(r"\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b")),
    ("URL", re.compile(r"\bhttps?://\S+")),
    ("UUID", re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b(?:[0-9a-fA-F]{1,4}:){3,7}[0-9a-fA-F]{1,4}\b")),
    ("interner Name", re.compile(r"\b[\w.-]+\.(?:" + "|".join(re.escape(d) for d in INTERNE_DOMAINS) + r")\b", re.I)),
    # Hostnamen nach Hausschema (kleinbuchstaben + ≥ 2 Ziffern, z. B. cue0148, nsa383, docker0218) — Fehlercodes in
    # Großbuchstaben (ORA-12541, AH01114) bleiben
    ("Hostname", re.compile(r"(?<![\w-])[a-z][a-z-]*\d{2,}[a-z0-9-]*(?![\w-])")),
    ("Geheimnis", re.compile(r"\b(?:password|passwd|pwd|token|secret|key|apikey|api_key)\s*[=:]\s*\S+", re.I)),
    ("Kennung", re.compile(r"\b[0-9a-fA-F]{12,}\b")),
]


def bereinige(anfrage: str) -> tuple[str, list[str]]:
    """Interne Namen aus einer Suchanfrage entfernen. Rückgabe (bereinigt, entfernte Arten). Rein — testbar."""
    t = anfrage or ""
    entfernt: list[str] = []
    for art, m in _MUSTER:
        if m.search(t):
            entfernt.append(art)
            t = m.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip(" \"'")
    return t, sorted(set(entfernt))


def oeffentlich(url: str) -> str | None:
    """None, wenn die URL eine öffentliche HTTP(S)-Adresse ist; sonst der Grund. Rein — testbar."""
    try:
        u = urlparse(url)
    except ValueError:
        return "URL nicht lesbar"
    if u.scheme not in ("http", "https") or not u.hostname:
        return "nur http(s)-Adressen"
    h = u.hostname.lower()
    if h == "localhost" or any(h == d or h.endswith("." + d) for d in INTERNE_DOMAINS) or "." not in h:
        return "interne Adresse — die KI lädt keine Seiten aus dem eigenen Netz"
    try:
        ip = ipaddress.ip_address(h)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return "private IP-Adresse"
    except ValueError:
        pass
    return None


def text_aus_html(roh: str) -> str:
    """Lesbarer Text einer HTML-Seite (ohne Skripte/Stile/Navigation). Rein."""
    t = re.sub(r"(?is)<(script|style|noscript|nav|footer|header|svg)[^>]*>.*?</\1>", " ", roh or "")
    t = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</pre>|</tr>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


async def _basis() -> str:
    from app.core.database import AsyncSessionLocal
    from app.services.settings import get_setting
    try:
        async with AsyncSessionLocal() as db:
            return ((await get_setting(db, EINSTELLUNG)) or SEARXNG_URL).rstrip("/")
    except Exception:  # noqa: BLE001
        return SEARXNG_URL


async def suche(anfrage: str, warum: str) -> dict:
    """Websuche über SearXNG — nur mit Begründung, nur bereinigt."""
    import httpx
    if not (warum or "").strip():
        return {"fehler": "warum ist Pflicht: welche Logzeile ist unklar und was soll die Suche klären?"}
    q, entfernt = bereinige(anfrage)
    if len(q.split()) < 2:
        return {"fehler": "nach dem Entfernen interner Namen bleibt keine sinnvolle Anfrage — suche nach der "
                          "Fehlermeldung oder dem Code, nicht nach Host oder Adresse", "entfernt": entfernt}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{await _basis()}/search", params={"q": q, "format": "json"})
        r.raise_for_status()
        j = r.json()
    except Exception as e:  # noqa: BLE001 — Ausfall benannt
        return {"fehler": f"Websuche nicht erreichbar: {type(e).__name__}: {str(e)[:160]}", "anfrage": q}
    treffer = [{"titel": x.get("title", ""), "url": x.get("url", ""), "auszug": (x.get("content") or "")[:400],
                "suchmaschine": x.get("engine", "")} for x in (j.get("results") or [])[:TREFFER_MAX]]
    return {"anfrage": q, "entfernt": entfernt, "treffer": treffer, "gesamt": len(j.get("results") or []),
            "hinweis": "Kontext, kein Beleg — im Befund mit URL nennen; Seite lesen mit web_seite(url)"}


#: Stack-Exchange-Seiten lassen keine Skripte auf die Webseite (gemessen 07.11.: 403 auch mit Browser-Kennung) —
#: die öffentliche API liefert Frage und beste Antworten (ohne Schlüssel 300 Abrufe/Tag, Rest wird genannt)
_SE_SITES = {"stackoverflow.com": "stackoverflow", "serverfault.com": "serverfault", "superuser.com": "superuser",
             "askubuntu.com": "askubuntu"}


def stackexchange(url: str) -> tuple[str, str] | None:
    """(site, frage_id) einer Stack-Exchange-Frage-URL, sonst None. Rein — testbar."""
    u = urlparse(url)
    h = (u.hostname or "").lower().removeprefix("www.")
    site = _SE_SITES.get(h) or (h.split(".")[0] if h.endswith(".stackexchange.com") else None)
    m = re.match(r"/questions/(\d+)", u.path or "")
    return (site, m.group(1)) if site and m else None


async def _stackexchange(site: str, frage: str, url: str) -> dict:
    import httpx
    api = "https://api.stackexchange.com/2.3"
    async with httpx.AsyncClient(timeout=20) as c:
        q = (await c.get(f"{api}/questions/{frage}", params={"site": site, "filter": "withbody"})).json()
        a = (await c.get(f"{api}/questions/{frage}/answers", params={"site": site, "order": "desc", "sort": "votes",
                                                                     "filter": "withbody", "pagesize": 3})).json()
        # die neueste Antwort dazu: im Forum steht die Lösung meist im letzten Beitrag (Nutzer 07.11.)
        neu = (await c.get(f"{api}/questions/{frage}/answers", params={"site": site, "order": "desc",
                                                                       "sort": "creation", "filter": "withbody",
                                                                       "pagesize": 1})).json()
    if q.get("error_message"):
        return {"fehler": f"Stack Exchange: {q['error_message']}", "url": url}
    f = (q.get("items") or [{}])[0]
    teile = [f"FRAGE: {html.unescape(f.get('title', ''))}\n{text_aus_html(f.get('body', ''))}"]
    beste = {x.get("answer_id") for x in a.get("items") or []}
    for x in neu.get("items") or []:
        if x.get("answer_id") not in beste:
            teile.append(f"NEUESTE ANTWORT (oft die Lösung; Punkte {x.get('score')}"
                         f"{', akzeptiert' if x.get('is_accepted') else ''}):\n{text_aus_html(x.get('body', ''))}")
    for x in a.get("items") or []:
        teile.append(f"ANTWORT (Punkte {x.get('score')}{', akzeptiert' if x.get('is_accepted') else ''}):\n"
                     f"{text_aus_html(x.get('body', ''))}")
    text = "\n\n".join(teile)
    return {"url": url, "text": text[:SEITE_MAX], "laenge": len(text), "quelle": "api.stackexchange.com",
            "kontingent_rest": a.get("quota_remaining")}


async def seite(url: str) -> dict:
    """Eine öffentliche Seite als Text (höchstens SEITE_MAX Zeichen)."""
    import httpx
    grund = oeffentlich(url)
    if grund:
        return {"fehler": grund, "url": url}
    se = stackexchange(url)
    if se:
        try:
            return await _stackexchange(*se, url)
        except Exception as e:  # noqa: BLE001
            return {"fehler": f"Stack Exchange nicht lesbar: {type(e).__name__}: {str(e)[:160]}", "url": url}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": "CentralStation"}) as c:
            r = await c.get(url)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return {"fehler": f"Seite nicht lesbar: {type(e).__name__}: {str(e)[:160]}", "url": url}
    if r.url and oeffentlich(str(r.url)):                 # Weiterleitung ins eigene Netz
        return {"fehler": "Weiterleitung auf eine interne Adresse", "url": url}
    text = text_aus_html(r.text) if "html" in r.headers.get("content-type", "") else r.text
    return {"url": str(r.url), "text": kuerze_mit_ende(text), "laenge": len(text),
            **({"gekuerzt": f"{len(text)} Zeichen: Anfang und Ende gezeigt (in Foren steht die Lösung meist im "
                            f"letzten Beitrag)"} if len(text) > SEITE_MAX else {})}


def kuerze_mit_ende(text: str, grenze: int = SEITE_MAX) -> str:
    """Lange Seite → Anfang (Frage) + Ende (letzte Beiträge, meist die Lösung — Nutzer 07.11.). Rein."""
    if len(text) <= grenze:
        return text
    kopf = grenze // 3
    return text[:kopf] + "\n[… Mitte ausgelassen …]\n" + text[-(grenze - kopf - 30):]
