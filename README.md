# CentralStation

Zentrales IT-Operations-Dashboard für Linux-Systemadministratoren.  
Aggregiert Alerts aus Wazuh, Graylog und CheckMK, synchronisiert Jira-Tickets und
unterstützt mit KI bei der gesamten ITIL-konformen Arbeitsdokumentation.

---

## Features

| Bereich | Funktionen |
|---------|------------|
| **Operations Cockpit** | Konfigurierbares Widget-Dashboard (GridStack), Stat-Karten, Listen, Donut-Charts, Zeitreihen (Prometheus), KI-Lagebericht, Top-Hosts |
| **Alert-Aggregation** | CheckMK, Graylog, Wazuh — zentrale Timeline, Acknowledge, Severity-Filter, OS/Standort/VE/Hostgruppen-Filter |
| **News Feed** | Unified OpenSearch Feed, gespeicherte Suchen (Lucene), Last-Seen-Divider, KI-Anreicherung (ai_insight pro Alert) |
| **Kanban-Board** | Drag-Drop, bidirektionaler Jira-/ServiceDesk-Sync, automatische Jira-Importe, AI-erstellte Cards |
| **Meine Tickets** | Per-User Jira-Sicht, JQL-Filter-Verwaltung, KI-JQL-Generator (Freitext → JQL), Live-Ergebnisse |
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

## Architektur

```
Browser (Angular 20 LTS)
  └── REST + WebSocket (JWT Bearer)
        └── FastAPI Backend (Python 3.12)
              ├── PostgreSQL 16 (SQLAlchemy async + Alembic)
              ├── Redis 7 (WebSocket Pub/Sub, Sessions)
              ├── OpenSearch (cs-feed-* Indices für alle Alert-Quellen)
              ├── LangGraph (SysAdmin + Network AI Agents)
              └── Externe Systeme (CheckMK, Graylog, Wazuh, Jira, O365, Prometheus, ...)
```

---

## Operations Cockpit (Dashboard)

Das Dashboard besteht aus frei konfigurierbaren GridStack-Widgets.

### Widget-Typen

| Typ | Beschreibung | Datenquelle |
|-----|--------------|-------------|
| `stat` | Einzelne Zahl (Alert-Count) | OpenSearch count |
| `list` | Alert-Liste mit Severity-Dot | OpenSearch query |
| `donut` | Severity-Verteilung als Donut-Chart | OpenSearch aggregation |
| `top_hosts` | Hosts mit den meisten Alerts | OpenSearch aggregation |
| `ai_summary` | Letzter KI-Lagebericht (Findings + Empfehlungen) | PostgreSQL (ai_analyses) |
| `timeseries` | Zeitreihen-Liniendiagramm | Prometheus (PromQL) |
| `grafana_panel` | Eingebettetes Grafana-Panel als iFrame | Grafana URL |

### Widget-Konfiguration

- **Dashboard anpassen** → Drag/Resize aktivieren, **Widget hinzufügen** öffnet den Konfigurations-Dialog
- **Suche wählen**: Jedes stat/list/donut-Widget kann an eine gespeicherte OpenSearch-Suche gebunden werden
- **Prometheus-Widget**: PromQL direkt eingeben oder den **→ PromQL Konverter** nutzen (s.u.)
- **Layout speichern**: Beim Verlassen des Konfigurations-Modus wird die Position aller Widgets gespeichert
- **Defaults**: Setzt das Dashboard auf das Standard-Layout zurück (4 Stat-Karten + KI-Lagebericht + Liste + Top-Hosts + Donut)

---

## OpenSearch-Suchen (FeedSearches)

### Wo werden Suchen definiert?

**Einstellungen → Feed → System-Suchen** (Admin-Bereich)

Dort können Administratoren system-weite Suchen anlegen, bearbeiten und per Vorschau testen.  
Jeder Benutzer kann im **News Feed** eigene persönliche Suchen anlegen.

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

### OpenSearch-Index-Schema

