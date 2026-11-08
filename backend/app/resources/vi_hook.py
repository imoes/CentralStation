#!/usr/bin/env python3
"""vi_hook — meldet jede Handlung einer Claude-Code-Sitzung an vi-mcp. Nur Standardbibliothek, eine Datei.

Claude Code ruft das Skript als Hook auf (Ereignis aus stdin: `hook_event_name`):
  PreToolUse          → vi_vorher. Gemerkte Antwort (nur MCP-Werkzeuge, nur lesend) → Aufruf verweigert, die Antwort
                        ist der Grund (Hooks können einen Aufruf erlauben/verweigern, nicht ersetzen). Hinweise der VI
                        gehen als Kontext an das Modell.
  PostToolUse         → vi_nachher mit ok=true, Ergebnis, Dauer.
  PostToolUseFailure  → vi_nachher mit ok=false, Fehler.
  SessionEnd          → Gespräch in vi-mcp beenden (DELETE).
  UserPromptSubmit    → erkennt lokal eine Korrektur („nein, …“) oder eine Note (#gut, #schlecht, #note 0.8). Gesendet wird
                        nur dieses Ergebnis, nie der Text.
Jede Claude-Sitzung (`session_id`) ist in vi-mcp genau ein Gespräch; die Mcp-Session-Id liegt in einer Zustandsdatei.

Ausfall: ist vi-mcp nicht erreichbar oder antwortet falsch, läuft der Aufruf durch (Exit 0, keine Ausgabe) — und der
Grund steht in der Protokolldatei (benannt, nicht still). Werkzeuge des Gedächtnisses selbst (vi_*) werden nicht gemeldet.

Datensparsamkeit: Geheimnisse in Argumenten und Fehlern werden vor dem Senden maskiert. Ergebnisse gehen nur bei
MCP-Werkzeugen an den Server, weil nur dort eine gemerkte Antwort möglich ist; eingebaute Werkzeuge (Bash, Read, …) und
Werkzeuge für Zugangsdaten melden nur den Ausgang. Lange Argumente eingebauter Werkzeuge werden gekürzt.

Umgebung:
  VI_MCP_URL         Standard http://127.0.0.1:8765/mcp
  VI_MCP_TOKEN_FILE  Standard ~/.config/vi-mcp/token-$USER  (Bearer-Token der Instanz, 0600)
  VI_HOOK_STATE      Standard ~/.cache/vi-mcp/hook        (Zustand je Sitzung und je tool_use_id, 0700)
  VI_HOOK_TIMEOUT    Sekunden je HTTP-Anfrage, Standard 3
"""
from __future__ import annotations

import getpass
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL = os.environ.get("VI_MCP_URL", "http://127.0.0.1:8765/mcp")
TOKEN_DATEI = Path(os.environ.get("VI_MCP_TOKEN_FILE", f"~/.config/vi-mcp/token-{getpass.getuser()}")).expanduser()
ZUSTAND = Path(os.environ.get("VI_HOOK_STATE", "~/.cache/vi-mcp/hook")).expanduser()
TIMEOUT = float(os.environ.get("VI_HOOK_TIMEOUT", "3"))
SIGNAL_TOKEN_DATEI = Path(os.environ.get("VI_MCP_SIGNAL_TOKEN_FILE", "~/.config/vi-mcp/token-nutzer")).expanduser()
KORREKTUR = re.compile(r"^\s*(nein\b|falsch\b|das (ist|war) falsch\b|das stimmt nicht\b|nicht so\b|stop+\b|doch nicht\b|no\b[,.! ]|wrong\b|that'?s (wrong|not)\b)", re.I)
NOTE = re.compile(r"(?:^|\s)#note\s+(0(?:[.,]\d+)?|1(?:[.,]0+)?)(?=\s|$)", re.I)
GUT, SCHLECHT = re.compile(r"(?:^|\s)#gut(?=\s|$)", re.I), re.compile(r"(?:^|\s)#schlecht(?=\s|$)", re.I)
MAX_ERGEBNIS = 200_000
MAX_ARG_EINGEBAUT = 300
MAX_ARG_MCP = 2000
GEHEIM_WERKZEUG = re.compile(r"pwdepot|passw|secret|credential|_auth_|token", re.I)
GEHEIM_MUSTER = [
    (re.compile(r"(?i)\bbearer\s+[^\s\"']+"), "Bearer ***"),
    (re.compile(r"(sshpass\s+-p\s*)\S+"), r"\1***"),
    (re.compile(r"(\s-u\s+[^:\s]+:)\S+"), r"\1***"),
    (re.compile(r"(://[^/\s:@]+:)[^@/\s]+@"), r"\1***@"),
    (re.compile(r"(?i)((?:passwor[dt]|passwd|pwd|secret|token|api[_-]?key)[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,;]+"), r"\1***"),
]


