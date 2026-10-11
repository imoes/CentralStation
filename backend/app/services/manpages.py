"""Manpages und Fähigkeiten eines Servers — über den Prüf-Container, knapp aufbereitet.

Eine ganze Manpage (rsync: ~4 500 Zeilen) würde den Kontext eines Agenten füllen. Hier
wird geschnitten: NAME, SYNOPSIS, der Anfang von DESCRIPTION und — mit `focus` — genau
die Optionen/Absätze, um die es geht. Ergebnisse werden zwischengespeichert; dieselbe
Seite auf demselben Host ändert sich selten.

Fehler bleiben unterscheidbar (ein Fehler sieht nie aus wie "keine Treffer"):
host_unknown | not_configured | refused | runner_error | ssh_failed | no_manpage.
"""
from __future__ import annotations

import re
import time
from typing import Any

from app.services.probe_client import ProbeError, run_probe

_CACHE_TTL = {"man_read": 6 * 3600.0, "man_search": 3600.0, "host_capabilities": 3600.0}
_cache: dict[tuple, tuple[float, dict]] = {}
MAX_CHARS = 7000

_APROPOS = re.compile(r"^(?P<name>\S+(?:, \S+)*)\s+\((?P<sec>[^)]+)\)\s+-+\s+(?P<desc>.*)$")
_HEADING = re.compile(r"^[A-Z][A-Z0-9 ,/&()-]{1,60}$")


def _cached(key: tuple) -> dict | None:
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL.get(key[0], 3600.0):
        return {**hit[1], "cached": True}
    return None


def _store(key: tuple, value: dict) -> dict:
    _cache[key] = (time.monotonic(), value)
    return value


def _error(exc: ProbeError) -> dict:
    return {"ok": False, "error": exc.kind, "message": str(exc)}


async def _probe(host, template, params, user_id) -> dict:
    return await run_probe(host, template, params, user_id=user_id, source="mcp", agent=True)


# ── Suchen ───────────────────────────────────────────────────────────────────