| Index | Quelle | Wichtige Felder |
|-------|--------|-----------------|
| `cs-feed-checkmk` | CheckMK REST API | `severity`, `title`, `metadata.host`, `metadata.location`, `metadata.os` |
| `cs-feed-graylog` | Graylog REST API | `severity`, `title`, `body`, `metadata.source_host`, `metadata.http_response_code`, `metadata.hyde_relevant` |
| `cs-feed-wazuh` | Wazuh Indexer API | `severity`, `title`, `metadata.rule_level`, `metadata.agent`, `metadata.agent.name` |
| `cs-feed-o365` | Microsoft Graph | `severity`, `title`, `body`, `user_id` |
| `cs-feed-teams` | Microsoft Graph | `severity`, `title`, `body`, `user_id` |

### Query-Syntax (Lucene)

```
# Graylog: alle Container-Fehler von docker086
metadata.container_name:docker086* AND metadata.http_response_code:>=400

# Wazuh: Security-Events Level 10+ für bestimmten Host
metadata.rule_level:>=10 AND metadata.agent.name:docker086

# Alle: kritische Alerts der letzten 24h
severity:critical AND NOT status:resolved

# Graylog: Hyde-relevante Meldungen ohne NSA/NSS/NSC
metadata.hyde_relevant:true AND NOT metadata.source_host:(nsa* OR nss* OR nsc*)
```

---

## Prometheus-Metriken & PromQL

### Lucene → PromQL Konverter

Im Dashboard **Widget hinzufügen → Zeitreihe** gibt es einen eingebauten Konverter:

- Feld **Beschreibung / Lucene-Suchterme** ausfüllen
- Button **→ PromQL** klickt → KI (oder Regel-Fallback) generiert PromQL
- Das PromQL-Feld wird automatisch befüllt

**Beispiele:**

| Eingabe | Generierte PromQL |
|---------|-------------------|
| `CPU-Auslastung docker086` | `100 - (avg(rate(node_cpu_seconds_total{instance="docker086:9100",mode="idle"}[5m])) * 100)` |
| `host:docker086 AND metric:memory` | `100 * (1 - node_memory_MemAvailable_bytes{instance="docker086:9100"} / node_memory_MemTotal_bytes{instance="docker086:9100"})` |
| `Netzwerk-Traffic srv023` | `rate(node_network_receive_bytes_total{instance="srv023:9100"}[5m])` |
| `disk` | `100 * (1 - node_filesystem_free_bytes / node_filesystem_size_bytes)` |

API-Endpunkt: `POST /api/ai/promql-assistant` mit `{"message": "..."}` → `{"promql": "...", "explanation": "..."}`

---

## CheckMK-Daten in Prometheus

CheckMK kann Metriken nativ nach Prometheus exportieren. So richtest du den Export ein:

### Option A: CheckMK Built-in Prometheus-Export (empfohlen)

CheckMK 2.x hat einen eingebauten Prometheus-Exporter über die Livestatus/REST-API.

**Schritt 1 – Prometheus-Exporter in CheckMK aktivieren**

```bash
# In CheckMK RAW Edition: mkp-Plugin installieren
# In CheckMK Enterprise/CEE: Built-in Exporter nutzen

# CheckMK REST API – Metrik-Endpunkt
GET https://monitoring.ippen.media/cmk/api/1.0/domain-types/metric/collections/all
```

**Schritt 2 – Prometheus `prometheus.yml` erweitern**

```yaml
scrape_configs:
  # Bestehende node_exporter Targets
  - job_name: 'node'
    static_configs:
      - targets:
          - 'docker001.ippen.media:9100'
          - 'docker086.ippen.media:9100'
          # ... alle Hosts mit node_exporter

  # CheckMK als Prometheus-Quelle
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
    tls_config:
      insecure_skip_verify: true
```

### Option B: node_exporter auf allen Hosts (für Host-Metriken)

```bash
# node_exporter auf jedem Linux-Host deployen
docker run -d \
  --net="host" \
  --pid="host" \
  -v "/:/host:ro,rslave" \
  quay.io/prometheus/node-exporter:latest \
  --path.rootfs=/host \
  --web.listen-address=":9100"

# Oder via Ansible (empfohlen):
ansible all -m apt -a "name=prometheus-node-exporter state=present" -b
ansible all -m service -a "name=prometheus-node-exporter enabled=yes state=started" -b
```

### Option C: CheckMK-Agenten als Prometheus-Target via `cmk_agent_exporter`

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'checkmk_agent'
    static_configs:
      - targets: ['monitoring.ippen.media:8080']
    metrics_path: '/metrics'
    # Liefert cmk_service_state, cmk_host_state, etc.