class Ausfall(Exception):
    """vi-mcp nicht nutzbar — der Aufruf läuft ohne Gedächtnis durch, der Grund wird protokolliert."""


def protokoll(text: str) -> None:
    try:
        ZUSTAND.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(ZUSTAND / "hook.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {text}\n")
    except OSError:
        pass


# ── Werkzeugname: mcp__<server>__<werkzeug> → <werkzeug>, sonst unverändert ─────

def werkzeug(tool_name: str) -> tuple[str, str | None]:
    """(Werkzeugname für das Gedächtnis, MCP-Server oder None). Rein."""
    if tool_name.startswith("mcp__"):
        teile = tool_name.split("__", 2)
        if len(teile) == 3 and teile[2]:
            return teile[2], teile[1]
    return tool_name, None


def lesend_angabe(tool_name: str) -> bool | None:
    """Eingebaute Werkzeuge (Bash, Read, Edit, …) werden NIE aus dem Gedächtnis beantwortet — Dateien und
    Systemzustand ändern sich zwischen zwei Aufrufen. Bei MCP-Werkzeugen entscheidet vi-mcp nach dem Namen."""
    return None if tool_name.startswith("mcp__") and not GEHEIM_WERKZEUG.search(tool_name) else False


def maskiere(text: str) -> str:
    for muster, ersatz in GEHEIM_MUSTER:
        text = muster.sub(ersatz, text)
    return text


def bereinige(wert, max_laenge: int):
    """Geheimnisse maskieren und lange Texte kürzen, rekursiv. Rein."""
    if isinstance(wert, str):
        return maskiere(wert)[:max_laenge]
    if isinstance(wert, dict):
        return {k: bereinige(v, max_laenge) for k, v in wert.items()}
    if isinstance(wert, list):
        return [bereinige(v, max_laenge) for v in wert[:50]]
    return wert


# ── Zustand: Gespräch je Sitzung, Vorher je tool_use_id ───────────────────────

def _datei(art: str, schluessel: str) -> Path:
    sicher = "".join(c if c.isalnum() or c in "-_." else "_" for c in schluessel)[:120]
    return ZUSTAND / art / f"{sicher}.json"


def lies(art: str, schluessel: str) -> dict | None:
    try:
        return json.loads(_datei(art, schluessel).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def schreibe(art: str, schluessel: str, daten: dict) -> None:
    p = _datei(art, schluessel)
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    alte = os.umask(0o077)
    try:
        tmp.write_text(json.dumps(daten))
    finally:
        os.umask(alte)
    os.replace(tmp, p)


def entferne(art: str, schluessel: str) -> None:
    try:
        _datei(art, schluessel).unlink()
    except OSError:
        pass


# ── MCP über HTTP ─────────────────────────────────────────────────────────────

def _token() -> str:
    try:
        return TOKEN_DATEI.read_text().strip()
    except OSError as e:
        raise Ausfall(f"Token-Datei {TOKEN_DATEI} nicht lesbar: {e}") from e


def _post(koerper: dict | None, sid: str | None = None, methode: str = "POST") -> tuple[int, dict, dict | None]:
    kopf = {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"}
    if sid:
        kopf["Mcp-Session-Id"] = sid
    daten = json.dumps(koerper).encode() if koerper is not None else None
    req = urllib.request.Request(URL, data=daten, headers=kopf, method=methode)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            roh = r.read()
            return r.status, dict(r.headers), (json.loads(roh) if roh else None)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        raise Ausfall(f"{URL}: {type(e).__name__}: {e}") from e


def _initialisiere(claude_sitzung: str, cwd: str | None) -> str:
    st, kopf, a = _post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "claude-code-hook", "version": "1",
                                                   "claude_session": claude_sitzung, "cwd": cwd}}})
    sid = next((v for k, v in kopf.items() if k.lower() == "mcp-session-id"), None)
    if st != 200 or not sid:
        raise Ausfall(f"initialize: HTTP {st}")
    _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    schreibe("sitzung", claude_sitzung, {"mcp_session_id": sid, "seit": time.time()})
    return sid


