"""Hermes API — FastAPI wrapper around Hermes AIAgent.

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
import re
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
log = logging.getLogger("userenv")

# This process runs unprivileged as yolo (uid 1000); /root is 0700 root:root and
# unwritable here. All state that used to assume HOME=/root lives under yolo's home.
# Getrennter Modus (Block 5): der Prozess läuft als `agent` mit HOME=/home/agent — alles
# Agenten-eigene (Claude/Codex/Hermes-Konfiguration, pip) liegt dann dort; der Workspace
# bleibt /home/yolo/workspaces (geteilt über die Gruppe cs-work).
_YOLO_HOME = os.environ.get("HOME") or "/home/yolo"

#: Arbeitsverzeichnis der Agenten — dasselbe, das die Werkbank (code-server) öffnet.
#: Die Konsole und die Werkbank sollen an denselben Dateien arbeiten: was der Agent
#: anlegt, muss in der Werkbank sofort editierbar sein und umgekehrt. Liefe der Agent
#: wie früher in /app, landeten seine Dateien in der Container-Schicht — unsichtbar
#: für die Werkbank und beim nächsten Neuaufbau verloren.
AGENT_CWD = os.environ.get("CS_AGENT_CWD", "/home/yolo/workspaces")

#: Untergrenze für Hermes' LLM-Timeouts (s) — siehe _make_agent. Liegt über dem
#: ~240-s-Abbruch des llmbase-Gateways, damit dessen Fehler ankommt.
HERMES_MIN_LLM_TIMEOUT = 300

class _ProviderNoticeHandler(logging.Handler):
    """Leitet Hermes' Fehlversuche beim LLM-Anbieter als Hinweis an die Konsole weiter.

    Nur Einträge aus dem Thread DIESER Sitzung (Name hermes-<sid8>) — andere Sitzungen
    laufen im selben Prozess.
    """
    _FAILED = re.compile(r"API call failed \(attempt (\d+)/(\d+)\).*?summary=(.*?)(?:\s+\w+=|$)")

    def __init__(self, thread_name: str, emit_notice) -> None:
        super().__init__(level=logging.WARNING)
        self.thread_name = thread_name
        self.emit_notice = emit_notice

    def emit(self, record: logging.LogRecord) -> None:
        if record.threadName != self.thread_name:
            return
        try:
            m = self._FAILED.search(record.getMessage())
            if m:
                self.emit_notice(f"LLM-Anbieter: {m.group(3).strip()[:160]} — Versuch {m.group(1)}/{m.group(2)}")
        except Exception:
            pass


#: Claude CLI built-ins the Console grants. Write/Edit/MultiEdit/NotebookEdit are
#: path-checked by the PreToolUse hook (cs-readonly-guard.py) — free inside the
#: workspace and /tmp, behind the write window everywhere else.
CLAUDE_BUILTIN_TOOLS = ("Bash,Read,Glob,Grep,Write,Edit,MultiEdit,NotebookEdit,"
                        "TodoWrite,WebFetch,WebSearch")


def _agent_cwd() -> str:
    """AGENT_CWD, angelegt falls nötig; fällt auf das aktuelle Verzeichnis zurück."""
    try:
        os.makedirs(AGENT_CWD, exist_ok=True)
        return AGENT_CWD
    except OSError as exc:
        log.warning("Arbeitsverzeichnis %s nicht nutzbar (%s) — bleibe in %s",
                    AGENT_CWD, exc, os.getcwd())
        return os.getcwd()


app = FastAPI(title="UserEnv Agent Service", version="1.0.0")
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
    "Du: '...Wenn du willst, prüfe ich docker50 im Detail.'\n"
    "Nutzer: 'ja'\n"
    "Du: [rufst get_checkmk_host('docker50') auf und zeigst das Ergebnis]\n\n"

    "## KRITISCHE REGEL: SCHREIBOPERATIONEN — ABSOLUTES VERBOT OHNE BESTÄTIGUNG\n"
    "**READ-ONLY bedeutet READ-ONLY.** Wenn der Nutzer 'nur lesen', 'read-only', 'keine Änderungen',\n"
    "'nur analysieren' oder ähnliches sagt, gilt dies als ABSOLUTES VERBOT für ALLE Schreiboperationen.\n\n"
    "Schreiboperationen die NIEMALS ohne explizite Nutzer-Bestätigung ausgeführt werden dürfen:\n"
    "→ SSH-Befehle die das System verändern: apt/yum/dnf install|remove|upgrade, systemctl start|stop|\n"
    "  restart|reload|enable|disable, rm, mv, cp (nach /etc oder /var), chmod, chown, useradd, passwd,\n"
    "  iptables, ufw, sed -i, tee, echo > (Umleitung), nano/vim, git push/commit/merge, docker restart|\n"
    "  stop|rm|pull, kubectl apply|delete|scale|rollout, npm install\n"
    "→ Pakete: apt/yum/dnf install, npm/gem/cargo install\n"
    "  AUSNAHME pip: Python-Bibliotheken darfst du OHNE Rückfrage in dein eigenes venv\n"
    "  installieren — `pip install <paket>` (venv: /home/yolo/pip/venv, liegt im PATH und\n"
    "  überlebt Neustarts). Fehlt dir etwas für eine Auswertung, installiere es einfach;\n"
    "  `pip list` zeigt Vorhandenes. Gesperrt bleibt: `sudo pip`, `/usr/bin/pip`,\n"
    "  `--break-system-packages`, `--target` in ein Systemverzeichnis und jedes\n"
    "  `pip install` auf einem ENTFERNTEN Host via ssh.\n"
    "→ HOCHGELADENE DATEIEN: Steht in einer Nachricht `[Dateien: <pfad> …]`, hat der\n"
    "  Nutzer Dateien angehängt (Excel, CSV, PDF, Word, Logs …). Öffne sie, bevor du\n"
    "  antwortest — Excel mit `pd.read_excel(pfad, sheet_name=None)`, CSV mit\n"
    "  `pd.read_csv(pfad, sep=None, engine='python', encoding='cp1252')`, PDF mit pypdf.\n"
    "  Die Originaldatei nicht verändern; Ergebnisse als neue Datei in den Workspace.\n"
    "→ MCP-Tools die schreiben: create_jira_ticket, acknowledge_alert, post_alert_comment, store_knowledge,\n"
    "  store_skill, schedule_downtime, reschedule_check, acknowledge_problem, activate_changes,\n"
    "  und alle anderen Tools die etwas anlegen/ändern/löschen\n"
    "→ Am Ticket: jira_add_comment, jira_update_issue, jira_transition_issue, jira_close_issue.\n"
    "  Ein Ticket-Kommentar ist KEINE Nebensache — er geht an Menschen, oft an Externe, und ist\n"
    "  nicht zurücknehmbar. Niemals ungefragt kommentieren, auch nicht 'zur Dokumentation'.\n"
    "→ Dateien anlegen oder überschreiben (außer in /home/yolo/workspaces/ nach Aufforderung)\n\n"
    "**Procedure bei Schreiboperationen:**\n"
    "  1. STOPPE — führe die Aktion NICHT aus\n"
    "  2. Beschreibe was du tun würdest: Befehl, Ziel, erwartete Wirkung.\n"
    "     Bei einem Ticket-Kommentar: den vollständigen Text im Wortlaut zeigen.\n"
    "  3. Frage EXPLIZIT: 'Soll ich das ausführen?'\n"
    "  4. Erst nach eindeutiger Bestätigung ('ja', 'mach das', 'führe aus') ausführen\n\n"
    "**Das ist keine bloße Bitte.** Schreibende Werkzeuge sind technisch gesperrt, bis der\n"
    "Nutzer in der CentralStation-Konsole 'Schreibzugriff freigeben' geklickt hat. Ein 'ja'\n"
    "im Chat allein reicht dafür nicht — die Sperre sieht die Unterhaltung nicht. Versuche\n"
    "nicht, sie zu umgehen (etwa per curl auf die Jira-API); bitte stattdessen um die\n"
    "Freigabe und nenne dabei genau, was du vorhast.\n\n"
    "Beispiele:\n"
    "  Falsch: [führt 'systemctl restart nginx' aus ohne zu fragen]\n"
    "  Richtig: 'nginx hat Config-Fehler. Soll ich nginx neu starten? (systemctl restart nginx)'\n"
    "  Falsch: [führt create_jira_ticket aus ohne zu fragen]\n"
    "  Richtig: 'Soll ich ein Jira-Ticket anlegen? (Titel: X, Priorität: Y)'\n\n"
    "**Wenn der Nutzer EXPLIZIT 'read-only' / 'nur lesen' / 'keine Änderungen' gesagt hat:**\n"
    "→ Lehne Schreiboperationen KATEGORISCH ab — nicht fragen, direkt ablehnen\n"
    "→ Antworte: 'Ich bin im Read-Only-Modus und führe keine Änderungen durch. [Was du stattdessen tun wirst]'\n"
    "→ Diese Einschränkung gilt für die GESAMTE Session bis der Nutzer sie aufhebt\n\n"

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
    "- mcp_centralstation_search_knowledge_base(query, deepsearch?) → Confluence KB / Runbooks\n"
    "- mcp_centralstation_lw_suche(filter, stunden, bedeutung?) → relevante, getaggte Lognachrichten aller "
    "Quellen + Facetten; Filter: host:x thema:oom netz:dmz relevanz>=3 befund:<id> !art:wert\n"
    "- mcp_centralstation_lw_netzplan(filter) → was an denselben Nachrichten zusammen auftritt (keine Ursache)\n"
    "- mcp_centralstation_lw_wissen(filter) → frühere KI-Befunde zu Host/Muster — zuerst fragen\n"
    "- mcp_centralstation_lw_nachricht(id), lw_facetten(filter), lw_definition() → Details, Facetten, "
    "was als relevant gilt\n"
    "- mcp_centralstation_lw_gespraech(filter) → Gesprächsprotokolle früherer KI-Analysen zum Filter\n"
    "- mcp_centralstation_lw_feld(name) → Bedeutung eines Felds/Hashtags/Signals (ohne Name: alle) — "
    "nachschlagen statt raten\n"
    "- mcp_centralstation_web_suche(query, warum), web_seite(url) → Websuche, nur wenn unklar ist, was eine "
    "Logzeile bedeutet (interne Namen werden entfernt); in Foren steht die Lösung meist im letzten Beitrag\n\n"

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

    "## TICKETS SUCHEN (MCP, nur lesend):\n"
    "search_tickets(query, target, project, open_only) durchsucht Jira UND ServiceDesk mit "
    "Freitext oder Ticketschlüssel — nutze es, wenn nach Tickets gefragt wird oder du prüfen "
    "willst, ob es zu einem Problem schon ein Ticket gibt. Details danach mit jira_get_issue. "
    "Nenne Treffer mit Schlüssel, Instanz und Link.\n\n"

    "## STAMMDATEN: ID-Generator (MCP, nur lesend):\n"
    "- lookup_idgen_host(name): Standort (location_id, Name, Stadt) zu Host, IP-Adresse oder Switch.\n"
    "- list_idgen_records(resource, filter, limit): Datensätze einer Tabelle, z. B. locations, "
    "companies, ip_networks, virt_servers; filter 'feld=wert&feld2=wert2'.\n"
    "- query_idgen_sql(sql, limit): eine SELECT-Abfrage (MySQL) für Verknüpfungen, z. B. "
    "virt_servers JOIN locations.\n"
    "Nutze sie, wenn nach Standort, Firma, Netz oder Zuordnung eines Servers gefragt wird.\n\n"

    "## SERVER-DOKU: Manpages und Fähigkeiten (MCP, nur lesend):\n"
    "- host_capabilities(host): OS/Version, vorhandene Werkzeuge (docker? journalctl? zfs?) und "
    "sudo-Rechte — rufe es ZUERST auf, wenn du einen unbekannten Host untersuchst, statt Befehle "
    "auszuprobieren.\n"
    "- search_manpages(host, query): welche Befehle es zu einem Thema auf DIESEM Host gibt.\n"
    "- read_manpage(host, command, focus): Optionen in der dort installierten Version, z. B. "
    "read_manpage('web01', 'rsync', '--delete'). Prüfe so jede Option, bei der du unsicher bist, "
    "bevor du einen Befehl vorschlägst.\n"
    "Nur Hosts aus dem CheckMK-Inventar. Fehler sind benannt (host_unknown, no_manpage, ssh_failed) — "
    "ein Fehler heißt NICHT, dass es den Befehl nicht gibt.\n\n"

    "## SSH-ZUGRIFF (Serverdiagnose und Fehlerbehebung):\n"
    "Nutze SSH wenn du einen Server direkt untersuchen oder reparieren sollst.\n"
    "Befehl: ssh <hostname> '<befehl>' — nutze den Hostnamen so, wie er dir genannt\n"
    "wurde oder wie er aus CheckMK/NetBox/dem Feed kommt. Hänge KEINE Domain an und\n"
    "ersetze sie nicht; es gibt keine Liste erlaubter Domains.\n"
    "(User und Key sind per SSH-Config für JEDEN Host voreingestellt — KEIN -i, -l\n"
    " oder -o IdentityFile nötig)\n"
    "Scheitert es mit 'Permission denied (publickey)', ist der Host erreichbar und\n"
    "lehnt den Schlüssel ab — ein Befund über DIESEN Host. Behaupte dann NICHT, die\n"
    "Domain sei nicht eingerichtet.\n"
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
    "Dein persönlicher Arbeitsbereich ist `/home/yolo/workspaces/`. Alle Skripte, Configs,\n"
    "Analysen und sonstige Artefakte die du erzeugst, legst du dort ab — NIEMALS in\n"
    "`/tmp`, `/app` oder anderen flüchtigen Verzeichnissen.\n"
    "Struktur-Empfehlung:\n"
    "  /home/yolo/workspaces/scripts/   → ausführbare Skripte (.py, .sh)\n"
    "  /home/yolo/workspaces/reports/   → Analysen und Berichte (.md, .txt)\n"
    "  /home/yolo/workspaces/configs/   → Konfigurationsdateien\n"
    "  /home/yolo/workspaces/ansible/   → Ansible Playbooks (SCM-Verzeichnis)\n"
    "LANGE DATEIEN IN TEILEN SCHREIBEN: Ein einzelner Werkzeugaufruf darf höchstens\n"
    "~150 Zeilen bzw. ~1 500 Wörter Dateiinhalt enthalten. Lege längere Berichte und\n"
    "Skripte in Abschnitten an: erst die Datei mit dem ersten Teil, dann jeden weiteren\n"
    "Teil anhängen (z. B. `cat >> datei.md <<'EOF' … EOF`). Grund: manche LLM-Anbieter\n"
    "brechen zu lange Werkzeugaufrufe ab, dann geht die ganze Antwort verloren.\n"
    "Hast du eine Datei angelegt, nenne am Ende deiner Antwort ihren VOLLSTÄNDIGEN Pfad\n"
    "(z. B. `/home/yolo/workspaces/reports/bericht.md`) — die Konsole macht daraus einen Link,\n"
    "der die Datei gerendert in einem neuen Fenster öffnet.\n\n"

    "## AGENTS.MD — AGENTEN-ÜBERGREIFENDE KOORDINATION:\n"
    "Pflege `/home/yolo/workspaces/agents.md` als geteiltes Logbuch zwischen Hermes und der\n"
    "Werkbank-IDE. Trage dort jede erzeugte Datei und jeden wesentlichen Schritt ein.\n"
    "Format:\n"
    "```\n"
    "## [Datum] Thema (Session-Label)\n"
    "- Aktion: was wurde getan\n"
    "- Datei: `/home/yolo/workspaces/pfad/datei.py`\n"
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
    "- Datei: `/home/yolo/workspaces/scripts/cleanup_cue0175.sh`\n"
    "- Quelle: Alert glog:c91a32dd53935673\n"
    "```\n\n"

    "## LIVING DOCUMENTATION — ERKENNTNISSE SPEICHERN:\n"
    "Du hast Zugriff auf store_knowledge und search_knowledge.\n\n"
    "**Bevor du ein Problem untersuchst:** Rufe search_knowledge auf — vielleicht\n"
    "wurde das Problem bereits gelöst:\n"
    "  mcp_centralstation_search_knowledge('opensearch disk full', service='opensearch')\n\n"
    "**Nach erfolgreicher Problemlösung:** Rufe store_knowledge auf:\n"
    "  mcp_centralstation_store_knowledge(\n"
    "    kind='lesson', title='OpenSearch Disk full — Lösung',\n"
    "    problem='OpenSearch meldet disk watermark überschritten',\n"
    "    solution='Index-Rotation via _cat/indices + DELETE alte Indizes',\n"
    "    service='opensearch', confidence=0.9, tags=['opensearch', 'disk', 'gelöst']\n"
    "  )\n"
    "Speichere auch erkannte Service-Abhängigkeiten (kind='dependency').\n"
    "Speichere NUR verifizierte Erkenntnisse — confidence < 0.5 → nicht speichern.\n\n"
    "**Wenn eine Erkenntnis veraltet oder falsch ist:** Zuerst search_knowledge aufrufen\n"
    "um die doc_id zu ermitteln, dann:\n"
    "  update_knowledge(doc_id='...', solution='neue/korrigierte Lösung', confidence=0.9)\n"
    "Oder wenn komplett irrelevant — erst Nutzer fragen, dann:\n"
    "  forget_knowledge(doc_id='...')\n\n"

    "## SKILL-BIBLIOTHEK — BEWÄHRTE ABLÄUFE:\n"
    "Du hast Zugriff auf list_skills, get_skill und store_skill.\n\n"
    "**Zu Beginn einer komplexen Aufgabe:** Prüfe ob es bereits einen Skill gibt:\n"
    "  mcp_centralstation_list_skills() → alle verfügbaren Skills (Name + Beschreibung)\n"
    "  mcp_centralstation_get_skill('ssh-diagnose') → vollständige Anleitung laden\n\n"
    "**Nach einer nicht-trivialen erfolgreichen Lösung** (>3 Schritte, wird wieder gebraucht):\n"
    "  mcp_centralstation_store_skill(\n"
    "    name='opensearch-reindex-rotation',\n"
    "    title='OpenSearch Index-Rotation bei Disk-Problemen',\n"
    "    description='Wenn OpenSearch disk watermark meldet: alte Indizes identifizieren und löschen',\n"
    "    content='## Schritt 1: Disk-Nutzung prüfen\\n...vollständige Anleitung...',\n"
    "    tags=['opensearch', 'disk', 'index']\n"
    "  )\n"
    "Speichere store_skill NICHT automatisch — frage den Nutzer zuerst:\n"
    "'Soll ich den Ablauf als wiederverwendbaren Skill speichern?'\n\n"
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
            # Hermes moved discovery out of tools.mcp_tool into tools.mcp_tool_discovery.
            # The old import failed silently ("MCP pre-discovery failed" as a warning),
            # no server was registered, and every session started with "Unknown
            # toolset: mcp-…" — Hermes had no MCP tools at all. Try both locations.
            try:
                from tools.mcp_tool_discovery import discover_mcp_tools, get_mcp_status
            except ImportError:
                from tools.mcp_tool import discover_mcp_tools, get_mcp_status
            log.info("Pre-discovering MCP tools…")
            discover_mcp_tools()
            status = get_mcp_status()
            total_tools = sum(s.get("tools", 0) or 0 for s in status)
            log.info("MCP discovery done: %d tool(s) from %d server(s): %s", total_tools,
                     len(status),
                     ", ".join(f"{s.get('name')}={s.get('tools', 0)}"
                               f"{'' if s.get('connected') else ' (NOT connected)'}"
                               for s in status))
        except Exception as exc:
            log.error("MCP pre-discovery failed — Hermes sessions get NO MCP tools: %s", exc)

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
    # SSH username from the user's SSH connector. Informational only — the effective
    # user comes from ~/.ssh/config, which configure_ssh writes from the same source.
    ssh_username: str | None = None
    # Console agent backend: "hermes" (default) | "claude_cli" | "codex_cli"
    agent_type: str | None = None


def _read_mcp_toolsets_from_config() -> list:
    """Read ~/.hermes/config.yaml and return mcp-{name} toolset names.

    Falls back to centralstation-only if the file is missing or unreadable.
    The per-user config is written by userenv_manager.write_hermes_config()
    before the container starts, so this reflects all connectors the user
    has configured (centralstation always + personal servers like vibemk).
    """
    import yaml as _yaml
    for path in [
        f"{_YOLO_HOME}/.hermes/config.yaml",
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


#: Dieselbe Schreibsperre wie für die Claude CLI: cs-readonly-guard.py, EINE Regelstelle.
#: Als Python-pre_tool_call-Hook statt als Hermes-Shell-Hook, weil Hermes einen
#: gesperrten Aufruf abfängt, BEVOR er als Werkzeug erscheint — die Konsole zeigte ihn
#: gar nicht, und das Modell durfte frei erzählen, was passiert sei ("Ausgeführt — aber
#: blockiert"). Dieser Hook kennt die session_id und meldet jede Sperre sichtbar.
WRITE_GUARD = "/opt/cs-readonly-guard.py"
WRITE_GUARD_TIMEOUT = 20
#: sid → Funktion, die ein Ereignis in den laufenden Konsolen-Stream legt.
_stream_sinks: dict[str, Any] = {}


def _guard_preview(tool: str, args: Any) -> str:
    if isinstance(args, dict):
        inner = args.get("command") or args.get("data") or ""
        if inner:
            return f"{tool}: {str(inner)[:160]}"
    return tool


def _write_guard_hook(**kw: Any) -> dict | None:
    """Jeder Werkzeugaufruf geht durch den Guard. Fail closed: was nicht geprüft werden
    konnte (Absturz, Zeitüberschreitung, unbekannte Antwort), wird gesperrt."""
    import subprocess
    tool, args = kw.get("tool_name") or "", kw.get("args")
    sid = kw.get("session_id") or ""
    payload = json.dumps({"hook_event_name": "pre_tool_call", "tool_name": tool,
                          "tool_input": args if isinstance(args, dict) else None,
                          "session_id": sid, "cwd": "/home/yolo/workspaces"}, default=str)
    try:
        r = subprocess.run(["python3", WRITE_GUARD], input=payload, capture_output=True,
                           text=True, timeout=WRITE_GUARD_TIMEOUT)
        code, out, err = r.returncode, r.stdout or "", (r.stderr or "").strip()
    except Exception as exc:  # noqa: BLE001
        code, out, err = -1, "", f"Schreibsperre nicht prüfbar ({exc})"
    sink = _stream_sinks.get(sid)
    if code == 0:
        if "Schreibfreigabe aktiv" in err and sink:
            sink({"type": "notice",
                  "text": f"🔓 Mit Schreibfreigabe ausgeführt — {_guard_preview(tool, args)}"})
        return None
    reason = ""
    try:
        reason = json.loads(out).get("reason", "")
    except Exception:  # noqa: BLE001
        pass
    if code != 2:
        reason = (f"{err or 'Schreibsperre lieferte keine gültige Antwort'} — der Aufruf "
                  "wurde vorsichtshalber gesperrt.")
    log.warning("[%s] write guard blocked %s", sid[:8], _guard_preview(tool, args))
    if sink:
        # Als Werkzeugschritt mit Fehler: bleibt im Verlauf stehen, statt nur kurz in
        # der Statuszeile aufzublitzen.
        label = f"⛔ Schreibsperre: {_guard_preview(tool, args)}"
        sink({"type": "tool_start", "tool": label})
        sink({"type": "tool_done", "tool": label, "error": True})
    return {"action": "block", "message": reason or err or "Durch die Schreibsperre gesperrt."}


def _ensure_write_guard() -> None:
    """Hook registrieren — vor JEDEM Agentenlauf geprüft, nicht nur einmal.

    Bis 2026-10-02 hatte Hermes KEINE technische Sperre, nur einen Satz im Prompt. Ein
    Plugin-Neuladen (discover_and_load(force=True)) leert die Hook-Liste; ein einmal
    gesetztes Flag hielte dann einen ungeschützten Agenten für geschützt.
    """
    from hermes_cli.plugins import get_plugin_manager
    hooks = get_plugin_manager()._hooks.setdefault("pre_tool_call", [])
    if _write_guard_hook not in hooks:
        hooks.insert(0, _write_guard_hook)
        log.info("write guard registered as Hermes pre_tool_call hook (fail closed)")
    if _write_guard_hook not in get_plugin_manager()._hooks.get("pre_tool_call", []):
        raise RuntimeError("Schreibsperre (cs-readonly-guard) nicht registriert — "
                           "Hermes startet ohne sie nicht.")


def _make_agent(sid: str, cfg: CreateSessionBody):
    _ensure_write_guard()
    from run_agent import AIAgent
    from hermes_state import SessionDB

    base_url   = cfg.llm_base_url or os.getenv("LLM_BASE_URL", "")
    model      = (cfg.llm_model    or os.getenv("LLM_MODEL", "")).strip()
    api_key    = cfg.llm_api_key  or os.getenv("LLM_API_KEY")
    api_mode   = cfg.llm_api_mode or os.getenv("LLM_API_MODE", "chat_completions")

    # Hermes extracts query params from base_url via _extract_url_query_params()
    # and forwards them as default_query to every SDK call.  The Codex
    # /responses endpoint requires client_version as a query parameter.
    if api_mode == "codex_responses" and base_url and "client_version" not in base_url:
        sep = "&" if "?" in base_url else "?"
        base_url = base_url.rstrip("/") + sep + "client_version=1.0.0"

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
        # only auto-raises for localhost, not for a remote host like llm-host03.
        #   HERMES_API_TIMEOUT:          total request timeout per LLM call
        #   HERMES_STREAM_READ_TIMEOUT:  max seconds to first/next byte on the stream
        #   HERMES_STREAM_STALE_TIMEOUT: Hermes' own no-progress watchdog
        #
        # Mindestens HERMES_MIN_LLM_TIMEOUT: manche Gateways (llmbase.ai, gemessen
        # 2026-10-02) puffern einen Werkzeugaufruf komplett und schicken minutenlang KEIN
        # Byte, bis er fertig ist — bei 120 s brach Hermes lange Aufrufe (ab ~2 400
        # Tokens bei GLM 5.3 Flash, ~20 Tokens/s) selbst ab und probierte es endlos neu.
        # Das Gateway selbst schneidet nach ~240 s; darüber zu warten zeigt dessen
        # eindeutigen Fehler statt eines eigenen Abbruchs.
        effective = max(int(timeout_seconds), HERMES_MIN_LLM_TIMEOUT)
        os.environ["HERMES_API_TIMEOUT"] = str(effective)
        os.environ["HERMES_STREAM_READ_TIMEOUT"] = str(effective)
        os.environ["HERMES_STREAM_STALE_TIMEOUT"] = str(effective)

    log.info("[%s] creating AIAgent: model=%s base_url=%s mode=%s searxng=%s timeout=%s",
             sid[:8], model or "(default)", base_url or "(default)", api_mode,
             searxng_url or "(none)",
             (f"{timeout_seconds}s (wirksam {max(int(timeout_seconds), HERMES_MIN_LLM_TIMEOUT)}s)"
              if timeout_seconds else "(default)"))

    # The system prompt names no SSH user and no domain: ~/.ssh/config sets both, for
    # every host. (There used to be a replace() here meant to substitute the username,
    # but its search string never occurred in the prompt — it was a no-op that read
    # like configuration.)
    system_prompt = SYSTEM_PROMPT

    # Toolsets are derived from ~/.hermes/config.yaml — the per-user config
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
    # ~/.hermes/config.yaml and discovered at container startup — no
    # per-session dynamic registration needed.
    from hermes_cli.mcp_startup import wait_for_mcp_discovery
    wait_for_mcp_discovery(timeout=8.0)

    return agent


# ── Session endpoints ──────────────────────────────────────────────

def _hermes_user_message(content: str, image_paths: list[str] | None):
    """Nachricht für Hermes — Text, oder Content-Blöcke wenn Bilder dabei sind.

    Hermes nimmt für ``user_message`` ausdrücklich auch eine Liste entgegen und legt
    sie unverändert als ``content`` ab (agent/conversation_loop.py: der Zweig
    ``isinstance(user_message, list)``). Wir bauen deshalb das OpenAI-Vision-Format
    mit einer data-URL.

    Ob daraus eine brauchbare Antwort wird, hängt am eingestellten LLM: ein lokales
    Modell ohne Bildverständnis ignoriert den Block oder lehnt ihn ab. Das ist eine
    Eigenschaft der Konfiguration, keine dieses Codes — deshalb wird hier nichts
    geraten und nichts stillschweigend weggelassen.
    """
    if not image_paths:
        return content

    import base64 as _b64
    import mimetypes as _mt

    blocks: list[dict] = [{"type": "text", "text": content}]
    for path in image_paths:
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            log.warning("Bild %s nicht lesbar: %s", path, exc)
            continue
        mime = _mt.guess_type(path)[0] or "image/png"
        url = f"data:{mime};base64," + _b64.b64encode(raw).decode()
        blocks.append({"type": "image_url", "image_url": {"url": url}})

    # Kam kein Bild durch, bleibt es bei reinem Text — eine Liste mit nur einem
    # Textblock wäre eine unnötige Abweichung vom Normalfall.
    return blocks if len(blocks) > 1 else content


async def _run_cli_agent(
    agent_type: str, sid: str, model: str, message: str,
    history: list[dict], session: dict, image_paths: list[str] | None = None,
):
    """Async generator: stream output from claude/codex CLI subprocess as SSE events.

    Both CLIs run unprivileged as yolo (uid 1000) inside the per-user container with
    credentials injected by the backend (claude → ~/.claude/.credentials.json, codex →
    $CODEX_HOME/config.toml + OPENAI_API_KEY). The console never exposes the CLI itself
    — only the streamed answer.

    sid is the CentralStation session UUID. Claude uses it as --session-id so the
    conversation is persisted in ~/.claude/sessions/<sid>.json on the cs-ide-cfg volume
    and survives container restarts. Codex captures its own internal session ID from the
    JSONL stream for resume on subsequent turns.
    """
    # yolo (uid 1000) owns /home/yolo; /root is 0700 root:root and unwritable by this
    # process. HOME="/root" here made Claude fail internal writes outside
    # CLAUDE_CONFIG_DIR (npm/node cache dirs etc.), which corrupted .credentials.json
    # mid-run (emptied accessToken) rather than just erroring cleanly.
    env = {**os.environ, "HOME": _YOLO_HOME}
    # MCP tool-call timeout (ms). A deepsearch can run up to 900s (aikb
    # DEEPSEARCH_TIMEOUT_S); the Claude CLI default (60s) would abort it. 930s sits above
    # it — same value as MCP_TOOL_TIMEOUT_S for Hermes and Codex in userenv_manager.
    env.setdefault("MCP_TOOL_TIMEOUT", "930000")
    env.setdefault("MCP_TIMEOUT", "60000")

    if agent_type == "claude_cli":
        # --session-id: sets the UUID for a NEW session (first message).
        # --resume <sid>: continues an EXISTING session (subsequent messages / after restart).
        # --permission-mode dontAsk: suppresses interactive approval dialogs without the root
        #   restriction of bypassPermissions. --allowedTools Bash: explicitly grant Bash so
        #   ssh/commands work (dontAsk still enforces permissions without allowedTools).
        # No manual history prepending — Claude reads it from ~/.claude/projects/.../<sid>.jsonl
        # (on the cs-ide-cfg named volume — survives container restarts).
        # Only pass --model when the model string is a Claude model (claude-* prefix);
        # the injected LLM config model is usually an OpenAI/custom name that Claude rejects.

        # Pre-flight auth check: claude clears .credentials.json on failed token refresh.
        # Re-inject from backend if the file is missing or has empty tokens.
        try:
            import json as _json
            _creds_path = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude")), ".credentials.json")
            _creds_ok = False
            try:
                _c = _json.load(open(_creds_path))
                _creds_ok = bool(_c.get("claudeAiOauth", {}).get("accessToken", ""))
            except Exception:
                pass
            if not _creds_ok:
                # Fetch fresh tokens from backend and re-inject
                _backend = os.getenv("CENTRALSTATION_BACKEND_URL", "http://backend:8000")
                _cs_uid = os.getenv("CS_USER_ID", "")
                import aiohttp as _aiohttp
                async with _aiohttp.ClientSession() as _s:
                    async with _s.get(f"{_backend}/api/computer/agent-credentials/claude_cli",
                                      headers={"X-CS-User-ID": _cs_uid,
                                               "X-CS-Agent-Token": os.getenv("CS_AGENT_TOKEN", "")},
                                      timeout=_aiohttp.ClientTimeout(total=5)) as _r:
                        if _r.status == 200:
                            _tok = await _r.json()
                            _at = _tok.get("access_token", "")
                            _rt = _tok.get("refresh_token", "")
                            _exp = _tok.get("expires_at", 0)
                            if _at:
                                import time as _t
                                # expires_at from the backend is an ISO-8601 string (or ms int).
                                # Parse to ms; NEVER fabricate a now+1h expiry (that made the CLI
                                # refresh hourly and rotate/break the token). int() on an ISO
                                # string used to throw here and abort the whole re-inject.
                                def _to_ms(v):
                                    if not v:
                                        return 0
                                    try:
                                        return int(v)
                                    except (ValueError, TypeError):
                                        pass
                                    try:
                                        import datetime as _dt
                                        _s = str(v).replace("Z", "+00:00")
                                        _d = _dt.datetime.fromisoformat(_s)
                                        if _d.tzinfo is None:
                                            _d = _d.replace(tzinfo=_dt.timezone.utc)
                                        return int(_d.timestamp() * 1000)
                                    except (ValueError, TypeError):
                                        return 0
                                _exp_i = _to_ms(_exp) or (int(_t.time() * 1000) + 8 * 3_600_000)
                                _existing: dict = {}
                                try:
                                    _existing = _json.load(open(_creds_path))
                                except Exception:
                                    pass
                                _oauth = _existing.get("claudeAiOauth") or {}
                                _oauth.update({"accessToken": _at, "refreshToken": _rt, "expiresAt": _exp_i})
                                _oauth.setdefault("scopes", ["user:file_upload","user:inference","user:mcp_servers","user:profile","user:sessions:claude_code"])
                                _oauth.setdefault("subscriptionType", "pro")
                                _oauth.setdefault("rateLimitTier", "default_raven")
                                _existing["claudeAiOauth"] = _oauth
                                with open(_creds_path, "w") as _f:
                                    _json.dump(_existing, _f)
                                log.info("[cli:claude] re-injected credentials from backend")
        except Exception as _e:
            log.warning("[cli:claude] credential preflight failed: %s", _e)

        # Determine if a session JSONL already exists for this sid.
        # After a container restart the in-memory flag is gone, so we also check the
        # filesystem. Claude stores sessions under ~/.claude/projects/<cwd-slug>/<sid>.jsonl.
        # The working dir inside the container is /app, so the slug is "-app".
        claude_started = session.get("claude_session_started", False)
        if not claude_started:
            import glob as _glob
            _claude_dir = os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude"))
            jsonl_files = _glob.glob(f"{_claude_dir}/projects/*/{sid}.jsonl")
            claude_started = bool(jsonl_files)
            if claude_started:
                session["claude_session_started"] = True

        # Build --allowedTools dynamically: built-ins + every MCP server in .claude.json.
        # configure_claude_credentials writes all personal MCP connectors (centralstation,
        # VibeMK, AWX-NG, …) into .claude.json at session-create time.
        # File tools are listed explicitly: in dontAsk mode an unlisted tool is simply
        # denied, so without Write/Edit the agent could not save a report to the
        # workspace at all — not even inside a write window, which only reaches the
        # hook. Where they may write is the hook's call (cs-readonly-guard.py:
        # workspace + /tmp free, everything else behind the write window).
        _allowed_tools = CLAUDE_BUILTIN_TOOLS
        try:
            import json as _jcfg
            _claude_cfg = os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude"))
            _cj = _jcfg.load(open(os.path.join(_claude_cfg, ".claude.json")))
            _mcp_names = list(_cj.get("mcpServers", {}).keys())
            if _mcp_names:
                _allowed_tools += "," + ",".join(f"mcp__{n}" for n in _mcp_names)
        except Exception:
            _allowed_tools = CLAUDE_BUILTIN_TOOLS + ",mcp__centralstation"

        if claude_started:
            # Veraltete Werkzeug-Momentaufnahmen entfernen: beim --resume nähme die CLI
            # sonst die beim Sitzungsstart gespeicherte Werkzeugliste statt der aktuellen
            # (claude_session.py). Ein erweitertes MCP-Werkzeug bliebe für immer unsichtbar.
            try:
                from claude_session import refresh_tool_snapshots
                _n = refresh_tool_snapshots(
                    os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude")), sid)
                if _n:
                    log.info("[cli:claude] %s: %d stale tool snapshot(s) dropped before resume",
                             sid[:8], _n)
            except Exception as _e:
                log.warning("[cli:claude] tool snapshot refresh failed: %s", _e)
            # Session file exists — resume it.
            cmd = ["claude", "--print", "--resume", sid,
                   "--permission-mode", "dontAsk", "--allowedTools", _allowed_tools]
        else:
            # First call for this sid — create the session file with this UUID.
            cmd = ["claude", "--print", "--session-id", sid,
                   "--permission-mode", "dontAsk", "--allowedTools", _allowed_tools]
        if model and model.lower().startswith("claude"):
            cmd += ["--model", model]
        # stream-json emits JSONL per event so we can surface tool calls;
        # --verbose is required when using --output-format with --print.
        cmd += ["--output-format", "stream-json", "--verbose"]
        # "--" terminates option parsing so the message is not consumed as a tool name
        # by the variadic --allowedTools argument.
        cmd += ["--", message]

        # Backup credentials before running — Claude sometimes clears .credentials.json
        # on a failed internal token refresh (race with VS Code Extension or revoked token).
        # We restore from backup so the user doesn't lose a valid token permanently.
        _claude_dir_run = os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude"))
        _creds_path_run = os.path.join(_claude_dir_run, ".credentials.json")
        _creds_bak_run  = os.path.join(_claude_dir_run, ".credentials.json.prerun")
        try:
            import shutil as _sh
            _sh.copy2(_creds_path_run, _creds_bak_run)
        except Exception:
            _creds_bak_run = None  # no backup possible — skip restore

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,   # never inherit the server's stdin
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=4 * 1024 * 1024,  # 4 MB — stream-json init line can exceed 64 KB default
            env=env,
            cwd=_agent_cwd(),
        )
        pending_tool: str | None = None
        tool_labels: dict = {}
        log.info("[cli:claude] subprocess started pid=%s cmd=%s", proc.pid, " ".join(cmd[:6]))
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                log.debug("[cli:claude] non-json stdout: %.120s", line)
                continue
            ev_type = ev.get("type", "")
            log.debug("[cli:claude] event type=%s", ev_type)
            if ev_type == "assistant":
                for block in ev.get("message", {}).get("content", []):
                    btype = block.get("type", "")
                    if btype == "tool_use":
                        if pending_tool:
                            # consecutive tool call — close the previous one before opening new
                            log.info("[cli:claude] tool_done (implicit) tool=%s", pending_tool)
                            yield {"type": "tool_done", "tool": pending_tool}
                        pending_tool = block.get("name", "tool")
                        _inp = block.get("input") or {}
                        tool_labels[block.get("id")] = (
                            f"{pending_tool}: "
                            f"{str(_inp.get('command') or _inp.get('file_path') or '')[:160]}")
                        log.info("[cli:claude] tool_start tool=%s", pending_tool)
                        yield {"type": "tool_start", "tool": pending_tool}
                    elif btype == "text":
                        text = block.get("text", "")
                        if text:
                            if pending_tool:
                                log.info("[cli:claude] tool_done tool=%s", pending_tool)
                                yield {"type": "tool_done", "tool": pending_tool}
                                pending_tool = None
                            log.debug("[cli:claude] delta len=%d", len(text))
                            yield {"type": "delta", "text": text}
            elif ev_type == "user":
                # Werkzeugergebnisse. Eine Sperre durch cs-readonly-guard sichtbar machen —
                # sonst stünde in der Konsole nur "Bash", und was geschah, wüsste nur das
                # Modell.
                for block in ev.get("message", {}).get("content", []) or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    body = block.get("content")
                    if isinstance(body, list):
                        body = " ".join(str(b.get("text", "")) for b in body if isinstance(b, dict))
                    if "READ-ONLY-MODUS" in str(body or ""):
                        label = f"⛔ Schreibsperre: {tool_labels.get(block.get('tool_use_id'), 'tool')}"
                        yield {"type": "tool_start", "tool": label}
                        yield {"type": "tool_done", "tool": label, "error": True}
            elif ev_type == "result":
                log.info("[cli:claude] result subtype=%s is_error=%s turns=%s",
                         ev.get("subtype"), ev.get("is_error"), ev.get("num_turns"))
        log.info("[cli:claude] stdout closed, waiting for process")
        if pending_tool:
            log.info("[cli:claude] tool_done (at exit) tool=%s", pending_tool)
            yield {"type": "tool_done", "tool": pending_tool}
            pending_tool = None
        await proc.wait()
        log.info("[cli:claude] process exited rc=%s", proc.returncode)

        # Restore credentials if Claude cleared them during the run.
        if _creds_bak_run:
            try:
                import json as _json2
                _cleared = False
                try:
                    _cur = _json2.load(open(_creds_path_run))
                    _cleared = not bool(_cur.get("claudeAiOauth", {}).get("accessToken", ""))
                except Exception:
                    _cleared = True
                if _cleared:
                    import shutil as _sh2
                    _sh2.copy2(_creds_bak_run, _creds_path_run)
                    log.info("[cli:claude] credentials were cleared by claude — restored from backup")
                import os as _os2
                _os2.unlink(_creds_bak_run)
            except Exception as _re:
                log.warning("[cli:claude] credential restore failed: %s", _re)

        if proc.returncode not in (0, None):
            err = (await proc.stderr.read()).decode(errors="replace")
            if err.strip():
                log.warning("[cli:claude] stderr: %.300s", err)
                yield {"type": "error", "text": err[:500]}
        else:
            # Mark that the session file now exists so the next call uses --resume.
            session["claude_session_started"] = True
        yield {"type": "done"}
        return

    # ── codex_cli ─────────────────────────────────────────────────────
    # `codex exec --json` emits JSONL events; the answer arrives as an
    # item.completed event with item.type == "agent_message". We source
    # /root/.profile first so OPENAI_API_KEY (the ChatGPT OAuth token) is set.
    # approval_policy = "never" is set in config.toml; no CLI flag needed.
    # On subsequent turns, use `codex exec resume <codex_session_id>` if we captured
    # the internal session ID from a previous turn's JSONL stream.
    # Codex config lives under a yolo-writable CODEX_HOME (the process runs as yolo,
    # so /root/.codex is unreadable). userenv_manager writes config.toml + env there.
    codex_home = os.environ.get("CODEX_HOME") or f"{_YOLO_HOME}/.codex"
    codex_session_id = session.get("codex_session_id", "")
    if codex_session_id:
        # --skip-git-repo-check auch hier: ohne bricht resume im Workspace mit "Not inside
        # a trusted directory" ab.
        codex_cmd = f"exec resume {codex_session_id} --json --skip-git-repo-check"
        if model:
            codex_cmd += f' --model "$CODEX_MODEL"'
        sh_cmd = f'. "$CODEX_HOME/env"; exec codex {codex_cmd} "$MSG"'
    else:
        codex_cmd = "exec --json --skip-git-repo-check"
        if model:
            codex_cmd += f' --model "$CODEX_MODEL"'
        sh_cmd = f'. "$CODEX_HOME/env"; exec codex {codex_cmd} "$MSG"'

    # Bilder: codex nimmt sie als -i/--image <FILE>. Die Pfade gehen als eigene
    # Umgebungsvariablen mit und werden im Kommando nur referenziert — in den
    # sh -c-String interpoliert wären sie eine Shell-Injektion, denn sie stammen
    # letztlich aus einem Upload.
    img_env: dict[str, str] = {}
    if image_paths:
        flags = []
        for i, path in enumerate(image_paths):
            var = f"CS_IMG_{i}"
            img_env[var] = path
            flags.append(f'-i "${var}"')
        sh_cmd = sh_cmd.replace("exec codex ", "exec codex " + " ".join(flags) + " ", 1)

    proc = await asyncio.create_subprocess_exec(
        "sh", "-c", sh_cmd,
        stdin=asyncio.subprocess.DEVNULL,   # codex exec else tries to read piped stdin → "Reading additional input from stdin..."
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**env, "HOME": _YOLO_HOME, "CODEX_HOME": codex_home,
             "MSG": message, "CODEX_MODEL": model or "", **img_env},
        cwd=_agent_cwd(),
    )
    emitted = False
    log.info("[cli:codex] subprocess started pid=%s", proc.pid)
    started_items: set = set()
    thread_id = codex_session_id
    audit_pos = _guard_audit_size()
    async for raw in proc.stdout:
        line = raw.decode(errors="replace").strip()
        # Sperren dieser Sitzung aus dem Guard-Protokoll nachtragen (siehe _guard_denials).
        audit_pos, denied = _guard_denials(audit_pos, thread_id)
        for label in denied:
            yield {"type": "tool_start", "tool": label}
            yield {"type": "tool_done", "tool": label, "error": True}
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            log.debug("[cli:codex] non-json stdout: %.120s", line)
            continue
        # Capture codex internal session ID for resume on next turns.
        ev_type = ev.get("type", "")
        log.debug("[cli:codex] event type=%s", ev_type)
        # `codex exec --json` meldet die Sitzung als thread.started{thread_id} — nach
        # session.started wurde bis 2026-10-02 vergeblich gesucht, jede Folgenachricht
        # begann deshalb ohne Verlauf.
        if ev_type == "thread.started":
            thread_id = ev.get("thread_id") or thread_id
        if ev_type in ("thread.started", "session.started", "session_started") and not codex_session_id:
            captured = (ev.get("thread_id") or ev.get("session_id") or ev.get("id")
                        or ev.get("session", {}).get("id", ""))
            if captured:
                session["codex_session_id"] = captured
                log.info("[cli:codex] captured codex session_id=%s for sid=%s", captured[:12], sid[:8])
        item = ev.get("item") or {}
        itype = item.get("type", "")
        # `codex exec --json` meldet Werkzeuge als command_execution / mcp_tool_call /
        # file_change. Bis 2026-10-02 erwartete die Konsole hier function_call — die
        # gibt es nur im Sitzungsprotokoll, nicht im exec-Stream; bei Codex war deshalb
        # nie ein Werkzeugschritt zu sehen, auch keine Sperre.
        if itype in _CODEX_TOOL_ITEMS and ev_type == "item.started":
            started_items.add(item.get("id"))
            yield {"type": "tool_start", "tool": _codex_tool_label(item)}
            continue
        if itype in _CODEX_TOOL_ITEMS and ev_type == "item.completed":
            label = _codex_tool_label(item)
            if item.get("id") not in started_items:
                yield {"type": "tool_start", "tool": label}
            failed = item.get("status") not in ("completed", None) or (
                item.get("exit_code") not in (0, None))
            yield {"type": "tool_done", "tool": label, "error": failed}
            if _codex_blocked(item):
                blocked = f"⛔ Schreibsperre: {label}"
                yield {"type": "tool_start", "tool": blocked}
                yield {"type": "tool_done", "tool": blocked, "error": True}
            continue
        if ev_type == "item.completed":
            log.info("[cli:codex] item.completed type=%s name=%s", itype, item.get("name", ""))
            if itype == "function_call":
                tool_name = item.get("name") or item.get("call_id") or "tool"
                log.info("[cli:codex] tool_start tool=%s", tool_name)
                yield {"type": "tool_start", "tool": tool_name}
            elif itype == "function_call_output":
                log.info("[cli:codex] tool_done")
                yield {"type": "tool_done", "tool": "tool"}
            elif itype == "agent_message":
                text = item.get("text", "")
                if text:
                    # codex emits several agent_message events per turn (interim
                    # planning + final answer). Separate them with a blank line so
                    # they don't run together in the console transcript.
                    if emitted:
                        text = "\n\n" + text
                    emitted = True
                    yield {"type": "delta", "text": text}
    await proc.wait()
    audit_pos, denied = _guard_denials(audit_pos, thread_id)
    for label in denied:
        yield {"type": "tool_start", "tool": label}
        yield {"type": "tool_done", "tool": label, "error": True}
    if proc.returncode not in (0, None):
        err = (await proc.stderr.read()).decode(errors="replace")
        if err.strip():
            log.warning("[cli:codex] stderr: %.300s", err)
            if not emitted:
                yield {"type": "error", "text": err[:500]}
    yield {"type": "done"}


GUARD_AUDIT_LOG = "/tmp/cs-readonly-guard.log"


def _guard_audit_size() -> int:
    try:
        return os.path.getsize(GUARD_AUDIT_LOG)
    except OSError:
        return 0


def _guard_denials(pos: int, session_id: str) -> tuple[int, list[str]]:
    """Neue Sperren einer Sitzung aus dem Guard-Protokoll ab Byte `pos`.

    Codex reicht einen gesperrten Aufruf nur ans Modell weiter, im exec-Stream erscheint
    nichts — die Konsole zeigte deshalb nur, was das Modell darüber erzählte.
    """
    if not session_id:
        return pos, []
    try:
        with open(GUARD_AUDIT_LOG, encoding="utf-8") as fh:
            fh.seek(pos)
            chunk = fh.read()
    except OSError:
        return pos, []
    # Nur vollständige Zeilen verbrauchen; eine halb geschriebene kommt beim nächsten Mal.
    done, _, _rest = chunk.rpartition("\n")
    out = []
    for raw in done.splitlines():
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if e.get("decision") != "deny" or e.get("session") != session_id:
            continue
        try:
            inp = json.loads(e.get("input") or "{}")
        except ValueError:
            inp = {}
        what = inp.get("command") or inp.get("file_path") or ""
        out.append(f"⛔ Schreibsperre: {e.get('tool')}: {str(what)[:160]}")
    return pos + len((done + "\n").encode("utf-8")) if done else pos, out


_CODEX_TOOL_ITEMS = {"command_execution", "mcp_tool_call", "file_change", "web_search"}


def hermes_tool_label(name: str) -> str:
    """Anzeigename eines Hermes-Werkzeugs: "mcp__<server>__<werkzeug>" → "werkzeug" (mit Leerzeichen).

    Bis 25.10. schnitt ein gieriger Ausdruck (^mcp_[a-z0-9_]+_) bis zum LETZTEN Unterstrich
    ab: query_idgen_sql wurde zu "sql", lookup_idgen_host zu "host", search_knowledge_base
    zu "base" — in der Konsole stand dann "sql: SELECT …" statt des Werkzeugs.
    """
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) == 3 and parts[2]:
            name = parts[2]
    return name.replace("_", " ")


def _codex_tool_label(item: dict) -> str:
    t = item.get("type")
    if t == "command_execution":
        cmd = str(item.get("command") or "")
        for prefix in ("/bin/bash -lc ", "bash -lc ", "/bin/sh -c "):
            if cmd.startswith(prefix):
                cmd = cmd[len(prefix):].strip("'\"")
        return f"Shell: {cmd[:160]}"
    if t == "mcp_tool_call":
        return f"{item.get('server', 'mcp')}: {item.get('tool', '')}"
    if t == "file_change":
        paths = [c.get("path", "") for c in item.get("changes") or []]
        return f"Datei: {', '.join(paths)[:160]}"
    if t == "web_search":
        return f"Websuche: {str(item.get('query') or '')[:120]}"
    return t or "tool"


def _codex_blocked(item: dict) -> bool:
    """Hat cs-readonly-guard diesen Aufruf gesperrt? Codex reicht die Hook-Meldung durch."""
    text = " ".join(str(item.get(k) or "") for k in ("aggregated_output", "error", "result"))
    return "READ-ONLY-MODUS" in text or "blocked by PreToolUse hook" in text


@app.post("/sessions", status_code=201)
def create_session(body: CreateSessionBody = None):
    body = body or CreateSessionBody()
    sid = str(uuid.uuid4())
    label = f"Session {len(_sessions) + 1}"
    agent_type = (body.agent_type or "hermes").strip()
    log.info("Creating session %s (%s) agent_type=%s", sid[:8], label, agent_type)

    if agent_type in ("claude_cli", "codex_cli"):
        # CLI-backed session — no Hermes agent, subprocess invoked per message.
        _sessions[sid] = {
            "agent": None,
            "agent_type": agent_type,
            "label": label,
            "msg_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "llm_model": (body.llm_model or "").strip(),
            "llm_base_url": "",
            "history": [],  # in-memory conversation history for CLI agents
        }
        log.info("Session %s ready (%s) [%s], total active: %d",
                 sid[:8], label, agent_type, len(_sessions))
        return {"session_id": sid, "label": label}

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
        "agent_type": "hermes",
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


def _find_lineage_tip(db, sid: str) -> str:
    """Walk child-session chain and return the leaf (tip) session ID.

    Hermes branches a new session each time run_conversation() is called with
    conversation_history=[…].  The new session's parent_session_id points to
    the CentralStation UUID.  Walking to the tip and fetching with
    include_ancestors=True gives the complete, deduplicated conversation.
    """
    current = sid
    seen = {current}
    while True:
        try:
            row = db._conn.execute(
                "SELECT id FROM sessions WHERE parent_session_id = ? ORDER BY rowid DESC LIMIT 1",
                (current,),
            ).fetchone()
        except Exception:
            break
        if not row or row[0] in seen:
            break
        seen.add(row[0])
        current = row[0]
    return current


# CLI agents keep in-memory history in _sessions[sid]["history"]. That is lost on a
# container restart / idle-reap. Claude persists the full conversation in its own
# ~/.claude/projects/<cwd-slug>/<sid>.jsonl (on the cs-ide-cfg volume), so we can
# reconstruct the Console display from it even when the in-memory copy is gone.
# Codex has no such file — its Console history does not survive a restart.

def _load_claude_history(sid: str) -> list[dict]:
    """Reconstruct [{role, content}] from Claude's own session .jsonl (durable).

    Claude writes one JSON object per line; user/assistant turns carry a `message`
    dict with role + content (content is a string or a list of blocks). We keep the
    text of user/assistant turns so the Console can replay the conversation after the
    container was restarted or idle-reaped (the in-memory copy is then empty).
    """
    import glob as _glob
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR", f"{_YOLO_HOME}/.claude")
    matches = _glob.glob(f"{cfg_dir}/projects/*/{sid}.jsonl")
    if not matches:
        return []
    out: list[dict] = []
    try:
        with open(matches[0]) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") not in ("user", "assistant"):
                    continue
                msg = ev.get("message") or {}
                role = msg.get("role")
                if role not in ("user", "assistant"):
                    continue
                content = msg.get("content")
                if isinstance(content, list):
                    text = "".join(
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                else:
                    text = content or ""
                if text.strip():
                    out.append({"role": role, "content": text})
    except Exception as exc:
        log.warning("[%s] claude history parse failed: %s", sid[:8], exc)
    return out


@app.get("/sessions/{sid}/history")
def get_history(sid: str):
    # Claude's JSONL is the authoritative, complete history.  The in-memory list only
    # contains messages exchanged since this userenv process created/restored the
    # session and would therefore hide older turns after a restart or idle reap.
    # Codex has no equivalent durable file, so it continues to use memory here.
    _agent_type = _sessions.get(sid, {}).get("agent_type")
    if _agent_type in ("claude_cli", "codex_cli"):
        mem = _sessions[sid].get("history") or []
        if _agent_type == "claude_cli":
            durable = _load_claude_history(sid)
            return durable or mem
        return mem
    # Not in memory (fresh container): try Claude's .jsonl before the Hermes path.
    if sid not in _sessions:
        claude_hist = _load_claude_history(sid)
        if claude_hist:
            return claude_hist

    # Hermes: always read from SessionDB — it is the authoritative source.
    # Branches child sessions when run_conversation() is called with
    # conversation_history=[…] — walk to the lineage tip and include ancestors
    # so all turns (parent + child branches) appear in the returned history.
    try:
        from hermes_state import SessionDB
        db = SessionDB()
        tip = _find_lineage_tip(db, sid)
        history = db.get_messages_as_conversation(tip, include_ancestors=(tip != sid))
        if history:
            return history
    except Exception as exc:
        log.debug("SessionDB read for %s failed: %s", sid[:8], exc)

    # Fallback: agent in-memory history (only non-empty mid-stream or for new sessions)
    if sid in _sessions:
        agent = _sessions[sid]["agent"]
        return getattr(agent, "conversation_history", None) or []

    raise HTTPException(404, "Session nicht gefunden")


@app.get("/sessions/{sid}/tool-calls")
def get_tool_calls(sid: str):
    """Die Werkzeugaufrufe einer Sitzung: [{tool, command, input}] — Quelle für Rezepte.

    Bewusst die AUSGEFÜHRTEN Aufrufe, nicht der Chat-Text: ein Rezept soll festhalten,
    was tatsächlich geholfen hat, nicht was das Modell darüber erzählt.
    - Hermes: SessionDB (assistant.tool_calls, OpenAI-Format)
    - Claude: ~/.claude/projects/*/<sid>.jsonl (tool_use-Blöcke)
    - Codex: Rollout der Thread-ID (function_call) — nur solange der Container die
      Thread-ID kennt (sie liegt im Speicher des Sitzungs-Dicts)
    """
    agent_type = _sessions.get(sid, {}).get("agent_type")
    calls: list[dict] = []
    if agent_type == "codex_cli":
        calls = _codex_tool_calls(_sessions[sid].get("codex_session_id") or "")
    elif agent_type == "claude_cli" or (sid not in _sessions and _claude_jsonl(sid)):
        calls = _claude_tool_calls(sid)
    else:
        try:
            from hermes_state import SessionDB
            db = SessionDB()
            tip = _find_lineage_tip(db, sid)
            for m in db.get_messages_as_conversation(tip, include_ancestors=(tip != sid)) or []:
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    calls.append(_call(fn.get("name", ""), fn.get("arguments")))
        except Exception as exc:  # noqa: BLE001
            log.debug("tool-calls: SessionDB read for %s failed: %s", sid[:8], exc)
    return {"session_id": sid, "agent_type": agent_type or "hermes", "calls": calls[:300]}


def _call(name: str, args) -> dict:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {"raw": args}
    args = args if isinstance(args, dict) else {}
    cmd = args.get("command") or args.get("cmd") or ""
    if isinstance(cmd, list):
        cmd = " ".join(str(c) for c in cmd)
        for prefix in ("/bin/bash -lc ", "bash -lc ", "/bin/sh -c "):
            if cmd.startswith(prefix):
                cmd = cmd[len(prefix):]
    return {"tool": name, "command": str(cmd)[:2000],
            "input": {k: (str(v)[:300] if not isinstance(v, (int, float, bool)) else v)
                      for k, v in list(args.items())[:12] if k not in ("command", "cmd", "content")}}


def _claude_jsonl(sid: str) -> str | None:
    import glob as _glob
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR", f"{_YOLO_HOME}/.claude")
    m = _glob.glob(f"{cfg_dir}/projects/*/{sid}.jsonl")
    return m[0] if m else None


