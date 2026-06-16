# Plan: Claude Code nativer OAuth-Flow — Token-Injektion aus DB entfernen

## Context

Aktuell wird beim Start des code-server-Containers `CLAUDE_CODE_OAUTH_TOKEN` als
Umgebungsvariable gesetzt (aus dem globalen Admin-Token in der DB). Das verhindert, dass
die Claude Code Extension ihren eigenen nativen Auth-Flow durchläuft — sie nimmt stattdessen
direkt den injizierten Token und meldet sich nie interaktiv an.

Der native Auth-Flow der Claude Code Extension funktioniert wie folgt:
1. Extension erkennt: keine Credentials → zeigt "Sign In"-Button
2. `vscode.env.openExternal()` öffnet `https://claude.ai/oauth/authorize?...` im Browser
   (code-server leitet `openExternal()`-Aufrufe zum Parent-Browser weiter → funktioniert)
3. User autorisiert auf claude.ai → Redirect zu `https://console.anthropic.com/oauth/code/callback`
4. Extension zeigt VS Code Input-Box: "Authorization code eingeben"
5. User kopiert Code → Extension tauscht gegen Token → speichert in `~/.claude/`

Die Credentials werden im benannten Volume `cs-ide-cfg-<uid>` (`/root/.claude`) gespeichert
und überleben Container-Neustarts. Jeder Nutzer meldet sich einmalig an.

**Warum Loopback kein Problem ist:** Claude Code 2.x nutzt NICHT loopback für den Callback —
es nutzt Anthropics Console-Page als Redirect und wartet auf manuellen Code-Input im VS Code UI.

---

## ⏸ PAUSIERT — Evaluation: Coder vs code-server

Der OAuth-Flow-Proxy-Ansatz ist gescheitert (Loopback-Ports nicht via Docker-Netz erreichbar).
Vor weiterer Arbeit wird **Coder** (https://github.com/coder/coder) als Alternative zu
code-server evaluiert. Ergebnisse ggf. in diesem Plan ergänzen oder separaten Plan erstellen.

Aktueller Zustand der Dateien:
- `backend/app/services/ide_manager.py` — Token-Injektion bereits entfernt (kein CLAUDE_CODE_OAUTH_TOKEN)
- `backend/app/api/ide.py` — `proxy_oauth_callback`-Endpoint vorhanden (broken, weil Loopback)
- `frontend/workbench.component.ts` — OAuth-Key-Button + Dialog vorhanden
- `Dockerfile.codeserver` — kein .bashrc-Token-Sourcing mehr

---

## Änderungen

### 1. `backend/app/services/ide_manager.py`

**`ensure_container()`**: `claude_token`-Parameter entfernen, Container-Env-Var streichen:

```python
def ensure_container(user_id: str, codex_token: str | None = None) -> str:
```

Im `environment`-Dict:
```python
# ENTFERNEN:
if claude_token:
    environment["CLAUDE_CODE_OAUTH_TOKEN"] = claude_token
```

**`_write_agent_env()`**: `claude_token`-Parameter und zugehörige Zeilen entfernen.
Nur noch `OPENAI_API_KEY` (für Terminal-CLI) oder ganz entfernen wenn auch Codex nativ:

```python
def _write_agent_env(user_id: str, codex_token: str | None) -> None:
    if not codex_token:
        return
    # schreibt nur OPENAI_API_KEY für Terminal-CLI-Tools
```

Falls auch `OPENAI_API_KEY` entfernt werden soll (alle Extensions nativ): Funktion ganz
löschen und alle Aufrufe entfernen.

### 2. `backend/app/api/ide.py`

`_agent_tokens()` vereinfachen oder Aufruf anpassen — Claude-Token wird nicht mehr benötigt:

```python
async def _agent_tokens(db: AsyncSession) -> str | None:
    """Codex-Token für Terminal-CLI. Claude Code authentifiziert sich nativ."""
    try:
        from app.api.oauth_providers import get_codex_access_token
        return await get_codex_access_token(db)
    except Exception as e:
        log.debug("codex token fetch failed: %s", e)
    return None
```

Alle drei Stellen wo `ensure_container(uid, claude, codex)` aufgerufen wird, anpassen:
```python
codex = await _agent_tokens(db)
await asyncio.to_thread(ide_manager.ensure_container, uid, codex)
```

### 3. Entrypoint bleibt unverändert

- `managed-settings.json` (`disableRemoteControl: true, autoUploadSessions: false`) bleibt —
  verhindert Remote-Session-Spam (unabhängig von OAuth-Login)
- CSS/CSP/Navigator-Patches bleiben
- `~/.claude`-Volume bleibt — genau hier landen die nativen Auth-Credentials

---

## Ablauf nach der Änderung

1. User öffnet Werkbank → Container startet ohne `CLAUDE_CODE_OAUTH_TOKEN`
2. Claude Code Extension startet, findet kein Token → zeigt "Anmelden"-Button
3. User klickt → Browser öffnet claude.ai → autorisiert → sieht Code
4. VS Code zeigt Input-Box → User gibt Code ein → Token in `~/.claude/` gespeichert
5. Beim nächsten Container-Start: Token bereits im Volume → kein erneutes Anmelden

---

## Offene Frage: OPENAI_API_KEY

Der `codex_token` (OPENAI_API_KEY) wird aktuell für Terminal-CLI-Tools gesetzt — nicht für
die Extension selbst. Falls auch Continue/Codex nativ konfiguriert werden soll:
`_write_agent_env()` vollständig entfernen und `ensure_container()` auf `(user_id: str)` reduzieren.

Falls Terminal-CLI-Tools (openai, codex) weiter funktionieren sollen: `OPENAI_API_KEY` behalten.

**Standard-Plan:** Nur Claude-Token entfernen, Codex-Token für Terminal behalten.

---

## Verifikation

1. `docker compose build backend && docker compose up -d backend`
2. Werkbank öffnen → neuen Container starten (oder bestehenden via `docker rm -f cs-ide-<uid>`)
3. Claude Code Extension öffnen → zeigt "Anmelden"-Button (kein Token-Fehler)
4. Anmelden durchführen → Code eingeben → Extension zeigt eingeloggten User
5. Container neu starten → Extension ist noch eingeloggt (Volume persistiert Credentials)
6. `docker exec cs-ide-<uid> env | grep CLAUDE` → leer (kein injizierter Token)