def rufe(claude_sitzung: str, cwd: str | None, name: str, args: dict) -> dict:
    """Werkzeug in vi-mcp aufrufen; bei unbekannter Sitzung (Server neu gestartet, abgelaufen) einmal neu anmelden."""
    z = lies("sitzung", claude_sitzung)
    sid = z["mcp_session_id"] if z else _initialisiere(claude_sitzung, cwd)
    for versuch in range(2):
        st, _k, a = _post({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": args}}, sid)
        if st == 404 and versuch == 0:
            sid = _initialisiere(claude_sitzung, cwd)
            continue
        if st != 200 or not a or "result" not in a:
            raise Ausfall(f"{name}: HTTP {st} {json.dumps(a)[:200] if a else ''}")
        text = a["result"]["content"][0]["text"]
        if a["result"].get("isError"):
            raise Ausfall(f"{name}: {text[:200]}")
        return json.loads(text)
    raise Ausfall(f"{name}: Sitzung nach Neuanmeldung weiter unbekannt")


def signal_aus(text: str) -> dict | None:
    """Rein und lokal: {art, note?} oder None. Eine ausdrückliche Note hat Vorrang vor der Korrektur."""
    if not text or text.lstrip().startswith(("/", "<")):
        return None
    if m := NOTE.search(text):
        return {"art": "note", "note": float(m.group(1).replace(",", "."))}
    if GUT.search(text):
        return {"art": "note", "note": 1.0}
    if SCHLECHT.search(text):
        return {"art": "note", "note": 0.0}
    return {"art": "korrektur"} if KORREKTUR.match(text) else None


def melde_signal(claude_sitzung: str, text: str) -> None:
    s = signal_aus(text)
    z = lies("sitzung", claude_sitzung)
    if not s or not z:
        return
    try:
        token = SIGNAL_TOKEN_DATEI.read_text().strip()
    except OSError as e:
        raise Ausfall(f"Signal-Token {SIGNAL_TOKEN_DATEI} nicht lesbar: {e}") from e
    req = urllib.request.Request(URL.rsplit("/mcp", 1)[0] + "/signal", data=json.dumps({**s, "mcp_session_id": z["mcp_session_id"]}).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=TIMEOUT).read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise Ausfall(f"Signal: {type(e).__name__}: {e}") from e


def beende(claude_sitzung: str) -> None:
    """SessionEnd hat in Claude Code standardmäßig nur 1,5 s — deshalb kurzer Timeout. Kommt das DELETE nicht an,
    schließt vi-mcp das Gespräch nach 4 h Leerlauf selbst (Zustand `abgelaufen`)."""
    global TIMEOUT
    z = lies("sitzung", claude_sitzung)
    if z:
        TIMEOUT = min(TIMEOUT, 1.0)
        _post(None, z["mcp_session_id"], "DELETE")
        entferne("sitzung", claude_sitzung)


# ── Ereignisse ────────────────────────────────────────────────────────────────

def _ergebnis_text(antwort) -> str:
    text = antwort if isinstance(antwort, str) else json.dumps(antwort, ensure_ascii=False)
    return maskiere(text)[:MAX_ERGEBNIS]


def vor_dem_werkzeug(e: dict) -> dict | None:
    """Liefert die Hook-Ausgabe (JSON für stdout) oder None (nichts zu sagen)."""
    tool_name = e.get("tool_name") or ""
    name, server = werkzeug(tool_name)
    args = bereinige(e.get("tool_input") or {}, MAX_ARG_MCP if server else MAX_ARG_EINGEBAUT)
    melde = {"tool": name, "args": args}
    lesend = lesend_angabe(tool_name)
    if lesend is not None:
        melde["lesend"] = lesend
    v = rufe(e["session_id"], e.get("cwd"), "vi_vorher", melde)
    if e.get("tool_use_id"):
        schreibe("vorher", e["tool_use_id"], {"vorher_id": v.get("vorher_id"), "t0": time.time(), "tool": name, "args": v.get("args", args)})
    if v.get("antwort") is not None:
        grund = (f"[VI-Gedächtnis] Dieselbe Anfrage wurde vor {v.get('antwort_alter_s')} s beantwortet (gilt "
                 f"{v.get('antwort_ttl_s')} s); hier die gemerkte Antwort statt eines neuen Aufrufs. Frisch erzwingen: "
                 f"vi_vergiss(tool=\"{name}\").\n\n{v['antwort']}")
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": grund[:MAX_ERGEBNIS]}}
    hinweise = v.get("hinweise") or []
    if hinweise:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "additionalContext": "[VI-Gedächtnis vor " + name + "]\n" + "\n".join(hinweise)}}
    return None