def parse_apropos(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        m = _APROPOS.match(line.strip())
        if m:
            out.append({"name": m["name"], "section": m["sec"], "summary": m["desc"].strip()})
    return out


async def search_manpages(host: str, query: str, user_id: str | None) -> dict[str, Any]:
    key = ("man_search", host.lower(), query.lower())
    if (hit := _cached(key)) is not None:
        return hit
    try:
        res = await _probe(host, "man_search", {"query": query}, user_id)
    except ProbeError as exc:
        return _error(exc)
    if res.get("error"):
        return {"ok": False, "error": res["error"], "message": (res.get("stderr") or "")[:300]}
    results = parse_apropos(res.get("stdout") or "")
    return _store(key, {"ok": True, "host": res["host"], "query": query, "results": results,
                        "note": None if results else "keine Treffer (oder apropos-Datenbank leer)"})


# ── Lesen ────────────────────────────────────────────────────────────────────

def split_sections(text: str) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    cur, buf = "", []
    for line in text.splitlines():
        if _HEADING.match(line.rstrip()) and not line.startswith(" "):
            if cur or buf:
                sections.append((cur, buf))
            cur, buf = line.strip(), []
        else:
            buf.append(line.rstrip())
    if cur or buf:
        sections.append((cur, buf))
    return sections


def _blocks(lines: list[str]) -> list[list[str]]:
    """Absätze/Options-Einträge: ein Eintrag beginnt mit einer (weniger eingerückten) Zeile."""
    blocks, cur = [], []
    for line in lines:
        if not line.strip():
            if cur:
                cur.append("")
            continue
        indent = len(line) - len(line.lstrip())
        if cur and indent <= _indent(cur[0]) and line.lstrip().startswith(("-", "+")):
            blocks.append(cur)
            cur = []
        cur.append(line)
    if cur:
        blocks.append(cur)
    return blocks


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _head_names(head: str) -> list[str]:
    """'--delete, -d' / '-n, --dry-run' / '--rsh=COMMAND, -e' → ['--delete', '-d', …]."""
    return [re.split(r"[=\s\[]", part.strip(), maxsplit=1)[0]
            for part in head.split(",") if part.strip()]


def _focus_matches(sections: list[tuple[str, list[str]]], f: str) -> list[str]:
    """Erst Einträge, die die gesuchte Option DEFINIEREN (Kopfzeile beginnt damit);
    nur wenn es keine gibt, die Absätze, die sie erwähnen — und dann nur den Absatz."""
    defs: list[str] = []
    for name, lines in sections:
        for block in _blocks(lines):
            head = block[0].strip().lower()
            is_option_entry = head.startswith(("-", "+"))
            if (is_option_entry and f in _head_names(head)) or \
                    (not f.startswith("-") and head.startswith(f)):
                defs.append(f"[{name}]\n" + "\n".join(block).rstrip())
    if defs:
        return defs
    mentions: list[str] = []
    for name, lines in sections:
        para: list[str] = []
        for line in lines + [""]:
            if line.strip():
                para.append(line)
                continue
            if para and f in " ".join(para).lower():
                mentions.append(f"[{name}]\n" + "\n".join(para).rstrip())
            para = []
    return mentions[:5]


def condense(text: str, focus: str = "") -> dict:
    """Manpage auf das Wesentliche schneiden (reine Funktion, testbar)."""
    sections = split_sections(text)
    by_name = {name: lines for name, lines in sections}
    parts: list[str] = []
    for name in ("NAME", "SYNOPSIS"):
        if name in by_name:
            parts.append(name + "\n" + "\n".join(by_name[name]).strip("\n"))
    matches: list[str] = []
    if focus:
        matches = _focus_matches(sections, focus.strip().lower())
        if matches:
            parts.append("TREFFER ZU '" + focus + "'\n" + "\n\n".join(matches[:8]))
    if not focus or not matches:
        desc = "\n".join(by_name.get("DESCRIPTION", [])).strip()
        if desc:
            parts.append("DESCRIPTION (Anfang)\n" + desc[:1500])
    parts.append("ABSCHNITTE: " + ", ".join(n for n, _ in sections if n))
    body = "\n\n".join(parts)
    return {"text": body[:MAX_CHARS], "truncated": len(body) > MAX_CHARS,
            "focus_found": bool(matches) if focus else None, "total_lines": len(text.splitlines())}


async def read_manpage(host: str, command: str, focus: str, section: str,
                       user_id: str | None) -> dict[str, Any]:
    key = ("man_read", host.lower(), command, section)
    raw = _cached(key)
    if raw is None:
        try:
            res = await _probe(host, "man_read", {"page": command, "section": section}, user_id)
        except ProbeError as exc:
            return _error(exc)
        if res.get("error"):
            return {"ok": False, "error": res["error"], "message": (res.get("stderr") or "")[:300]}
        text = res.get("stdout") or ""
        if not text.strip():
            return {"ok": False, "error": "no_manpage", "host": res["host"],
                    "message": f"Keine Manpage für '{command}' auf {res['host']} — "
                               "Befehl fehlt oder Manpages sind nicht installiert. "
                               "host_capabilities zeigt, welche Werkzeuge vorhanden sind."}
        raw = _store(key, {"host": res["host"], "text": text})
    return {"ok": True, "host": raw["host"], "command": command, "section": section or None,
            **condense(raw["text"], focus), "cached": raw.get("cached", False)}


# ── Fähigkeiten ──────────────────────────────────────────────────────────────

def parse_capabilities(text: str) -> dict:
    blocks: dict[str, list[str]] = {"os": []}
    cur = "os"
    for line in text.splitlines():
        if line.startswith("---"):
            cur = line[3:].strip()
            blocks[cur] = []
        else:
            blocks.setdefault(cur, []).append(line)
    os_info = {}
    for line in blocks.get("os", []):
        if "=" in line:
            k, v = line.split("=", 1)
            if k in ("ID", "VERSION_ID", "PRETTY_NAME", "ID_LIKE"):
                os_info[k.lower()] = v.strip('"')
    tools = {}
    for line in blocks.get("tools", []):
        name, _, path = line.partition(" ")
        if name and path:
            tools[name] = path
    sudo = [l.strip() for l in blocks.get("sudo", []) if l.strip()]
    return {"os": os_info, "kernel": " ".join(blocks.get("uname", [])).strip(),
            "identity": " ".join(blocks.get("id", [])).strip(), "tools": tools,
            "sudo": sudo[:30]}


async def host_capabilities(host: str, user_id: str | None) -> dict[str, Any]:
    key = ("host_capabilities", host.lower())
    if (hit := _cached(key)) is not None:
        return hit
    try:
        res = await _probe(host, "host_capabilities", {}, user_id)
    except ProbeError as exc:
        return _error(exc)
    if res.get("error"):
        return {"ok": False, "error": res["error"], "message": (res.get("stderr") or "")[:300]}
    caps = parse_capabilities(res.get("stdout") or "")
    return _store(key, {"ok": True, "host": res["host"], **caps,
                        "note": "Ermittelt mit dem System-Prüfaccount — die Rechte (sudo) "
                                "gelten für diesen Account, nicht zwingend für deinen SSH-Nutzer."})