```

**Verfügbare CheckMK-Metriken in Prometheus:**
```promql
# Host-Status (0=UP, 1=DOWN, 2=UNREACHABLE)
cmk_host_state{hostname="docker086"}

# Service-Status (0=OK, 1=WARN, 2=CRIT, 3=UNKNOWN)
cmk_service_state{hostname="docker086", service="CPU load"}

# Alle kritischen Services
cmk_service_state == 2
```

### Schritt 3 – Prometheus-Connector in CentralStation konfigurieren

1. **Einstellungen → Connectors → Prometheus** → Neuen Connector anlegen
2. **Base URL**: `http://<prometheus-host>:9090`
3. Optional: Basic Auth oder Bearer Token
4. **Verbindung testen**

Danach können Zeitreihen-Widgets mit PromQL-Queries erstellt werden.

---

## KI-Templates (workflow_ai.py)

Alle KI-Funktionen nutzen OpenAI-kompatible Endpunkte (konfigurierbar über Frontend → Einstellungen → KI).

---

## Persönliche Konnektoren

Neben globalen Admin-Konnektoren unterstützt CentralStation benutzerspezifische Konnektoren für:
`checkmk`, `graylog`, `wazuh`, `jira`, `jira_sd`, `o365`, `teams`

Diese werden im Setup-Wizard unter **Meine Konnektoren** gepflegt und gelten nur für das jeweilige Benutzerkonto.

| Template / Funktion | Endpunkt | Beschreibung |
|---------------------|----------|--------------|
| `generate_comment` | `POST /api/workflow/{id}/generate-comment` | Jira-Kommentar (Fortschritt / Pending / Eskalation / Übergabe) |
| `generate_resolution` | `POST /api/workflow/{id}/generate-resolution` | Lösungsdokumentation mit Root Cause und Maßnahmen |
| `auto_categorize` | `POST /api/workflow/{id}/auto-categorize` | Kategorie, Unterkategorie, Impact, Urgency aus Titelbeschreibung |
| `suggest_solution` | `POST /api/workflow/{id}/suggest-solution` | Lösungsschritte + RAG (it-aikb) + SearXNG-Websuche |
| `analyze_mail` | `POST /api/workflow/analyze-mail` | O365 E-Mail → strukturiertes JSON (Zusammenfassung, Dringlichkeit, Ticket-Key) |
| `run_5why_analysis` | `POST /api/workflow/{id}/5why` | 5-Why Root Cause Analysis (ITIL Problem Management) |
| `generate_jql` | `POST /api/preferences/jira-queries/generate` | KI JQL-Generator: Freitext → JQL |
| `search_assistant` | `POST /api/ai/search-assistant` | Freitext → OpenSearch Lucene Query |
| `promql_assistant` | `POST /api/ai/promql-assistant` | Lucene/Freitext → PromQL |

### KI JQL-Generator

```
Eingabe:  "meine offenen Bugs mit hoher Priorität aus dieser Woche"
Ausgabe:  { "jql": "assignee = currentUser() AND issuetype = Bug AND priority in (Highest, High) AND created >= -7d AND status != Done ORDER BY priority ASC", "name": "Meine Bugs (hoch, diese Woche)" }
```

---

## Kanban und Jira

- Bidirektionaler Sync mit Jira und Jira ServiceDesk
- Statuswechsel per Drag-and-Drop lösen Jira-Transitions aus
- Lokale Karten können als Jira-Ticket erstellt werden

**Status-Mapping:**

| CentralStation | Jira |
|----------------|------|
| `backlog` | Backlog, Open, Selected for Development |
| `todo` | To Do, Open, Ready |
| `in_progress` | In Progress, Doing, Implementing, In Bearbeitung |
| `review` | Review, In Review, Testing, QA |
| `done` | Done, Resolved, Closed, Erledigt |

---

## ITIL Prioritätsmatrix

| Impact ↓ / Urgency → | Hoch | Mittel | Niedrig |
|----------------------|------|--------|---------|
| **Hoch** | P1 (Response 15min) | P2 (60min) | P3 (4h) |
| **Mittel** | P2 (60min) | P3 (4h) | P4 (24h) |
| **Niedrig** | P3 (4h) | P4 (24h) | P4 (24h) |