def nach_dem_werkzeug(e: dict, ok: bool) -> dict | None:
    tool_name = e.get("tool_name") or ""
    name, server = werkzeug(tool_name)
    v = lies("vorher", e.get("tool_use_id") or "") or {}
    entferne("vorher", e.get("tool_use_id") or "")
    # Dauer: von Claude Code gemessen (duration_ms, ohne Rückfragen und PreToolUse-Hooks), sonst eigene Zeitnahme
    dauer = e.get("duration_ms")
    melde = {"tool": name, "args": v.get("args", bereinige(e.get("tool_input") or {}, MAX_ARG_MCP if server else MAX_ARG_EINGEBAUT)), "ok": ok,
             "dauer_ms": int(dauer) if isinstance(dauer, (int, float)) else int((time.time() - v["t0"]) * 1000) if v.get("t0") else 0}
    if v.get("vorher_id"):
        melde["vorher_id"] = v["vorher_id"]
    lesend = lesend_angabe(tool_name)
    if lesend is not None:
        melde["lesend"] = lesend
    if ok:
        if lesend is None:  # nur hier kann eine Antwort gemerkt werden; sonst bleibt das Ergebnis in der Sitzung
            melde["ergebnis"] = _ergebnis_text(e.get("tool_response"))
    else:
        fehler = e.get("error") or e.get("tool_response") or "unbekannter Fehler"
        fehler = maskiere(fehler) if isinstance(fehler, str) else fehler
        melde["fehler"] = ("[abgebrochen] " if e.get("is_interrupt") else "") + _ergebnis_text(fehler)[:2000]
    n = rufe(e["session_id"], e.get("cwd"), "vi_nachher", melde)
    hinweise = n.get("hinweise") or []
    if hinweise:
        return {"hookSpecificOutput": {"hookEventName": e.get("hook_event_name", "PostToolUse"),
                                       "additionalContext": "[VI-Gedächtnis nach " + name + "]\n" + "\n".join(hinweise)}}
    return None


def verarbeite(e: dict) -> dict | None:
    ereignis = e.get("hook_event_name")
    if not e.get("session_id"):
        raise Ausfall("session_id fehlt im Hook-Ereignis")
    name, server = werkzeug(e.get("tool_name") or "")
    if ereignis in ("PreToolUse", "PostToolUse", "PostToolUseFailure") and server and name.startswith("vi_"):
        return None
    if ereignis == "PreToolUse":
        return vor_dem_werkzeug(e)
    if ereignis == "PostToolUse":
        return nach_dem_werkzeug(e, ok=True)
    if ereignis == "PostToolUseFailure":
        return nach_dem_werkzeug(e, ok=False)
    if ereignis == "UserPromptSubmit":
        melde_signal(e["session_id"], e.get("prompt") or "")
        return None
    if ereignis == "SessionEnd":
        beende(e["session_id"])
        return None
    return None


def main() -> int:
    try:
        e = json.load(sys.stdin)
    except json.JSONDecodeError as ex:
        protokoll(f"kein JSON auf stdin: {ex}")
        return 0
    try:
        aus = verarbeite(e)
    except Ausfall as ex:
        protokoll(f"{e.get('hook_event_name')} {e.get('tool_name', '')}: ohne Gedächtnis weiter — {ex}")
        return 0
    except Exception as ex:  # noqa: BLE001 — ein Hook-Fehler darf die Sitzung nie aufhalten
        protokoll(f"{e.get('hook_event_name')} {e.get('tool_name', '')}: interner Fehler {type(ex).__name__}: {ex}")
        return 0
    if aus:
        print(json.dumps(aus, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
