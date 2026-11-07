# CentralStation

Central IT operations dashboard for Linux system administrators.  
Aggregates alerts from Wazuh, Graylog and CheckMK, synchronises Jira tickets and
assists with the entire ITIL-compliant work documentation using AI.

> **Language:** the UI defaults to **English** and can be switched to German at runtime.
> The AI answers in the operator's selected language (user preference `ui_language`).

**[CHANGELOG.md](CHANGELOG.md)** — what changed in the product, with the evidence for each entry.

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [Architecture](#architecture)
3. [CheckMK as the Single Source of Truth](#checkmk-as-the-single-source-of-truth)
4. [Feature Overview](#feature-overview)
5. [Operations Cockpit (Dashboard)](#operations-cockpit-dashboard)
6. [Host-Cockpit (Host Detail Panel)](#host-cockpit-host-detail-panel)
7. [News Feed](#news-feed)
8. [OpenSearch Searches (FeedSearches)](#opensearch-searches-feedsearches)
9. [Alert Aggregation and Enrichment](#alert-aggregation-and-enrichment)
10. [Error Clusters & Incidents](#error-clusters--incidents)
11. [Kanban and Jira](#kanban-and-jira)
12. [AI Features](#ai-features)
13. [Computer Console (Hermes AI Panel)](#computer-console-hermes-ai-panel)
14. [Werkbank (Web-IDE)](#werkbank-web-ide)
15. [Maschinenraum (Ansible Remediation)](#maschinenraum-ansible-remediation)
16. [Prometheus Metrics & PromQL](#prometheus-metrics--promql)
17. [Connectors](#connectors)
18. [User Management and RBAC](#user-management-and-rbac)
19. [Settings and Preferences](#settings-and-preferences)
20. [API Reference](#api-reference)
21. [Database Migrations](#database-migrations)
22. [Deployment](#deployment)
23. [Upgrading](#upgrading)

---

## Getting Started

### Requirements

- Docker + Docker Compose (V2)
- OpenSearch 2.x (or an OpenSearch-compatible cluster)
- Optional dependencies: LLM endpoint (OpenAI-compatible), Jira, CheckMK, Graylog, Wazuh

### Quick start

```bash
# 1. Clone the repository
git clone <repo-url> centralstation
cd centralstation

# 2. Create the configuration file
cp .env.example .env

# 3. Set the required fields in .env:
#    ENCRYPTION_KEY  – Fernet key (32-byte base64): python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#    DATABASE_URL    – postgresql+asyncpg://user:pass@db/centralstation
#    REDIS_URL       – redis://redis:6379/0
#    SECRET_KEY      – random JWT signing key: openssl rand -hex 32

# 4. Start the stack
docker compose up -d

# 5. Wait until all containers are healthy
docker compose ps

# 6. Database migrations (applied automatically on first start)
docker compose exec backend alembic upgrade head

# 7. Create the first admin user
docker compose exec backend python -c "
from app.core.database import sync_engine
from app.core.security import hash_password
from app.models.user import User, Base
import sqlalchemy as sa
Base.metadata.create_all(sync_engine)
with sync_engine.begin() as conn:
    conn.execute(sa.insert(User).values(email='admin@example.com', hashed_password='$(python3 -c "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash('changeme'))")', role='admin', is_active=True))
"
# Alternatively: use the API endpoint /api/auth/register (if enabled)
```

### First login and setup wizard

1. Open the browser: `http://localhost` (or the configured host)
2. Sign in with the admin credentials
3. The **setup wizard** starts automatically (once per user):
   - **Step 1 – LLM connection**: OpenAI-compatible endpoint, model ID, API key (optional)
   - **Step 2 – Configure Jira**: Jira URL, personal token, default project
   - **Step 3 – Personal filters**: CheckMK locations, VE, criticality (for feed filtering)
   - **Step 4 – JQL templates**: default JQL queries for the Jira view
4. After completing the wizard → main dashboard

### Setting up admin connectors

After the first login, create the global system connectors under **Settings → Connectors**:

| Connector | Type | Required for |
|-----------|------|-------------|
| CheckMK | `checkmk` | alert aggregation, host metadata, filter values |
| Graylog | `graylog` | log aggregation |
| Wazuh | `wazuh` | security alerts |
| Prometheus | `prometheus` | time-series widgets |

### Recommended: vibeMK MCP server for the Computer Console

The **Computer Console** (the `centralcore` Hermes agent, see [Computer Console](#computer-console-hermes-ai-panel)) talks to MCP servers for live data. In addition to the built-in CentralStation MCP server we recommend adding **[vibeMK](https://github.com/imoes/vibeMK)** — an MCP server that manages CheckMK monitoring via natural language (host/service status, performance metrics, downtimes, acknowledgements, configuration). With it, operators can query *and act on* CheckMK directly from the Computer panel.

```bash
# Clone alongside CentralStation (Python 3.8+, standard library only)
git clone https://github.com/imoes/vibeMK.git

# vibeMK needs a CheckMK automation user + API key:
#   CHECKMK_SERVER_URL   – e.g. https://checkmk.example.com
#   CHECKMK_SITE         – the CheckMK site name
#   CHECKMK_USERNAME     – automation user (e.g. vibemk)
#   CHECKMK_PASSWORD     – the API key generated in CheckMK
```

Register it under `mcp_servers:` in `centralcore/hermes_config.yaml` (this fills the optional `checkmk` slot the Hermes agent looks for):

```yaml
mcp_servers:
  centralstation:
    transport: sse
    url: http://backend:8000/api/mcp/sse
  checkmk:                       # vibeMK
    transport: stdio
    command: python3
    args: ["/absolute/path/to/vibeMK/main.py"]
    env:
      CHECKMK_SERVER_URL: "https://checkmk.example.com"
      CHECKMK_SITE: "<site>"
      CHECKMK_USERNAME: "vibemk"
      CHECKMK_PASSWORD: "<api-key>"
```

See vibeMK's [INSTALL.md](https://github.com/imoes/vibeMK/blob/main/INSTALL.md) for the authoritative setup and transport options.

---

## Architecture

```
Browser (Angular 20 LTS)
  └── REST + WebSocket (JWT Bearer)
        └── FastAPI Backend (Python 3.12)
              ├── PostgreSQL 16        – users, connectors, kanban, workflows, AI analyses
              ├── Redis 7              – WebSocket pub/sub, sessions
              ├── OpenSearch           – cs-feed-* indices (all alert sources)
              ├── LangGraph            – SysAdmin + Network AI agents
              └── External systems
                    ├── CheckMK REST API
                    ├── Graylog REST API
                    ├── Wazuh Indexer API
                    ├── Jira / Jira ServiceDesk
                    ├── Microsoft O365 / Teams (Graph API)
                    ├── Prometheus HTTP API
                    ├── SearXNG (web search)
                    └── ID-Generator (sites, switches)
```

### OpenSearch indices

| Index | Source | Key fields |
|-------|--------|-----------------|
| `cs-feed-checkmk` | CheckMK REST API | `severity`, `title`, `metadata.host`, `metadata.location`, `metadata.os`, `metadata.ve`, `metadata.criticality`, `metadata.hostgroups` |
| `cs-feed-graylog` | Graylog REST API | `severity`, `title`, `body`, `metadata.source_host`, `metadata.http_response_code`, `metadata.hyde_relevant`, `metadata.container_name` |
| `cs-feed-wazuh` | Wazuh Indexer | `severity`, `title`, `metadata.rule_level`, `metadata.agent`, `metadata.agent.name`, `metadata.location` |
| `cs-feed-o365` | Microsoft Graph | `severity`, `title`, `body`, `user_id`, `metadata.from`, `metadata.received_at` |
| `cs-feed-teams` | Microsoft Graph | `severity`, `title`, `body`, `user_id`, `metadata.from`, `metadata.channel_id` |

Every document also contains: `id`, `type`, `source`, `status`, `created_at`, `location_name`, `location_city`, `external_url`, `external_id`, `ai_insight`.

---

## CheckMK as the Single Source of Truth

### Concept

CheckMK is the primary inventory and metadata provider for all hosts in the organisation. Every monitored host carries metadata tags in CheckMK:

| CheckMK tag | Meaning | CentralStation field |
|-------------|---------|----------------------|
| `tg-os` | operating system (`os-linux`, `os-windows`, …) | `metadata.os` |
| `tg-location` / `host_filename` | site (WATO folder, e.g. `Munich`) | `metadata.location` |
| `tg-ve` / `tg-virt_env` | virtualization environment | `metadata.ve` |
| `tg-criticality` | host criticality | `metadata.criticality` |
| Host groups | the host's CheckMK host groups | `metadata.hostgroups` |

This metadata is read from CheckMK during alert aggregation and written into the OpenSearch index (`cs-feed-checkmk`).

### Filter mechanism (Single Source of Truth)

When a user sets filters under **My Settings** (e.g. `Location = Munich`, `OS = Linux`), those filters are **applied to all sources**:

1. **CheckMK alerts**: filtered directly via `metadata.os`, `metadata.location`, `metadata.ve`, `metadata.criticality`, `metadata.hostgroups`
2. **Graylog/Wazuh alerts**: CentralStation derives from the CheckMK index all hosts matching the filter criteria (→ `host_scope`). Only Graylog/Wazuh items whose hostnames fall within that scope are then shown.
3. **Items without host metadata**: always shown (never hidden by missing fields)

```
Example:
  User filter: Location = "Munich"
  
  1. CentralStation asks cs-feed-checkmk: "Which hosts have location=Munich?"
     → [docker001, docker086, srv023, ...]
  
  2. The Graylog search is restricted to metadata.source_host IN [docker001, docker086, ...]
  
  3. The Wazuh search is restricted to metadata.agent.name IN [docker001, docker086, ...]
  
  Effect: the feed only shows events from hosts at the Munich site —
          regardless of source (CheckMK, Graylog or Wazuh).
```

### `get_user_checkmk_host_scope(db, user_id)`

Core function in `backend/app/services/feed_index.py`:

1. Reads the user's saved filter preferences (`checkmk_os`, `checkmk_locations`, `checkmk_ve`, `checkmk_criticality`)
2. If no filter is set → returns an empty list (no scope restriction)
3. If a filter is active → queries OpenSearch `cs-feed-checkmk` and applies `_apply_metadata_filters()`
4. Returns the list of `metadata.host` values from the filtered CheckMK items

### Post-processing filter (`_apply_metadata_filters`)

Since OS/location/VE/criticality are CheckMK-native concepts, the filter accesses metadata fields directly **only for CheckMK items**. For all other sources (Graylog, Wazuh), the `host_scope` is used as an indirect filter.

**Logic:** `items with the metadata value + value doesn't match → hidden. Items without the metadata value → always shown.`

---

## Feature Overview

| Bereich | Funktionen |
|---------|------------|
| **Operations Cockpit** | Dual-Mode Dashboard (Klassisch/Generativ), Widget-Typen: Stat, Liste, Donut, Balken, Zeitreihe, Forecast, KI-Lagebericht, Top-Hosts, War Room; klickbare Charts; Pin/Reset im generativen Modus; Hostname in Top-Hosts klickbar |
| **Generatives Dashboard** | KI komponiert situativ ein maßgeschneidertes Dashboard — analysiert Findings, Worklist, Vitals + Forecast-Kandidaten; Rationale als Lage-Briefing; Neu-Generieren-Button + WS-Eskalation-Trigger; CUE-Produktionshosts priorisiert |
| **Host-Cockpit** (bis 06.11.2026 „Server Cockpit“) | Klick auf Hostnamen im News Feed öffnet `/cockpit/:hostname` in neuem Fenster; LCARS-Design; Hero-Gauges (CPU/RAM/Disk) + Sparklines; **Voll-Service-Liste** aller CheckMK-Checks mit Status + Summary; hierarchische Filter-Chips (FEHLER/WARN/CRIT/UNKNOWN); Klick auf Service → on-demand 24h-Graph; Font Roboto; Navbar wird ausgeblendet |
| **Brücke (Bridge)** | Star-Trek-LCARS-Cockpit unter `/bridge`; drei Themes (Classic/Holo/LCARS); Prioritäten-Worklist, Fleet-Vitals, Forecasts, Sektoren, Live-Logs; Primärer-Incident-Panel; Font Roboto |
| **Adaptives Alert-Scoring** | Deterministisches Basis-Scoring (Severity/Novelty/Alter/Flapping/Cross-Source); adaptiver Lern-Feedback-Loop (Jira-Tickets, Acks, Ignorieren → `alert_score_adjustments`); Score-Delta-Verfall |
| **Fehler-Cluster & Incidents** | Erkennt Ausbrüche selbständig: jede Graylog-Fehlerzeile zählt, Drain3-Muster mit eigener Grundlast, Regeln für neu/Spitze/Ausbreitung, CheckMK-Flotten-Ausfälle; ein Vorfall = ein Incident = eine Mail mit KI-Einschätzung; Widget im KI-Lagebild |
| **Alert-Aggregation** | CheckMK, Graylog, Wazuh — zentrale Timeline, Acknowledge, Severity-Filter; Graylog: Python-Loglevel-Erkennung (INFO→low, ERROR→high, verhindert Docker-GELF-Fehleinstufung) |
| **News Feed** | Unified OpenSearch Feed, gespeicherte Suchen (Lucene), Last-Seen-Divider, KI-Anreicherung, KI-Ignorieren; Hostname anklickbar → Feed-Filter; Severity-Filter ignoriert aktive Saved-Searches korrekt |
| **KI-Insights** | Befunde + zugehörige Empfehlungen direkt zusammen (kein getrenntes Panel); Datenquelle-Badge je Befund; Hostname/Feed-Links; Empfehlungen fließen in generatives Dashboard ein |
| **Fehler-Cluster (Root-Cause)** | Die KI fasst im selben Analyse-Lauf mehrere Befunde mit gemeinsamer Ursache zu einer Diagnose zusammen (z.B. „Core-Switch in MUE-0 ausgefallen" erklärt 10 nicht erreichbare Hosts); nutzt Blast-Radius-Topologie; sichtbar in KI-Insights, Hermes-Konsole und Brücke |
| **Werkbank (Web-IDE)** | Pro-User code-server (VS Code im Browser) unter `/workbench`; integriertes Terminal, Git/GitLab, SSH zu `*.example.com`; **vorinstallierte KI-Agenten-Extensions** (Claude Code + OpenAI Codex); Ansible editierbar (geteiltes `ansible/`-Verzeichnis im Standard-Layout: playbooks/roles/host_vars/group_vars/inventory, sofort in AWX sichtbar); Hermes-Analyse als Markdown übergeben |
| **Maschinenraum (Remediation)** | Engineering-Cockpit unter `/engineering`; KI-gestützte Ansible-Remediation mit Human-in-the-Loop: `playbook_author` → AWX Job Template → `remediation_matcher` → Lern-Loop; cs-meta-Konvention im Playbook-Kopf (`matches`/`params`); Pending/Active/History/Catalog |
| **AI War Room** | Blast-Radius-Analyse bei Critical/High; Ko-VMs, Ko-lokalisierte Hosts; Empfehlungen mit Ein-Klick-Jira |
| **CheckMK Metriken** | Collector schreibt CPU/RAM/Disk/Agent-Zeit in `cs-metrics-checkmk`; Bridge zeigt Fleet-Vitals + Forecasts (lineare Regression); stabile Metriken (< 90 % ohne Trend) werden aus generativem Kontext gefiltert |
| **Computer Console Agent-Auswahl** | Drei Agenten pro User wählbar: **Hermes** (Standard, eigener LLM-Endpunkt), **Claude CLI** (eigener Claude-Account via OAuth, persistente Sessions auf Volume), **Codex CLI** (ChatGPT-Account via Device-Code-Flow, GPT-5.x); OAuth-Flow im Browser, kein CLI nötig; Token verschlüsselt in DB; konfiguriert auch die VS-Code-Extensions in der Werkbank automatisch |
| **OpenAI Codex OAuth** | Browser-initiierter Device-Code-Flow (kein CLI nötig); Provider umschaltbar zwischen lokalem LLM und OpenAI Codex (GPT-5.x); Token verschlüsselt in DB, automatischer Refresh |
| **3 App-weite Themes** | **Classic** (hell, blauer Schleier), **Holo** (dunkelblau/cyan), **LCARS** (schwarz/orange — offizielles Neon Carrot + Golden Tanoi + Anakiwa + Lilac Palette); in Einstellungen wählbar |
| **Kanban-Board** | Drag-Drop, bidirektionaler Jira-/ServiceDesk-Sync, automatische Jira-Importe, AI-erstellte Cards |
| **Meine Tickets** | Per-User Jira-Sicht, JQL-Filter-Verwaltung, KI-JQL-Generator; Unread-Badge; roter Punkt bei Aktivität |
| **Arbeitsdokumentation** | ITIL Work Sessions: Impact/Urgency/Priorität P1–P4, SLA-Tracking, Arbeitsnotizen |
| **KI-Kommentare** | Fortschritt, Pending, Eskalation, Übergabe — per KI generiert, direkt in Jira kopierbar |
| **Abschlussdokumentation** | KI-generierte Lösungsdokumentation mit Root Cause, Maßnahmen, Closure Code |
| **5-Why-Analyse** | ITIL Problem Management — KI führt 5-Why-Analyse durch, schlägt Kernursache vor |
| **Lösungssuche** | SearXNG Web-Suche + KI-gestützte Lösungsvorschläge |
| **Netzwerk-Modul** | Switch-Alerts (NSA/NSS/NSC), Standort-Zuordnung (ID-Generator), Vendor-Erkennung |
| **RBAC** | Admin / SysAdmin / Network-Technician / Viewer — rollenbasierte UI und API |
| **Audit-Log** | Protokollierung aller schreibenden Operationen |

---

## Operations Cockpit (Dashboard)

The dashboard consists of freely configurable GridStack widgets. Each widget can be resized and moved independently.

### Widget types

| Type | Description | Data source | Required config |
|------|-------------|-------------|-----------------|
| `stat` | single number (alert count) | OpenSearch count query | `severity` or `search_id` |
| `list` | alert list with severity dot + host/container | OpenSearch query | `sources`, `limit` (default 10) |
| `donut` | severity distribution as a donut chart (ECharts) | OpenSearch aggregation | `sources` |
| `bar` | bar chart over an aggregation field (ECharts) | OpenSearch terms aggregation | `agg_field`, `limit` (default 10) |
| `top_hosts` | hosts with the most alerts | OpenSearch aggregation | `sources`, `limit` (default 5) |
| `ai_summary` | latest AI situation report (findings + recommendations) | PostgreSQL `ai_analyses` | *(none)* |
| `timeseries` | time-series line chart (ECharts) | Prometheus PromQL / CheckMK RRD | `promql`, `step`, `hours` |
| `forecast` | CheckMK RRD history + linear trend projection + ±1σ confidence band | CheckMK `get_forecast_data()` | `host`, `service`, `metric_id`, `history_hours` (default 72), `horizon_hours` (default 24) |
| `grafana_panel` | embedded Grafana panel as an iframe | Grafana embed URL | `panel_url` |

### Widget config schemas

```jsonc
// stat
{ "severity": "critical", "sources": ["checkmk", "wazuh"] }
{ "search_id": "uuid-of-a-saved-search" }

// list
{ "sources": ["checkmk", "graylog", "wazuh"], "severity": "high", "limit": 10 }
{ "search_id": "uuid", "limit": 15 }

// donut
{ "sources": ["checkmk", "graylog", "wazuh"] }

// bar — agg_field: severity | source | metadata.host.keyword | metadata.hostgroups.keyword
{ "index_pattern": "cs-feed-*", "query_string": "", "agg_field": "severity", "limit": 10 }

// top_hosts
{ "sources": ["checkmk", "wazuh"], "limit": 5 }

// ai_summary
{}  // no configuration needed

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

### Dashboard management

- **Enable config mode**: gear icon → widget drag/resize is unlocked
- **Add widget**: button opens a multi-step dialog (type → title + sources → type-specific)
- **Save layout**: automatically on leaving config mode
- **Multiple dashboards**: tab bar on top; create a new dashboard via the `+` icon
- **Default layout**: each dashboard can be reset to the default widgets (`POST /api/dashboard-widgets/dashboards/{id}/reset-defaults`)

### Dual mode: Classic ↔ Generative

A toggle button in the dashboard header switches between two modes:

**Classic** (default): manual drag-and-drop layout, `saveLayout` on leaving config mode — as before.

**Generative**: the AI layout engine adapts the dashboard situationally:
- On activation + on every `ai_insight` WebSocket event, `POST /dashboard-widgets/dashboards/{id}/suggest-layout` is called
- The engine scores widgets by AI analysis (severity_summary, finding sources) + live OpenSearch counts
- Relevant widgets move up / grow; quiet widgets shrink / are hidden
- **Pin button** per widget (NASA override rule): pinned widgets are never moved by the AI
- **Reset button** restores the default layout in one click
- The mode is stored in `Dashboard.mode` and restored on the next visit
- **Click on widget (background)**: opens the News Feed with the widget's matching filters
- **Click on donut segment**: opens the feed filtered by the clicked severity
- **Click on bar (`bar`)**: opens the feed filtered by the bar's field value
- **Click on AI findings**: deep link from the `ai_summary` widget finding into **AI Insights** (`/ai-insights?analysis=<id>`) — the related analysis is highlighted and the findings panel expanded
- **Internal clicks vs. widget click**: dedicated clicks (donut/bar/finding/item) take precedence; the background click (`openWidget`) is suppressed via a flag so the filter is not overwritten

### Default widgets (created automatically on first login)

| Widget | Type | Position | Config |
|--------|------|----------|--------|
| Severity distribution | `donut` | 0,0 (5×5) | all sources |
| Critical | `stat` | 5,0 (2×2) | severity=critical |
| High | `stat` | 7,0 (2×2) | severity=high |
| Newest alerts | `list` | 5,2 (4×3) | all sources, limit=8 |
| AI situation report | `ai_summary` | 0,5 (5×4) | — |
| Top hosts | `top_hosts` | 5,5 (4×4) | all sources |

---

## Host-Cockpit (Host Detail Panel)

Ein Klick auf einen Servernamen im **News Feed** öffnet das Host-Cockpit in einem eigenständigen Browser-Fenster (`window.open`, Route `/cockpit/:hostname`). Das Fenster hat kein Navigationsmenü — es ist ein vollbildiges LCARS-Dashboard für genau einen Host.

### Layout

```
┌─────────────────────────────────────────────────────┐
│ ████ COCKPIT — hostname  ██████  [LIVE ●]  [✕]      │  ← orange Cap-Bar
├─────────────────────────────────────────────────────┤
│ ██ PERFORMANCE ██████████████████  [cached|live]    │
│   [CPU Gauge + Sparkline]  [RAM]  [Disk]            │
├─────────────────────────────────────────────────────┤
│ ██ SERVICES (N) ██  [ALLE] [FEHLER] [CRIT] [WARN]  │
│   ● CRIT  Filesystem /var         87% used (...)    │
│   ● WARN  CPU load                load 3.4 (...)    │
│   ● OK    Memory                  12.3% used        │
│   → Klick auf Zeile → 24h-Graph erscheint inline    │
├─────────────────────────────────────────────────────┤
│ ██ ALERTS ██  [Severity▼] [Source▼]                 │
│   sev●  title  host  source  time                   │
└─────────────────────────────────────────────────────┘
```

### Performance Block

- **Hero-Gauges**: CPU / RAM / Disk als ECharts-Gauge-Charts (140px), Farbe nach Level: crit `#ff4433` / high `#ffcc00` / ok `#66cc66`
- **Sparklines**: Kompakte 52px-Liniencharts direkt unterhalb der Gauges (24h-Historie)
- **Zweistufiges Laden**: Zuerst gecachte Werte aus `cs-metrics-checkmk` (sofort), dann Live-Refresh via CheckMK RRD (`?live=true`, ~1-2s)
- **LIVE-Badge**: wechselt von `cached` auf `LIVE ●` sobald die aktuellen Werte eintreffen

### Service List

Zeigt alle CheckMK-Services des Hosts mit aktuellem Status und `plugin_output` (menschenlesbare Zusammenfassung, z.B. „15.2% used (3.04 GB of 20.0 GB)").

**Service-Farben:**

| Status | Farbe |
|--------|-------|
| CRIT | `#ff4433` (rot) |
| WARN | `#ffcc00` (gelb) |
| UNKNOWN | `#99ccff` (hellblau) |
| OK | `#66cc66` (grün) |

**Hierarchische Filter-Chips:**

| Chip | Zeigt |
|------|-------|
| ALLE | alle Services |
| FEHLER | CRIT + WARN + UNKNOWN (Standard-Ansicht beim Öffnen) |
| WARN | CRIT + WARN |
| CRIT | nur CRIT |
| UNKNOWN | nur UNKNOWN |

Filter-Chips mit 0 Treffern bleiben immer sichtbar, werden aber ausgegraut (`opacity: 0.25`, nicht klickbar).

**On-Demand Graph:**
- Klick auf eine Service-Zeile → `GET /api/hosts/{host}/graph?service=<name>&metric=<id>`
- Metric-Inferenz aus Service-Name: `Filesystem*` → `fs_used_percent`, `Memory` → `mem_used_percent`, `CPU*` → `load1`
- 24h-Linienchart erscheint inline unter der Zeile; erneuter Klick schließt ihn

### API Endpoints (Backend)

| Endpunkt | Beschreibung |
|----------|-------------|
| `GET /api/hosts/{hostname}/health` | Performance-Vitals (gecacht + `?live=true`) + Host-Alerts |
| `GET /api/hosts/{hostname}/services` | Alle CheckMK-Services mit `state_label` + `summary`; sortiert CRIT→WARN→UNKNOWN→OK |
| `GET /api/hosts/{hostname}/graph` | 24h-Zeitreihe: `?service=<name>&metric=<id>` → `{series, title, unit}` |

### Technical Details

- **Navbar-Ausblendung**: `ngOnInit` setzt `document.body.classList.add('cockpit-active')`; `styles.scss` versteckt `.sidenav` und entfernt `mat-sidenav-content`-Margin (analog zu `bridge-active`)
- **Font**: Roboto (identisch mit News Feed und Alerts)
- **Auth**: Verwendet denselben `cs_access_token` aus `localStorage` — kein erneuter Login nötig
- **Mehrere Tabs**: `window.open` mit `target='cockpit-{hostname}'` — pro Host ein Fenster, kein Duplikat

---

## News Feed

### How it works

The News Feed shows all events from the active OpenSearch indices (`cs-feed-*`) in reverse chronological order.

**Sources** (toggleable):
- CheckMK alerts (monitoring events)
- Graylog logs (system logs, container logs)
- Wazuh alerts (security events, FIM)
- O365 emails (personal, your own only)
- Microsoft Teams messages (personal, your own channels only)

### Last-seen divider

- On opening the feed, the view automatically scrolls to the `Last seen` divider
- New messages (since the last visit) appear **above** the divider
- Older messages appear **below**
- After 3 seconds in the feed the timestamp is stored as `feed_last_seen` and the nav badge is reset

### Nav badge (unread count)

- Red number on the "News Feed" nav entry
- Refreshes automatically every 60 seconds
- Calls `GET /api/feed/unread-count?since=<ISO>`
- Disappears after 3 seconds in the feed

### My Tickets — unread indicators

- The red number on the "My Tickets" nav entry shows the number of tickets with new activity
- A **red dot** on individual ticket rows appears when the ticket was updated since it was last opened (new comment, status change, etc.)
- On closing the WorkSession dialog, the ticket list is reloaded so the dot appears correctly based on the fresh `updated` time
- Tracking is **server-side** via `user_preferences.ticket_seen_map` (JSON, `{jira_key: ISO time}`) — persistent across devices and browsers (no more localStorage)
- Loaded/written via `GET`/`PATCH /api/preferences` (field `ticket_seen_map`)
- On the first visit to the page all visible open tickets are marked as "seen" (no dots on first visit)
- **Closed tickets** (`statusCategory = done`) are removed from the map; if a ticket is reopened, tracking restarts
- The nav badge is recomputed live in the 60-second polling (tickets + `ticket_seen_map`), regardless of whether the "My Tickets" page is open

### Ignore button (AI exclusion)

- Every feed card has an **Ignore** button
- A click calls `POST /api/feed/{id}/ignore`: the AI generates an OpenSearch Lucene exclusion query from the item (characteristic phrase from `title`/`body`, possibly the container)
- The query is stored as a **system exclusion search** (`is_system=true, is_exclusion=true`) → similar messages permanently disappear from the feed
- The clicked item is immediately removed from the list locally

### AI analysis with web search

- The **AI Analysis** button per alert calls `POST /api/feed/{id}/enrich`
- When `workflow.web_search` is enabled (default on), a SearXNG web search adds context to the AI explanation
- Automatic enrichment is controlled via `agent.auto_enrich`

### "Newest messages" button

- Appears as a floating button on top once you scroll more than 350px down
- A click scrolls the page back to the top (smooth scroll)

### Saved searches in the feed

- **Searches panel** (expandable): shows all system searches and personal searches
- **Toggle switches**: disable individual searches (writes `feed_disabled_search_ids` to preferences)
- **Create a personal search**: via the `+` icon in the searches panel
- **AI assistant**: button in the search dialog → free-text input → AI generates the Lucene query automatically

### Highlight mode

When an item is opened directly from a widget or an external source:
- URL parameter `highlight_id=<OpenSearch doc ID>` and/or `host=`, `source=`, `severity=`
- The clicked item is **pinned** to the end of the first page if it is older than the current page
- After loading, the feed **automatically scrolls to the highlighted item** (blue outline, 2.8s)
- The feed shows matching filter values from the URL parameters

### `GET /api/feed/` — full parameter list

| Parameter | Type | Description |
|-----------|------|-------------|
| `limit` | int (max 200) | number of results (default 50) |
| `offset` | int | pagination |
| `sources` | string | comma-separated sources: `checkmk,graylog,wazuh` |
| `severity` | string | severity filter: `critical`, `high`, `medium`, `low`, `info` |
| `host` | string | hostname search (wildcard, case-insensitive) |
| `os` | string | OS filter (CheckMK) |
| `location` | string | location filter (CheckMK) |
| `criticality` | string | criticality filter (CheckMK) |
| `ve` | string | VE filter (CheckMK) |
| `hostgroup` | string | comma-separated CheckMK host groups |
| `search_id` | UUID | run a saved search directly |
| `index` | string | OpenSearch index pattern (direct mode) |
| `q` | string | Lucene query string (direct mode) |
| `highlight_id` | string | pin + highlight an item |

---

## OpenSearch Searches (FeedSearches)

### Where are searches defined?

- **Admin area**: Settings → Feed → system searches (create, edit, test)
- **User area**: News Feed → searches panel → `+` personal search

### Search model

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | unique ID |
| `user_id` | UUID? | NULL = system search, set = personal search |
| `name` | string | display name |
| `index_pattern` | string | OpenSearch pattern, e.g. `cs-feed-*`, `cs-feed-wazuh` |
| `query_string` | string | Lucene query; empty = match_all |
| `enabled` | bool | active? |
| `is_system` | bool | visible system-wide (admin) |
| `is_exclusion` | bool | if true: items are **hidden** from the feed |
| `position` | int | order in the list |

### Exclusion searches

Searches with `is_exclusion=true` generate `must_not` clauses in **all** feed queries. This permanently hides unwanted messages:

```
Example: hide /etc/patchmon/config.yml FIM alerts
  query_string: "metadata.syscheck.path:/etc/patchmon/config.yml"
  is_exclusion:  true
```

### Bundled system searches

| Name | Index | Query |
|------|-------|-------|
| Filebeat (Hyde-relevant) | `cs-feed-graylog` | `metadata.hyde_relevant:true AND NOT metadata.source_host:(nsa* OR nss* OR nsc*)` |
| HTTP errors (container) | `cs-feed-graylog` | `metadata.http_response_code:>=400 AND metadata.container_name:*` |
| Syslog Errors | `cs-feed-graylog` | `metadata.level:<=4 AND NOT body:uprobes` |
| Wazuh Security Alerts (Level 7+) | `cs-feed-wazuh` | `metadata.rule_level:>=7` |
| All CheckMK alerts | `cs-feed-checkmk` | *(empty = all)* |
| All Graylog logs | `cs-feed-graylog` | *(empty = all)* |
| All Wazuh alerts | `cs-feed-wazuh` | *(empty = all)* |
| All sources | `cs-feed-*` | *(empty = all)* |
| Critical and high alerts | `cs-feed-*` | `severity:(critical OR high)` |

### Query syntax (Lucene)

```
# Graylog: all container errors from docker086
metadata.container_name:docker086* AND metadata.http_response_code:>=400

# Wazuh: security events level 10+ for a specific host
metadata.rule_level:>=10 AND metadata.agent.name:docker086

# All: critical alerts
severity:critical

# Graylog: Hyde-relevant messages excluding NSA/NSS/NSC
metadata.hyde_relevant:true AND NOT metadata.source_host:(nsa* OR nss* OR nsc*)

# CheckMK: all Linux hosts with high severity
severity:(high OR critical) AND metadata.os:Linux
```

---

## Alert Aggregation and Enrichment

### Pipeline

```
APScheduler (every 10 min)
    │
    └── alert_aggregator.py
          ├── CheckMKConnector.get_alerts()        → cs-feed-checkmk
          ├── GraylogConnector.get_alerts()         → cs-feed-graylog
          ├── WazuhConnector.get_alerts()           → cs-feed-wazuh
          └── feed_index.index_items()              → OpenSearch bulk index
                │
                └── feed_enricher.py (async background task)
                      └── LLM: 2-3 sentence explanation + first action → ai_insight field
```

### CheckMK aggregation

**Endpoint:** `GET /domain-types/host/collections/all` (all hosts + tags)

Fields extracted from CheckMK:

| CheckMK field | Normalization | OpenSearch field |
|---------------|---------------|------------------|
| `tags.tg-os` / `tags.operatingsystem` | `_OS_LABEL_MAP` (os-linux → Linux) | `metadata.os` |
| `extensions.attributes.tag_location` / `host_filename` | folder from the WATO path | `metadata.location` |
| `tags.tg-criticality` | raw value | `metadata.criticality` |
| `tags.tg-ve` / `tags.tg-virt_env` | raw value | `metadata.ve` |
| Host groups | list of all groups | `metadata.hostgroups` |
| `extensions.attributes.alias` | — | `metadata.alias` |

**Wazuh filter (configurable via the connector form):**

FIM exclusions can be configured through the connector credentials:
```json
{
  "excluded_rule_ids": ["503", "504", "533", "591", "5402", "5501", "5502", "5715"],
  "excluded_fim_paths": ["/etc/cmk-update-agent.state", "/etc/patchmon/config.yml"]
}
```
If these are not set, the internal defaults apply.

### AI enrichment (ai_insight)

After indexing, new alerts with severity `critical`, `high` or `warning` are enriched in the background by an LLM call:

- **Prompt:** system prompt as an experienced Linux sysadmin; 2-3 sentence explanation + concrete first action, in the operator's language
- **Input:** `{source}: {title}\n{body}\nHost: {host}\nLocation: {location}`
- **Result:** plain text (max 400 chars), stored as `ai_insight` in the OpenSearch document
- **Configuration:** `agent.auto_enrich` (default `true`) — can be disabled under Settings → AI

---

## Error Clusters & Incidents

CentralStation erkennt **Fehlerausbrüche** selbständig, bündelt sie zu **Incidents** und
meldet sie per **Mail** — ohne Dauerrauschen. Code: `backend/app/services/error_clusters/`.
Die frühere Host-Korrelation (`incident/correlator.py`) ist darin aufgegangen; Incidents
entstehen nur noch hier.

### Ablauf (alle 5 Minuten)

```
Graylog (level:<=4 + Container-Zeilen mit eigenem ERROR, ohne Switches) ── jede Zeile, KEINE Deduplizierung
   │   Fenster > 10 000 Treffer werden geteilt (OpenSearch-Grenze)
   ▼
Drain3-Muster (Zahlen/IDs/IPs maskiert, HTTP-Codes bleiben: 401 ≠ 500)
   ▼
Zählwerte je Muster und 5-min-Fenster (Postgres)  →  Grundlast = Median (Nullen inklusive)
   ▼
Regeln (fest, kein LLM):  NEU · ANHALTEND NEU · SPITZE · AUSBREITUNG     CheckMK: FLOTTE
   ▼
Episode (bis 60 min Ruhe) → Incident → KI-Einschätzung → Sofort-Mail / Tagesübersicht
```

| Regel | Bedingung |
|---|---|
| **NEU** | 14 Tage nicht gesehen und ≥ 20 Zeilen in 15 min oder ≥ 2 Quellen. Sofort bei Ausbreitung oder ≥ 500 Zeilen, sonst Tagesübersicht |
| **ANHALTEND NEU** | erstmals vor ≤ 24 h gesehen, in jeder halben Stunde der letzten 2 h aktiv, ≥ 100 Zeilen — sofort (Retry-Schleifen nach einem Deploy) |
| **SPITZE** | ≥ 200 Zeilen in 15 min, ≥ 5 × Grundlast, Poisson p < 1e-6, 2 von 3 Fenstern erhöht. Wiederkehrende Muster (≥ 3 aktive Tage) müssen ihren bisherigen Höchstwert verdoppeln. Sofort ab 1 000 Zeilen, sonst Tagesübersicht |
| **AUSBREITUNG** | ≥ 8 Container/Quellen, ≥ 100 Zeilen, ≥ 3 × üblich — immer sofort |
| **FLOTTE** (CheckMK) | derselbe Service auf ≥ 5 Hosts in 15 min neu nicht OK, ≥ 3 × dem 7-Tage-Median |
| **KRITISCHES EREIGNIS** | OOM-Kill, Kernel panic, hung task, Lockup, I/O-/Dateisystem-/Hardwarefehler (nur Kernelzeilen): 1× → Tagesübersicht, 3× in 6 h auf demselben Host → Incident + Mail |
| **QUELLENWECHSEL** | ≥ 60 neue Muster in einem 5-min-Fenster (normal max. 46): Beginn der Beobachtung, 24 h keine NEU/SPITZE/AUSBREITUNG für diese Muster, ein Feed-Eintrag statt Einzelmails |
| **WIEDERKEHREND** (Flotte) | derselbe Ausfall an ≥ 3 der letzten 7 Tage zur gleichen Uhrzeit (± 30 min), ähnlich groß, sonst ruhig → geplanter Job: nur Tagesübersicht, kein Incident, keine Mail |
| Erneut melden | nur bei Verdopplung der 5-min-Rate oder der Container, höchstens stündlich |
| Ein Vorfall | ein Muster, das ≤ 30 min nach einem gemeldeten Ausbruch auf denselben Containern/Hosts beginnt, wird Teil davon (eine Mail, ein Incident) |

Die Sofort-Mail enthält neben der KI-Einschätzung den Abschnitt **Recherche**: ähnliche
Meldungen (andere Muster mit ≥ 50 % gleichen Wörtern im selben 15-min-Fenster) und
CheckMK-Alerts der betroffenen Hosts —
die Daten, auf denen die Einschätzung beruht; leere Funde stehen als „keine" da. Der
Incident-Link steht oben und unten.

Docker liefert stdout pauschal als Level 6. Mitgelesen werden davon Container-Zeilen, deren
**erstes Level-Wort** ERROR/FATAL/CRITICAL/SEVERE ist (sonst `level=error`, JSON-`level`,
Traceback); `INFO … outcome=ERROR` zählt nicht. Muster, die in den ersten 2 h nach dem Lesen
einer neuen Quelle auftauchen, gehen nur in die Tagesübersicht (sie liefen womöglich schon
vorher) — Stand je Quelle in `error_clusters.coverage_since.<version>`.

### Incidents

`incidents.kind`: **`host`** (mehrere critical/high-Alerts eines Hosts; ≥ 2 Alerts oder
≥ 2 Quellen; offen solange in 30 min neue kommen), **`log_cluster`** (Log-Muster über viele
Container), **`fleet`** (CheckMK-Service auf vielen Hosts). Alerts eines Hosts, der zu einem
offenen übergreifenden Cluster gehört, werden dort Mitglied; offene host-Incidents seiner
Hosts gehen darin auf („aufgegangen in …"). Reine CheckMK-host-Incidents schließen im selben
Aggregationslauf, in dem der letzte Check wieder OK ist.

Die Incident-Ansicht im Feed zeigt bei Clustern Muster, Begründung, KI-Einschätzung,
gleichzeitig auffällige Dienste, weitere Muster desselben Ausbruchs und den Graylog-Link;
„An Konsole übergeben" setzt am Muster an, nicht nur am Host. Admins können ein Muster dort
**stummschalten** (protokolliert, umkehrbar).

**Ticket erstellen** (Incident-Ansicht, neben „Computer, untersuche das"): öffnet den gemeinsamen
Ticket-Dialog (ServiceDesk/Jira, Projekt, Typ, Priorität aus der Incident-Schwere). Den Entwurf
schreibt die KI aus demselben Lagebild wie die Konsolen-Übergabe (`feed.incident_briefing`:
Muster, Belege, Recherche, Einschätzung, Prüfergebnisse, Rezept, Lösungsvermerk) — vor dem
Anlegen frei bearbeitbar. Das Backend hängt den Link zum Incident an und vermerkt das Ticket am
Incident; dort erscheint es als Link („Tickets: IMIT-…").

### Mails, Dashboard, Einstellungen

- **Nutzer** (Einstellungen → E-Mail-Berichte): „Sofort-Mail bei einem Ausbruch" und „Neue,
  ruhige Fehlermuster im täglichen Bericht" — nur für Hosts aus dem eigenen CheckMK-Filter
  (Docker-Hosts **und** Container, die CheckMK als Hosts führt).
- **Admin** (Einstellungen → KI → Fehler-Cluster): `error_clusters.enabled`,
  `error_clusters.mail_enabled` (Not-Aus). Erster Start holt 24 h still nach.
- **KI-Einschätzung**: je Episode ein Aufruf, kein Budget, kein Token-Limit. Die Mail wartet
  höchstens 240 s; danach „KI-Einschätzung folgt" + zweite Mail. Nicht zugestellte Mails
  (Neustart) holt der Runner nach (`mailed_at`).
- **Mindestalter 30 min** (`rules.min_age_minutes`): Flotten-Cluster zählen nur Probleme, die
  seit 30–45 min bestehen und noch offen sind; Mails zu Log-Clustern werden zurückgehalten, bis
  der Ausbruch 30 min alt und noch aktiv ist — Neustarts und kurze Jobs lösen keine Mail aus.
- **Eine Mail je Vorfall**: Flotten-Ausfälle, die höchstens 30 min nacheinander beginnen und
  sich Hosts teilen (Jaccard ≥ 0,2), sind ein Incident mit mehreren Mustern und eine Mail
  (z. B. ORD1/ORD2/ORD3 auf denselben 5 Hosts). Kehrt ein Ausfall binnen 3 h nach dem
  Schließen zurück, läuft die alte Episode weiter; neu gemeldet wird nur bei Verdopplung
  der Hosts, höchstens stündlich — wie bei Log-Mustern.
- **KI-Lagebild**: Widget `error_clusters` erscheint bei jedem offenen Ausbruch ganz oben;
  ein neuer Ausbruch stößt die Neuberechnung sofort an.

### API

| Endpunkt | Beschreibung |
|---|---|
| `GET /api/error-clusters?status=open\|closed\|all&hours=24&kind=immediate\|digest\|all` | Cluster im eigenen Host-Scope |
| `GET /api/error-clusters/{id}` | Detail mit 5-min-Verlauf |
| `POST/DELETE /api/error-clusters/templates/{key}/mute` | Muster stumm/frei (Admin, Audit-Log) |
| `GET /api/feed/incidents` · `…/{id}/timeline` · `…/{id}/claude-prompt` | Incidents, Timeline (mit `kind`, `error_cluster`, `merged_into`), Konsolen-Briefing |
| `POST /api/tickets/draft` · `/api/tickets/create` mit `incident_id` | KI-Ticketentwurf aus dem Incident-Lagebild; Anlage mit Rückverweis, Vermerk in `timeline.incident.tickets` |

### Backtest

```bash
docker compose run --rm --no-deps -v /pfad/cache:/bt backend \
  python -m app.scripts.error_cluster_backtest --until 2026-10-01T12:00Z --hours 72
```

Spielt echte Graylog-Fenster durch dieselbe Engine (ohne Mail/LLM/DB), Rohdaten im Cache.
Stand 01.10.2026: 864 Fenster, 3 991 137 Zeilen — der 401-Ausbruch wird in beiden Wellen im
ersten 5-min-Lauf gemeldet, das Dauerrauschen nie; 4 Sofort-Mails in 48 h.

---

## Kanban and Jira

### Kanban board

- Drag-and-drop board with five columns: Backlog → Todo → In Progress → Review → Done
- Status changes via drag-and-drop trigger Jira transitions (bidirectional sync)
- **Jira import**: Jira tickets can be imported into the board automatically via JQL
- **AI card creation**: on critical findings the AI agent creates Kanban cards automatically
- **Alert linking**: cards can be linked to feed alerts

### Bidirectional Jira sync

**Status mapping:**

| CentralStation | Jira status (examples) |
|----------------|------------------------|
| `backlog` | Backlog, Open, Selected for Development |
| `todo` | To Do, Open, Ready |
| `in_progress` | In Progress, Doing, Implementing |
| `review` | Review, In Review, Testing, QA |
| `done` | Done, Resolved, Closed |

### My Tickets (Jira view)

- Shows Jira tickets according to configured JQL queries
- **JQL templates**: default templates preinstalled, customizable
- **AI JQL generator**: free text → optimized JQL (`POST /api/preferences/jira-queries/generate`)
- **Widget display**: selected JQL queries appear as a widget on the Jira page
- **Open in Jira / Service Desk with one click**: the ↗ icon on every list row and board
  card opens the ticket in its own instance, in a new tab. Clicking the row still opens the
  work dialog. The link comes from the backend (`_centralstation.url`, built from the instance
  the ticket was found in), so a key that exists in both Jira and Service Desk still leads to
  the right one.
- **Console:** a Console session that belongs to a ticket shows a **↗ KEY** pill in the
  action rail. The key in the "Neue Aktivität" banner is a link too
  (`ticket_ref.url` in `/api/computer/sessions`).
- **One rule for the link:** `app/core/jira_links.browse_url(base_url, key)` →
  `<base>/browse/<KEY>`. Every place that links a ticket (incidents, ticket creation, ticket
  search, kanban, work sessions, My Tickets, Console) uses it.

### ITIL work sessions

Work sessions document the handling of an incident:

1. **Create**: enter the Jira ticket key → CentralStation pulls the ticket data from Jira
2. **Categorization**: impact/urgency → automatic P1–P4 priority + SLA deadline
3. **Work notes**: timestamped entries of who did what and when
4. **AI actions**:
   - **Generate comment**: choose the type (progress / pending / escalation / handover) → optional free-text field "current developments" → AI drafts a Jira comment
   - **Resolution documentation**: root cause, actions, lessons learned
   - **5-whys analysis**: ITIL problem management
   - **Solution search**: RAG + web search

### ITIL priority matrix

| Impact ↓ / Urgency → | High | Medium | Low |
|----------------------|------|--------|-----|
| **High** | P1 (15 min) | P2 (60 min) | P3 (4 h) |
| **Medium** | P2 (60 min) | P3 (4 h) | P4 (24 h) |
| **Low** | P3 (4 h) | P4 (24 h) | P4 (24 h) |

---

## AI Features

### LangGraph agents

Two autonomous agents run in the background (APScheduler, every 10 minutes):

#### SysAdmin agent

```
Node 1: collect_data
  → CheckMK: open problems (last 1h)
  → Graylog: ERROR/CRITICAL (last 1h)
  → Wazuh: security alerts
  → Jira: new/unassigned tickets

Node 2: enrich
  → IP → site (ID-Generator)
  → hostname → device (NetBox)
  → vendor detection (Juniper/Cisco/VMware from Graylog messages)

Node 3: rag_lookup
  → LLM decides if additional context is needed (SearXNG web search)
  → CheckMK metrics for affected hosts

Node 4: analyze
  → correlate events + web/metrics context
  → findings + recommendations + error clusters (structured, Pydantic AnalysisResult)
  → store in PostgreSQL (ai_analyses table; findings, recommendations, clusters)

Node 5: act
  → critical findings → Jira ticket (JQL dedup prevents duplicate tickets)
  → WebSocket push → all connected SysAdmin/Admin clients
```

#### Network agent

```
Node 1: collect_switch_logs
  → Graylog: source:(nsa* OR nss* OR nsc*) — last 1h, deduplicated

Node 2: enrich_switches
  → switch name → ID-Generator (location_id → site name + city)
  → NetBox: interface/VLAN data

Node 3: analyze_network
  → STP, LACP, port flapping, MAC flooding
  → vendor detection (Juniper NSA/NSS patterns)

Node 4: act
  → findings → PostgreSQL
  → WebSocket push → Network Technician clients
```

### Error clusters (root-cause grouping)

In the `analyze` node the SysAdmin agent sees all open alerts at once (plus the
blast-radius topology). When several findings plausibly share **one** root cause,
it groups them into an **error cluster** with a single diagnosis instead of
reporting each symptom in isolation. Typical patterns:

- A network device (router/switch/uplink) down → many downstream hosts unreachable.
- A shared storage / hypervisor / Proxmox node down → multiple VM or filesystem alerts.
- A site-wide outage (power, uplink, DNS) → many hosts at the same location at once.

Each cluster carries `diagnosis`, `severity`, `root_cause_host`, `affected_hosts`,
`explanation` and a `recommendation`. Clusters are a per-run snapshot stored
alongside the analysis (`ai_analyses.clusters`) — there is no cross-run lifecycle.
The same anti-hallucination/evidence rules apply: an uncertain diagnosis must start
with `"Vermutete Korrelation — unbestätigt:"`.

Clusters surface in three places (all read the latest sysadmin run):

- **KI-Insights** view — a "Diagnose / Fehler-Cluster" section above the findings.
- **Hermes console** ("Computer, prüfe das") — a "KI-Fehler-Cluster (Case-Analyse)"
  section, shown when the inspected host is in a cluster's `affected_hosts` or is the
  `root_cause_host`, so Hermes considers the shared cause before looking at the host
  in isolation (`search_recent_ai_clusters` in `feed_index.py`).
- **Bridge** (`/bridge`) — a cluster-diagnosis banner above the primary incident
  (`error_clusters` in `GET /api/bridge/status`, filtered by the user's host scope).

### AI chat endpoints

| Endpoint | Function | Input | Output |
|----------|----------|-------|--------|
| `POST /api/ai/search-assistant` | free text → Lucene query + optionally create a FeedSearch/widget | `{"message": "..."}` | `{"reply": "...", "actions": [...]}` |
| `POST /api/ai/promql-assistant` | free text/Lucene → PromQL | `{"message": "..."}` | `{"promql": "...", "explanation": "..."}` |
| `POST /api/workflow/{id}/generate-comment` | generate a Jira comment | `{"comment_type": "progress", "additional_context": "..."}` | `{"comment": "..."}` |
| `POST /api/workflow/{id}/generate-resolution` | closure documentation | — | `{"resolution": "..."}` |
| `POST /api/workflow/{id}/5why` | 5-whys root cause analysis | — | `{"analysis": "..."}` |
| `POST /api/workflow/{id}/suggest-solution` | RAG + web solution search | — | `{"steps": [...], "sources": [...]}` |
| `POST /api/workflow/analyze-mail` | analyze an O365 email | `{"content": "..."}` | `{"summary": "...", "ticket_key": ...}` |
| `POST /api/preferences/jira-queries/generate` | JQL from free text | `{"description": "..."}` | `{"jql": "...", "name": "..."}` |

### AI settings (Settings → AI)

| Setting key | Description | Default |
|-------------|-------------|---------|
| `ui_language` | Per-user UI and AI response language (`en`, `de`) | `en` |
| `llm.provider` | active LLM provider: `custom` (local endpoint) or `openai-codex` (OAuth) | `custom` |
| `llm.base_url` | OpenAI-compatible endpoint (only for `custom`) | — |
| `llm.model` | model ID for the `custom` provider (e.g. `qwen3next-79b`) | — |
| `llm.api_key` | API key (optional, only for `custom`) | — |
| `llm.codex_model` | model ID for the `openai-codex` provider (e.g. `gpt-5.5`) | `gpt-4o` |
| `llm.codex_timeout_seconds` | timeout for Codex requests | `60` |
| `llm.vision_model_url` | vision model endpoint | — |
| `llm.vision_model` | vision model ID | — |
| `llm.thinking_mode` | enable extended thinking (only `custom`) | `false` |
| `agent.auto_enrich` | automatic AI enrichment after aggregation (off = on-demand) | `true` |
| `workflow.web_search` | web search (SearXNG) during AI analysis of feed/alerts | `true` |
| `agent.interval_minutes` | interval for background agents (minutes) | `10` |
| `agent.auto_jira` | create Jira tickets automatically | `true` |
| `agent.jira_severity_threshold` | minimum severity to create tickets | `critical` |
| `searxng.base_url` | SearXNG web search URL | — |

### OpenAI Codex OAuth provider

CentralStation can optionally use OpenAI Codex (GPT-5.x) as the LLM provider — without a paid API key, just a regular ChatGPT account.

**Setup (one-time, in the browser):**
1. Settings → AI → card **"OpenAI Codex — Sign in"**
2. Button **"Sign in with OpenAI"** → a browser code is shown (e.g. `28UF-4FKHV`)
3. Open `https://auth.openai.com/codex/device` in the browser, enter the code, sign in with the ChatGPT account
4. CentralStation detects the successful login automatically (polling every 5 seconds)
5. The token is stored Fernet-encrypted in the database and refreshed automatically via the refresh token

**Switch the provider:**
- Settings → AI → **LLM configuration** → set "LLM Provider" to `OpenAI Codex (OAuth)`
- Enter the model (e.g. `gpt-5.5`) → Save
- Test the connection → shows `Connection OK — OpenAI Codex / model 'gpt-5.5' responds`

**Technical background:**
- Endpoint: `https://chatgpt.com/backend-api/codex/responses` (Responses API, not Chat Completions)
- Streaming is mandatory (`stream: true`) — the endpoint rejects non-streaming requests
- `max_output_tokens` is not supported by the endpoint
- OAuth flow: device code + PKCE (RFC 8628)
- API endpoints: `GET/DELETE /api/oauth/openai-codex/status|logout`, `POST /api/oauth/openai-codex/start|poll/{session_id}`

**AI output behaviour:**
- The SysAdmin agent emits all text fields (findings, recommendations) **in the operator's language** (`ui_language`) — even when web context is in another language
- **No hallucinations**: if context is missing, the AI says so explicitly instead of inventing causes

---

## Computer Console (Hermes AI Panel)

CentralStation includes an interactive AI assistant panel — the **Computer Console** — that wraps an AI agent session in a floating LCARS-styled popup. It gives operators a Star Trek-inspired interface for diagnosing alerts and querying the monitoring infrastructure through natural language.

### Architecture

Each user gets a dedicated **`cs-userenv-{uid}`** container (shared with the Werkbank) that runs both the AI agent (Hermes FastAPI) and code-server. The backend proxies browser requests to this container after JWT authentication.

```
Browser (Computer panel)
    │  POST /api/computer/sessions/{sid}/message  (SSE stream)
    ▼
backend (computer_proxy.py)  ←→  JWT auth + agent-type dispatch
    │  HTTP proxy
    ▼
cs-userenv-{uid}  (FastAPI on port 8001)
    ├── Hermes AIAgent.run_conversation()   ← agent_type: "hermes" (default)
    ├── claude --print --session-id <sid>   ← agent_type: "claude_cli"
    └── codex exec --json                   ← agent_type: "codex_cli"
         │
         ▼
MCP servers:
  /api/mcp/sse          (SSE, legacy — Hermes)
  /api/mcp-http/        (streamable-http — Claude CLI, Codex CLI)
  playwright            (stdio — browser automation, all three agents)
  + personal connectors (VibeMK, AWX-NG, …)
```

### Searching your sessions (live search)

The field **SUCHEN** above the session list filters the list while you type.
- **Searched:** session names and the full history the Console shows (it loads every
  session's history when it opens). The search runs in the browser; nothing is sent to the
  server.
- **What counts as a hit:** a message that contains **all** search words (case-insensitive,
  minimum 2 characters), or a session name that does. Words scattered across a long session
  are not a hit.
- **Shown per session:** the number of matching messages and an excerpt around the first
  hit, with the words marked. The status line says "N / M SITZUNGEN".
  - Sessions whose history could not be loaded are counted as "· N nur Name" (only their
    name was searched).
  - "KEINE TREFFER" and "mind. 2 Zeichen" are shown as such.
- **Jumping to hits:** clicking a hit opens the session, scrolls to the first matching
  message, and makes it flash. Clicking it again jumps to the next hit. Matching messages
  have a bar on the left, and the words are highlighted via the CSS Custom Highlight API,
  without touching the rendered Markdown.
- **Esc** in the field clears the search.
- **Not covered:** Codex sessions keep their history only in memory, so after a container
  restart only their name is searchable.


`search_tickets(query, target, project, open_only, max_results)` (MCP) searches both
instances with free text or a ticket key, `ORDER BY updated DESC`.
- **Projects:** a project is only searched where it exists.
- **Each hit:** instance, link (`…/browse/KEY`) and the matching text passage.
- **Complex filters:** use `jira_search_issues` (JQL); details come from `jira_get_issue`.

### Master data: ID-Generator (read-only)

The CentralStation MCP server offers three read-only tools on the ID-Generator connector
(`id_generator`, API `/api/v2`, Basic Auth from the connector config):
- `lookup_idgen_host(name)` — location for a host/FQDN (via `virt_servers`, else DNS + `ip_networks`),
  an IPv4 address or a switch (NSA/NSS/NSC).
- `list_idgen_records(resource, filter, limit)` — rows of a table (`locations`, `companies`,
  `ip_networks`, `virt_servers`, …), equality filter `field=value&field2=value2`, max 500.
- `query_idgen_sql(sql, limit)` — exactly one `SELECT` (no `;`, no file/sleep functions), max 500 rows.
Inputs are validated (hostname/IP pattern, table name pattern) before they reach SQL.

### Server documentation: man pages and capabilities (probe runner)

The Console agents can look things up on the server itself, in the version installed there:

| MCP tool | What it does |
|---|---|
| `host_capabilities(host)` | OS/version, kernel, available tools (docker, journalctl, zfs, …), sudo rights of the probe account |
| `search_manpages(host, query)` | `apropos` for sections 1 and 8 |
| `read_manpage(host, command, focus)` | NAME, SYNOPSIS and — with `focus` — exactly the matching option entries (e.g. `--delete`) |

- **How it runs:** the tools go through the **probe runner** (`probe/`, compose service
  `probe-runner`). It accepts only the fixed read-only templates of `cs_policy/templates.py`:
  template id plus parameters, each validated against its pattern and shell-quoted, never free
  command text.
- **Which hosts:** only hosts from the CheckMK inventory, plus `CS_PROBE_EXTRA_HOSTS`. The
  Docker host is never a target (`CS_SSH_DENIED_HOSTS`).
- **Who may call:** only callers that authenticate with the agent token. Every call is
  recorded in `remote_exec_log` (user, host, template, exit code; not the output).
- **Account:** the probe runner's SSH account is set by the administrator under Admin → KI →
  Prüf-Container (`probe.ssh_user`, `probe.ssh_key` encrypted, `probe.test_host` for the
  connection test). The backend hands it to the runner (`POST /configure`) and re-sends it
  after a restart or change, detected by fingerprint.
- **Results are cached** (man pages 6 h, search and capabilities 1 h).
- **Errors are named:** `host_unknown`, `not_configured`, `no_manpage`, `ssh_failed`.

### Log watch: field catalog (Graylog, Wazuh, CheckMK)

First building block of the log watch modelled on the research tool
(`~/Dev/code/recherche-stack`):
- Scripts classify the stream of all three sources.
- Only messages tagged as relevant are kept as history.
- The AI filters that history until a relevant result remains.

The field catalog comes first. Every field of every source is known with its role, because hashtags,
filters and the network graph later work only on fields and their values.

- **Measured in the source, nothing copied.** One adapter per source
  (`backend/app/services/log_watch/quellen/`), all with the same interface:
  - **Graylog:** `/api/views/fields`, counted by Views pivot (`count`, `card`) over 15 min of all
    messages.
    - Pure text fields (not *enumerable*) are counted via `_exists_`.
    - A block Graylog rejects is split in half until the offending field stands alone.
    - Where Graylog refuses the value list, example values come from a 200-message sample and are
      marked `stichprobe`.
  - **Wazuh:** the union of the mappings of the last 7 `wazuh-alerts-*` daily indices, counted on the
    indexer (`exists` filter, `cardinality`, `terms`) over 24 h.
  - **CheckMK:** the current service problems with their columns and host labels.
    - Columns take the names used by the other sources (`description` → `service`).
    - Label keys containing a host name (`cmk/piggyback_source_<host>`) become one field with the host
      as its value.
- **Roles** (`catalog.bestimme_rolle`):

  | Role | Meaning |
  |---|---|
  | `entitaet` | host, container, agent, service, IP → later `#host:`-style hashtags and graph nodes |
  | `kategorie` | ≤ 50 values: level, state, HTTP code |
  | `kennung` | rule ID, check, ≤ 5 000 values |
  | `messwert` | port, bytes, duration, counter |
  | `freitext` | message text |
  | `ignorieren` | IDs, timestamps, nearly-unique values, or more than 5 000 values |
  | `unbekannt` | no values in the window |

  The name decides first, then the measurement; every role carries a reason.
- **Status of a field:** `aktiv`, `ohne_werte`, `nicht_gemessen` (the source refused, which is not the
  same as empty) or `verschwunden` (the source no longer knows the field; it stays in the catalog).
- **Admin overrides:** a role set by an admin is never overwritten by a measurement and can be reset.
- **API** (admin):
  - `GET /api/log-watch/fields?quelle=&rolle=&status=`
  - `POST /api/log-watch/fields/refresh?quelle=`
  - `PUT` / `DELETE /api/log-watch/fields/{quelle}/{feld}/rolle`
- **Schedule:** daily at 03:45 (`log_watch_field_catalog`). Table `field_catalog` (migration 0054).

### Log watch: raster (tagging the stream; dry run)

The raster tags and rates the stream of all three sources by script, without AI.

**Output:** one `Treffer` per message group, with relevance, reasons and hashtags. A group is
pattern × host for Graylog, rule × agent for Wazuh, and service × host for CheckMK.

**Relevance:**

| Value | Meaning | What happens |
|---|---|---|
| 0 | routine | stays in the source |
| 1 | normal | stays in the source |
| 2 | conspicuous | becomes history |
| 3 | urgent | becomes history and goes to the AI |

**Hashtags** have exactly one form, `art:wert`, in one field. They are at the same time the filter, the
facet and the network-graph node:

| Kind | Meaning |
|---|---|
| `quelle:` | source |
| `host:` | canonical name from CheckMK |
| `ort:` | location |
| `hg:` | host group |
| `app:` | container |
| `muster:` | Drain pattern |
| `regel:`, `regelgruppe:`, `mitre:` | Wazuh rule, rule group, MITRE tactic |
| `dienst:`, `zustand:` | CheckMK service and state |
| `thema:` | fixed vocabulary, `services/log_watch/vokabular.py` |
| `hostliste:` | why a name has no CheckMK host: `unbekannt`, `mehrdeutig` or `container-id` |

**Rules per source** (`services/log_watch/raster.py`):

- **Graylog:** runs inside the 5-minute pass of the error-cluster detection. The lines are already
  fetched and assigned to patterns there, so there is no second fetch.
  - An error line is 1.
  - A pattern rule (new, spike, spread) is 2; an immediate report is 3.
  - A critical kernel signature on the host is 3.
  - The themes `oom`, `disk-full`, `fs-readonly`, `kernel-io` and `absturz` are at least 2.
  - A muted pattern is 0.
- **Wazuh:** `rule.level` ≥ 12 → 3, 7–11 → 2, 4–6 → 1, otherwise 0.
- **CheckMK:** only changes count as messages: a new problem or a different state.
  - CRIT is 2; CRIT on criticality `prod` or `critical` is 3; WARN or UNKNOWN is 1.
  - An acknowledged problem is at most 1.
  - The first comparison after a start counts as the existing state, not as new.
  - A vanished problem is counted as `behoben`.
- **Across sources:** a host that is relevant in two sources within 15 minutes rises to 3. The reason
  says explicitly "common occurrence, no cause established".
- **Flood brake:** at most 5 stored messages per source × group × host × hour; the rest is only
  counted.

**Host names** (`services/log_watch/hosts.py`) work like name variants in the research tool. The
canonical name is the CheckMK host. A name is matched in this order:
1. exact name or IP;
2. short name without leading zeros (`aim012` = `aim12`);
3. short name with a glued-on domain removed (`cue0104REDACTED-CREDENTIAL`).

A short name that fits several hosts is not guessed.

**Dry run:** for now the raster only counts and stores nothing.
- One row per 5-minute window and source in `raster_lauf`.
- Wazuh and CheckMK run in the job `log_watch_raster` every 5 minutes.
- `GET /api/log-watch/raster/trockenlauf?stunden=24` (admin) returns, per source, the messages, the
  relevance distribution, what would be stored per day (messages and MB), the top hashtags and the
  unmatched hosts.

### Log watch: what "relevant" means (relevance definition)

Every verdict (`urteil`: `relevant` | `nicht_relevant`) cites the criteria it rests on. The definition
is versioned in the table `relevanz_definition` (`services/log_watch/definition.py`). An admin changes
it by adding a new version with a reason; the AI may propose criteria. Version 1 was agreed with the
user on 2026-11-06.

**Relevant**

| ID | Criterion | How it is recognised |
|---|---|---|
| R1 | Disruption in production | CheckMK CRIT on criticality `prod`, `staging` or `critical` |
| R2 | New pattern | error-cluster rules "new" and "persistent" |
| R3 | Spread | error-cluster rule "spread" |
| R4 | Spike | error-cluster rule "spike" |
| R5 | Damage to resources | themes oom, disk-full, fs-readonly, kernel-io, absturz; kernel signatures; CheckMK CRIT on filesystem, memory or disk I/O |
| R6 | Security | Wazuh level 7 or higher |
| R7 | Coincidence | the same host conspicuous in two sources within 15 minutes; no cause established |
| R8 | Expiring deadline | themes cert, backup; CheckMK certificate or backup services not OK |

**Not relevant**

| ID | Criterion |
|---|---|
| N0 | No criterion met (the named default case) |
| N1 | Routine: a known pattern within its usual volume |
| N2 | Known and handled: acknowledged or in downtime. Open ticket and earlier AI finding are not wired yet. |
| N3 | Not production: criticality `test` or `dev` |
| N4 | Noise: PAM session opened/closed (rules 5501, 5502), web scanners (`web_scan`) |
| N5 | Generated by monitoring itself (`Check_MK…`) |
| N6 | Muted by an admin |

**Precedence:** human before AI before rule. Within the same origin:
1. N6 (muted) wins over everything.
2. N2 (handled) wins over every R. As decided, acknowledged means handled, without exception.
3. N4 (noise) wins over every R, including R6. Noise by definition covers events at level 7 and above;
   if R6 won, N4 would have no effect.
4. R5 and R6 win over N3 and N5.
5. N3 and N5 win over the remaining R.
6. Any R wins over N0 and N1.

**API:**
- `GET /api/log-watch/definition[?alle=true]`
- `POST /api/log-watch/definition` (admin): a new version with a reason. Every version must keep all
  criterion IDs, and unknown parameters are rejected.

### Log watch: history `cs-logs-*` (active since 2026-11-06 20:44 UTC)

- **Content:** only messages that are relevant, or that were judged to be relevant or not. One document
  per source message.
  - Original fields under `roh` (`flat_object`: every field searchable without 1 191 mapped Graylog
    fields).
  - Hashtags, reasons, the text, and a bge-m3 vector (`vektor`, 1 024 dimensions, Lucene HNSW, cosine).
- **Selectors** (filters, facets, graph): `quelle`, `host`, `hashtags`, `relevanz`, `zeit`, plus:
  - `urteil` with exactly two values, `relevant` | `nicht_relevant`;
  - `herkunft` with `regel` | `ki` | `mensch`, saying who judged.

  A message judged not relevant stays in the history as a counter-example.
- **Daily indices** `cs-logs-YYYY.MM.DD` from the index template `cs-logs` (created at startup). The
  document ID is `<quelle>:<ID in the source>`, so a second run overwrites instead of duplicating.
- **Embeddings:** `https://llm-host03.example.com/embed/v1/embeddings` (override:
  `LOG_WATCH_EMBED_URL`). If embedding fails, the document is still written and the missing vector is
  named in the result.
- **Switch:** the global setting `log_watch.historie_aktiv` (on since 2026-11-06 20:44 UTC).
- **What is written:** per relevant hit exactly the number of messages the flood brake allows
  (`Treffer.kontingent_n`, the same decision the count uses). The full message is fetched per source:
  - Graylog, firewall, switches: by the message IDs remembered in the window;
  - Wazuh: by the alert IDs the aggregation returns per rule × agent (`top_hits`);
  - CheckMK: the problem itself is the message (ID `host|service|last_state_change`).

  A message missing at the source is not invented. Each run stores `historie` = requested, found,
  written, rejected, vector missing in its `raster_lauf` row.
- **Catch-up embedding:** `historie.nachbetten()` embeds up to 200 documents without a vector per raster
  run (row `historie` in `raster_lauf`).

### Log watch: ruleset (firewall semantics: forward / block)

The relevance decision works like a firewall (`services/log_watch/regelwerk.py`). Rules are ordered,
and **the first matching rule decides FORWARD (relevant) or BLOCK**. If none matches, the **default
policy BLOCK** applies (N0).

- **What a rule checks:** only hashtags and a few fields, so a rule filters exactly what a person
  filters in the search. Each source sets what it observes as a signal hashtag:

  | Signal | Meaning |
  |---|---|
  | `signal:neu` | new pattern |
  | `signal:anstieg` | spike |
  | `signal:ausbreitung` | spread |
  | `signal:schaden` | damage to resources |
  | `signal:frist` | expiring deadline |
  | `signal:behandelt` | acknowledged or in downtime |
  | `signal:stumm` | muted by an admin |
  | `signal:dringend` | urgent |

- **Conditions** are JSON, ANDed: `hat`, `eines_von`, `praefix` (string or list), `feld` with `ab`/`bis`
  (e.g. Wazuh `level` ≥ 7), and `nicht`. An unknown clause never matches.
- **Standard order** is the agreed precedence:

  N6 stumm → N2 behandelt → N4 Rauschen → R5 Schaden → R6 Sicherheit → N3 Nicht-Produktion → N5 selbst
  erzeugt → R1 Störung in Produktion → R2 neu → R3 Ausbreitung → R4 Anstieg → R8 Frist → N1 Routine → Default N0.

  A test runs all 8 192 combinations of the criteria against the original precedence and gets the same
  verdict every time.
- **The verdict cites** the deciding rule, its criterion first, then further matching criteria with the
  same action.
- **Counted per 5-minute window** in `raster_lauf`:
  - `je_regel`: decisions per rule;
  - `regel_verdraengt`: the rule matched, but an earlier one decided. A rule that is only ever displaced
    is shadowed; this is measured, not assumed;
  - `regel_test`: what rules in test mode would have caught. Test-mode rules never decide.
- **Rules are part of the versioned definition** (column `regeln`). `GET /api/log-watch/definition`
  returns them with the default policy. `POST` accepts a new rule set; IDs, actions, modes, criteria and
  conditions are checked. AI proposals go in as rules in test mode, and an admin activates them.
- **Performance** (measured 2026-11-06): 5.2 µs per verdict, about 1 060 groups per window, so about
  5.5 ms. Compiling the rules to C++ (like Varnish VCL) was considered and deferred: rules as data stay
  verifiable. It comes back up if judging takes more than 50 % of a window.

### Log watch: firewall logs (Palo Alto)

Measured 2026-11-06: about 2.9 million firewall messages per hour, almost all from `nfw062`.

| Kind | Per hour |
|---|---|
| allowed traffic (end/allow) | 2.07 million |
| URL alerts | 525 000 |
| drop | 257 000 |
| deny | 74 000 |
| spyware | about 4 500 |
| sinkhole | 18 |

Reading every line would make no sense; `services/log_watch/firewall.py` splits it:

- **Threats are read one by one.** These are the threat types (`pan_log_subtype`): spyware, virus,
  vulnerability, wildfire, file, scan, flood and data, without "informational", about 600 per
  5 minutes.
  - They are grouped by threat × affected host × action, and the ruleset decides as for every source.
  - Do not select them via `gim_event_type`: "palo_threat" is set on all Palo Alto messages, including
    traffic.
- **Affected host** is the side in an internal zone. A connection from an external zone
  (`firewall_externe_zonen`, default `UNTRUST`) means the target is affected; otherwise the source is.
  The other side is tagged `gegenstelle:`.
- **Hashtags:** `quelle:firewall`, `firewall:`, `bedrohung:`, `aktion:`, `schwere:`, `zone:`,
  `zielzone:`, `zielland:`, `ip:`, `gegenstelle:`.
- **Verdict** (criterion R6, rule `R6-FW`):
  - **relevant** for a sinkhole or severity medium and above;
  - **urgent** for a sinkhole or high/critical;
  - **not relevant (N0)** for a low-severity threat the firewall already blocked, with the reason
    "von der Firewall abgewehrt, niedrige Stufe".
- **Traffic is only counted** per 5-minute window via Graylog pivot:
  - per subtype/action;
  - per zone pair for deny/drop;
  - per destination country for deny/drop.

  The baseline for spikes is building up.
- **One source per message:** Palo Alto messages with level ≤ 4 (sinkholes) also run through the error
  cluster pass. They are still counted there, but the raster does not rate them a second time as
  Graylog.

### Log watch: switch logs (nsa/nss/nsc, Juniper EX)

Measured 2026-11-06: about 241 000 switch messages per hour, of which level ≤ 4 about 16 700 (about
1 400 per 5 minutes). The error-cluster detection excludes switches on purpose.
`services/log_watch/switch.py` gives them their **own pattern learner** (Drain). Its state is stored in
`netz_zustand`, and the known patterns with first and last sighting in `netz_muster`.

- **Input:** `source:(nsa* OR nss* OR nsc*) AND level:<=4`, every line, every window.
- **Before learning, the leading device name is removed** ("nsa429 fpc1 … Link 7 DOWN"). Otherwise
  every switch would get its own pattern, and an outage across many switches would look like many single
  cases.
- **"New" (R2) only counts after a 24-hour learning phase.** During it, the verdict carries "Lernphase
  bis …".
- **Switch vocabulary** (`vokabular.THEMEN_SWITCH`): link-down (including Juniper `Link N DOWN` and
  `SNMP_TRAP_LINK_DOWN`), err-disable, stp, schleife, crc, poe, hardware. Hardware (power supply, fan,
  temperature) counts as damage (R5).
- **Syslog level ≤ 2** is a critical state (`zustand:crit`, urgent).
- **A known pattern without a signal is routine (N1)**, as with Graylog (definition version 4).
- **Hashtags:** `quelle:switch`, `switch:`, `muster:`, `level:`, host tags from CheckMK.

The old network agent (200 messages per hour, judged by LLM) is replaced after a shadow run.

### Log watch: one filter language: objects are saved searches, rules are searches with an action

`services/log_watch/filter.py` is the single language for the search bar, objects, rules, the network
graph and the MCP tools.

| Syntax | Meaning | Example |
|---|---|---|
| `art:wert` | hashtag | `thema:oom` |
| `art:(a OR b)` | one of | `kritikalitaet:(prod OR staging)` |
| `art:wert*` | prefix | `dienst:Check_MK*` |
| `art:"…"` | value with spaces | `mitre:"Credential Access"` |
| `level>=7` | field comparison (`level`, `relevanz`) | `quelle:wazuh level>=7` |
| `@Name` | object | `zustand:crit @Produktion` |
| `!x` / `NOT x` | not | `!signal:stumm` |
| space / `OR` / `( )` | and / or / grouping | `(thema:oom OR signal:schaden) quelle:graylog` |
| word / `"words"` | free text, search only | `"connection refused"` |

- **One parse tree, two translations:**
  - `als_bedingung`: the rule condition, evaluated in memory per verdict;
  - `als_opensearch`: the query over `cs-logs-*`.

  They hit the same messages. A random test compares 400 filters × 300 messages. A live probe against
  the real OpenSearch compared 150 filters × 200 messages and found 0 differences.
- **Objects are named filters (saved searches)**, as on a Palo Alto firewall. They are versioned with the
  definition:

  | Object | Filter |
  |---|---|
  | `Produktion` | `kritikalitaet:(prod OR staging OR critical)` |
  | `Nicht-Produktion` | `kritikalitaet:(test OR dev)` |
  | `Rauschen` | `regel:(5501 OR 5502) OR regelgruppe:web_scan` |
  | `Selbst-erzeugt` | `dienst:Check_MK*` |
  | `Routine-Quellen` | `quelle:(graylog OR switch)` |

  Objects may reference objects. A cycle or an unknown object is rejected, with the reason.
- **Rules are filter texts with an action.** For example R1 = `zustand:crit @Produktion` → FORWARD.
  Free text is not allowed in rules, because rules check hashtags.
- **Compared fields are in the index:** `level` is mapped in `cs-logs-*` and written into the document,
  so search and rule see the same field.

### Log watch: stage 0, Graylog's own drop rules

Graylog pipeline rules with `drop_message()` remove messages at the source, before anything is stored.
The raster never sees them. `services/log_watch/stufe0.py` makes that visible and does so read-only:
changes in Graylog are production and need approval.

**The three drop rules, measured 2026-11-06:**

| Rule | Where | Drops | Per hour |
|---|---|---|---|
| `drop_http_success` | `[proc]routing` stage 6 | `http_response_code < 400` | ≈ 928 000 |
| `drop_container_noise` | stage 9 | container logs without an error word | ≈ 155 000 |
| `drop_cue_zipline_xml` | stage 7 | — | ≈ 2 900 |
| **Total** | | | **≈ 1.09 million** |

- **Counting:** Graylog counts per node, and the load balancer spreads requests across nodes at random.
  The code keeps asking until every node has answered. A missing node is reported as "unvollständig n/3".
  It runs every 15 minutes as a `stufe0` row in the dry run.
- **The cockpit** (`lw_korpus`) shows "verworfen an der Quelle".
- **Contradiction check** (`stufe0.widersprueche`):
  - It reads the keep words from the rule text and reports every topic whose typical line (each topic
    has a `beispiel`) stage 0 would drop from container logs. Example: "connection refused" or
    "x509: certificate has expired" contain no error word.
  - Topics stage 0 keeps through a field are excluded (HTTP 5xx via `http_response_code >= 400`).
- **API (admin):** `GET /api/log-watch/stufe0` returns the rules with source text, location, fields,
  latest count and contradictions.
- **MCP skill `graylog-api`:** `graylog_drop_rules` shows the same for exploration;
  `graylog_get_pipeline_rules` accepts `title` (source text) and `nur_verwerfen`.

### Log watch: AI cockpit (dashboard widgets)

The generative dashboard is the **"🪄 KI-Cockpit"**. Besides the existing widget types, the AI can
place six log-watch widgets. Their data comes from `services/log_watch/cockpit.py`, via the same
`GET /api/dashboard-widgets/{id}/data`.

| Type | Content |
|---|---|
| `lw_kriterien` | verdicts per criterion of the relevance definition and per source ("why") |
| `lw_volumen` | per source and hour: messages read, relevant, would be stored; coverage (windows seen against expected) and failures |
| `lw_hashtags` | top hashtags of relevant messages, with a factor against the previous period (`null` = new) |
| `lw_korpus` | field catalog per status, unmatched hosts per reason, last run per source (`ok`/`veraltet`/`fehler`/`nie gelaufen`), definition version, history switch |
| `lw_befunde` | AI findings as objects: relevance, title, host, time; unfold for the finding, hypotheses with status, checked quotes, the funnel steps and hashtags. Without a finding a named state: how many messages wait and whether the AI funnel is on |
| `lw_ki` | AI analysis statistics per funnel run: queue (open messages, cases), cases analysed, findings, without finding, errors, expired (left the 2 h check window unseen), model seconds per case, queue trend. Comes with `lw_befunde` |
| `lw_netzplan` | network graph of the relevant situation (config `filter`, `knoten`, `hours`). Nodes are hashtags of the result (at most 5 per type: host, finding, topic, service, rule, MITRE …), an edge means both hashtags are on the same message, with the count. Clicking a node adds it to the filter chain, a chip removes it; the list on the right is the search result of the same filter |

**Rules:**
- **Core of the AI cockpit** (code-enforced, fixed order). Whenever there is a finding in 24 h or a
  message waiting for one, two rows come first:
  - row 1: `lw_befunde` (6×5) and `lw_netzplan` with filter `relevanz>=3` (6×5);
  - row 2: `lw_ki` (6×3) and `lw_kriterien` (6×3).

  After that come running error clusters, `lw_korpus` when a source is failing, then what the AI picks.
  Area budget `AREA_BUDGET = 150` cells: the core takes 96, error clusters 48.
- `lw_kriterien` always appears; if the AI leaves it out, code adds it.
- Log-watch widgets do not open the feed on click (there is no feed query behind them).
- `lw_korpus` comes first when a source is not running.
- Only rows of the current raster version are counted; older rows are reported as `alte_fassung`.
- The widgets show counts, not host names.

### Log watch: hashtags from host and source fields

- **From CheckMK, on messages from every source:** `host:`, `ort:`, `kritikalitaet:`, `hg:`, and
  `netz:dmz|intern` with `netzbereich:<folder segment>`.
  - Effective host attributes are used, including inherited ones (`connectors/checkmk.host_aus_config`).
  - The network zone comes from the folder path: a segment containing "dmz". The tag group `networking`
    is practically unused.
- **From Wazuh:** `regel:`, every `regelgruppe:`, `mitre:` (tactic), `mitre-technik:` (T-ID), `label:`
  (agent labels as `key=value`), `decoder:`, `logquelle:`.

### Log watch: AI funnel and findings

The AI only works when a check is true, and it only writes its own analysis.

- **Check** (`trichter.kandidaten`, no AI): messages in the history of the last 2 h matching
  `(relevanz>=3 OR signal:neu) !quelle:(ki OR konsole)` without a `befund:` hashtag, grouped by host.
- **Funnel** (`trichter.analysiere`): one tool loop per case (same `muster:` across hosts, else per host;
  12 steps at most).
- **Agent fleet** (`trichter.agent`). The context lives in the database (host object by search, protocol
  by search), so any agent can take any case.
  - Every minute one agent may start, at most 3 at once (`AGENTEN`), all on the configured model.
  - An agent takes the next free case until the queue is empty or its 480 s budget is used up.
  - Per conversation (host) the work is serial, across conversations parallel. A case leases all its
    hosts in `ki_lease` atomically, or none of them. A lease left by a crashed agent ends after 900 s.
  - The queue is measured separately every 10 minutes (row `ki-pruefung`: open, cases, expired), so
    `verfallen` counts once per cycle and not once per agent. The
  model asks earlier knowledge first, reads facets, narrows the filter and ends with `lw_befund`.
  Every step (tool, arguments, hit count, model seconds) is kept in the finding.
- **Host object as prompt** (`hostobjekt.py`). Before the model works, the worker searches and hands
  over one object per affected host (at most 3):
  - `felder`: CheckMK master data, or "not in CheckMK" with the reason;
  - `lage`: relevant messages with ID and text to quote, plus facets;
  - `nachbarschaft`: graph edges of the host;
  - `gedaechtnis`: earlier findings and the conversation protocol of the last sessions.

  Before this, every analysis spent its first steps (`lw_wissen` → `lw_suche`/`lw_facetten`) gathering
  exactly this: measured on 20 findings, 19.9 s of 62.2 s model time.
- **Conversation protocol** (`gespraech.py`, index `cs-gespraech-YYYY.MM`, strict):
  - Every step of an analysis is one document: the worker's prompt, AI turns, tool results, the
    finding, and later human notes.
  - Each step carries session, step number, role, case, hosts, model and model seconds.
  - Each step also carries the hashtags of the host fields, the case and `befund:<id>`.
  - Loading is a search: `host:<name>` returns every conversation about that host, whatever the
    problem; `netz:dmz` returns conversations about DMZ hosts.
  - It has its own index because the steps are not messages about the network and would distort the
    graph and the statistics.
  - **Console sessions with a reference** (incident, alert or ticket) are written to it as well
    (`konsole.py`): roles `mensch`/`ki`, hashtags `quelle:konsole`, the reference and `host:`.
    - The source is the console's own history, rewritten idempotently after every answer.
    - Sessions without a reference are not stored.
    - The funnel sees them in `gedaechtnis` with `herkunft: konsole`, as context, not as evidence.
- **Tools** (`werkzeuge.py`, one registry for the funnel and the MCP server):

  | Tool | Purpose |
  |---|---|
  | `lw_suche` | hits + facets for a filter (filter language), time range, optional meaning (kNN) |
  | `lw_facetten` | count + facets only |
  | `lw_nachricht` | one message with all original fields |
  | `lw_wissen` | earlier AI findings and console knowledge (`quelle:ki`/`konsole`) |
  | `lw_netzplan` | nodes and edges for a filter (what occurs together — no cause) |
  | `lw_definition` | the relevance definition: criteria, objects, rules |
  | `lw_gespraech` | conversation protocols of earlier analyses for a filter (e.g. `host:x`) |
  | `lw_befund` | the only writing tool: files the finding |

- **A finding is a document** `quelle:ki`, `herkunft:ki` in `cs-logs-*` (`befund.py`). The code writes
  it, not the model, and checks first:
  - every quote must stand verbatim (case and whitespace ignored) in the text of a history document;
    a finding cannot cite a finding;
  - without one checked quote there is no finding; the reason goes back to the model;
  - hashtags only from the evidence, plus `quelle:ki` and `befund:<id>`;
  - hypotheses carry `offen` | `gestuetzt` | `widerlegt`;
  - leftovers of the model's tool-call syntax (`</parameter>` …) are rejected.

  The evidence documents get the same hashtag `befund:<id>` as the finding: one name, so the filter `befund:<id>` finds finding and evidence, and the graph draws the edge.
- **MCP server:** `lw_suche`, `lw_facetten`, `lw_netzplan`, `lw_nachricht`, `lw_wissen` and
  `lw_definition` are MCP tools, calling the same functions. `lw_befund` is not: only the funnel files
  findings, after checking the quotes. The console agents' prompt (`userenv/main.py`) lists the tools.
- **Search** (`suche.py`): one search over `cs-logs-*`: filter + time → hits and facets per hashtag type.
  `netzplan()` builds the graph from two aggregations (`terms` over `hashtags`, then `adjacency_matrix`
  over the chosen nodes), with no documents transferred.
- **API:** `GET /api/log-watch/suche?filter=&stunden=&limit=&bedeutung=` and
  `GET /api/log-watch/netzplan?filter=&stunden=&knoten=` (any logged-in user, read-only).
- **R7 on stored messages:** when a host is relevant in two sources within 20 minutes, the stored relevant
  messages of that host get R7 and relevance 3 afterwards (`historie.markiere_r7`, `update_by_query`).
  The reason says "gemeinsames Auftreten, keine Ursache belegt". Findings are not touched.
- **Switch:** `log_watch.ki_aktiv` (on since 2026-11-06 21:21 UTC). Job `log_watch_trichter` starts an agent every minute (at most 3 at once) and does nothing
  while the switch is off; its result goes to `raster_lauf` row `ki`.

### Check steps and recipes (incidents)

- **Check steps:** removed from the incident view on 2026-11-06. The investigation runs
  through **"Computer, untersuche das"** (Console hand-off). The backend endpoint
  `POST /api/feed/incidents/{id}/run-checks` (switch `error_clusters.checks_enabled`) still
  exists, but nothing in the interface calls it. Results of earlier runs remain in the
  incident's `causal_context` and in the Console hand-off.
- **Recipes:**
  - **Source:** "✓ GELÖST" on a Console session that came from an incident
    (`external_id = incident:<id>`).
  - **Content:** the session's executed tool calls (userenv `/sessions/{sid}/tool-calls`),
    mapped to templates by the AI and validated. Unmatched commands stay text.
  - **Storage:** table `recipes`, keyed by pattern (`LogTemplate.key`) or host, mirrored
    as a cs-knowledge runbook.
  - **Shown:** in the incident, the immediate mail and the Console handoff of the next
    outbreak.

### Separate agent user and SSH gate (block 5, canary users first)

For users listed in `CS_AGENT_SPLIT_USERS` (user ids, `*` = everyone) the Console container
starts as root, and `userenv/gate/cs-split-start.sh` splits it into three users:

| Who | Runs | May |
|---|---|---|
| root | SSH gate `/opt/cs-gate/cs-ssh-gate.py`, holds the key in `/etc/cs-ssh` (0600) | — |
| `agent` (1001) | Hermes, Claude CLI, Codex CLI | no sudo, `NoNewPrivs`, no capabilities, no key; iptables blocks its ports 22/2222/8080 |
| `yolo` (1000) | code-server (Werkbank, terminal) | unchanged, with sudo and its own `~/.ssh` |

- **SSH for the agent:** `ssh`/`scp` are a client of the gate (`/usr/local/lib/cs-agent-bin`).
  The gate checks the caller with `SO_PEERCRED`.
  - Without approval it runs only commands that `cs_policy/remote_allowlist.py` accepts
    as read-only. This is a positive list of programs with sub-command checks; sensitive
    paths are refused even for reading.
  - Anything else needs the write window, until the action list replaces it.
  - Every decision goes to `/var/log/cs-gate/audit.jsonl` (root-only).
  - `scp` only fetches. Interactive sessions are not possible.
- **Workspace:** shared through the group `cs-work`. `.git`, `.vscode` and `ansible` are
  not group-writable: hooks placed there would run with yolo's rights.
- **Backend:** every container exec for agent data goes through `_agent_exec` (uid 1001,
  `/home/agent`). `configure_ssh` writes to the gate and to yolo, never to the agent.

### Write operations need consent (read-only by default)

The Console agent is read-only until a human opens a **write window**. This is enforced in
two places that share **one** approval, stored in **one** location:

| Layer | What it guards | Where |
|---|---|---|
| `userenv/cs-readonly-guard.py` | shell commands that modify a system (service control, package management, remote writes over SSH, …), input typed into a running process, MCP tools of personal connectors (VibeMK, AWX-NG, …) and Playwright interactions | Claude CLI: `PreToolUse` hook (`Bash`, file tools, `mcp__.*`); Codex CLI: `PreToolUse` hook from `/etc/codex/requirements.toml`; Hermes: Python `pre_tool_call` hook (`userenv/main.py`, `_write_guard_hook`) |
| `_require_write()` in `backend/app/api/mcp_server.py` | MCP tools that act outward (see list below) | every guarded tool's first statement |

**One rule file, all three agents.** Hermes used to have no technical block at all, only a
sentence in its prompt; since 2026-10-02 the same guard runs before every Hermes tool call.
It fails closed (a crash, timeout or unreadable answer blocks the call), and no Hermes run
starts while the hook is missing. What the guard looks at beyond plain commands:

- **Indirect remote execution:** a script fed to `ssh` via stdin, a pipe or a heredoc,
  `bash -s`, `base64`/`eval`/`$(…)` inside the remote command, an interactive `ssh host`
  without a command, Python SSH libraries (`paramiko`, `asyncssh`, `fabric`, …).
- **Executed scripts are read:** `bash fix.sh` / `python3 x.py` is judged by the file's
  content, so a local script (allowed to write) cannot become a detour.
- **MCP tools of other servers:** allowed only when the tool starts with a read verb
  (`get`, `list`, `search`, `show`, …); everything else, including tools nobody has
  classified yet, needs the write window. Playwright: navigate, read and screenshot are free;
  click, type, forms, uploads and scripts are not. The CentralStation server checks itself.
- **Visible in the Console:** a blocked call appears as a failed step "⛔ Schreibsperre: …",
  a call made under an approval as "🔓 Mit Schreibfreigabe ausgeführt — …". Every decision
  is logged with its command in `/tmp/cs-readonly-guard.log` inside the container.
- **Codex CLI:** the same guard runs as a `PreToolUse` hook from `/etc/codex/requirements.toml`
  (`userenv/codex-requirements.toml`). Those are admin requirements, so neither the user's
  `config.toml` nor a CLI flag can lift them. Codex sends Claude's dialect (`Bash`,
  `mcp__srv__tool`) plus `apply_patch`, whose targets must lie in the workspace or `/tmp`.
  Codex keeps `sandbox_mode = "danger-full-access"`, because bwrap does not work in the
  container; the hook, not the sandbox, enforces read-only.
- **Agents cannot reconfigure themselves:** shell writes to `~/.codex`, `~/.claude`,
  `~/.hermes`, `~/.ssh` and the shell profiles need the write window.

Guarded MCP tools: `jira_add_comment`, `jira_update_issue`, `jira_transition_issue`,
`jira_close_issue`, `create_jira_ticket`, `acknowledge_alert`, `run_remediation`,
`gitlab_create_branch`, `gitlab_create_merge_request`. Read tools are never affected.

**Python packages are the deliberate exception.** The agent may run `pip install` into
its own virtualenv (`/home/yolo/pip/venv`, on the `cs-pip-{uid}` volume, first on PATH)
without asking — it sometimes needs a library to evaluate anything at all, and that venv
belongs to the agent and touches no system. Still blocked: `sudo pip`, `/usr/bin/pip`,
`--break-system-packages`, `--target` into a system directory, and any `pip install` on a
remote host over ssh. Other package managers (`npm`, `gem`, `cargo`, `apt`) stay blocked.
The policy is pinned by `backend/tests/test_readonly_guard.py`.

Both CLI agents are told this in the same words: `agent_instructions()` in
`userenv_manager.py` produces one text, written as `~/.claude/CLAUDE.md` for Claude and
`$CODEX_HOME/AGENTS.md` for Codex. Hermes gets the same content through its system prompt.

**How consent is given:** the operator clicks **"Schreibzugriff freigeben"** in the Console
(`POST /api/computer/write-approval`, 1–120 minutes). The backend writes
`/opt/cs-write-approval.json` into the user's container as root; the agent runs as `yolo`
and cannot create or extend it. **Agreement typed into the chat does not count** — the agent
controls the chat, so consent has to arrive through a channel it does not own. A refused tool
returns the reason plus the exact next step, so the agent asks instead of retrying.

**How the user is identified:** Hermes, Claude CLI and Codex send `X-CS-User-ID` with every
MCP call (`userenv_manager.write_hermes_config`, `configure_claude_credentials`,
`_codex_config_toml`). Without it, no approval can be looked up and the tool refuses.

**Limit, stated plainly:** the MCP endpoint is not authenticated on the internal Docker
network and the user ID is self-declared. This stops an over-eager agent, not a malicious one —
the latter could call the Jira API directly. The goal is that *accidental* action is impossible.

### Ticket context does not start the agent

Handing a ticket to the Console splits the old single prompt in two: the **ticket
content** is attached to the session, and the **task** ("Analysiere das Ticket und
schlage konkrete nächste Schritte vor …") goes visibly into the input field, where it
can be read and edited. Two buttons on the context bar offer both ways —
**LEG LOS** sends it as it stands, **BEARBEITEN** hands over to the input field. Either
way the agent starts only on send.

Handing a ticket to the Console — the first handoff from the Jira view, and
**"ALS KONTEXT ANHÄNGEN"** on the activity banner — **attaches** the text to the session.
It does not go to the agent and it does not go into the input field: a wall of text there
would have to be cleared away before the operator could type their own question.

The attachment is shown as a named bar above the input ("geht mit deiner nächsten Nachricht
mit"), can be expanded to read in full, and can be discarded. It travels with the **next
message the operator writes**, prepended to it. In the transcript it then appears as an
ordinary message — the ticket context rendered as Markdown, a rule, then the operator's own
line. That is exactly what the agent received, and exactly what comes back from the agent's
own history after a reload, so the live view and the reloaded view cannot drift apart.

Once attached, the activity banner and the header badge disappear for **that** state of
the ticket: the context bar above the input shows the same fact and says what happens next,
and leaving the banner up would let the same comment be attached a second time. A comment
arriving afterwards has a different activity version, so it announces itself again.

Two or more attachments on one session are appended, not replaced — otherwise the first
change would be lost while the banner claimed it had been taken over.

**When a comment counts as read.** The Jira state is acknowledged
(`POST …/ticket-activity/ack`) as soon as the operator acts on it — clicking
**ALS KONTEXT ANHÄNGEN** or **Später** on the activity banner. Only a comment that arrives
*after* that announces itself again; later changes to the ticket, including the operator's
own comments, do not bring an already-seen comment back.

This used to be tied to the successful send of the message carrying the context, which
turned out to be the wrong moment: a failed turn, a reload or a restart lost the in-memory
"attached" mark while the database still said "unread", so a comment could stay "new" for
a week. A failed acknowledgement is now reported in the UI instead of only in the console.

### One workspace, shared with the Werkbank

The Console agents run **in** `/home/yolo/workspaces` — the same folder code-server
opens in the Werkbank. A file the agent writes is immediately editable in the browser
IDE, and edits made there are what the agent reads back. Relative paths in the Console
mean that folder.

It is a bind mount from the host (`IDE_WORKSPACES_BASE/<uid>/workspaces`), so it also
survives container recreation — unlike the container layer, where files written to
`/app` used to land.

`CS_AGENT_CWD` overrides the directory; if it cannot be created the agent falls back to
its previous working directory rather than failing to start.

### Moving files between servers

`ssh`, `scp`, `sftp` and `rsync` are in the image and use the same user and key.
Fetching from a host is reading and is always allowed; **pushing to one is a change to
that host** and needs a write window like any other outward action. `rsync` has to
exist on both ends — where it does not, `scp` and `sftp` still work.

### Attaching files and images

Files can be attached to the next message three ways — the **paperclip** next to the
microphone, **drag-and-drop** onto the conversation, or **Ctrl+V** (screenshots as well as
files copied in the file manager). All three go through one endpoint,
`POST /api/computer/uploads`, and land in the shared workspace under
`.console-uploads/<YYYY-MM>/`. Nothing large travels through the chat protocol; the message
carries a marker line with the path:

```
[Bilder: /home/yolo/workspaces/.console-uploads/2026-09/<id>.png]
[Dateien: /home/yolo/workspaces/.console-uploads/2026-09/<id>-umsatz_q3.xlsx]
```

Images and other files are kept apart because the agents treat them differently: images
reach Codex as `-i` and Hermes as an image block, while files are opened by the agent
itself. The readers it needs — `pandas`, `openpyxl`, `xlrd`, `pypdf` — are baked into the
image as Debian packages, because the agent's venv sees `/usr/lib/python3` but not
`/opt/venv`.

**What is accepted** is decided by `app/services/upload_policy.py`, from the extension
**and** the content, never the browser's content-type: spreadsheets (xlsx, xlsm, xls, ods,
csv, tsv), documents (docx, doc, odt, rtf, pptx, ppt, odp), PDF, text and data files, logs,
configuration, scripts, archives (zip, gz, tgz), and images. A `.xlsx` must be a ZIP, a
`.pdf` must start with `%PDF-`, a `.csv` must contain no NUL byte — so a renamed executable
is refused with a stated reason. Limits: 20 MB per file, 10 MB per image (images go into
the LLM request as a data URL for Hermes).

Stored names are `<uuid>-<cleaned original name>`, without spaces — the marker separates
paths with spaces, and the agent would otherwise have to quote every path in the shell.

**Displaying them.** The endpoints need a Bearer token, which an `<img src>` or a plain
download link does not send, so the frontend fetches attachments with the token and shows
them as blob URLs. Non-image files are only ever served as `application/octet-stream`
downloads — an uploaded `.html` would otherwise run inside CentralStation's origin.

Because every file lands in the workspace it is also editable in the Werkbank, and
`jira_add_attachment` can put it on a ticket without extra code.

### Ticket attachments

The agent writes the file into its workspace and then names the path — the content
never travels through the chat:

```
ssh host 'journalctl -u foo --since -2h' > /home/yolo/workspaces/foo.log
jira_add_attachment(issue_key="IMIT-1234", file_path="foo.log",
                    filename="foo-journal-2026-09-18.log")
```

This works because `IDE_WORKSPACES_BASE/<uid>/workspaces` is mounted into **both**
containers — the agent sees it as `/home/yolo/workspaces`, the backend at the host
path. The user is identified by the same `X-CS-User-ID` header as the write approval,
which is also what scopes the lookup to that user's workspace.

Only files under that workspace can be attached. The path is resolved with
`os.path.realpath`, so `..` and symlinks pointing outside are refused rather than
followed — the backend has far more of the host mounted than the agent does, so this
check is the boundary, and `backend/tests/test_mcp_attachment_paths.py` pins it.
Uploads are capped at 25 MB and need a write window like every other outward action.

### Browser automation (Playwright MCP)

All three agents share a **Playwright MCP** server (`playwright-mcp`, stdio) baked into the
`cs-userenv` image together with a headless Chromium browser. It gives the Console agents the
23 `browser_*` tools (navigate, click, type, snapshot, evaluate, screenshot, …) so they can
drive a real browser — open a dashboard, read a web UI, fill a form — from inside the container.

- Image: `@playwright/mcp` (global) + `npx playwright install chromium` into
  `PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright`; a stable `chrome-stable` symlink is passed via
  `--executable-path` (the `--browser chromium` default maps to the missing "chrome-for-testing"
  channel). Spawned with `--headless --no-sandbox --isolated`.
- Wiring per agent (`userenv_manager.py`): Hermes → `mcp_servers.playwright` (command+args) in
  `hermes_config.yaml`; Claude CLI → `claude mcp add --scope user playwright -- playwright-mcp …`;
  Codex CLI → `[mcp_servers.playwright]` in `$CODEX_HOME/config.toml`.

### Agent selection

The Computer Console supports three agent backends, configurable per user under **Settings → Console** or during the Setup Wizard:

| Agent | Description | Auth |
|-------|-------------|------|
| **Hermes** (default) | CentralStation's own Python AI agent; uses the active LLM from general AI settings; full MCP tool access | — (uses system LLM) |
| **Claude CLI** | Your own Claude account (claude.ai); persistent sessions via `--session-id`; `--permission-mode acceptEdits` for headless operation; credentials also configure the Claude Code VS Code extension in the Werkbank | Claude OAuth (PKCE) |
| **Codex CLI** | Your own ChatGPT account (GPT-5.x); custom ChatGPT backend provider; session resume via `codex exec resume`; credentials also configure the Codex VS Code extension in the Werkbank | ChatGPT Device Code OAuth |

**Setting up a CLI agent:**
1. Go to **Settings → Console** (or the Setup Wizard "Console Agent" step)
2. Click **Connect Claude Account** or **Connect ChatGPT Account** — a browser-based OAuth flow runs, no CLI needed
3. The token is stored encrypted in the database and auto-injected into the user's container on every session start

**Session persistence:**
- Hermes: history in `state.db` (host-mounted); survives container restarts
- Claude CLI: JSONL in `~/.claude/projects/.../<session-id>.jsonl` on the `cs-ide-cfg-{uid}` named volume; survives container restarts — full conversation context is restored automatically
- Codex CLI: internal session ID captured from JSONL stream; resume via `codex exec resume`; history is in-memory per container lifetime (lost on container restart)

**Session list:** `GET /api/computer/sessions` returns an `agent_type` field (`hermes` | `claude_cli` | `codex_cli`) for each session. The UI shows a `[CL]` / `[CO]` badge next to Claude/Codex sessions.

### MCP tools available to all agents

**`backend/app/services/mcp_server.py`** exposes CentralStation tools as an MCP server:

| Tool | Description |
|------|-------------|
| `get_bridge_status()` | Overall monitoring status |
| `list_alerts(severity, source, hours)` | Filtered alert list |
| `search_feed(query)` | Lucene query against all feed indices |
| `get_checkmk_host(hostname)` | Host status and services |
| `acknowledge_alert(alert_id)` | Acknowledge an alert |
| `create_jira_ticket(title, description, priority)` | Create a Jira ticket |
| `jira_add_attachment(issue_key, file_path, filename)` | Attach a workspace file to a ticket |
| `jira_list_attachments(issue_key)` | List a ticket's attachments |

Two MCP transports are mounted simultaneously:
- `/api/mcp/sse` — legacy SSE (used by Hermes)
- `/api/mcp-http/` — streamable-http (used by Claude CLI and Codex CLI native MCP clients)

Personal connectors (MCP-type ConnectorConfigs) and AWX-NG connectors are automatically included in both Hermes config and Codex/Claude config.toml.

### Computer diagnoses alerts

When the user clicks **"Computer"** on a News Feed alert, `GET /api/feed/{id}/hermes-context` runs automatically:
1. Looks up the alert in OpenSearch to get host, severity, container name
2. Runs `run_diagnostics(host)` — CheckMK status, recent logs, metrics, topology, past incidents
3. Adds **AI error-clusters** for the host (`search_recent_ai_clusters`) so the agent sees the shared root cause if the host is part of a larger correlated incident (see [Error clusters](#error-clusters-root-cause-grouping))
4. Searches for **past AI-resolved similar alerts** (see below) and prepends them as context
5. Returns a structured prompt that is sent directly to the agent session

### Computer learns — AI resolution notes

When the operator clicks **✓ RESOLVED** in the Computer panel:
- The conversation is summarised into a 2–3 sentence English lesson-learned note via LLM
- The note is saved as an `AlertComment` (kind=`ai`) on the alert timeline
- The OpenSearch document is updated with `has_ai_resolution: true` and `ai_resolution_text`
- Future diagnostics for similar problems automatically retrieve this note via `search_ai_resolved()`

### OpenSearch tags

Every indexed alert automatically gets a `tags` keyword array for precise filtering:

| Tag source | Examples |
|------------|---------|
| Alert source | `graylog`, `checkmk`, `wazuh` |
| Severity | `critical`, `high`, `medium`, `low`, `info` |
| Container presence | `docker` |
| Service keywords in title/container | `nginx`, `postgres`, `redis`, `cue`, `zipline`, `keycloak`, `ssl`, `dns`, `backup`, … |
| OS keywords | `linux`, `windows` |
| Symptom keywords | `oom`, `disk`, `cpu`, `network` |
| AI resolution | `ai_resolved` (added when Computer marks problem solved) |

**Example OpenSearch queries using tags:**
```
tags:postgres                       → all PostgreSQL-related alerts
tags:docker AND tags:critical       → critical container alerts
tags:ai_resolved                    → all problems the Computer has solved
tags:oom                            → out-of-memory events
```

### Setup

1. Enable **Computer Console** for a user in Admin → Users.
2. The `cs-userenv-{uid}` container starts automatically on the user's first session (no separate `docker-compose.yml` entry needed — the backend manages the lifecycle via the Docker SDK).
3. **Optional:** Connect a CLI agent in Settings → Console or the Setup Wizard. Hermes works out of the box using the system LLM.
4. **Optional:** Add personal MCP servers (VibeMK, AWX-NG, etc.) under Settings → Connectors; they are automatically included in the agent's tool list.

### System prompt

The Hermes system prompt (`userenv/main.py:SYSTEM_PROMPT`) defines the default agent behaviour:
- Uses MCP tools for all IT queries (never local shell)
- Searches Graylog (via `search_feed`) for container logs — no SSH needed
- Appends `[FEED:host=<hostname>]` markers that the frontend renders as clickable feed-filter buttons

---

## Werkbank (Web-IDE)

The **Werkbank** (`/workbench`) is a full VS Code instance in the browser — one
[code-server](https://github.com/coder/code-server) container per user, started on
demand by the backend (`ide_manager.ensure_container`). It is the place to write code,
run commands in an integrated terminal, use Git/GitLab, and edit Ansible playbooks.

### Architecture

```
Browser  ──/ide/<uid>/──▶  nginx  ──auth_request──▶  backend /api/ide/authz
                            │  (validates the session cookie, returns the
                            │   per-user upstream in X-IDE-Upstream)
                            ▼
                       cs-ide-<uid>  (code-server, --auth none, no published port)
```

- **No published host port.** The nginx `auth_request` gate is load-bearing: every
  request to `/ide/<uid>/` (and the Claude Code WebSocket at `/ws`) is authorised by
  the backend before being proxied to the user's container.
- **Per-user bind mounts** under `IDE_WORKSPACES_BASE/<uid>/` (workspaces + VS Code
  state) so a single `tar`/`rsync` of that directory backs up everything. Claude Code
  credentials live on a separate named volume.
- **SSH** to `*.example.com` is wired up from the host `~/.ssh` mount (marvin key)
  by the entrypoint.

### Bundled AI coding-agent extensions

The image (`Dockerfile.codeserver`) pre-installs two extensions from Open VSX into a
staging dir `/opt/cs-extensions` (Claude Code `Anthropic.claude-code`, OpenAI Codex
`openai.chatgpt`). Because `/root/.local/share/code-server` is a **per-user bind mount**
at runtime, build-time-installed extensions there would be masked — so the entrypoint
**seeds** them into each user's extensions dir on first start (idempotent; existing
extensions are left untouched). The entrypoint also applies several Claude-Code
compatibility patches (CSP font/style, manual OAuth flow, navigator shim, webview CSS
inlining) needed to run the extension inside a proxied code-server.

### Ansible

The shared `ansible/` directory follows the standard Ansible layout and is bind-mounted
into every IDE container at `/root/workspaces/ansible` (same host path that AWX mounts as
its Manual SCM project root):

```
ansible/
  ansible.cfg          # roles_path=./roles, inventory=./inventory/hosts
  inventory/hosts      # static inventory placeholder (AWX usually supplies its own)
  group_vars/all.yml   # vars for all hosts (group_vars/<group>.yml per group)
  host_vars/           # per-host vars: host_vars/<hostname>.yml
  roles/               # roles (auto-discovered via roles_path)
  playbooks/           # the playbooks + .cs-validate.py
```

Edit anything in VS Code → **AWX sees the change immediately**, no Git sync. AWX job
templates reference playbooks as `playbooks/<name>.yml` (relative to the project root).
The folder appears directly in the VS Code Explorer. (`IDE_ANSIBLE_PATH` env on the
backend; see [Maschinenraum](#maschinenraum-ansible-remediation).)

> **Note:** existing `cs-ide-*` containers are reused as-is; after changing the image
> or the mount set, remove the user's container (`docker rm -f cs-ide-<uid>`) so the
> backend recreates it with the new image, the playbook mount and the seeded extensions.

### Hermes → Werkbank handoff

From the Computer Console the current Hermes analysis can be exported as an editable
Markdown file into the user's workspace (`POST /api/ide/open-chat`), and a `CLAUDE.md`
is injected so Claude Code in the terminal starts with full context.

---

## Maschinenraum (Ansible Remediation)

The **Maschinenraum** (`/engineering`) is the ops cockpit for AI-assisted, Ansible-based
remediation with a human in the loop. AWX (`docker-compose.awx.yml`) executes the
playbooks; CentralStation orchestrates matching, approval and learning.

### Remediation loop

```
alert ─▶ playbook_author (LLM drafts a playbook)
     ─▶ publish_playbook → AWX Job Template (description + labels + survey)
     ─▶ remediation_matcher: deterministic label pre-filter, then LLM fallback
     ─▶ human approves (Pending → Active)  ─▶ AWX runs the job
     ─▶ remediation_learning: OpenSearch + AlertComment + AIKB runbook
```

The Maschinenraum has Pending / Active / History / Catalog views for the
alert-triggered remediations.

### cs-meta convention

Every playbook carries a commented YAML metadata block in its header — the single
source of truth, parsed by `playbook_meta.parse_meta()` and synced into the AWX Job
Template (`description`, `labels` from `matches`, `survey` from `params`):

```yaml
# ─── cs-meta ───────────────────────────────────────────────
# id: disk-resize
# title: Disk vergrößern (LVM)
# description: Vergrößert ein LVM Logical Volume und das Dateisystem online
# matches: ["checkmk:Filesystem*", "no space left on device"]
# target: linux            # linux | windows | network | generic
# risk: medium             # low | medium | high
# params:
#   - {name: lv_path,  example: "/dev/vg0/root"}
#   - {name: add_size, example: "+10G"}
# ─── /cs-meta ──────────────────────────────────────────────
```

Required fields: `id, title, description, matches, target, risk`. The `matches` field
answers *"which alerts does this playbook handle"* — so the matcher uses an exact label
pre-filter before falling back to the LLM. Authors write/validate playbooks in the
Werkbank (`.cs-validate.py`, `ansible-lint`); the `~/skills/ansible-playbooks/SKILL.md`
skill documents the convention.

---

## CheckMK Metrics Collector

### Architecture

An APScheduler job (`run_metrics_collection`) runs every 5 minutes:
1. Finds all hosts with active WARN/CRIT problems via CheckMK `get_problems()`
2. Fetches standard metrics (CPU load, memory, disk, cmk_time_agent) per host via `get_graph_data()`
3. Writes the latest data point as an OpenSearch document into `cs-metrics-checkmk`

**Index:** `cs-metrics-checkmk` — fields: `host`, `service`, `metric`, `value`, `unit`, `timestamp`

### AI correlation

During the `rag_lookup` step the AI agent reads current metric points for the affected hosts from `cs-metrics-checkmk`. This lets the LLM see patterns like *"CPU was 94% → 5 min later OOM kill → CheckMK alert"* in one context instead of searching for them separately.

### AI War Room: blast radius

On critical/high alerts a blast-radius analysis is started automatically (`blast_radius.py`):
- **Site** of the host via ID-Generator (`resolve_host_to_location()`, uses `virt_servers` SQL)
- **Co-located VMs** on the same physical host via NetBox (`get_vm_host()`)
- **Co-located hosts** at the same site via `cs-metrics-checkmk`
- The blast radius is passed to the LLM as context → causal narrative possible

---

## Prometheus Metrics & PromQL

### Lucene → PromQL converter

In the dashboard, **Add widget → Time series → PromQL converter**:

| Input | Generated PromQL (example) |
|-------|----------------------------|
| `CPU usage docker086` | `100 - (avg(rate(node_cpu_seconds_total{instance="docker086:9100",mode="idle"}[5m])) * 100)` |
| `memory docker086` | `100 * (1 - node_memory_MemAvailable_bytes{instance="docker086:9100"} / node_memory_MemTotal_bytes{instance="docker086:9100"})` |
| `network traffic srv023` | `rate(node_network_receive_bytes_total{instance="srv023:9100"}[5m])` |
| `disk` | `100 * (1 - node_filesystem_free_bytes / node_filesystem_size_bytes)` |

### CheckMK metrics — integration direction

> **Important:** CheckMK does **not export** to Prometheus. The native Prometheus integration is one-way — CheckMK *scrapes* Prometheus (CheckMK as a consumer). Native metric export only goes to InfluxDB/Graphite.

Therefore there are two real ways to get CheckMK performance data into CentralStation:

**Option A – CheckMK RRD via REST API (used):**
`CheckMKConnector.get_graph_data()` pulls RRD time series via `/domain-types/metric/actions/get/invoke`. The `timeseries` widget can access it directly with `data_source: "checkmk"` — **no Prometheus needed**.

**Option B – node_exporter → Prometheus (optional, for host metrics):**
```bash
# Ansible deploy on all hosts:
ansible all -m apt -a "name=prometheus-node-exporter state=present" -b
ansible all -m service -a "name=prometheus-node-exporter enabled=yes state=started" -b
```
Prometheus then scrapes the node_exporters and the `timeseries` widget uses `data_source: "prometheus"` with PromQL.

**Forecast:** CheckMK CEE has **no** forecast REST endpoint (only a GUI dashlet). CentralStation therefore implements its own linear regression on the historical RRD data: `get_forecast_data()` fetches 72h of history, projects it via linear regression + computes a ±1σ confidence band. Result: `series_history`, `series_forecast`, `confidence_band`.

---

## Connectors

### Global connectors (admin)

Global connectors apply to all users and are used by the background agents.

| Type | Auth method | Required credential fields |
|------|-------------|----------------------------|
| `checkmk` | Bearer `<user> <password>` | `username`, `password`, optional: `site` (CheckMK instance name) |
| `graylog` | Basic Auth | `username`, `password` |
| `wazuh` | JWT (own auth) | `username`, `password`, `indexer_url`, `indexer_username`, `indexer_password`, optional: `excluded_rule_ids`, `excluded_fim_paths` |
| `icinga2` | Basic Auth (ApiUser) | `username`, `password` (API port 5665) |
| `prometheus` | optional Basic/Bearer | `username` (optional), `password` (optional) |
| `netbox` | Bearer Token | `api_token` |
| `id_generator` | Basic Auth | `username` (`idgen_reader`), `password` |

### Personal connectors (per user)

Personal connectors are created by each user in the setup wizard or under **Settings → Connectors → My Connectors**.

| Type | Auth method | Description |
|------|-------------|-------------|
| `jira` | Bearer Token | Jira access (tickets, Kanban sync) |
| `jira_sd` | Bearer Token | Jira ServiceDesk (separate token possible) |
| `o365` | OAuth2 device code flow | Microsoft 365 emails via Graph API |
| `teams` | OAuth2 device code flow | Microsoft Teams channel messages |

**Setting up Microsoft connectors (O365/Teams):**
1. Create the connector with the Azure **tenant ID** and **client ID** (existing app registration, no admin needed)
2. Click **"Sign in with Microsoft"** → a device code is shown
3. Open `microsoft.com/devicelogin`, enter the code, sign in
4. The connector is saved automatically with a `refresh_token`
5. The token is refreshed automatically

**Connector priority with multiple connectors of the same type:**  
A personal connector always takes precedence over the global admin connector.

### Connector actions

- **Test connection**: `POST /api/connectors/{id}/test` — checks reachability and auth
- **Delete connector**: admins can delete any connector; users can delete their personal connectors (`DELETE /api/connectors/my/{type}`)

---

## Writing your own connectors (Connector SDK)

CentralStation is built around a small, clearly defined connector interface. A new
connector — whether monitoring, ticketing or inventory — needs only **4 steps**. This
section is meant as an **LLM skill**: an AI agent (Claude CLI, Codex, your own agent) can
load it as context and produce a working connector in one pass.

### Architecture at a glance

```
ConnectorConfig (DB, Fernet-encrypted: base_url + credentials)
      │
      ▼
get_connector(type, base_url, credentials)      # factory  (connectors/__init__.py)
      │
      ▼
class MyConnector(BaseConnector)                # your class (connectors/my.py)
   ├── test_connection() -> ConnectorTestResult # required: reachability + auth
   └── get_problems() / get_alerts() / ...      # data method(s)
      │
      ▼
collect_my(connector, time_range_minutes)       # mapping → feed-alert dicts (alert_aggregator.py)
      │
      ▼
cs-feed-{source} (OpenSearch)  →  Feed · Bridge · Dashboard · AI agent
```

### Step 1 — Connector class

`backend/app/services/connectors/<type>.py`. Extends `BaseConnector` (provides `self.base_url`,
`self.credentials`, `self._client()` with `verify=False` for self-signed certs).

```python
from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector


class MyConnector(BaseConnector):
    def _headers(self) -> dict:
        # build auth from the (decrypted) credentials
        token = self.credentials.get("api_token", "")
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    async def test_connection(self) -> ConnectorTestResult:
        """REQUIRED: checks reachability + auth. Used by the 'Test connection' button."""
        try:
            async with self._client() as client:
                r = await client.get(f"{self.base_url}/api/status", headers=self._headers())
            r.raise_for_status()
            return ConnectorTestResult(success=True, message="MySystem reachable")
        except Exception as e:
            return ConnectorTestResult(success=False, message=str(e))

    async def get_problems(self) -> list[dict]:
        """Data method: returns open problems in the UNIFIED schema (see below)."""
        async with self._client() as client:
            r = await client.get(f"{self.base_url}/api/problems", headers=self._headers())
        r.raise_for_status()
        out = []
        for p in r.json().get("items", []):
            out.append({
                "severity": _map_severity(p["state"]),   # critical|high|medium|low|info
                "host":     p["host"],
                "service":  p["service"],
                "output":   p.get("plugin_output", ""),
                "acknowledged": bool(p.get("acknowledged")),
                "last_state_change": p.get("last_state_change"),
                "host_address": p.get("address", ""),
                "metadata": {"os": p.get("os", ""), "location": p.get("site", "")},
            })
        return out
```

**Unified problem schema** (as expected by the aggregator):

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `severity` | `critical\|high\|medium\|low\|info` | ✓ | normalized severity |
| `host` | str | ✓ | hostname (correlation/filter key) |
| `service` | str | ✓ | affected service/check |
| `output` | str | – | plugin/status text |
| `acknowledged` | bool | – | acknowledged? |
| `last_state_change` | epoch/ISO | – | time of the state change |
| `host_address` | str | – | IP |
| `metadata` | dict | – | `os`, `location`, `criticality`, `ve` … (CheckMK filters apply to this) |

### Step 2 — Register in the factory

`backend/app/services/connectors/__init__.py` → import + mapping entry:

```python
from app.services.connectors.my import MyConnector
mapping = { ..., "my": MyConnector }
```

`backend/app/api/connectors.py` → add to `VALID_TYPES` (`"my"`). Optionally add it to
`USER_MANAGED_TYPES` if every user (not just admins) may create the connector.

### Step 3 — Collector in the aggregator

`backend/app/services/alert_aggregator.py` — maps the connector output to feed-alert dicts:

```python
async def collect_my(connector: ConnectorConfig, time_range_minutes: int = 60) -> list[dict]:
    from app.services.connectors.my import MyConnector
    creds = decrypt_credentials(connector.encrypted_credentials)
    svc = MyConnector(base_url=connector.base_url, credentials=creds)
    items = await svc.get_problems()
    return [{
        "source": "my",
        "severity": i["severity"],
        "title": f"{i['host']} — {i['service']}",
        "body": i.get("output", ""),
        "external_id": f"my:{i['host']}:{i['service']}",   # STABLE dedup key!
        "external_url": f"{connector.base_url}/host/{i['host']}",
        "metadata": {**(i.get("metadata") or {}), "host": i["host"], "service": i["service"]},
    } for i in items]

# further down, in the _COLLECTORS map:
_COLLECTORS = { ..., "my": collect_my }
```

**`external_id` is the most important value**: a stable, deterministic dedup key across all runs
(e.g. `my:host:service`). Deduplication, incident correlation, claim/status and timeline all depend on it.

### Step 4 — Frontend form fields (optional)

`frontend/src/app/features/settings/connectors/connector-form/` → extend `CRED_FIELDS` with the
fields of the new type (e.g. `api_token`, `username`/`password`). Without an entry the connector
does not appear in the creation dialog (but can still be created via the API/seed).

### Checklist

- [ ] `MyConnector(BaseConnector)` with `test_connection()` + data method
- [ ] registered in the `get_connector()` factory + `VALID_TYPES`
- [ ] `collect_my()` + `_COLLECTORS` entry in the aggregator
- [ ] `external_id` is stable and deterministic
- [ ] severity normalized to `critical|high|medium|low|info`
- [ ] (optional) frontend `CRED_FIELDS`
- [ ] `POST /api/connectors/{id}/test` green

> **Reference implementations:** `checkmk.py` (Bearer, monitoring → `get_problems`),
> `wazuh.py` (JWT login, security → `get_alerts`), `graylog.py` (Views API, logs),
> and `icinga2.py` (Basic Auth, monitoring → `get_problems`) as a complete worked example
> built exactly according to this guide.

---

## User Management and RBAC

### Roles

| Role | Scope | Restrictions |
|------|-------|--------------|
| `admin` | everything: user management, connectors, settings, audit log | — |
| `sysadmin` | all alerts (CheckMK/Wazuh/Graylog general), Kanban, Jira, AI Insights, Feed | no connector/user management |
| `network` | Graylog switch alerts (nsa*/nss*/nsc*), NetBox, ID-Generator, network Kanban | no SysAdmin alerts, no Wazuh, no connector config |
| `viewer` | read access to own area | no write operations |

### User preferences

| Preference | Description |
|------------|-------------|
| `checkmk_locations` | CheckMK locations for feed filtering (Single Source of Truth) |
| `checkmk_ve` | virtualization environment filter |
| `checkmk_criticality` | criticality filter |
| `checkmk_os` | operating system filter |
| `checkmk_hostgroups` | host group filter |
| `feed_disabled_search_ids` | disabled saved searches |
| `ticket_seen_map` | JSON `{jira_key: ISO time}` — ticket badge tracking (replaces localStorage) |
| `feed_checkmk_min_age_minutes` | CheckMK minimum age (hide very recent items) |
| `feed_sources_enabled` | which sources are shown in the feed |
| `feed_teams_channels` | Microsoft Teams channel IDs for the personal feed |
| `o365_mailbox` | O365 mailbox address |
| `o365_folder` | O365 folder (default: `Inbox`) |
| `jira_project` | default Jira project |
| `sla_notify_p1_minutes` | SLA notification threshold P1 |
| `sla_notify_p2_minutes` | SLA notification threshold P2 |

---

## Settings and Preferences

### Global settings (Admin → Settings)

All settings are stored encrypted in the database and managed via `GET/PATCH /api/settings`.

**Language:**
- `ui_language` — per-user UI and AI response language (`en` default, `de`)

**LLM configuration:**
- `llm.provider` — `custom` (local endpoint, default) or `openai-codex` (OAuth, no API key needed)
- `llm.base_url`, `llm.model`, `llm.api_key` — for the `custom` provider
- `llm.codex_model` — for the `openai-codex` provider (e.g. `gpt-5.5`)
- `llm.vision_model_url`, `llm.vision_model`
- `llm.thinking_mode` (extended thinking, default `false`)

**Agent configuration:**
- `agent.auto_enrich` — automatic AI enrichment after aggregation
- `agent.interval_minutes` — background agent interval
- `agent.auto_create_jira` — create tickets automatically
- `agent.jira_severity_threshold` — minimum severity for auto-ticketing

**Web search:**
- `searxng.base_url` — SearXNG web search

### Fetch filter values

`GET /api/feed/checkmk-filter-values` — returns the available values for all CheckMK filter dropdowns (OS, location, VE, criticality, host groups) directly from OpenSearch.

---

## API Reference

### Authentication

| Pfad | Methode | Beschreibung |
|------|---------|-------------|
| `/api/auth/login` | POST | Login; gibt `access_token` + setzt `refresh_token` HttpOnly-Cookie |
| `/api/auth/refresh` | POST | Access Token erneuern via Cookie |
| `/api/auth/logout` | POST | Refresh Token revoken |
| `/api/auth/me` | GET | Eigenes User-Profil (prüft Token-Gültigkeit) |

**Token-Lebensdauer und Persistenz:**
- Access Token ist **8 Stunden** gültig (konfigurierbar via `access_token_expire_minutes`)
- Token wird im Browser unter `localStorage['cs_access_token']` gespeichert — überlebt Seiten-Reload und Browser-Neustart ohne erneutes Login
- `AuthService` liest Token beim Angular-Start aus `localStorage` → `isLoggedIn()` ist sofort `true`
- Fehlt das User-Profil (z.B. neuer Tab), wird es via `GET /auth/me` nachgeladen; schlägt das fehl, wird Cookie-basierter Silent-Refresh versucht
- Logout löscht Token aus `localStorage` und revoked das Refresh-Cookie

### Hosts

| Pfad | Methode | Beschreibung |
|------|---------|-------------|
| `/api/hosts/{hostname}/health` | GET | Performance-Vitals (gecacht); `?live=true` für CheckMK-RRD-Refresh |
| `/api/hosts/{hostname}/services` | GET | Alle CheckMK-Services mit `state_label` + `summary`; sortiert nach Schweregrad |
| `/api/hosts/{hostname}/graph` | GET | 24h-Zeitreihe: `?service=<name>&metric=<id>` → `{series, title, unit}` |

### Feed

| Pfad | Methode | Beschreibung |
|------|---------|-------------|
| `/api/feed/` | GET | Unified Alert Feed (OpenSearch), alle Filter-Parameter |
| `/api/feed/unread-count` | GET | Ungelesene Alerts seit `?since=<ISO>` |
| `/api/feed/checkmk-filter-values` | GET | Verfügbare Filter-Werte aus CheckMK-Index |
| `/api/feed/{item_id}/acknowledge` | POST | Alert als bestätigt markieren |
| `/api/feed/{item_id}/enrich` | POST | KI-Anreicherung (optional SearXNG) für einzelnes Item |
| `/api/feed/{item_id}/ignore` | POST | KI generiert OpenSearch-Ausschluss-Query → als System-Exclusion-Suche speichern |
| `/api/feed/incidents` | GET | Offene Incidents (status: open/investigating) |
| `/api/feed/incidents/{id}/timeline` | GET | Chronologische Timeline eines Incidents (Alerts + Kommentare + KI-Diagnosen) |

### FeedSearches

| Path | Method | Description |
|------|--------|-------------|
| `/api/feed-searches/` | GET | all searches (system + own) |
| `/api/feed-searches/` | POST | create a new personal search |
| `/api/feed-searches/system` | POST | create a new system search (admin) |
| `/api/feed-searches/{id}` | PATCH | edit a search |
| `/api/feed-searches/{id}` | DELETE | delete a search (own only; system → 403) |
| `/api/feed-searches/{id}/preview` | GET | preview (5 hits) |

### Dashboard widgets

| Path | Method | Description |
|------|--------|-------------|
| `/api/dashboard-widgets/dashboards` | GET | all dashboards of the user |
| `/api/dashboard-widgets/dashboards` | POST | create a new dashboard |
| `/api/dashboard-widgets/dashboards/{id}` | PATCH | rename a dashboard |
| `/api/dashboard-widgets/dashboards/{id}` | DELETE | delete a dashboard |
| `/api/dashboard-widgets/dashboards/{id}/reset-defaults` | POST | restore the default widgets |
| `/api/dashboard-widgets/dashboards/{id}/suggest-layout` | POST | compute a generative layout suggestion (does not write itself) |
| `/api/dashboard-widgets/` | GET | all widgets of the user (by dashboard) |
| `/api/dashboard-widgets/` | POST | create a new widget |
| `/api/dashboard-widgets/{id}` | PATCH | edit a widget (layout/config/title) |
| `/api/dashboard-widgets/{id}` | DELETE | delete a widget |
| `/api/dashboard-widgets/{id}/data` | GET | fetch widget data (OpenSearch / Prometheus) |

### AI

| Path | Method | Description |
|------|--------|-------------|
| `/api/ai/search-assistant` | POST | free text → Lucene query; can create a FeedSearch/widget |
| `/api/ai/promql-assistant` | POST | Lucene/free text → PromQL |
| `/api/ai/trigger/{agent_type}` | POST | trigger an agent manually (`sysadmin` / `network`) |
| `/api/ai/analyses` | GET | latest AI analyses (ai_analyses) |
| `/api/ai/analyses/{analysis_id}` | GET | a single analysis (deep link from the AI summary widget: `/ai-insights?analysis=<id>`) |

### Preferences

| Path | Method | Description |
|------|--------|-------------|
| `/api/preferences/` | GET | own preferences |
| `/api/preferences/` | PATCH | update preferences (CheckMK filters etc.) |
| `/api/preferences/jira-queries/` | GET | own JQL queries |
| `/api/preferences/jira-queries/` | POST | new JQL query |
| `/api/preferences/jira-queries/{id}` | PATCH | edit a JQL query |
| `/api/preferences/jira-queries/{id}` | DELETE | delete a JQL query |
| `/api/preferences/jira-queries/generate` | POST | AI JQL generator |

### Connectors

| Path | Method | Description |
|------|--------|-------------|
| `/api/connectors/` | GET | all connectors (admin: all; user: own) |
| `/api/connectors/` | POST | create a new connector (admin) |
| `/api/connectors/{id}` | PATCH | edit a connector |
| `/api/connectors/{id}` | DELETE | delete a connector (admin) |
| `/api/connectors/{id}/test` | POST | test the connection |
| `/api/connectors/my` | GET | personal connectors |
| `/api/connectors/my/{type}` | POST | create/update a personal connector |
| `/api/connectors/my/{type}` | DELETE | delete a personal connector |
| `/api/connectors/my/{type}/test` | POST | test a personal connector |
| `/api/connectors/my/{type}/device-code/start` | POST | start the Microsoft device code flow |
| `/api/connectors/my/{type}/device-code/poll` | POST | check/finish the device code flow |

### Workflow / Work Sessions

| Path | Method | Description |
|------|--------|-------------|
| `/api/workflow/` | GET | all work sessions of the user |
| `/api/workflow/` | POST | create a new work session |
| `/api/workflow/{id}` | GET | work session with all notes |
| `/api/workflow/{id}` | PATCH | edit a work session |
| `/api/workflow/{id}/notes` | POST | add a note |
| `/api/workflow/{id}/generate-comment` | POST | generate an AI Jira comment |
| `/api/workflow/{id}/post-comment` | POST | post a comment to Jira (`{"comment": "..."}`) — AI-generated or manual |
| `/api/workflow/{id}/generate-resolution` | POST | generate closure documentation |
| `/api/workflow/{id}/auto-categorize` | POST | AI categorization |
| `/api/workflow/{id}/suggest-solution` | POST | web solution search |
| `/api/workflow/{id}/5why` | POST | 5-whys root cause analysis |
| `/api/workflow/analyze-mail` | POST | analyze an O365 email |

### Other endpoints

| Path | Method | Description |
|------|--------|-------------|
| `/api/alerts/` | GET | aggregated alerts from PostgreSQL |
| `/api/kanban/` | GET, POST, PATCH, DELETE | Kanban cards |
| `/api/kanban/import-jira` | POST | import Jira tickets into the board |
| `/api/jira-view/my-tickets` | GET | Jira tickets by JQL filters |
| `/api/settings/` | GET | global settings (admin) |
| `/api/settings/` | PATCH | edit global settings (admin) |
| `/api/settings/test/llm` | POST | test the LLM connection |
| `/api/users/` | GET, POST | manage users (admin) |
| `/api/users/{id}` | PATCH, DELETE | edit/delete users (admin) |
| `/api/audit/` | GET | audit log (admin) |
| `/api/network/events` | GET | network switch events |
| `/api/ws` | WebSocket | real-time push (alerts, AI results) |
| `/api/help/` | GET | context-aware help texts |

---

## Database Migrations

| Revision | Description |
|----------|-------------|
| `0001` | initial schema: `users`, `connector_configs`, `alerts`, `kanban_cards`, `ai_analyses`, `audit_logs`, `global_settings` |
| `0002` | `network_switch_events` + `global_settings` table |
| `0003` | `workflow_sessions` + `workflow_notes` (ITIL work sessions) |
| `0004` | `refresh_tokens` + `audit_log` table |
| `0005` | `user_preferences`: CheckMK filters (`checkmk_locations`, `checkmk_ve`, `checkmk_criticality`) |
| `0006` | personal connectors: `owner_user_id` FK in `connector_configs` |
| `0007` | setup wizard state: `setup_completed` in `user_preferences` |
| `0008` | `user_preferences`: `checkmk_os` + `checkmk_hostgroups` + `jira_project` |
| `0009` | `feed_searches` Tabelle + `feed_disabled_search_ids` in `user_preferences`; 4 System-Suchen als Seeds |
| `0010` | `dashboard_widgets` Tabelle |
| `0011` | `dashboards` Tabelle + `dashboard_id` FK in `dashboard_widgets` |
| `0012` | `user_preferences.checkmk_hostgroups` (falls fehlend) |
| `0013` | `feed_searches.is_exclusion` Boolean-Feld |
| `0014` | `user_preferences.ticket_seen_map` (JSON) — serverseitiges Ticket-Badge-Tracking |
| `0015` | `dashboards.mode` (`classic`/`generative`), `dashboard_widgets.pinned`, `dashboard_widgets.hidden` — Generativer Modus |
| `0016` | `alert_score_adjustments` — adaptives Scoring (Feedback-Loop, Deltas mit Verfall) |
| `0017` | `worklist_snapshots`, `ai_insight_cache` — KI-Worklist-Cache und Alert-Insight-Cache |
| `0018` | `user_preferences.ui_theme` (`classic`/`holo`/`lcars`) — app-weites Theme |
| `0019` | `dashboards.rationale`, `dashboards.generated_at` — Generatives Dashboard mit KI-Lagebild |
| `0020` | `alert_collaboration` + `alert_comments` — kollaboratives Alert-Handling (Claim/Status/Timeline) |
| `0021` | `incidents` + `incident_members` — automatische Incident-Korrelation (FQDN-only, 30-Min-Zeitfenster, Cross-Source) |
| `0022` | `user_preferences.computer_console_enabled` + `ui_language` (Computer Console freischalten, per-User UI/KI-Sprache) |
| `0023` | `computer_sessions` — Persistenz der Computer-Console-Sessions |
| `0024` | `computer_sessions.external_id` + `resolved` (GELÖST-Button-Persistenz) |
| `0025` | `work_sessions.computer_session_id` — Verknüpfung Work Session ↔ Computer-Session |
| `0026` | `work_sessions` GitLab-Spalten — GitLab-Integration |
| `0027` | `remediation_proposals` — AWX-Remediation-Pipeline (Human-in-the-Loop) |
| `0028` | `playbook_drafts` — KI-Playbook-Authoring |
| `0029` | `work_sessions.workspace_path` — Werkbank Web-IDE |
| `0030` | `ai_analyses.clusters` — KI-Insights Fehler-Cluster (Root-Cause-Diagnosen) |

---

## Deployment

### Minimal ENV variables

```env
# Required
ENCRYPTION_KEY=<Fernet key, 32-byte base64>
DATABASE_URL=postgresql+asyncpg://user:pass@db/centralstation
REDIS_URL=redis://redis:6379/0
SECRET_KEY=<JWT signing key, min. 32 chars>

# OpenSearch
OPENSEARCH_URL=http://opensearch:9200
OPENSEARCH_USER=admin
OPENSEARCH_PASSWORD=<password>
```

All other configuration (LLM URL, connector credentials, SearXNG) is stored Fernet-encrypted in the database and managed via the frontend.

### Docker Compose

```bash
# Start the stack
docker compose up -d

# Watch logs
docker compose logs -f backend

# Apply migrations manually
docker compose exec backend alembic upgrade head

# Back up the database
docker compose exec db pg_dump -U postgres centralstation > backup_$(date +%Y%m%d).sql

# Check OpenSearch status
curl http://localhost:9200/_cluster/health?pretty
```

### Production checklist

- [ ] `ENCRYPTION_KEY` generated securely and set in `.env` (never commit to Git)
- [ ] `SECRET_KEY` with at least 64 chars of entropy
- [ ] Nginx SSL certificate configured (`nginx/nginx.conf`)
- [ ] OpenSearch with auth and TLS
- [ ] regular PostgreSQL backup set up
- [ ] `docker compose up -d --no-build` for production (use pre-built images)
- [ ] change the admin password after the first login
- [ ] rate limiting on `/api/auth/login` (10 requests/minute, already built in)

---

## Upgrading

### Pulling the latest code

```bash
git pull origin main
```

### Rebuild and restart containers

```bash
# Rebuild all images (picks up code changes)
docker compose build

# Restart with the new images
docker compose up -d
```

Only the `backend`, `frontend`, and `centralcore` services contain baked code.
`db`, `redis`, `opensearch`, and `nginx` use upstream images and only need a
pull + restart when you want a newer upstream version.

### Apply database migrations

Migrations run automatically when the backend starts. To apply them manually
(e.g. after a failed start):

```bash
docker compose exec backend python -m alembic upgrade head
```

Check current revision:

```bash
docker compose exec backend python -m alembic current
```

### Updating a single service

```bash
# Rebuild and restart only the backend
docker compose build backend && docker compose up -d --no-deps backend

# Rebuild and restart only the frontend
docker compose build frontend && docker compose up -d --no-deps frontend

# Rebuild and restart centralcore (includes Hermes re-install)
docker compose build centralcore && docker compose up -d --no-deps centralcore

# Pull NEW versions of the agent CLIs into the Console image.
# Hermes, Claude Code, Codex and Playwright MCP are deliberately unpinned
# (@latest / git --depth=1). Which version lands in the image is keyed on the
# versioned file userenv/AGENT_REFRESH — bump it to fetch, commit it with the change:
date -u +%Y-%m-%dT%H:%MZ > userenv/AGENT_REFRESH && docker compose build userenv
```

A plain `docker compose build userenv` afterwards keeps the refreshed versions. That is
the point of using a file instead of a build-arg: with the arg, a build *with* a value and
a later build *without* one left two cache entries, and the plain build silently picked
the older one — it once rolled Claude Code back from 2.1.281 to 2.1.278.

Running `cs-userenv-*` and `cs-vibemk*` containers are **not** rebuilt by Compose — the
backend owns their lifecycle. Both now compare their running image against the current tag
and recreate themselves on the next use, so a rebuild takes effect without manual `docker rm`.

### Rollback

```bash
# Roll back to the previous migration
docker compose exec backend python -m alembic downgrade -1

# Or roll back to a specific revision
docker compose exec backend python -m alembic downgrade <revision>

# Restore the database from backup
docker compose exec -T db psql -U centralstation centralstation < backup_YYYYMMDD.sql
```

### Zero-downtime upgrade (production)

1. `git pull origin main`
2. `docker compose build backend frontend userenv`
3. `docker compose up -d --no-deps backend frontend` — new containers replace old ones
4. Migrations apply automatically on backend startup
5. Verify health: `docker compose ps` and `curl http://localhost/api/health`
6. Per-user userenv containers are recreated automatically on next session start (existing containers continue to run the old image until the user starts a new session or you remove them manually with `docker rm -f cs-userenv-<uid>`)

---

## Changelog

### v0.4.0 — Computer Console CLI Agents + Session Management

**New: Claude CLI and Codex CLI as Computer Console backends**

Users can now choose their preferred AI agent for the Computer Console — per user, configured in Settings → Console or the Setup Wizard. All three agents share the same MCP tool access (alert list, feed search, CheckMK, Jira, …).

- **Hermes** (default): unchanged — uses the system LLM configured in general AI settings
- **Claude CLI** (`claude_cli`): authenticates via Claude OAuth (PKCE); runs `claude --print --session-id <uuid>` inside the per-user `cs-userenv` container; conversation history persists in `~/.claude/projects/.../<sid>.jsonl` on the `cs-ide-cfg-{uid}` named volume — survives container restarts; only Claude model names (`claude-*`) are forwarded as `--model`, preventing OpenAI model names from causing errors
- **Codex CLI** (`codex_cli`): authenticates via ChatGPT Device Code OAuth; uses a custom `~/.codex/config.toml` provider pointing at `chatgpt.com/backend-api/codex`; MCP tools via streamable-http (`/api/mcp-http/`); captures internal codex session ID for `codex exec resume` on subsequent turns

**New: MCP streamable-http endpoint**

`/api/mcp-http/` (fastmcp 2.13.3 `http_app`) runs alongside the legacy `/api/mcp/sse` SSE endpoint. Both serve identical tools. Claude CLI and Codex CLI native MCP clients connect to the streamable-http endpoint; Hermes continues using SSE unchanged.

**New: agent_type in session list**

`computer_sessions.agent_type` DB column (migration `0034`). `GET /api/computer/sessions` returns `agent_type` per session. Existing sessions are migrated to `hermes`. The UI shows `[CL]` / `[CO]` badges in the session rail for Claude/Codex sessions.

**Improved: session resume after container restart**

- `send_message` now calls `ensure_container` — the per-user container restarts automatically on the first incoming message after a Docker restart or `docker rm -f`
- CLI sessions no longer 404 after restart: a fresh in-memory entry is created and Claude picks up its conversation history from the volume-backed JSONL
- Claude: `--session-id` on first call, `--resume <sid>` on subsequent calls (detected via JSONL glob on the volume)
- Codex: `--ask-for-approval never` flag; `approval_policy = "never"` in config.toml

**Improved: credential injection**

`configure_claude_credentials` now merges into the existing credentials file (preserves `scopes`, `subscriptionType`, `rateLimitTier` written by the VS Code extension); `expiresAt` is stored as an integer. Missing fields get safe defaults so `claude auth status` reports `loggedIn: true`.

**Fixed: `AiInsightsComponent` and `AddWidgetDialogComponent`** missing `readonly i18n = inject(I18nService)` injection (caused build failure).

---

### v0.3.0 — Werkbank Web-IDE + Infrastructure Topology

- Per-user `cs-userenv-{uid}` containers (code-server + Hermes in one image) replace the shared `centralcore` approach
- `/workbench` route with nginx `auth_request` gate
- Pre-installed Claude Code and Codex VS Code extensions; credentials shared with Computer Console CLI agents
- Infrastructure topology graph (`/topology`) via ECharts force-layout from NetBox + alert overlay
- Bilingual EN/DE UI across all 25 frontend components

### v0.2.0 — AI Insights + Incident Correlation

- AI error-cluster analysis (root-cause grouping across alerts)
- Incident correlation with blast-radius topology
- KI-Insights panel with findings + recommendations
- Generative dashboard mode

### v0.1.0 — Initial release

- Operations cockpit, news feed, alert aggregation (CheckMK, Graylog, Wazuh)
- Hermes Computer Console
- Kanban/Jira integration
- LCARS design system