SLA-Fristen werden automatisch beim Erstellen einer Work Session berechnet.

---

## Deployment

```bash
# Erste Inbetriebnahme
cp .env.example .env          # ENCRYPTION_KEY, DATABASE_URL, REDIS_URL, SECRET_KEY setzen
docker compose up -d

# Datenbank-Migrationen (beim ersten Start automatisch, oder manuell)
docker compose exec backend alembic upgrade head
```

### Minimale ENV-Variablen

```env
ENCRYPTION_KEY=<Fernet-Key, 32 Byte Base64>
DATABASE_URL=postgresql+asyncpg://user:pass@db/centralstation
REDIS_URL=redis://redis:6379/0
SECRET_KEY=<JWT-Signing-Key>
```

Alle anderen Konfigurationen (LLM-URL, Connector-Zugangsdaten, SearXNG, RAG) werden verschlüsselt in der Datenbank gespeichert und über das Frontend verwaltet.

---

## Datenbankmigrationen

| Revision | Beschreibung |
|----------|--------------|
| 0001 | Initial Schema (users, connectors, alerts, kanban, ai_analyses) |
| 0002 | Network events + global settings |
| 0003 | Workflow / Work Sessions (ITIL) |
| 0004 | Refresh tokens + audit log |
| 0005 | User preferences (CheckMK filter) |
| 0006 | Personal connectors |
| 0007 | Setup wizard state |
| 0008 | User preferences: os-Filter + hostgroups |
| 0009 | feed_searches + feed_disabled_search_ids in user_preferences |
| 0010 | dashboard_widgets |
| 0011 | dashboards table + dashboard_id FK in dashboard_widgets |
| 0012 | user_preferences: checkmk_hostgroups |

---

## API-Übersicht

| Pfad | Methode | Beschreibung |
|------|---------|--------------|
| `/api/auth/login` | POST | Login (rate-limited: 10/min) |
| `/api/auth/refresh` | POST | Token-Refresh (HttpOnly Cookie) |
| `/api/preferences` | GET, PATCH | Benutzer-Präferenzen + CheckMK-Filter |
| `/api/preferences/jira-queries/generate` | POST | KI JQL-Generator |
| `/api/jira-view/my-tickets` | GET | Jira-Tickets nach aktiven JQL-Filtern |
| `/api/feed` | GET | Unified Alert Feed (OpenSearch) |
| `/api/feed/unread-count` | GET | Ungelesene Alerts seit `since` |
| `/api/feed-searches` | GET, POST | Gespeicherte OpenSearch-Suchen |
| `/api/feed-searches/system` | POST | System-Suche anlegen (Admin) |
| `/api/feed-searches/{id}/preview` | GET | Vorschau (5 Treffer) |
| `/api/dashboard-widgets/dashboards` | GET, POST | Dashboard-Verwaltung |
| `/api/dashboard-widgets` | GET, POST | Widget-CRUD |
| `/api/dashboard-widgets/{id}/data` | GET | Widget-Daten (OpenSearch / Prometheus) |
| `/api/dashboard-widgets/dashboards/{id}/reset-defaults` | POST | Standard-Layout wiederherstellen |
| `/api/ai/search-assistant` | POST | Freitext → OpenSearch Query (+ create search/widget) |
| `/api/ai/promql-assistant` | POST | Lucene/Freitext → PromQL |
| `/api/ai/trigger/{sysadmin\|network}` | POST | KI-Agent manuell auslösen (SysAdmin) |
| `/api/alerts` | GET | Aggregierte Alerts |
| `/api/connectors` | GET, POST, PATCH, DELETE | Globale Connector-Verwaltung (Admin) |
| `/api/connectors/my` | GET | Persönliche Konnektoren |
| `/api/workflow` | GET, POST | Work Sessions (ITIL) |
| `/api/workflow/{id}/generate-comment` | POST | KI-Ticket-Kommentar |
| `/api/workflow/{id}/generate-resolution` | POST | KI-Lösungsdokumentation |
| `/api/workflow/{id}/5why` | POST | 5-Why Root Cause Analyse |
| `/api/workflow/{id}/suggest-solution` | POST | RAG + Web Lösungssuche |
| `/api/settings` | GET, PATCH | Globale Einstellungen (Admin) |