def _claude_tool_calls(sid: str) -> list[dict]:
    path = _claude_jsonl(sid)
    out: list[dict] = []
    if not path:
        return out
    with open(path) as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") != "assistant":
                continue
            for b in (ev.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    out.append(_call(b.get("name", ""), b.get("input") or {}))
    return out


def _codex_tool_calls(thread_id: str) -> list[dict]:
    import glob as _glob
    out: list[dict] = []
    if not thread_id:
        return out
    home = os.environ.get("CODEX_HOME", f"{_YOLO_HOME}/.codex")
    for path in _glob.glob(f"{home}/sessions/**/rollout-*{thread_id}*.jsonl", recursive=True):
        with open(path) as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                pl = ev.get("payload") or {}
                if ev.get("type") == "response_item" and pl.get("type") == "function_call":
                    out.append(_call(pl.get("name", ""), pl.get("arguments")))
    return out


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
    # Pfade eingefügter Bilder, im Container sichtbar (/home/yolo/workspaces/...).
    # Das Bild selbst reist nicht durch das Protokoll — siehe computer_proxy.
    image_paths: list[str] | None = None
    # Console agent type override forwarded from the backend
    agent_type: str | None = None


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
            # CLI agents never write to SessionDB and keep no on-disk history cache
            # (never under .hermes — that's Hermes-agent state only). On container
            # restart their in-memory entry is gone; instead of 404, create a fresh
            # empty entry so the session can continue. Claude's own conversation is
            # NOT lost — it resumes via --resume from ~/.claude/sessions/<sid>.json on
            # the cs-ide-cfg volume; only the Console UI's pre-restart message replay
            # is empty. Codex has no equivalent — its Console history is genuinely gone.
            if body.agent_type in ("claude_cli", "codex_cli"):
                log.info("CLI session %s: creating fresh entry after container restart (agent_type=%s)",
                         sid[:8], body.agent_type)
                _sessions[sid] = {
                    "agent": None,
                    "agent_type": body.agent_type,
                    "label": "Session (restored)",
                    "msg_count": 0,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "history": [],
                }
            else:
                log.warning("Restore failed for session %s — not found in SessionDB", sid[:8])
                raise HTTPException(404, "Session nicht gefunden")

    else:
        # Session is in memory — check if LLM config changed since it was created.
        # Re-initialize the agent when base_url or model differ so the user does not
        # need to reload after changing the LLM in CentralStation settings.
        # CLI sessions (codex_cli / claude_cli) do NOT use a Hermes agent — skip re-init.
        session = _sessions[sid]
        if session.get("agent_type") not in ("claude_cli", "codex_cli"):
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

    # ── CLI agent fast path ─────────────────────────────────────────
    session_agent_type = _sessions[sid].get("agent_type", "hermes")
    # Session type is authoritative — never let a per-request body.agent_type override
    # a session that was created as hermes. Otherwise a codex_cli request body against
    # a hermes session (e.g. old session selected while in codex_cli mode) would call
    # codex exec on a hermes session, causing 401 + stdin errors.
    effective_agent_type = session_agent_type
    if effective_agent_type in ("claude_cli", "codex_cli"):
        _sessions[sid]["msg_count"] += 1
        history: list[dict] = _sessions[sid].setdefault("history", [])
        history.append({"role": "user", "content": body.content})

        async def cli_event_stream():
            output_parts: list[str] = []
            try:
                async for event in _run_cli_agent(effective_agent_type, sid, body.llm_model or "", body.content, history[:-1], _sessions[sid], body.image_paths):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event["type"] == "delta":
                        output_parts.append(event["text"])
                    if event["type"] in ("done", "error"):
                        break
            except Exception as exc:
                log.error("[%s] cli agent error: %s", sid[:8], exc, exc_info=True)
                yield f"data: {json.dumps({'type': 'error', 'text': str(exc)})}\n\n"
            full = "".join(output_parts)
            if full:
                history.append({"role": "assistant", "content": full})

        return StreamingResponse(
            cli_event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    # ────────────────────────────────────────────────────────────────

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

    _tool_label = hermes_tool_label

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
            if db:
                tip = _find_lineage_tip(db, sid)
                history = db.get_messages_as_conversation(tip, include_ancestors=(tip != sid))
            else:
                history = []
            # Tool/reasoning callbacks are AIAgent attributes, NOT run_conversation
            # kwargs — only stream_callback is accepted by run_conversation. Set the
            # progress callback on the agent right before the run (requests to a
            # single session are sequential, so this is safe).
            agent.tool_progress_callback = on_tool_progress
            try:
                agent.run_conversation(
                    user_message=_hermes_user_message(body.content, body.image_paths),
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

    # Fehlversuche beim LLM-Anbieter sichtbar machen. Hermes meldet sie nur ins Log
    # ("API call failed (attempt 1/2) … summary=HTTP 504: …"); in der Konsole stand
    # derweil minutenlang nur "verarbeitet" — es sah aus, als hinge die Sitzung.
    thread_name = f"hermes-{sid[:8]}"
    notice_handler = _ProviderNoticeHandler(
        thread_name, lambda text: loop.call_soon_threadsafe(
            q.put_nowait, {"type": "notice", "text": text}))
    logging.getLogger("agent.conversation_loop").addHandler(notice_handler)
    _stream_sinks[sid] = lambda ev: loop.call_soon_threadsafe(q.put_nowait, ev)

    def run_and_detach() -> None:
        try:
            try:
                _ensure_write_guard()
            except Exception as exc:  # noqa: BLE001 — ohne Sperre kein Lauf
                log.error("[%s] %s", sid[:8], exc)
                loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "text": str(exc)})
                return
            run_sync()
        finally:
            logging.getLogger("agent.conversation_loop").removeHandler(notice_handler)
            _stream_sinks.pop(sid, None)

    threading.Thread(target=run_and_detach, daemon=True, name=thread_name).start()

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
