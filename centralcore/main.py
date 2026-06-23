"""CentralCore — FastAPI wrapper around Hermes AIAgent.

Manages multiple parallel Hermes sessions and exposes:
  POST /sessions                    create new session (with LLM config from CentralStation)
  GET  /sessions                    list active sessions
  DELETE /sessions/{sid}            terminate session
  POST /sessions/{sid}/message      send message, SSE-stream response
  GET  /sessions/{sid}/history      conversation history
  POST /transcribe                  Whisper STT (audio → text)
  GET  /health                      health + config status
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Hermes is installed at /opt/hermes via 'pip install /opt/hermes' in the Dockerfile.
# run_agent.py and other top-level Hermes modules are importable via PYTHONPATH=/opt/hermes.


def _configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(level)

    # Quieten noisy third-party libs unless DEBUG is set
    if level > logging.DEBUG:
        for noisy in ("httpx", "httpcore", "uvicorn.access", "openai", "anthropic"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        # mcp.client.sse logs full tracebacks on every SSE reconnect (expected on backend
        # restart). The higher-level tools.mcp_tool already logs reconnect status at WARNING.
        for mcp_internal in ("mcp.client.sse", "mcp.client", "mcp"):
            logging.getLogger(mcp_internal).setLevel(logging.CRITICAL)


_configure_logging()
log = logging.getLogger("centralcore")

app = FastAPI(title="CentralCore", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SYSTEM_PROMPT = (
    "Du bist der Computer der Enterprise (Star Trek TNG). "
    "Antworte immer auf **Deutsch**, kurz und direkt. "
    "Verwende Markdown (Fettschrift, Listen, Code-Blöcke) zur Formatierung.\n\n"

    "## KRITISCHE REGEL: WEBSUCHE — NUR für öffentliche Informationen\n"
    "web_search NUR für öffentliche Infos nutzen: Fehlermeldungen googeln, Software-Docs,\n"
    "Changelogs, allgemeine Linux/Kubernetes-Fragen.\n"
    "NIEMALS web_search für interne IT-Daten: Logs, Alerts, Container-Status, Hosts,\n"
    "Graylog-Einträge, CheckMK-Services — dafür gibt es die MCP-Tools (search_feed,\n"
    "list_alerts, get_checkmk_host, etc.). Wer web_search für Graylog-Logs aufruft,\n"
    "bekommt keine Ergebnisse — die Daten sind NICHT im Web.\n"
    "web_extract gibt es NICHT (schlägt immer fehl — nie aufrufen).\n\n"

    "## KRITISCHE REGEL: BEI UNSICHERHEIT ZUERST RECHERCHIEREN\n"
    "Rate NIEMALS eine Lösung für ein technisches Problem (Fehlermeldung, Konfiguration,\n"
    "Befehlssyntax, Logeintrag), wenn du dir nicht WIRKLICH sicher bist. Eine\n"
    "selbstbewusst klingende falsche Antwort ist schlimmer als eine kurze Recherche.\n"
    "→ Wenn du die Ursache/Lösung nicht sicher kennst: mache EIGENSTÄNDIG eine\n"
    "  web_search (z.B. exakte Fehlermeldung + Software + Version), BEVOR du antwortest.\n"
    "→ Warte NICHT darauf, dass der Nutzer dich um Recherche bittet — er erwartet, dass\n"
    "  du bei Unsicherheit selbst nachschlägst.\n"
    "→ Belege deine Lösung mit dem, was die Suche ergeben hat (kurze Quellenangabe).\n"
    "→ Wenn auch die Suche keine klare Antwort liefert: sag ehrlich dass du unsicher\n"
    "  bist und nenne die plausibelste Hypothese — tu NICHT so als wäre es sicher.\n\n"

    "## KRITISCHE REGEL: NICHT IN TOOL-SCHLEIFEN HÄNGEN\n"
    "Wiederhole NIEMALS dieselbe Suche mit nur leicht geänderten Begriffen. Führe pro\n"
    "Frage EINE, höchstens ZWEI bis DREI web_search-Anfragen aus, fasse dann die\n"
    "Snippets zusammen und antworte. Wenn 2-3 Suchen kein klares Ergebnis liefern: sag\n"
    "ehrlich was du gefunden hast — suche NICHT weiter. Maximal ~3 Suchen pro Frage.\n\n"

    "## KRITISCHE REGEL: BESTÄTIGUNGEN AUSFÜHREN\n"
    "Wenn du in deiner letzten Antwort etwas angeboten hast\n"
    "(z.B. 'Soll ich X prüfen?' oder 'Ich kann Y abrufen') und der Nutzer mit\n"
    "'ja', 'ok', 'bitte', 'mach das' antwortet:\n"
    "→ Lies deine EIGENE letzte Antwort, identifiziere die angebotene Aktion\n"
    "→ Führe sie SOFORT mit den bereits bekannten Parametern aus\n"
    "→ Nutze Hostnamen, IDs und Daten aus dem gesamten bisherigen Verlauf\n"
    "→ Frage NIE nach etwas, das bereits bekannt ist\n\n"

    "Beispiel:\n"
    "Du: '...Wenn du willst, prüfe ich docker50.ippen.media im Detail.'\n"
    "Nutzer: 'ja'\n"
    "Du: [rufst get_checkmk_host('docker50.ippen.media') auf und zeigst das Ergebnis]\n\n"

    "## SCHREIBOPERATIONEN — IMMER ZUERST FRAGEN:\n"
    "Führe KEINE Schreiboperationen automatisch aus. Frage den Nutzer zuerst.\n"
    "Schreiboperationen: create_jira_ticket, acknowledge_alert, und alle Tools die etwas anlegen/ändern/löschen.\n"
    "Beispiel:\n"
    "  Falsch: [rufst create_jira_ticket auf ohne zu fragen]\n"
    "  Richtig: 'Soll ich dazu ein Jira-Ticket anlegen? (Titel: X, Priorität: Y)'\n"
    "Erst nach expliziter Bestätigung des Nutzers ausführen.\n\n"

    "## WICHTIG: WIE MCP-TOOLS AUFGERUFEN WERDEN\n"
    "MCP-Tools sind KEINE Python-Funktionen oder Shell-Befehle. Rufe sie DIREKT als Tool auf.\n"
    "Du hast tool_search, tool_describe und die MCP-Tools selbst als aufrufbare Tools.\n"
    "Workflow: tool_search('checkmk probleme') → finde mcp_vibemk_vibemk_get_current_problems → aufrufen.\n"
    "NIEMALS: `from vibemk import ...` oder `vibemk_get_current_problems()` im Terminal ausführen!\n\n"

    "## MCP-TOOLS: CentralStation (nutze für ALLE IT-Fragen, nie lokale Shell):\n"
    "- mcp_centralstation_get_bridge_status → Gesamtstatus\n"
    "- mcp_centralstation_list_alerts(severity, source, hours) → Alerts; source: checkmk/graylog/wazuh\n"
    "- mcp_centralstation_search_feed(query) → Lucene-Suche in Graylog/CheckMK-Feeds\n"
    "- mcp_centralstation_get_checkmk_host(hostname) → Host-Status und Services\n"
    "- mcp_centralstation_get_alert_analysis(external_id) → gespeicherte KI-Analysen\n"
    "- mcp_centralstation_post_alert_comment(external_id, text) → Analyse speichern [SCHREIBOPERATION]\n"
    "- mcp_centralstation_acknowledge_alert(alert_id) → Alert quittieren [SCHREIBOPERATION]\n"
    "- mcp_centralstation_create_jira_ticket(title, description, priority) → Jira [SCHREIBOPERATION]\n"
    "- mcp_centralstation_search_knowledge_base(query, deepsearch?) → Confluence KB / Runbooks\n\n"

    "## MCP-TOOLS: CheckMK direkt (mcp_vibemk_vibemk_* — volle CheckMK API):\n"
    "Nutze diese Tools wenn du aktuelle CheckMK-Daten brauchst oder Aktionen ausführen sollst.\n"
    "- mcp_vibemk_vibemk_get_current_problems → alle offenen Probleme (Hosts + Services)\n"
    "- mcp_vibemk_vibemk_get_host_status(hostname) → Status aller Services eines Hosts\n"
    "- mcp_vibemk_vibemk_get_service_status(hostname, service_description) → einzelnen Service\n"
    "- mcp_vibemk_vibemk_get_checkmk_services(hostname) → alle Services eines Hosts\n"
    "- mcp_vibemk_vibemk_reschedule_check(hostname, service?) → Check neu einplanen [SCHREIBOPERATION]\n"
    "- mcp_vibemk_vibemk_acknowledge_problem(hostname, comment) → quittieren [SCHREIBOPERATION]\n"
    "- mcp_vibemk_vibemk_schedule_downtime(hostname, ...) → Downtime anlegen [SCHREIBOPERATION]\n"
    "- mcp_vibemk_vibemk_activate_changes → Änderungen aktivieren [SCHREIBOPERATION — immer fragen]\n"
    "- mcp_vibemk_vibemk_get_sites → alle konfigurierten CheckMK-Sites\n"
    "Über 150 weitere mcp_vibemk_* Tools — nutze tool_search('vibemk <stichwort>') um sie zu finden.\n\n"
    "## ANALYSE SPEICHERN:\n"
    "Wenn du eine detaillierte Incident-Analyse durchgeführt hast (mehrere Tools genutzt, "
    "Befunde zusammengeführt), frage den Nutzer ob du die Analyse mit post_alert_comment speichern sollen.\n"
    "Beispiel: 'Soll ich diese Analyse an dem Alert speichern, damit sie für spätere Incidents verfügbar ist?'\n\n"

    "## SSH-ZUGRIFF (Serverdiagnose und Fehlerbehebung):\n"
    "Nutze SSH wenn du einen Server direkt untersuchen oder reparieren sollst.\n"
    "Befehl: ssh <hostname>.ippen.media '<befehl>'\n"
    "(User und Key sind per SSH-Config voreingestellt — KEIN -i, -l oder -o IdentityFile nötig)\n"
    "System-Diagnose:\n"
    "  ssh <host> 'df -h; du -sh /var/log/* | sort -rh | head -5'\n"
    "  ssh <host> 'free -h; top -bn1 | head -20'\n"
    "  ssh <host> 'systemctl status <service>; journalctl -u <service> -n 50 --no-pager'\n"
    "Docker-Container auf einem Host: ssh <host> 'docker ps' ODER 'sudo docker ps'\n\n"
    "## KRITISCHE REGEL: SSH-FEHLER → SOFORT MELDEN, NICHT AUSWEICHEN\n"
    "Wenn SSH fehlschlägt:\n"
    "→ ZEIGE den genauen Fehler (exit code, stderr) im Code-Block\n"
    "→ Versuche EINMAL 'sudo docker ps' falls 'docker ps' permission denied gibt\n"
    "→ Weiche NICHT auf CheckMK, web_search oder andere Umwege aus — CheckMK kennt\n"
    "  keinen aktuellen 'docker ps'-Output. web_search für interne Server-Daten ist sinnlos.\n"
    "→ Melde dem Nutzer klar: 'SSH schlägt fehl mit: <Fehlermeldung>' und stoppe.\n"
    "NIEMALS: 'SSH funktioniert nicht, ich prüfe stattdessen CheckMK' — das ist falsch.\n"
    "CheckMK liefert KEINEN Echtzeit-docker-ps-Output.\n\n"
    "## KRITISCHE REGEL: TIMEOUTS BEI SUBPROCESS/SSH\n"
    "Wenn du Python-Scripts im Terminal ausführst, die SSH oder andere Netzwerkbefehle verwenden:\n"
    "→ subprocess.run() IMMER mit timeout=120 aufrufen — NIEMALS ohne Timeout.\n"
    "→ Bei mehreren Hosts in einer Schleife: jeden SSH-Aufruf einzeln mit timeout=120 absichern.\n"
    "→ ConnectTimeout=10 (SSH-Option) schützt nur den TCP-Handshake, NICHT die Remote-Laufzeit.\n"
    "→ Ohne timeout= blockiert subprocess.run() unbegrenzt wenn der Remote-Befehl hängt.\n"
    "Beispiel:\n"
    "  p = subprocess.run(['ssh', '-o', 'ConnectTimeout=10', host, cmd],\n"
    "                     capture_output=True, text=True, timeout=120)\n"
    "Bei TimeoutExpired Exception: Host als 'timeout' markieren und mit nächstem weitermachen.\n\n"

    "## DOCKER-LOGS (Container-Diagnose via Graylog):\n"
    "Container-Logs landen via Logspout automatisch in Graylog — kein SSH nötig.\n"
    "NIEMALS web_search für Container-Logs — nur MCP search_feed:\n"
    "  search_feed('container_name:\"<container>\"')  → aktuelle Logs des Containers\n"
    "  list_alerts(source='graylog')                → Graylog-Alerts aller Container\n"
    "  search_feed('container_name:\"<container>\" AND level:<=3')  → nur Fehler\n"
    "SSH für Docker-Logs NICHT verwenden — die Daten sind bereits in Graylog.\n\n"

    "## LOG-AUSGABE: IMMER VOLLSTÄNDIG UND WORTGENAU\n"
    "Wenn du Log-Einträge, Fehlermeldungen, Stack-Traces oder journalctl-Ausgaben ausgibst:\n"
    "→ Zeige ALLE relevanten Log-Zeilen WORTGENAU und VOLLSTÄNDIG — niemals kürzen, \n"
    "  umschreiben oder mit '...' abbrechen.\n"
    "→ Verwende immer Code-Blöcke (```log ... ```) für Log-Ausgaben.\n"
    "→ Relevante Zeilen = alle mit ERROR, WARN, CRIT, Exception, Traceback, OOM, \n"
    "  Timeout, Connection refused, Exit-Code != 0, sowie die umliegenden Kontext-Zeilen.\n"
    "→ Zeige Zeitstempel, Hostname/Container, Service und die vollständige Meldung je Zeile.\n"
    "→ Interpretiere Log-Inhalte ERST NACH den vollständigen Log-Zeilen im Code-Block —\n"
    "  NIEMALS statt ihnen. Reihenfolge: Code-Block mit allen Zeilen → deine Analyse.\n"
    "→ 'Kurz und direkt' gilt für deine Analyse-Texte, NICHT für Log-Zeilen selbst.\n\n"

    "## FEED-NAVIGATION (am Ende deiner Antwort, wenn du Hosts/Alerts gezeigt hast):\n"
    "Füge EXAKT eine dieser Zeilen ans Ende wenn du Infrastruktur-Daten ausgibst:\n"
    "[FEED:host=docker*] — bei Docker-Hosts\n"
    "[FEED:host=vpp*] — bei Proxmox-Hosts\n"
    "[FEED:severity=critical] — bei kritischen Alerts (ohne Hostfocus)\n"
    "[FEED:host=docker*&severity=critical] — Docker + nur kritisch\n"
    "[FEED:host=<exakter-hostname>] — bei einem einzelnen Host\n"
    "Diese Marker werden vom Frontend als Button gerendert — der Nutzer sieht sie NICHT als Text.\n\n"

    "Netzwerk-Diagnose (ping, traceroute, curl): Terminal-Tool verwenden.\n\n"

    "## WORKSPACE — DATEIEN IMMER HIER ABLEGEN:\n"
    "Dein persönlicher Arbeitsbereich ist `/root/workspaces/`. Alle Skripte, Configs,\n"
    "Analysen und sonstige Artefakte die du erzeugst, legst du dort ab — NIEMALS in\n"
    "`/tmp`, `/app` oder anderen flüchtigen Verzeichnissen.\n"
    "Struktur-Empfehlung:\n"
    "  /root/workspaces/scripts/   → ausführbare Skripte (.py, .sh)\n"
    "  /root/workspaces/reports/   → Analysen und Berichte (.md, .txt)\n"
    "  /root/workspaces/configs/   → Konfigurationsdateien\n"
    "  /root/workspaces/ansible/   → Ansible Playbooks (SCM-Verzeichnis)\n\n"

    "## AGENTS.MD — AGENTEN-ÜBERGREIFENDE KOORDINATION:\n"
    "Pflege `/root/workspaces/agents.md` als geteiltes Logbuch zwischen Hermes und der\n"
    "Werkbank-IDE. Trage dort jede erzeugte Datei und jeden wesentlichen Schritt ein.\n"
    "Format:\n"
    "```\n"
    "## [Datum] Thema (Session-Label)\n"
    "- Aktion: was wurde getan\n"
    "- Datei: `/root/workspaces/pfad/datei.py`\n"
    "- Quelle: Alert-ID / Hostname / Jira-Ticket\n"
    "```\n"
    "Regeln:\n"
    "→ IMMER anhängen (append) — nie überschreiben\n"
    "→ Eintrag anlegen wenn du: eine Datei erzeugst, ein Playbook schreibst, eine\n"
    "  Analyse abschließt, oder eine Remediation durchführst\n"
    "→ Existiert die Datei noch nicht: Header `# Agents Log\\n` voranstellen\n"
    "→ Der Eintrag ermöglicht dem Werkbank-Nutzer direkt die erzeugte Datei zu öffnen\n\n"
    "Beispiel-Eintrag:\n"
    "```markdown\n"
    "## [2026-06-21] Disk-Analyse cue0175 (Alert glog:c91a32dd)\n"
    "- Aktion: SSH-Diagnose + Cleanup-Skript erstellt\n"
    "- Datei: `/root/workspaces/scripts/cleanup_cue0175.sh`\n"
    "- Quelle: Alert glog:c91a32dd53935673\n"
    "```\n"
)

# session_id → {agent, label, msg_count, created_at, llm_model}
_sessions: dict[str, dict[str, Any]] = {}
_whisper_model = None


@app.on_event("startup")
async def _startup_mcp_discovery() -> None:
    """Pre-discover MCP tools at startup so they are ready before the first session.

    Hermes's background discovery thread only waits 0.75s. For a network SSE
    connection that's not enough. Running discovery at startup gives it time to
    complete before any agent is created.
    Requires the 'mcp' SDK package to be installed (added to requirements.txt).
    """
    def _discover() -> None:
        try:
            from tools.mcp_tool import discover_mcp_tools
            log.info("Pre-discovering MCP tools (centralstation SSE)…")
            discover_mcp_tools()
            from tools.mcp_tool import get_mcp_status
            status = get_mcp_status()
            total_tools = sum(s.get("tools", 0) for s in status)
            log.info("MCP discovery done: %d tool(s) from %d server(s)", total_tools, len(status))
        except Exception as exc:
            log.warning("MCP pre-discovery failed: %s", exc)

    import threading
    t = threading.Thread(target=_discover, daemon=True, name="mcp-prediscovery")
    t.start()
    await asyncio.sleep(0)


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        log.info("Loading Whisper model 'base'...")
        _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
        log.info("Whisper model loaded.")
    return _whisper_model


# ── Session model ──────────────────────────────────────────────────

class CreateSessionBody(BaseModel):
    """LLM config forwarded from CentralStation backend settings."""
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_api_mode: str | None = None   # "chat_completions" | "responses" | "codex_responses"
    searxng_url: str | None = None
    llm_timeout_seconds: int | None = None
    # Per-session extra MCP servers (user-personal connectors).
    # Each entry: {name, url, transport?, token?}
    extra_mcp_servers: list[dict] | None = None
    # SSH username from user's SSH connector (overwrites "marvin" in system prompt).
    ssh_username: str | None = None


def _read_mcp_toolsets_from_config() -> list:
    """Read /root/.hermes/config.yaml and return mcp-{name} toolset names.

    Falls back to centralstation-only if the file is missing or unreadable.
    The per-user config is written by userenv_manager.write_hermes_config()
    before the container starts, so this reflects all connectors the user
    has configured (centralstation always + personal servers like vibemk).
    """
    import yaml as _yaml
    for path in [
        "/root/.hermes/config.yaml",
        os.path.join(os.path.dirname(__file__), "hermes_config.yaml"),
    ]:
        try:
            with open(path) as f:
                cfg_data = _yaml.safe_load(f) or {}
            names = list((cfg_data.get("mcp_servers") or {}).keys())
            if names:
                log.info("MCP toolsets from config (%s): %s", path, names)
                return [f"mcp-{n}" for n in names]
        except Exception:
            continue
    log.warning("No hermes_config.yaml found — using mcp-centralstation only")
    return ["mcp-centralstation"]


def _make_agent(sid: str, cfg: CreateSessionBody):
    from run_agent import AIAgent
    from hermes_state import SessionDB

    base_url   = cfg.llm_base_url or os.getenv("LLM_BASE_URL", "")
    model      = (cfg.llm_model    or os.getenv("LLM_MODEL", "")).strip()
    api_key    = cfg.llm_api_key  or os.getenv("LLM_API_KEY")
    api_mode   = cfg.llm_api_mode or os.getenv("LLM_API_MODE", "chat_completions")

    # Hermes requires api_key AND base_url for explicit-creds path.
    # Local endpoints have no key — use a placeholder so the condition holds.
    if base_url and not api_key:
        api_key = "none"
    searxng_url     = cfg.searxng_url or os.getenv("SEARXNG_URL", "")
    timeout_seconds = cfg.llm_timeout_seconds or int(os.getenv("HERMES_API_TIMEOUT", 0)) or None

    # Hermes reads these from the environment — set before agent init.
    if searxng_url:
        os.environ["SEARXNG_URL"] = searxng_url
    if timeout_seconds:
        # Hermes reads all three at request time. The one that actually fires for a
        # remote OpenAI-compatible endpoint is HERMES_STREAM_READ_TIMEOUT (httpx read
        # timeout, default 120s) — without it a large local model that needs >120s of
        # prefill before the first token raises APITimeoutError. is_local_endpoint()
        # only auto-raises for localhost, not for a remote host like llamacpp03.
        #   HERMES_API_TIMEOUT:          total request timeout per LLM call
        #   HERMES_STREAM_READ_TIMEOUT:  max seconds to first/next byte on the stream
        #   HERMES_STREAM_STALE_TIMEOUT: Hermes' own no-progress watchdog
        os.environ["HERMES_API_TIMEOUT"] = str(timeout_seconds)
        os.environ["HERMES_STREAM_READ_TIMEOUT"] = str(timeout_seconds)
        os.environ["HERMES_STREAM_STALE_TIMEOUT"] = str(timeout_seconds)

    log.info("[%s] creating AIAgent: model=%s base_url=%s mode=%s searxng=%s timeout=%s",
             sid[:8], model or "(default)", base_url or "(default)", api_mode,
             searxng_url or "(none)", f"{timeout_seconds}s" if timeout_seconds else "(default)")

    # Build final system prompt — replace default SSH user if user configured their own.
    ssh_user = (cfg.ssh_username or "").strip() or "marvin"
    system_prompt = SYSTEM_PROMPT
    if ssh_user != "marvin":
        system_prompt = system_prompt.replace(
            "ssh marvin@<hostname>.ippen.media",
            f"ssh {ssh_user}@<hostname>.ippen.media",
        )

    # Toolsets are derived from /root/.hermes/config.yaml — the per-user config
    # written by userenv_manager.write_hermes_config() at container start.
    # This includes centralstation (always) + any user-configured servers (vibemk, awx-ng…).
    _mcp_toolsets = _read_mcp_toolsets_from_config()

    agent = AIAgent(
        session_id=sid,
        # SessionDB persists conversation to ~/.hermes/state.db (mounted from
        # ${PWD}/.hermes on the host). History survives container restarts and
        # is loaded back via get_messages_as_conversation() on each turn.
        session_db=SessionDB(),
        base_url=base_url or None,
        api_key=api_key or None,
        api_mode=api_mode,
        # Required for OAuth tokens (sk-ant-oat*): activates _is_anthropic_oauth=True
        # in Hermes, which prepends the "You are Claude Code" system prompt prefix —
        # mandatory for Anthropic's OAuth token routing to accept the request.
        provider="anthropic" if api_mode == "anthropic_messages" else None,
        model=model or None,
        enabled_toolsets=["terminal", "web"] + _mcp_toolsets,
        ephemeral_system_prompt=system_prompt,
        # Cap tool/LLM iterations per user turn. Web search spirals are bounded by
        # the system prompt rule (max 3 web_search per question), not this limit.
        # This limit only guards against hard runaway loops (infinite tool chains).
        # 60 is enough for complex multi-tool diagnosis without blocking legitimate
        # workflows (vibemk_* + search_feed + checkmk + SSH + KB = easily 30+ steps).
        max_iterations=40,
        quiet_mode=False,   # print tool calls + responses to stdout → Docker log → Logspout
        verbose_logging=False,
    )
    # Give MCP discovery a generous window to complete before the first turn.
    # All MCP servers (centralstation + user-specific) are defined in
    # /root/.hermes/config.yaml and discovered at container startup — no
    # per-session dynamic registration needed.
    from hermes_cli.mcp_startup import wait_for_mcp_discovery
    wait_for_mcp_discovery(timeout=8.0)

    return agent


# ── Session endpoints ──────────────────────────────────────────────

@app.post("/sessions", status_code=201)
def create_session(body: CreateSessionBody = None):
    body = body or CreateSessionBody()
    sid = str(uuid.uuid4())
    label = f"Session {len(_sessions) + 1}"
    log.info("Creating session %s (%s)", sid[:8], label)
    try:
        agent = _make_agent(sid, body)
    except ImportError as exc:
        log.error("Hermes import failed: %s", exc, exc_info=True)
        raise HTTPException(503, f"Hermes nicht verfügbar: {exc}")
    except Exception as exc:
        log.error("Agent init failed: %s", exc, exc_info=True)
        raise HTTPException(503, f"Agent-Initialisierung fehlgeschlagen: {exc}")
    _sessions[sid] = {
        "agent": agent,
        "label": label,
        "msg_count": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "llm_model":   (body.llm_model    or os.getenv("LLM_MODEL",    "(default)")).strip(),
        "llm_base_url": (body.llm_base_url or os.getenv("LLM_BASE_URL", "")).strip(),
    }
    log.info("Session %s ready (%s), model=%s, total active: %d",
             sid[:8], label, _sessions[sid]["llm_model"], len(_sessions))
    return {"session_id": sid, "label": label}


@app.get("/sessions")
def list_sessions():
    return [
        {
            "session_id": sid,
            "label": s["label"],
            "msg_count": s["msg_count"],
            "created_at": s["created_at"],
        }
        for sid, s in _sessions.items()
    ]


@app.delete("/sessions/{sid}")
def delete_session(sid: str):
    session = _sessions.pop(sid, None)
    if session:
        log.info("Deleted session %s (%s), remaining: %d", sid[:8], session["label"], len(_sessions))
    else:
        log.debug("Delete: session %s not found (already gone or never created)", sid[:8])
    return {"ok": True}


@app.get("/sessions/{sid}/history")
def get_history(sid: str):
    # Always read from SessionDB — it is the authoritative source.
    # The agent's in-memory conversation_history is loaded lazily on the first
    # run_conversation() call and is empty for freshly restored sessions.
    try:
        from hermes_state import SessionDB
        db = SessionDB()
        history = db.get_messages_as_conversation(sid)
        if history:
            return history
    except Exception as exc:
        log.debug("SessionDB read for %s failed: %s", sid[:8], exc)

    # Fallback: agent in-memory history (only non-empty mid-stream or for new sessions)
    if sid in _sessions:
        agent = _sessions[sid]["agent"]
        return getattr(agent, "conversation_history", None) or []

    raise HTTPException(404, "Session nicht gefunden")


# ── Message → SSE stream ───────────────────────────────────────────

class MessageBody(BaseModel):
    content: str
    # Optional LLM config forwarded by the backend proxy on every message.
    # Used to restore sessions after container restarts when env-var defaults
    # are not configured (the proxy injects the active CentralStation LLM config).
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_api_mode: str | None = None
    searxng_url: str | None = None
    llm_timeout_seconds: int | None = None
    show_reasoning: bool = True
    extra_mcp_servers: list[dict] | None = None


def _restore_session(sid: str, cfg: CreateSessionBody | None = None) -> bool:
    """Recreate an in-memory session entry from SessionDB (after container restart).

    Returns True if history exists and the agent was successfully created.
    The agent will load the full history on its next run_conversation() call.
    """
    try:
        from hermes_state import SessionDB
        db = SessionDB()
        history = db.get_messages_as_conversation(sid)
        if not history:
            return False
        effective_cfg = cfg or CreateSessionBody()
        agent = _make_agent(sid, effective_cfg)
        user_turns = sum(1 for m in history if m.get("role") == "user")
        _sessions[sid] = {
            "agent": agent,
            "label": "Session (restored)",
            "msg_count": user_turns,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "llm_model":    (effective_cfg.llm_model    or os.getenv("LLM_MODEL",    "(default)")).strip(),
            "llm_base_url": (effective_cfg.llm_base_url or os.getenv("LLM_BASE_URL", "")).strip(),
        }
        log.info("Session %s restored from SessionDB (%d turns)", sid[:8], user_turns)
        return True
    except Exception as exc:
        log.warning("Session restore failed for %s: %s", sid[:8], exc)
        return False


@app.post("/sessions/{sid}/message")
async def send_message(sid: str, body: MessageBody):
    if sid not in _sessions:
        log.info("Session %s not in memory — attempting restore from SessionDB", sid[:8])
        llm_cfg = CreateSessionBody(
            llm_base_url=body.llm_base_url,
            llm_model=body.llm_model,
            llm_api_key=body.llm_api_key,
            llm_api_mode=body.llm_api_mode,
            searxng_url=body.searxng_url,
            llm_timeout_seconds=body.llm_timeout_seconds,
            extra_mcp_servers=body.extra_mcp_servers,
        )
        if not _restore_session(sid, llm_cfg):
            log.warning("Restore failed for session %s — not found in SessionDB", sid[:8])
            raise HTTPException(404, "Session nicht gefunden")

    else:
        # Session is in memory — check if LLM config changed since it was created.
        # Re-initialize the agent when base_url or model differ so the user does not
        # need to reload after changing the LLM in CentralStation settings.
        session = _sessions[sid]
        incoming_model   = (body.llm_model    or "").strip()
        incoming_base    = (body.llm_base_url  or "").strip()
        incoming_mode    = (body.llm_api_mode  or "").strip()
        stored_model     = (session.get("llm_model",    "") or "").strip()
        stored_base      = (session.get("llm_base_url", "") or "").strip()
        stored_mode      = (session.get("llm_api_mode",  "") or "").strip()
        if (incoming_model or incoming_base or incoming_mode) and (
            incoming_model != stored_model
            or incoming_base != stored_base
            or incoming_mode != stored_mode
        ):
            log.info(
                "Session %s: LLM config changed (model: %r→%r  base_url: %r→%r  mode: %r→%r) — re-init agent",
                sid[:8], stored_model, incoming_model, stored_base, incoming_base,
                stored_mode, incoming_mode,
            )
            try:
                new_cfg = CreateSessionBody(
                    llm_base_url=body.llm_base_url,
                    llm_model=body.llm_model,
                    llm_api_key=body.llm_api_key,
                    llm_api_mode=body.llm_api_mode,
                    searxng_url=body.searxng_url,
                    llm_timeout_seconds=body.llm_timeout_seconds,
                )
                new_agent = _make_agent(sid, new_cfg)
                session["agent"]       = new_agent
                session["llm_model"]   = incoming_model
                session["llm_base_url"] = incoming_base
                session["llm_api_mode"] = incoming_mode
            except Exception as exc:
                log.warning("Session %s: LLM re-init failed, keeping old agent: %s", sid[:8], exc)

    agent = _sessions[sid]["agent"]
    _sessions[sid]["msg_count"] += 1
    msg_num = _sessions[sid]["msg_count"]
    log.info("[%s] msg #%d: %.80s", sid[:8], msg_num, body.content)

    q: asyncio.Queue[dict] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    delta_count = 0
    response_buf: list[str] = []
    # Per-turn web_search counter — enforces the "max 3 searches" system prompt rule
    # programmatically because qwen3* models regularly ignore it.
    _web_search_count = 0
    _WEB_SEARCH_LIMIT = 5
    _WEB_TOOL_NAMES = {"web_search", "web_fetch", "web_browser", "web_extract"}

    def on_delta(text: str) -> None:
        nonlocal delta_count
        delta_count += 1
        response_buf.append(text)
        loop.call_soon_threadsafe(q.put_nowait, {"type": "delta", "text": text})

    def _tool_label(name: str) -> str:
        import re
        # Strip any mcp_{server}_ prefix so only the tool action name remains.
        name = re.sub(r'^mcp_[a-z0-9_]+_', '', name)
        return name.replace("_", " ")

    def on_tool_progress(event_type: str, *cb_args, **cb_kwargs) -> None:
        # Hermes' unified progress callback. Variadic signature per event:
        #   ("tool.started",   name, preview, args)
        #   ("tool.completed", name, None, None, duration=, is_error=, result=)
        #   ("reasoning.available", "_thinking", text, None)
        nonlocal _web_search_count
        try:
            if event_type == "tool.started":
                name = cb_args[0] if cb_args else ""
                preview = cb_args[1] if len(cb_args) > 1 else None
                label = _tool_label(name)

                # Hard limit: remove web tools from agent.tools after _WEB_SEARCH_LIMIT
                # calls per turn. agent.tools is passed directly to the LLM on every
                # API call, so this takes effect on the very next request.
                # (disabled_toolsets is only consulted at agent init, not per-call.)
                if name in _WEB_TOOL_NAMES:
                    _web_search_count += 1
                    if _web_search_count >= _WEB_SEARCH_LIMIT:
                        if not getattr(agent, "_web_tools_removed", False):
                            agent._saved_tools = list(agent.tools or [])
                            agent._saved_valid_tools = set(agent.valid_tool_names or set())
                            agent.tools = [
                                t for t in (agent.tools or [])
                                if t.get("function", {}).get("name") not in _WEB_TOOL_NAMES
                            ]
                            if agent.valid_tool_names:
                                agent.valid_tool_names = agent.valid_tool_names - _WEB_TOOL_NAMES
                            agent._web_tools_removed = True
                            log.warning(
                                "[%s] web_search limit (%d) reached — removed web tools from agent.tools",
                                sid[:8], _WEB_SEARCH_LIMIT,
                            )

                # Append the human-readable preview (command, query, host, …) so the
                # user sees exactly what the agent is doing, not just the tool name.
                detail = f"{label}: {preview}" if preview else label
                loop.call_soon_threadsafe(q.put_nowait, {
                    "type": "tool_start", "tool": detail,
                })
            elif event_type == "tool.completed":
                name = cb_args[0] if cb_args else ""
                is_error = cb_kwargs.get("is_error")
                loop.call_soon_threadsafe(q.put_nowait, {
                    "type": "tool_done", "tool": _tool_label(name),
                    "error": bool(is_error),
                })
            elif event_type == "reasoning.available":
                # Gated by the admin setting computer.show_reasoning (default on).
                if not body.show_reasoning:
                    return
                text = cb_args[1] if len(cb_args) > 1 else ""
                if text:
                    loop.call_soon_threadsafe(q.put_nowait, {
                        "type": "reasoning", "text": str(text)[:500],
                    })
        except Exception:
            pass  # never let display callbacks break the run

    def run_sync() -> None:
        try:
            # Load conversation history from Hermes state.db (native persistence).
            # Hermes run_conversation starts with messages=[] unless conversation_history
            # is passed explicitly — without this the agent forgets every previous turn.
            # SessionDB.get_messages_as_conversation() returns the full history for
            # this session_id, surviving container restarts (state.db is host-mounted).
            db = getattr(agent, "_session_db", None)
            history = db.get_messages_as_conversation(sid) if db else []
            # Tool/reasoning callbacks are AIAgent attributes, NOT run_conversation
            # kwargs — only stream_callback is accepted by run_conversation. Set the
            # progress callback on the agent right before the run (requests to a
            # single session are sequential, so this is safe).
            agent.tool_progress_callback = on_tool_progress
            try:
                agent.run_conversation(
                    user_message=body.content,
                    stream_callback=on_delta,
                    conversation_history=history if history else None,
                )
            finally:
                # Restore agent.tools/valid_tool_names if the web limiter trimmed them.
                if getattr(agent, "_web_tools_removed", False):
                    agent.tools = agent._saved_tools
                    agent.valid_tool_names = agent._saved_valid_tools
                    del agent._web_tools_removed, agent._saved_tools, agent._saved_valid_tools
            full_response = "".join(response_buf)
            log.info("[%s] response (%d chars): %.300s%s",
                     sid[:8], len(full_response), full_response,
                     "…" if len(full_response) > 300 else "")
            loop.call_soon_threadsafe(q.put_nowait, {"type": "done"})
        except Exception as exc:
            log.error("[%s] agent error: %s", sid[:8], exc, exc_info=True)
            loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "text": str(exc)})

    threading.Thread(target=run_sync, daemon=True, name=f"hermes-{sid[:8]}").start()

    async def event_stream():
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=15)
            except asyncio.TimeoutError:
                # Send a no-op SSE comment to keep the connection alive while
                # the agent is busy with a long tool call (CheckMK, web search, …).
                # Without this, nginx/browsers drop idle SSE connections after ~60 s.
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            if item["type"] in ("done", "error"):
                log.debug("[%s] SSE stream closed (type=%s)", sid[:8], item["type"])
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Whisper STT ────────────────────────────────────────────────────

@app.post("/transcribe")
async def transcribe(file: UploadFile):
    import shutil
    import tempfile

    suffix = ".webm"
    if file.filename and "." in file.filename:
        suffix = "." + file.filename.rsplit(".", 1)[-1]

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        path = tmp.name

    log.info("Transcribing audio (suffix=%s, size=%d B)", suffix, os.path.getsize(path))
    try:
        model = _get_whisper()
        segments, info = await asyncio.to_thread(
            model.transcribe, path, language="de", beam_size=5
        )
        text = " ".join(s.text.strip() for s in segments)
        log.info("Transcription done: lang=%s prob=%.2f text=%.60s",
                 info.language, info.language_probability, text)
        return {"text": text}
    except Exception as exc:
        log.error("Transcription failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"Transkription fehlgeschlagen: {exc}")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ── Health ─────────────────────────────────────────────────────────

@app.get("/health")
def health():
    hermes_ok = True
    hermes_err = ""
    try:
        import run_agent  # noqa: F401
    except ImportError as exc:
        hermes_ok = False
        hermes_err = str(exc)

    env_llm_url   = os.getenv("LLM_BASE_URL", "")
    env_llm_model = os.getenv("LLM_MODEL", "")

    return {
        "status": "ok" if hermes_ok else "degraded",
        "hermes_available": hermes_ok,
        "hermes_error": hermes_err or None,
        "active_sessions": len(_sessions),
        "env_llm_base_url": env_llm_url or None,
        "env_llm_model": env_llm_model or None,
    }
