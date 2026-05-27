# CentralStation

Zentrales IT-Operations-Dashboard für Linux-Systemadministratoren.  
Aggregiert Alerts aus Wazuh, Graylog und CheckMK, synchronisiert Jira-Tickets und
unterstützt mit KI bei der gesamten ITIL-konformen Arbeitsdokumentation.

---

## Inhaltsverzeichnis

1. [Getting Started](#getting-started)
2. [Architektur](#architektur)
3. [CheckMK als Single Source of Truth](#checkmk-als-single-source-of-truth)
4. [Features im Überblick](#features-im-überblick)
5. [Operations Cockpit (Dashboard)](#operations-cockpit-dashboard)
6. [News Feed](#news-feed)
7. [OpenSearch-Suchen (FeedSearches)](#opensearch-suchen-feedsearches)
8. [Alert-Aggregation und Enrichment](#alert-aggregation-und-enrichment)
9. [Kanban und Jira](#kanban-und-jira)
10. [KI-Funktionen](#ki-funktionen)
11. [Prometheus-Metriken & PromQL](#prometheus-metriken--promql)
12. [Konnektoren](#konnektoren)
13. [Benutzerverwaltung und RBAC](#benutzerverwaltung-und-rbac)
14. [Einstellungen und Präferenzen](#einstellungen-und-präferenzen)
15. [API-Referenz](#api-referenz)
16. [Datenbankmigrationen](#datenbankmigrationen)
17. [Deployment](#deployment)

---

## Getting Started

### Voraussetzungen

- Docker + Docker Compose (V2)
- OpenSearch 2.x (oder OpenSearch-kompatibler Cluster)
- Optionale Abhängigkeiten: LLM-Endpunkt (OpenAI-kompatibel), Jira, CheckMK, Graylog, Wazuh

### Schnellstart

```bash
# 1. Repository klonen
git clone <repo-url> centralstation
cd centralstation

# 2. Konfigurationsdatei anlegen
cp .env.example .env

# 3. Pflichtfelder in .env setzen:
#    ENCRYPTION_KEY  – Fernet-Key (32 Byte Base64): python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#    DATABASE_URL    – postgresql+asyncpg://user:pass@db/centralstation
#    REDIS_URL       – redis://redis:6379/0
#    SECRET_KEY      – zufälliger JWT-Signing-Key: openssl rand -hex 32

# 4. Stack starten
docker compose up -d

# 5. Warten bis alle Container grün sind
docker compose ps

# 6. Datenbank-Migrationen (beim ersten Start automatisch angewendet)
docker compose exec backend alembic upgrade head

# 7. Ersten Admin-Benutzer anlegen
docker compose exec backend python -c "
from app.core.database import sync_engine
from app.core.security import hash_password
from app.models.user import User, Base
import sqlalchemy as sa
Base.metadata.create_all(sync_engine)
with sync_engine.begin() as conn:
    conn.execute(sa.insert(User).values(email='admin@example.com', hashed_password='$(python3 -c "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash('changeme'))")', role='admin', is_active=True))
"
# Alternativ: API-Endpunkt /api/auth/register (falls aktiviert) nutzen
```

### Erster Login und Setup-Wizard

1. Browser öffnen: `http://localhost` (oder der konfigurierte Host)
2. Mit Admin-Zugangsdaten anmelden
3. Der **Setup-Wizard** startet automatisch (einmalig pro Benutzer):
   - **Schritt 1 – LLM-Verbindung**: OpenAI-kompatibler Endpunkt, Modell-ID, API-Key (optional)
   - **Schritt 2 – Jira konfigurieren**: Jira-URL, persönliches Token, Standard-Projekt
   - **Schritt 3 – Persönliche Filter**: CheckMK-Standorte, VE, Kritikalität (für Feed-Filterung)
   - **Schritt 4 – JQL-Vorlagen**: Standard-JQL-Abfragen für die Jira-Ansicht
4. Nach Abschluss des Wizards → Haupt-Dashboard

### Admin-Konnektoren einrichten

Nach dem ersten Login unter **Einstellungen → Konnektoren** die globalen Systemkonnektoren anlegen:

| Konnektor | Typ | Benötigt für |
|-----------|-----|-------------|
| CheckMK | `checkmk` | Alert-Aggregation, Host-Metadaten, Filter-Werte |
| Graylog | `graylog` | Log-Aggregation |
| Wazuh | `wazuh` | Security-Alerts |
| Prometheus | `prometheus` | Zeitreihen-Widgets |
| it-aikb RAG | `it_aikb` | Lösungssuche, KI-Wissenssuche |

---

## Architektur

```
Browser (Angular 20 LTS)
  └── REST + WebSocket (JWT Bearer)
        └── FastAPI Backend (Python 3.12)
              ├── PostgreSQL 16        – Benutzer, Konnektoren, Kanban, Workflows, KI-Analysen
              ├── Redis 7              – WebSocket Pub/Sub, Sessions
              ├── OpenSearch           – cs-feed-* Indices (alle Alert-Quellen)
              ├── LangGraph            – SysAdmin + Network AI Agents
              └── Externe Systeme
                    ├── CheckMK REST API
                    ├── Graylog REST API
                    ├── Wazuh Indexer API
                    ├── Jira / Jira ServiceDesk
                    ├── Microsoft O365 / Teams (Graph API)
                    ├── Prometheus HTTP API
                    ├── it-aikb RAG API (HyDE + OpenSearch)
                    ├── SearXNG (Web-Suche)
                    └── ID-Generator (Standorte, Switches)
```

### OpenSearch-Indices

| Index | Quelle | Wichtige Felder |
|-------|--------|-----------------|
| `cs-feed-checkmk` | CheckMK REST API | `severity`, `title`, `metadata.host`, `metadata.location`, `metadata.os`, `metadata.ve`, `metadata.criticality`, `metadata.hostgroups` |
| `cs-feed-graylog` | Graylog REST API | `severity`, `title`, `body`, `metadata.source_host`, `metadata.http_response_code`, `metadata.hyde_relevant`, `metadata.container_name` |
| `cs-feed-wazuh` | Wazuh Indexer | `severity`, `title`, `metadata.rule_level`, `metadata.agent`, `metadata.agent.name`, `metadata.location` |
| `cs-feed-o365` | Microsoft Graph | `severity`, `title`, `body`, `user_id`, `metadata.from`, `metadata.received_at` |
| `cs-feed-teams` | Microsoft Graph | `severity`, `title`, `body`, `user_id`, `metadata.from`, `metadata.channel_id` |

Jedes Dokument enthält auch: `id`, `type`, `source`, `status`, `created_at`, `location_name`, `location_city`, `external_url`, `external_id`, `ai_insight`.

---

## CheckMK als Single Source of Truth

### Konzept

CheckMK ist der primäre Inventar- und Metadaten-Lieferant für alle Hosts im Unternehmen. Jeder überwachte Host hat in CheckMK Metadaten-Tags:

| CheckMK-Tag | Bedeutung | CentralStation-Feld |
|-------------|-----------|---------------------|
| `tg-os` | Betriebssystem (`os-linux`, `os-windows`, …) | `metadata.os` |
| `tg-location` / `host_filename` | Standort (Ordner in WATO, z.B. `München`) | `metadata.location` |
| `tg-ve` / `tg-virt_env` | Virtualisierungsumgebung | `metadata.ve` |
| `tg-criticality` | Kritikalität des Hosts | `metadata.criticality` |
| Host-Gruppen | CheckMK-Hostgroups des Hosts | `metadata.hostgroups` |

Diese Metadaten werden bei der Alert-Aggregation aus CheckMK gelesen und in den OpenSearch-Index (`cs-feed-checkmk`) geschrieben.

### Filter-Mechanismus (Single Source of Truth)

Wenn ein Benutzer in **Meine Einstellungen** Filter setzt (z.B. `Standort = München`, `OS = Linux`), werden diese Filter **auf alle Quellen angewendet**:

1. **CheckMK-Alerts**: Direkte Filterung über `metadata.os`, `metadata.location`, `metadata.ve`, `metadata.criticality`, `metadata.hostgroups`
2. **Graylog/Wazuh-Alerts**: CentralStation ermittelt aus dem CheckMK-Index alle Hosts, die den Filterkriterien entsprechen (→ `host_scope`). Anschließend werden nur Graylog/Wazuh-Items angezeigt, deren Hostnamen in diesem Scope liegen.
3. **Items ohne Host-Metadaten**: Werden immer angezeigt (nie versteckt durch fehlende Felder)

```
Beispiel:
  User-Filter: Standort = "München"
  
  1. CentralStation fragt cs-feed-checkmk: "Welche Hosts haben location=München?"
     → [docker001, docker086, srv023, ...]
  
  2. Graylog-Suche wird auf metadata.source_host IN [docker001, docker086, ...] beschränkt
  
  3. Wazuh-Suche wird auf metadata.agent.name IN [docker001, docker086, ...] beschränkt
  
  Effekt: Der Feed zeigt nur Ereignisse aus Hosts im Münchener Standort —
          egal aus welcher Quelle (CheckMK, Graylog oder Wazuh).
```

### `get_user_checkmk_host_scope(db, user_id)`

Kernfunktion in `backend/app/services/feed_index.py`:

1. Liest die gespeicherten Filterpreferenzen des Users (`checkmk_os`, `checkmk_locations`, `checkmk_ve`, `checkmk_criticality`)
2. Wenn keine Filter gesetzt → gibt leere Liste zurück (kein Scope-Einschränkung)
3. Wenn Filter aktiv → fragt OpenSearch `cs-feed-checkmk` ab und wendet `_apply_metadata_filters()` an
4. Gibt die Liste der `metadata.host`-Werte aus den gefilterten CheckMK-Items zurück

### Post-Processing-Filter (`_apply_metadata_filters`)

Da OS/Standort/VE/Criticality CheckMK-eigene Konzepte sind, greift der Filter **nur für CheckMK-Items** direkt auf Metadatenfelder. Für alle anderen Quellen (Graylog, Wazuh) wird der `host_scope` als indirekter Filter verwendet.

**Logik:** `Items mit Metadaten-Wert + Wert passt nicht → ausgeblendet. Items ohne Metadaten-Wert → immer angezeigt.`

---

## Features im Überblick

| Bereich | Funktionen |
|---------|------------|
| **Operations Cockpit** | Konfigurierbares Widget-Dashboard (GridStack), Stat-Karten, Listen, Donut-Charts, Zeitreihen (Prometheus), KI-Lagebericht, Top-Hosts |
| **Alert-Aggregation** | CheckMK, Graylog, Wazuh — zentrale Timeline, Acknowledge, Severity-Filter, OS/Standort/VE/Hostgruppen-Filter |
| **News Feed** | Unified OpenSearch Feed, gespeicherte Suchen (Lucene), Last-Seen-Divider, KI-Anreicherung (ai_insight pro Alert) |
| **Kanban-Board** | Drag-Drop, bidirektionaler Jira-/ServiceDesk-Sync, automatische Jira-Importe, AI-erstellte Cards |
| **Meine Tickets** | Per-User Jira-Sicht, JQL-Filter-Verwaltung, KI-JQL-Generator (Freitext → JQL), Live-Ergebnisse; roter Punkt bei neuer Aktivität, Unread-Badge im Nav |
| **Arbeitsdokumentation** | ITIL Work Sessions: Impact/Urgency/Priorität P1–P4, SLA-Tracking, Arbeitsnotizen |
| **KI-Kommentare** | Fortschritt, Pending, Eskalation, Übergabe — per KI generiert, direkt in Jira kopierbar |
| **Abschlussdokumentation** | KI-generierte Lösungsdokumentation mit Root Cause, Maßnahmen, Closure Code |
| **5-Why-Analyse** | ITIL Problem Management — KI führt 5-Why-Analyse durch, schlägt Kernursache vor |
| **Lösungssuche** | RAG-Suche in it-aikb Wissensdatenbank + SearXNG Web-Suche, HyDE-Pattern |
| **Mail-Analyse** | O365 E-Mail → strukturierte Ticket-Informationen per KI |
| **Netzwerk-Modul** | Switch-Alerts (NSA/NSS/NSC), Standort-Zuordnung (ID-Generator), Vendor-Erkennung |
| **KI-Insights** | LangGraph Agenten (SysAdmin + Network), alle 10 Min., Jira Auto-Create |
| **Prometheus-Metriken** | Zeitreihen-Widgets mit PromQL, Lucene→PromQL-Konverter (KI), node_exporter + CheckMK |
| **Setup-Wizard** | Einrichtungsassistent bei Erstanmeldung (LLM-Check, JQL-Konfiguration) |
| **RBAC** | Admin / SysAdmin / Network-Technician / Viewer — rollenbasierte UI und API |
| **Audit-Log** | Protokollierung aller schreibenden Operationen |

---

## Operations Cockpit (Dashboard)

Das Dashboard besteht aus frei konfigurierbaren GridStack-Widgets. Jedes Widget ist unabhängig skalierbar und verschiebbar.

### Widget-Typen

| Typ | Beschreibung | Datenquelle | Pflicht-Config |
|-----|--------------|-------------|----------------|
| `stat` | Einzelne Zahl (Alert-Count) | OpenSearch count query | `severity` oder `search_id` |
| `list` | Alert-Liste mit Severity-Dot | OpenSearch query | `sources`, `limit` (default 10) |
| `donut` | Severity-Verteilung als Donut-Chart (ECharts) | OpenSearch aggregation | `sources` |
| `top_hosts` | Hosts mit den meisten Alerts | OpenSearch aggregation | `sources`, `limit` (default 5) |
| `ai_summary` | Letzter KI-Lagebericht (Findings + Empfehlungen) | PostgreSQL `ai_analyses` | *(keine)* |
| `timeseries` | Zeitreihen-Liniendiagramm (ECharts) | Prometheus PromQL | `promql`, `step`, `hours` |
| `grafana_panel` | Eingebettetes Grafana-Panel als iFrame | Grafana Embed-URL | `panel_url` |

### Widget-Config-Schemas

```jsonc
// stat
{ "severity": "critical", "sources": ["checkmk", "wazuh"] }
{ "search_id": "uuid-einer-gespeicherten-suche" }

// list
{ "sources": ["checkmk", "graylog", "wazuh"], "severity": "high", "limit": 10 }
{ "search_id": "uuid", "limit": 15 }

// donut
{ "sources": ["checkmk", "graylog", "wazuh"] }

// top_hosts
{ "sources": ["checkmk", "wazuh"], "limit": 5 }

// ai_summary
{}  // keine Konfiguration nötig

// timeseries (Prometheus)
{
  "data_source": "prometheus",
  "promql": "100 - (avg(rate(node_cpu_seconds_total{mode='idle'}[5m])) * 100)",
  "step": "1m",
  "hours": 4,
  "unit": "%"
}

// timeseries (CheckMK multi-host)
{
  "data_source": "checkmk",
  "hosts": ["docker086", "docker001"],
  "service": "CPU load",
  "metric": "load1",
  "hours": 4
}

// grafana_panel
{
  "panel_url": "http://grafana:3000/d-solo/<id>/panel?orgId=1&panelId=5&theme=dark",
  "refresh_seconds": 30
}
```

### Dashboard-Verwaltung

- **Konfigurationsmodus aktivieren**: Zahnrad-Icon → Widget-Drag/Resize wird freigeschaltet
- **Widget hinzufügen**: Button öffnet mehrstufigen Dialog (Typ → Titel + Quellen → Typ-spezifisch)
- **Layout speichern**: Automatisch beim Verlassen des Konfigurationsmodus
- **Mehrere Dashboards**: Tab-Leiste oben; über `+`-Icon neues Dashboard anlegen
- **Standard-Layout**: Pro Dashboard-Rücksetzung auf Default-Widgets möglich (`POST /api/dashboard-widgets/dashboards/{id}/reset-defaults`)
- **Klick auf Widget**: Öffnet den News Feed mit den passenden Filtern des Widgets
- **KI-Findings anklicken**: Direktlink aus dem `ai_summary`-Widget-Finding in den Feed mit Hostnamen-/Source-Filter

### Standard-Widgets (automatisch beim ersten Login)

| Widget | Typ | Position | Config |
|--------|-----|----------|--------|
| Severity-Verteilung | `donut` | 0,0 (5×5) | Alle Quellen |
| Kritisch | `stat` | 5,0 (2×2) | severity=critical |
| Hoch | `stat` | 7,0 (2×2) | severity=high |
| Neueste Alerts | `list` | 5,2 (4×3) | Alle Quellen, limit=8 |
| KI-Lagebericht | `ai_summary` | 0,5 (5×4) | — |
| Top-Hosts | `top_hosts` | 5,5 (4×4) | Alle Quellen |

---

## News Feed

### Funktionsweise

Der News Feed zeigt alle Ereignisse aus den aktiven OpenSearch-Indices (`cs-feed-*`) in umgekehrter chronologischer Reihenfolge.

**Quellen** (aktivierbar per Toggle):
- CheckMK-Alerts (Monitoring-Events)
- Graylog-Logs (Systemlogs, Container-Logs)
- Wazuh-Alerts (Security-Events, FIM)
- O365-E-Mails (persönlich, nur eigene)
- Microsoft Teams-Nachrichten (persönlich, nur eigene Kanäle)

### Last-Seen-Divider

- Beim Öffnen des Feeds scrollt die Ansicht automatisch zum `Zuletzt gesehen`-Trennstrich
- Neue Meldungen (seit dem letzten Besuch) erscheinen **oberhalb** des Trennstrichs
- Ältere Meldungen erscheinen **unterhalb**
- Nach 3 Sekunden im Feed wird der Zeitpunkt als `feed_last_seen` gespeichert und der Nav-Badge zurückgesetzt

### Nav-Badge (Unread-Count)

- Rote Zahl am „News Feed"-Navigationseintrag
- Aktualisiert sich automatisch alle 60 Sekunden
- Ruft `GET /api/feed/unread-count?since=<ISO>` auf
- Verschwindet nach 3 Sekunden Aufenthalt im Feed

### Meine Tickets — Unread-Indikatoren

- Rote Zahl am „Meine Tickets"-Navigationseintrag zeigt Anzahl Tickets mit neuer Aktivität
- **Roter Punkt** an einzelnen Ticket-Zeilen erscheint, wenn das Ticket seit dem letzten Öffnen aktualisiert wurde (neuer Kommentar, Statuswechsel, etc.)
- Punkt und Badge verschwinden automatisch, wenn das Ticket in der WorkSession geöffnet wird
- Tracking basiert auf `localStorage` (`ticket_seen_map`): kein separater Backend-Endpunkt nötig
- Beim ersten Besuch der Seite werden alle sichtbaren Tickets als „gesehen" markiert (keine Dots beim Erstbesuch)

### „Neueste Meldungen"-Button

- Erscheint als schwebender Button oben, wenn man mehr als 350px nach unten scrollt
- Klick scrollt die Seite zurück zum Anfang (smooth scroll)

### Gespeicherte Suchen im Feed

- **Suchen-Panel** (ausklappbar): Zeigt alle System-Suchen und persönliche Suchen
- **Toggle-Switches**: Einzelne Suchen deaktivieren (schreibt `feed_disabled_search_ids` in Präferenzen)
- **Persönliche Suche anlegen**: Über das `+`-Icon im Suchen-Panel
- **KI-Assistent**: Button im Suchen-Dialog → Freitext-Eingabe → KI generiert Lucene-Query automatisch

### Highlight-Modus

Wenn ein Item aus einem Widget oder einer externen Quelle direkt aufgerufen wird:
- URL-Parameter `highlight_id=<OpenSearch-Doc-ID>` und/oder `host=`, `source=`, `severity=`
- Das angeklickte Item wird ans Ende der ersten Seite **gepinnt**, falls es älter als die aktuelle Seite ist
- Nach dem Laden scrollt der Feed **automatisch zum hervorgehobenen Item** (blaue Umrandung, 2.8s)
- Der Feed zeigt passende Filterwerte aus den URL-Parametern

### `GET /api/feed/` — Vollständige Parameterliste

| Parameter | Typ | Beschreibung |
|-----------|-----|-------------|
| `limit` | int (max 200) | Anzahl Ergebnisse (default 50) |
| `offset` | int | Paginierung |
| `sources` | string | Kommagetrennte Quellen: `checkmk,graylog,wazuh` |
| `severity` | string | Severity-Filter: `critical`, `high`, `medium`, `low`, `info` |
| `host` | string | Hostnamen-Suche (Wildcard, case-insensitive) |
| `os` | string | OS-Filter (CheckMK) |
| `location` | string | Standort-Filter (CheckMK) |
| `criticality` | string | Kritikalitäts-Filter (CheckMK) |
| `ve` | string | VE-Filter (CheckMK) |
| `hostgroup` | string | Kommagetrennte CheckMK-Hostgroups |
| `search_id` | UUID | Gespeicherte Suche direkt ausführen |
| `index` | string | OpenSearch-Index-Pattern (Direktmodus) |
| `q` | string | Lucene-Query-String (Direktmodus) |
| `highlight_id` | string | Item anpinnen + hervorheben |

---

## OpenSearch-Suchen (FeedSearches)

### Wo werden Suchen definiert?

- **Admin-Bereich**: Einstellungen → Feed → System-Suchen (anlegen, bearbeiten, testen)
- **Benutzer-Bereich**: News Feed → Suchen-Panel → `+` persönliche Suche

### Suchen-Modell

| Feld | Typ | Beschreibung |
|------|-----|-------------|
| `id` | UUID | Eindeutige ID |
| `user_id` | UUID? | NULL = System-Suche, gesetzt = persönliche Suche |
| `name` | string | Anzeigename |
| `index_pattern` | string | OpenSearch-Pattern, z.B. `cs-feed-*`, `cs-feed-wazuh` |
| `query_string` | string | Lucene-Query; leer = match_all |
| `enabled` | bool | Aktiv? |
| `is_system` | bool | Systemweit sichtbar (Admin) |
| `is_exclusion` | bool | Wenn true: Items werden aus dem Feed **ausgeblendet** |
| `position` | int | Reihenfolge in der Liste |

### Ausschluss-Suchen (Exclusion)

Suchen mit `is_exclusion=true` erzeugen `must_not`-Clauses in **allen** Feed-Abfragen. Damit lassen sich dauerhaft unerwünschte Meldungen ausblenden:

```
Beispiel: /etc/patchmon/config.yml FIM-Alerts ausblenden
  query_string: "metadata.syscheck.path:/etc/patchmon/config.yml"
  is_exclusion:  true
```

### Mitgelieferte System-Suchen

| Name | Index | Query |
|------|-------|-------|
| Filebeat (Hyde-relevant) | `cs-feed-graylog` | `metadata.hyde_relevant:true AND NOT metadata.source_host:(nsa* OR nss* OR nsc*)` |
| HTTP-Fehler (Container) | `cs-feed-graylog` | `metadata.http_response_code:>=400 AND metadata.container_name:*` |
| Syslog Errors | `cs-feed-graylog` | `metadata.level:<=4 AND NOT body:uprobes` |
| Wazuh Security Alerts (Level 7+) | `cs-feed-wazuh` | `metadata.rule_level:>=7` |
| Alle CheckMK-Alerts | `cs-feed-checkmk` | *(leer = alle)* |
| Alle Graylog-Logs | `cs-feed-graylog` | *(leer = alle)* |
| Alle Wazuh-Alerts | `cs-feed-wazuh` | *(leer = alle)* |
| Alle Quellen | `cs-feed-*` | *(leer = alle)* |
| Kritische und Hohe Alerts | `cs-feed-*` | `severity:(critical OR high)` |

### Query-Syntax (Lucene)

```
# Graylog: alle Container-Fehler von docker086
metadata.container_name:docker086* AND metadata.http_response_code:>=400

# Wazuh: Security-Events Level 10+ für bestimmten Host
metadata.rule_level:>=10 AND metadata.agent.name:docker086

# Alle: kritische Alerts
severity:critical

# Graylog: Hyde-relevante Meldungen ohne NSA/NSS/NSC
metadata.hyde_relevant:true AND NOT metadata.source_host:(nsa* OR nss* OR nsc*)

# CheckMK: Alle Linux-Hosts mit hoher Severity
severity:(high OR critical) AND metadata.os:Linux
```

---

## Alert-Aggregation und Enrichment

### Pipeline

```
APScheduler (alle 10 Min.)
    │
    └── alert_aggregator.py
          ├── CheckMKConnector.get_alerts()        → cs-feed-checkmk
          ├── GraylogConnector.get_alerts()         → cs-feed-graylog
          ├── WazuhConnector.get_alerts()           → cs-feed-wazuh
          └── feed_index.index_items()              → OpenSearch bulk index
                │
                └── feed_enricher.py (async Background Task)
                      └── LLM: 2-3 Sätze Erklärung + erste Maßnahme → ai_insight Feld
```

### CheckMK-Aggregation

**Endpunkt:** `GET /domain-types/host/collections/all` (alle Hosts + Tags)

Felder, die aus CheckMK extrahiert werden:

| CheckMK-Feld | Normalisierung | OpenSearch-Feld |
|-------------|----------------|-----------------|
| `tags.tg-os` / `tags.operatingsystem` | `_OS_LABEL_MAP` (os-linux → Linux) | `metadata.os` |
| `extensions.attributes.tag_location` / `host_filename` | Ordner aus WATO-Pfad | `metadata.location` |
| `tags.tg-criticality` | Rohwert | `metadata.criticality` |
| `tags.tg-ve` / `tags.tg-virt_env` | Rohwert | `metadata.ve` |
| Host-Groups | Liste aller Gruppen | `metadata.hostgroups` |
| `extensions.attributes.alias` | — | `metadata.alias` |

**Wazuh-Filter (konfigurierbar über Connector-Formular):**

Über die Connector-Zugangsdaten können FIM-Ausschlüsse konfiguriert werden:
```json
{
  "excluded_rule_ids": ["503", "504", "533", "591", "5402", "5501", "5502", "5715"],
  "excluded_fim_paths": ["/etc/cmk-update-agent.state", "/etc/patchmon/config.yml"]
}
```
Werden diese nicht gesetzt, greifen die internen Defaults.

### KI-Anreicherung (ai_insight)

Nach der Indizierung werden neue Alerts mit Severity `critical`, `high` oder `warning` im Hintergrund durch einen LLM-Aufruf angereichert:

- **Prompt:** System-Prompt als erfahrener Linux-Sysadmin; 2-3 Sätze Erklärung + konkrete erste Maßnahme auf Deutsch
- **Input:** `{source}: {title}\n{body}\nHost: {host}\nLocation: {location}`
- **Ergebnis:** Plain-Text (max. 400 Zeichen), gespeichert als `ai_insight` im OpenSearch-Dokument
- **Konfiguration:** `agent.auto_enrich` (default `true`) — in Einstellungen → KI deaktivierbar

---

## Kanban und Jira

### Kanban-Board

- Drag-and-Drop-Board mit fünf Spalten: Backlog → Todo → In Bearbeitung → Review → Fertig
- Statuswechsel per Drag-Drop lösen Jira-Transitions aus (bidirektionaler Sync)
- **Jira-Import**: Über JQL können Jira-Tickets automatisch ins Board importiert werden
- **KI-Card-Erstellung**: KI-Agent erstellt bei kritischen Findings automatisch Kanban-Cards
- **Alert-Verknüpfung**: Cards können mit Feed-Alerts verknüpft werden

### Bidirektionaler Jira-Sync

**Status-Mapping:**

| CentralStation | Jira-Status (Beispiele) |
|----------------|-------------------------|
| `backlog` | Backlog, Open, Selected for Development |
| `todo` | To Do, Open, Ready |
| `in_progress` | In Progress, Doing, Implementing, In Bearbeitung |
| `review` | Review, In Review, Testing, QA |
| `done` | Done, Resolved, Closed, Erledigt |

### Meine Tickets (Jira-View)

- Zeigt Jira-Tickets nach konfigurierten JQL-Abfragen an
- **JQL-Templates**: Standard-Vorlagen vorinstalliert, anpassbar
- **KI-JQL-Generator**: Freitext → optimierte JQL (`POST /api/preferences/jira-queries/generate`)
- **Widget-Anzeige**: Ausgewählte JQL-Abfragen erscheinen als Widget auf der Jira-Seite

### ITIL Work Sessions

Work Sessions dokumentieren die Bearbeitung eines Incidents:

1. **Erstellen**: Jira-Ticket-Key eingeben → CentralStation zieht Ticket-Daten aus Jira
2. **Kategorisierung**: Impact/Urgency → Automatische P1–P4 Priorität + SLA-Frist
3. **Arbeitsnotizen**: Zeitstemple Einträge, wer was wann gemacht hat
4. **KI-Aktionen**:
   - **Kommentar generieren**: Typ wählen (Fortschritt / Pending / Eskalation / Übergabe) → optionales Freitextfeld „Aktuelle Entwicklungen" → KI formuliert Jira-Kommentar
   - **Lösungsdokumentation**: Root Cause, Maßnahmen, Lessons Learned
   - **5-Why-Analyse**: ITIL Problem Management
   - **Lösungssuche**: RAG + Web-Suche

### ITIL Prioritätsmatrix

| Impact ↓ / Urgency → | Hoch | Mittel | Niedrig |
|----------------------|------|--------|---------|
| **Hoch** | P1 (15 Min.) | P2 (60 Min.) | P3 (4 h) |
| **Mittel** | P2 (60 Min.) | P3 (4 h) | P4 (24 h) |
| **Niedrig** | P3 (4 h) | P4 (24 h) | P4 (24 h) |

---

## KI-Funktionen

### LangGraph-Agenten

Zwei autonome Agenten laufen im Hintergrund (APScheduler, alle 10 Minuten):

#### SysAdmin-Agent

```
Node 1: collect_data
  → CheckMK: offene Probleme (letzte 1h)
  → Graylog: ERROR/CRITICAL (letzte 1h)
  → Wazuh: Security-Alerts
  → Jira: neue/unassigned Tickets

Node 2: enrich
  → IP → Standort (ID-Generator)
  → Hostname → Device (NetBox)
  → Vendor-Erkennung (Juniper/Cisco/VMware aus Graylog-Messages)

Node 3: rag_lookup
  → LLM entscheidet: einfache Suche (it-aikb /search) oder Deep Search (/search/stream SSE)
  → Wissensdatenbank: Runbooks, frühere Vorfälle, Dokumentation

Node 4: analyze
  → Korrelation Events + RAG-Kontext
  → Findings + Recommendations (strukturiert, Pydantic AnalysisResult)
  → Speicherung in PostgreSQL (ai_analyses Tabelle)

Node 5: act
  → Kritische Findings → Jira-Ticket (JQL-Dedup verhindert Doppeltickets)
  → WebSocket Push → alle verbundenen SysAdmin/Admin-Clients
```

#### Network-Agent

```
Node 1: collect_switch_logs
  → Graylog: source:(nsa* OR nss* OR nsc*) — letztes 1h, dedupliziert

Node 2: enrich_switches
  → Switch-Name → ID-Generator (location_id → Standortname + Stadt)
  → NetBox: Interface/VLAN-Daten

Node 3: analyze_network
  → STP, LACP, Port-Flapping, MAC-Flut
  → Vendor-Erkennung (Juniper NSA/NSS Patterns)

Node 4: act
  → Findings → PostgreSQL
  → WebSocket Push → Network-Technician-Clients
```

### KI-Chat-Endpunkte

| Endpunkt | Funktion | Eingabe | Ausgabe |
|----------|----------|---------|---------|
| `POST /api/ai/search-assistant` | Freitext → Lucene-Query + optional FeedSearch/Widget anlegen | `{"message": "..."}` | `{"reply": "...", "actions": [...]}` |
| `POST /api/ai/promql-assistant` | Freitext/Lucene → PromQL | `{"message": "..."}` | `{"promql": "...", "explanation": "..."}` |
| `POST /api/workflow/{id}/generate-comment` | Jira-Kommentar generieren | `{"comment_type": "progress", "additional_context": "..."}` | `{"comment": "..."}` |
| `POST /api/workflow/{id}/generate-resolution` | Abschlussdokumentation | — | `{"resolution": "..."}` |
| `POST /api/workflow/{id}/5why` | 5-Why Root Cause Analyse | — | `{"analysis": "..."}` |
| `POST /api/workflow/{id}/suggest-solution` | RAG + Web-Lösungssuche | — | `{"steps": [...], "sources": [...]}` |
| `POST /api/workflow/analyze-mail` | O365 E-Mail analysieren | `{"content": "..."}` | `{"summary": "...", "ticket_key": ...}` |
| `POST /api/preferences/jira-queries/generate` | JQL aus Freitext | `{"description": "..."}` | `{"jql": "...", "name": "..."}` |

### KI-Einstellungen (Einstellungen → KI)

| Setting-Key | Beschreibung | Default |
|-------------|-------------|---------|
| `llm.base_url` | OpenAI-kompatibler Endpunkt | — |
| `llm.model` | Modell-ID (z.B. `qwen:35b`) | — |
| `llm.api_key` | API-Key (optional) | — |
| `llm.vision_model_url` | Vision-Modell Endpunkt | — |
| `llm.vision_model` | Vision-Modell-ID | — |
| `llm.thinking_mode` | Extended Thinking aktivieren | `false` |
| `agent.auto_enrich` | KI-Anreicherung automatisch nach Aggregation | `true` |
| `agent.interval_minutes` | Intervall für Hintergrund-Agenten (Minuten) | `10` |
| `agent.auto_create_jira` | Jira-Tickets automatisch anlegen | `true` |
| `agent.jira_severity_threshold` | Ab welcher Severity Tickets anlegen | `critical` |
| `rag.base_url` | it-aikb RAG API URL | — |
| `rag.api_token` | it-aikb Bearer Token | — |
| `searxng.base_url` | SearXNG Web-Suche URL | — |

---

## Prometheus-Metriken & PromQL

### Lucene → PromQL Konverter

Im Dashboard **Widget hinzufügen → Zeitreihe → PromQL-Konverter**:

| Eingabe | Generierte PromQL (Beispiel) |
|---------|------------------------------|
| `CPU-Auslastung docker086` | `100 - (avg(rate(node_cpu_seconds_total{instance="docker086:9100",mode="idle"}[5m])) * 100)` |
| `memory docker086` | `100 * (1 - node_memory_MemAvailable_bytes{instance="docker086:9100"} / node_memory_MemTotal_bytes{instance="docker086:9100"})` |
| `Netzwerk-Traffic srv023` | `rate(node_network_receive_bytes_total{instance="srv023:9100"}[5m])` |
| `disk` | `100 * (1 - node_filesystem_free_bytes / node_filesystem_size_bytes)` |

### CheckMK-Daten in Prometheus

**Option A – CheckMK Built-in Export:**
```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'checkmk'
    metrics_path: '/cmk/api/1.0/domain-types/metric/collections/all'
    params:
      output_format: ['openmetrics']
    basic_auth:
      username: 'automation'
      password: '<automation-password>'
    static_configs:
      - targets: ['monitoring.ippen.media:443']
    scheme: https
```

**Option B – node_exporter:**
```bash
# Ansible-Deploy auf allen Hosts:
ansible all -m apt -a "name=prometheus-node-exporter state=present" -b
ansible all -m service -a "name=prometheus-node-exporter enabled=yes state=started" -b
```

---

## Konnektoren

### Globale Konnektoren (Admin)

Globale Konnektoren gelten für alle Benutzer und werden von den Hintergrund-Agenten genutzt.

| Typ | Auth-Methode | Pflichtfelder in Credentials |
|-----|-------------|------------------------------|
| `checkmk` | Bearer `<user> <password>` | `username`, `password`, optional: `site` (CheckMK-Instanzname) |
| `graylog` | Basic Auth | `username`, `password` |
| `wazuh` | JWT (eigene Auth) | `username`, `password`, `indexer_url`, `indexer_username`, `indexer_password`, optional: `excluded_rule_ids`, `excluded_fim_paths` |
| `prometheus` | Optional Basic/Bearer | `username` (optional), `password` (optional) |
| `netbox` | Bearer Token | `api_token` |
| `id_generator` | Basic Auth | `username` (`idgen_reader`), `password` |
| `it_aikb` | Bearer Token | `api_token` |

### Persönliche Konnektoren (pro Benutzer)

Persönliche Konnektoren werden vom jeweiligen Benutzer im Setup-Wizard oder unter **Einstellungen → Konnektoren → Meine Konnektoren** angelegt.

| Typ | Auth-Methode | Beschreibung |
|-----|-------------|-------------|
| `jira` | Bearer Token | Jira-Zugriff (Tickets, Kanban-Sync) |
| `jira_sd` | Bearer Token | Jira ServiceDesk (separates Token möglich) |
| `o365` | OAuth2 Device Code Flow | Microsoft 365 E-Mails über Graph API |
| `teams` | OAuth2 Device Code Flow | Microsoft Teams Kanalnachrichten |

**Microsoft-Konnektoren (O365/Teams) einrichten:**
1. Connector anlegen mit Azure **Tenant-ID** und **Client-ID** (vorhandene App-Registration, kein Admin nötig)
2. Button **„Mit Microsoft anmelden"** klicken → Device Code wird angezeigt
3. `microsoft.com/devicelogin` aufrufen, Code eingeben, anmelden
4. Connector wird automatisch gespeichert mit `refresh_token`
5. Token wird automatisch erneuert

**Connector-Priorität bei mehreren Konnektoren gleichen Typs:**  
Persönlicher Konnektor hat immer Vorrang vor dem globalen Admin-Konnektor.

### Konnektor-Aktionen

- **Verbindung testen**: `POST /api/connectors/{id}/test` — prüft Erreichbarkeit und Auth
- **Konnektor löschen**: Admins können jeden Konnektor löschen; Benutzer können ihre persönlichen Konnektoren löschen (`DELETE /api/connectors/my/{type}`)

---

## Benutzerverwaltung und RBAC

### Rollen

| Rolle | Bereich | Einschränkungen |
|-------|---------|-----------------|
| `admin` | Alles: User-Management, Konnektoren, Einstellungen, Audit-Log | — |
| `sysadmin` | Alle Alerts (CheckMK/Wazuh/Graylog allgemein), Kanban, Jira, KI-Insights, Feed | Keine Konnektor-/User-Verwaltung |
| `network` | Graylog Switch-Alerts (nsa*/nss*/nsc*), NetBox, ID-Generator, Netzwerk-Kanban | Keine SysAdmin-Alerts, kein Wazuh, keine Konnektor-Konfig |
| `viewer` | Lesezugriff auf eigenen Bereich | Keine schreibenden Operationen |

### Benutzer-Präferenzen

| Präferenz | Beschreibung |
|-----------|-------------|
| `checkmk_locations` | CheckMK-Standorte für Feed-Filterung (Single Source of Truth) |
| `checkmk_ve` | Virtualisierungsumgebung-Filter |
| `checkmk_criticality` | Kritikalitäts-Filter |
| `checkmk_os` | Betriebssystem-Filter |
| `checkmk_hostgroups` | Hostgruppen-Filter |
| `feed_disabled_search_ids` | Deaktivierte gespeicherte Suchen |
| `feed_checkmk_min_age_minutes` | CheckMK-Mindest-Alter (sehr aktuelle Items ausblenden) |
| `feed_sources_enabled` | Welche Quellen im Feed angezeigt werden |
| `feed_teams_channels` | Microsoft Teams Kanal-IDs für persönlichen Feed |
| `o365_mailbox` | O365-Postfach-Adresse |
| `o365_folder` | O365-Ordner (default: `Inbox`) |
| `jira_project` | Standard-Jira-Projekt |
| `sla_notify_p1_minutes` | SLA-Benachrichtigungsschwelle P1 |
| `sla_notify_p2_minutes` | SLA-Benachrichtigungsschwelle P2 |

---

## Einstellungen und Präferenzen

### Globale Einstellungen (Admin → Einstellungen)

Alle Einstellungen werden verschlüsselt in der Datenbank gespeichert und über `GET/PATCH /api/settings` verwaltet.

**LLM-Konfiguration:**
- `llm.base_url`, `llm.model`, `llm.api_key`
- `llm.vision_model_url`, `llm.vision_model`
- `llm.thinking_mode` (Extended Thinking, default `false`)

**Agent-Konfiguration:**
- `agent.auto_enrich` — KI-Anreicherung automatisch nach Aggregation
- `agent.interval_minutes` — Hintergrund-Agenten-Intervall
- `agent.auto_create_jira` — Tickets automatisch anlegen
- `agent.jira_severity_threshold` — Mindest-Severity für Auto-Ticket

**RAG/Suche:**
- `rag.base_url`, `rag.api_token` — it-aikb Wissensdatenbank
- `searxng.base_url` — SearXNG Web-Suche

### Filter-Werte abrufen

`GET /api/feed/checkmk-filter-values` — liefert die verfügbaren Werte für alle CheckMK-Filter-Dropdowns (OS, Standort, VE, Kritikalität, Hostgruppen) direkt aus OpenSearch.

---

## API-Referenz

### Authentifizierung

| Pfad | Methode | Beschreibung |
|------|---------|-------------|
| `/api/auth/login` | POST | Login; gibt `access_token` + setzt `refresh_token` HttpOnly-Cookie |
| `/api/auth/refresh` | POST | Access Token erneuern via Cookie |
| `/api/auth/logout` | POST | Refresh Token revoken |

### Feed

| Pfad | Methode | Beschreibung |
|------|---------|-------------|
| `/api/feed/` | GET | Unified Alert Feed (OpenSearch), alle Filter-Parameter |
| `/api/feed/unread-count` | GET | Ungelesene Alerts seit `?since=<ISO>` |
| `/api/feed/checkmk-filter-values` | GET | Verfügbare Filter-Werte aus CheckMK-Index |
| `/api/feed/{item_id}/acknowledge` | POST | Alert als bestätigt markieren |
| `/api/feed/{item_id}/enrich` | POST | KI-Anreicherung für einzelnes Item auslösen |

### FeedSearches

| Pfad | Methode | Beschreibung |
|------|---------|-------------|
| `/api/feed-searches/` | GET | Alle Suchen (System + eigene) |
| `/api/feed-searches/` | POST | Neue persönliche Suche anlegen |
| `/api/feed-searches/system` | POST | Neue System-Suche anlegen (Admin) |
| `/api/feed-searches/{id}` | PATCH | Suche bearbeiten |
| `/api/feed-searches/{id}` | DELETE | Suche löschen (nur eigene; System → 403) |
| `/api/feed-searches/{id}/preview` | GET | Vorschau (5 Treffer) |

### Dashboard-Widgets

| Pfad | Methode | Beschreibung |
|------|---------|-------------|
| `/api/dashboard-widgets/dashboards` | GET | Alle Dashboards des Users |
| `/api/dashboard-widgets/dashboards` | POST | Neues Dashboard anlegen |
| `/api/dashboard-widgets/dashboards/{id}` | PATCH | Dashboard umbenennen |
| `/api/dashboard-widgets/dashboards/{id}` | DELETE | Dashboard löschen |
| `/api/dashboard-widgets/dashboards/{id}/reset-defaults` | POST | Standard-Widgets wiederherstellen |
| `/api/dashboard-widgets/` | GET | Alle Widgets des Users (nach Dashboard) |
| `/api/dashboard-widgets/` | POST | Neues Widget anlegen |
| `/api/dashboard-widgets/{id}` | PATCH | Widget (Layout/Config/Titel) ändern |
| `/api/dashboard-widgets/{id}` | DELETE | Widget löschen |
| `/api/dashboard-widgets/{id}/data` | GET | Widget-Daten abrufen (OpenSearch / Prometheus) |

### KI

| Pfad | Methode | Beschreibung |
|------|---------|-------------|
| `/api/ai/search-assistant` | POST | Freitext → Lucene-Query; kann FeedSearch/Widget anlegen |
| `/api/ai/promql-assistant` | POST | Lucene/Freitext → PromQL |
| `/api/ai/trigger/sysadmin` | POST | SysAdmin-Agent manuell auslösen |
| `/api/ai/trigger/network` | POST | Network-Agent manuell auslösen |
| `/api/ai/insights` | GET | Letzte KI-Analysen (ai_analyses) |

### Präferenzen

| Pfad | Methode | Beschreibung |
|------|---------|-------------|
| `/api/preferences/` | GET | Eigene Präferenzen |
| `/api/preferences/` | PATCH | Präferenzen aktualisieren (CheckMK-Filter etc.) |
| `/api/preferences/jira-queries/` | GET | Eigene JQL-Abfragen |
| `/api/preferences/jira-queries/` | POST | Neue JQL-Abfrage |
| `/api/preferences/jira-queries/{id}` | PATCH | JQL-Abfrage bearbeiten |
| `/api/preferences/jira-queries/{id}` | DELETE | JQL-Abfrage löschen |
| `/api/preferences/jira-queries/generate` | POST | KI JQL-Generator |

### Konnektoren

| Pfad | Methode | Beschreibung |
|------|---------|-------------|
| `/api/connectors/` | GET | Alle Konnektoren (Admin: alle; User: eigene) |
| `/api/connectors/` | POST | Neuen Konnektor anlegen (Admin) |
| `/api/connectors/{id}` | PATCH | Konnektor bearbeiten |
| `/api/connectors/{id}` | DELETE | Konnektor löschen (Admin) |
| `/api/connectors/{id}/test` | POST | Verbindung testen |
| `/api/connectors/my` | GET | Persönliche Konnektoren |
| `/api/connectors/my/{type}` | POST | Persönlichen Konnektor anlegen/aktualisieren |
| `/api/connectors/my/{type}` | DELETE | Persönlichen Konnektor löschen |
| `/api/connectors/my/{type}/test` | POST | Persönlichen Konnektor testen |
| `/api/connectors/my/{type}/device-code/start` | POST | Microsoft Device Code Flow starten |
| `/api/connectors/my/{type}/device-code/poll` | POST | Device Code Flow prüfen/abschließen |

### Workflow / Work Sessions

| Pfad | Methode | Beschreibung |
|------|---------|-------------|
| `/api/workflow/` | GET | Alle Work Sessions des Users |
| `/api/workflow/` | POST | Neue Work Session anlegen |
| `/api/workflow/{id}` | GET | Work Session mit allen Notizen |
| `/api/workflow/{id}` | PATCH | Work Session bearbeiten |
| `/api/workflow/{id}/notes` | POST | Notiz hinzufügen |
| `/api/workflow/{id}/generate-comment` | POST | KI-Jira-Kommentar generieren |
| `/api/workflow/{id}/generate-resolution` | POST | Abschlussdokumentation generieren |
| `/api/workflow/{id}/auto-categorize` | POST | Kategorisierung per KI |
| `/api/workflow/{id}/suggest-solution` | POST | RAG + Web-Lösungssuche |
| `/api/workflow/{id}/5why` | POST | 5-Why Root Cause Analyse |
| `/api/workflow/analyze-mail` | POST | O365 E-Mail analysieren |

### Weitere Endpunkte

| Pfad | Methode | Beschreibung |
|------|---------|-------------|
| `/api/alerts/` | GET | Aggregierte Alerts aus PostgreSQL |
| `/api/kanban/` | GET, POST, PATCH, DELETE | Kanban-Cards |
| `/api/kanban/import-jira` | POST | Jira-Tickets ins Board importieren |
| `/api/jira-view/my-tickets` | GET | Jira-Tickets nach JQL-Filtern |
| `/api/settings/` | GET | Globale Einstellungen (Admin) |
| `/api/settings/` | PATCH | Globale Einstellungen bearbeiten (Admin) |
| `/api/settings/test/llm` | POST | LLM-Verbindung testen |
| `/api/users/` | GET, POST | Benutzer verwalten (Admin) |
| `/api/users/{id}` | PATCH, DELETE | Benutzer bearbeiten/löschen (Admin) |
| `/api/audit/` | GET | Audit-Log (Admin) |
| `/api/network/events` | GET | Netzwerk-Switch-Events |
| `/api/ws` | WebSocket | Real-Time Push (Alerts, KI-Ergebnisse) |
| `/api/help/` | GET | Kontextbezogene Hilfe-Texte |

---

## Datenbankmigrationen

| Revision | Beschreibung |
|----------|-------------|
| `0001` | Initial Schema: `users`, `connector_configs`, `alerts`, `kanban_cards`, `ai_analyses`, `audit_logs`, `global_settings` |
| `0002` | `network_switch_events` + `global_settings` Tabelle |
| `0003` | `workflow_sessions` + `workflow_notes` (ITIL Work Sessions) |
| `0004` | `refresh_tokens` + `audit_log` Tabelle |
| `0005` | `user_preferences`: CheckMK-Filter (`checkmk_locations`, `checkmk_ve`, `checkmk_criticality`) |
| `0006` | Persönliche Konnektoren: `owner_user_id` FK in `connector_configs` |
| `0007` | Setup-Wizard-Status: `setup_completed` in `user_preferences` |
| `0008` | `user_preferences`: `checkmk_os` + `checkmk_hostgroups` + `jira_project` |
| `0009` | `feed_searches` Tabelle + `feed_disabled_search_ids` in `user_preferences`; 4 System-Suchen als Seeds |
| `0010` | `dashboard_widgets` Tabelle |
| `0011` | `dashboards` Tabelle + `dashboard_id` FK in `dashboard_widgets` |
| `0012` | `user_preferences.checkmk_hostgroups` (falls fehlend) |
| `0013` | `feed_searches.is_exclusion` Boolean-Feld |

---

## Deployment

### Minimale ENV-Variablen

```env
# Pflichtfelder
ENCRYPTION_KEY=<Fernet-Key, 32 Byte Base64>
DATABASE_URL=postgresql+asyncpg://user:pass@db/centralstation
REDIS_URL=redis://redis:6379/0
SECRET_KEY=<JWT-Signing-Key, min. 32 Zeichen>

# OpenSearch
OPENSEARCH_URL=http://opensearch:9200
OPENSEARCH_USER=admin
OPENSEARCH_PASSWORD=<password>
```

Alle anderen Konfigurationen (LLM-URL, Connector-Zugangsdaten, SearXNG, RAG) werden Fernet-verschlüsselt in der Datenbank gespeichert und über das Frontend verwaltet.

### Docker Compose

```bash
# Stack starten
docker compose up -d

# Logs beobachten
docker compose logs -f backend

# Migrationen manuell anwenden
docker compose exec backend alembic upgrade head

# Backup der Datenbank
docker compose exec db pg_dump -U postgres centralstation > backup_$(date +%Y%m%d).sql

# OpenSearch-Status prüfen
curl http://localhost:9200/_cluster/health?pretty
```

### Produktions-Checkliste

- [ ] `ENCRYPTION_KEY` sicher generiert und in `.env` gesetzt (nie in Git committen)
- [ ] `SECRET_KEY` min. 64 Zeichen Entropie
- [ ] Nginx SSL-Zertifikat konfiguriert (`nginx/nginx.conf`)
- [ ] OpenSearch mit Auth und TLS
- [ ] Regelmäßiges PostgreSQL-Backup eingerichtet
- [ ] `docker compose up -d --no-build` für Produktion (pre-built Images verwenden)
- [ ] Admin-Benutzer nach erstem Login Passwort ändern
- [ ] Rate-Limiting auf `/api/auth/login` (10 Requests/Minute, bereits eingebaut)
