# CentralStation

Central IT operations dashboard for Linux system administrators.  
Aggregates alerts from Wazuh, Graylog and CheckMK, synchronises Jira tickets and
assists with the entire ITIL-compliant work documentation using AI.

> **Language:** the UI defaults to **English** and can be switched to German at runtime.
> The AI answers in the operator's selected language (user preference `ui_language`).

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [Architecture](#architecture)
3. [CheckMK as the Single Source of Truth](#checkmk-as-the-single-source-of-truth)
4. [Feature Overview](#feature-overview)
5. [Operations Cockpit (Dashboard)](#operations-cockpit-dashboard)
6. [News Feed](#news-feed)
7. [OpenSearch Searches (FeedSearches)](#opensearch-searches-feedsearches)
8. [Alert Aggregation and Enrichment](#alert-aggregation-and-enrichment)
9. [Kanban and Jira](#kanban-and-jira)
10. [AI Features](#ai-features)
11. [Computer Console (Hermes AI Panel)](#computer-console-hermes-ai-panel)
12. [Prometheus Metrics & PromQL](#prometheus-metrics--promql)
13. [Connectors](#connectors)
14. [User Management and RBAC](#user-management-and-rbac)
15. [Settings and Preferences](#settings-and-preferences)
16. [API Reference](#api-reference)
17. [Database Migrations](#database-migrations)
18. [Deployment](#deployment)

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
| it-aikb RAG | `it_aikb` | solution search, AI knowledge search |

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
                    ├── it-aikb RAG API (HyDE + OpenSearch)
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

| Area | Functions |
|------|-----------|
| **Operations Cockpit** | Dual-mode dashboard (Classic/Generative), widget types: stat, list, donut, bar, time series, forecast, AI situation report, top hosts, war room; clickable charts; pin/reset in generative mode; clickable hostname in top hosts |
| **Generative Dashboard** | AI composes a tailored dashboard situationally — analyses findings, worklist, vitals + forecast candidates; rationale as a situation briefing; regenerate button + WS escalation trigger; CUE production hosts prioritised |
| **Bridge** | Star-Trek-LCARS cockpit at `/bridge`; three themes (Classic/Holo/LCARS); priority worklist, fleet vitals, forecasts, sectors, live logs; primary incident panel |
| **Adaptive Alert Scoring** | Deterministic base scoring (severity/novelty/age/flapping/cross-source); adaptive learning feedback loop (Jira tickets, acks, ignores → `alert_score_adjustments`); score-delta decay |
| **Alert Aggregation** | CheckMK, Graylog, Wazuh — central timeline, acknowledge, severity filter; Graylog: Python log-level detection (INFO→low, ERROR→high, prevents Docker GELF misclassification) |
| **News Feed** | Unified OpenSearch feed, saved searches (Lucene), last-seen divider, AI enrichment, AI ignore; clickable hostname → feed filter; severity filter correctly ignores active saved searches |
| **AI Insights** | Findings + their recommendations shown together (no separate panel); data-source badge per finding; hostname/feed links; recommendations feed into the generative dashboard |
| **AI War Room** | Blast-radius analysis on critical/high; co-located VMs, co-located hosts; recommendations with one-click Jira |
| **CheckMK Metrics** | Collector writes CPU/RAM/disk/agent time into `cs-metrics-checkmk`; the bridge shows fleet vitals + forecasts (linear regression); stable metrics (< 90% without trend) are filtered out of the generative context |
| **OpenAI Codex OAuth** | Browser-initiated device-code flow (no CLI needed); provider switchable between local LLM and OpenAI Codex (GPT-5.x); token encrypted in DB, automatic refresh |
| **3 App-wide Themes** | **Classic** (light, blue haze), **Holo** (dark blue/cyan), **LCARS** (black/orange — official Neon Carrot + Golden Tanoi + Anakiwa + Lilac palette); selectable in settings |
| **Kanban Board** | Drag-and-drop, bidirectional Jira/ServiceDesk sync, automatic Jira imports, AI-created cards |
| **My Tickets** | Per-user Jira view, JQL filter management, AI JQL generator; unread badge; red dot on activity |
| **Work Documentation** | ITIL work sessions: impact/urgency/priority P1–P4, SLA tracking, work notes |
| **AI Comments** | Progress, pending, escalation, handover — AI-generated, directly copyable to Jira |
| **Closure Documentation** | AI-generated resolution documentation with root cause, actions, closure code |
| **5-Whys Analysis** | ITIL problem management — AI runs a 5-whys analysis, proposes the root cause |
| **Solution Search** | RAG search in the it-aikb knowledge base + SearXNG web search, HyDE pattern |
| **Network Module** | Switch alerts (NSA/NSS/NSC), site mapping (ID-Generator), vendor detection |
| **RBAC** | Admin / SysAdmin / Network Technician / Viewer — role-based UI and API |
| **Audit Log** | Logging of all write operations |

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
- When `workflow.web_search` is enabled (default on), a SearXNG web search adds context on top of the it-aikb RAG search (HyDE)
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
  → LLM decides: simple search (it-aikb /search) or deep search (/search/stream SSE)
  → knowledge base: runbooks, past incidents, documentation

Node 4: analyze
  → correlate events + RAG context
  → findings + recommendations (structured, Pydantic AnalysisResult)
  → store in PostgreSQL (ai_analyses table)

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
| `agent.rag_enabled` | knowledge-base search (RAG/it-aikb) in the AI agent | `true` |
| `workflow.web_search` | web search (SearXNG) during AI analysis of feed/alerts | `true` |
| `agent.interval_minutes` | interval for background agents (minutes) | `10` |
| `agent.auto_jira` | create Jira tickets automatically | `true` |
| `agent.jira_severity_threshold` | minimum severity to create tickets | `critical` |
| `rag.base_url` | it-aikb RAG API URL | — |
| `rag.api_token` | it-aikb bearer token | — |
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
- The SysAdmin agent emits all text fields (findings, recommendations) **in the operator's language** (`ui_language`) — even when RAG/web context is in another language
- **No hallucinations**: if context is missing, the AI says so explicitly (`"No context available from the knowledge base…"`) instead of inventing causes
- it-aikb calls (standard + DeepSearch) have a timeout of **300 s** (DeepSearch takes ~2 min)

---

## Computer Console (Hermes AI Panel)

CentralStation includes an interactive AI assistant panel — the **Computer Console** — that wraps a [Hermes](https://github.com/your-org/hermes-agent) AI agent session in a floating LCARS-styled popup. It gives operators a Star Trek-inspired interface for diagnosing alerts and querying the monitoring infrastructure through natural language.

### Architecture

```
Browser (Computer panel)
    │  POST /api/computer/sessions/{sid}/message  (SSE stream)
    ▼
backend (centralcore_proxy.py)  ←→  JWT auth guard
    │  HTTP proxy
    ▼
centralcore container (FastAPI, port 8001)
    │  Hermes AIAgent.run_conversation()
    ▼
MCP server  (/api/mcp/sse)  →  CentralStation tools
```

**`centralcore/`** is a standalone Python service that:
- Manages multiple parallel Hermes sessions
- Exposes SSE-streaming message endpoints
- Provides Whisper STT for voice input
- Connects to the CentralStation MCP server for live IT data

**`backend/app/api/centralcore_proxy.py`** is a JWT-authenticated reverse proxy that forwards browser requests to the `centralcore` service.

**`backend/app/api/mcp_server.py`** exposes CentralStation tools as an MCP server:

| Tool | Description |
|------|-------------|
| `get_bridge_status()` | Overall monitoring status |
| `list_alerts(severity, source, hours)` | Filtered alert list |
| `search_feed(query)` | Lucene query against all feed indices |
| `get_checkmk_host(hostname)` | Host status and services |
| `acknowledge_alert(alert_id)` | Acknowledge an alert |
| `create_jira_ticket(title, description, priority)` | Create a Jira ticket |

### Computer diagnoses alerts

When the user clicks **"Computer"** on a News Feed alert, `GET /api/feed/{id}/hermes-context` runs automatically:
1. Looks up the alert in OpenSearch to get host, severity, container name
2. Runs `run_diagnostics(host)` — CheckMK status, recent logs, metrics, topology, past incidents
3. Searches for **past AI-resolved similar alerts** (see below) and prepends them as context
4. Returns a structured prompt that is sent directly to the Hermes session

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

1. Install [Hermes](https://github.com/your-org/hermes-agent) and configure an MCP server pointing at CentralStation:
   ```yaml
   # ~/.hermes/config.yaml
   mcp_servers:
     centralstation:
       transport: http
       url: http://backend:8000/api/mcp/sse
   ```
2. Set the `CENTRALCORE_URL` env variable in the backend service (default: `http://centralcore:8001`).
3. Enable **Computer Console** for a user in Admin → Users.
4. Add the `centralcore` service to your `docker-compose.yml` (see the included example).

### System prompt

The Computer system prompt (`centralcore/main.py:SYSTEM_PROMPT`) defines the agent's behaviour:
- Uses MCP tools for all IT queries (never local shell)
- Searches Graylog (via `search_feed`) for container logs — no SSH needed (Logspout sends all container output to Graylog)
- Always asks before executing write operations (Jira ticket creation, alert acknowledgement)
- Appends `[FEED:host=<hostname>]` markers that the frontend renders as clickable feed-filter buttons

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
| `it_aikb` | Bearer Token | `api_token` |

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

**RAG/search:**
- `rag.base_url`, `rag.api_token` — it-aikb knowledge base
- `searxng.base_url` — SearXNG web search

### Fetch filter values

`GET /api/feed/checkmk-filter-values` — returns the available values for all CheckMK filter dropdowns (OS, location, VE, criticality, host groups) directly from OpenSearch.

---

## API Reference

### Authentication

| Path | Method | Description |
|------|--------|-------------|
| `/api/auth/login` | POST | login; returns `access_token` + sets the `refresh_token` HttpOnly cookie |
| `/api/auth/refresh` | POST | renew the access token via the cookie |
| `/api/auth/logout` | POST | revoke the refresh token |

### Feed

| Path | Method | Description |
|------|--------|-------------|
| `/api/feed/` | GET | unified alert feed (OpenSearch), all filter parameters |
| `/api/feed/unread-count` | GET | unread alerts since `?since=<ISO>` |
| `/api/feed/checkmk-filter-values` | GET | available filter values from the CheckMK index |
| `/api/feed/{item_id}/acknowledge` | POST | mark an alert as acknowledged |
| `/api/feed/{item_id}/enrich` | POST | AI enrichment (it-aikb RAG + optional SearXNG) for a single item |
| `/api/feed/{item_id}/ignore` | POST | AI generates an OpenSearch exclusion query → store as a system exclusion search |

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
| `/api/workflow/{id}/generate-comment` | POST | generate an AI Jira comment (it-aikb DeepSearch context, 300 s timeout) |
| `/api/workflow/{id}/post-comment` | POST | post a comment to Jira (`{"comment": "..."}`) — AI-generated or manual |
| `/api/workflow/{id}/generate-resolution` | POST | generate closure documentation |
| `/api/workflow/{id}/auto-categorize` | POST | AI categorization |
| `/api/workflow/{id}/suggest-solution` | POST | RAG + web solution search |
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
| `0009` | `feed_searches` table + `feed_disabled_search_ids` in `user_preferences`; 4 system searches as seeds |
| `0010` | `dashboard_widgets` table |
| `0011` | `dashboards` table + `dashboard_id` FK in `dashboard_widgets` |
| `0012` | `user_preferences.checkmk_hostgroups` (if missing) |
| `0013` | `feed_searches.is_exclusion` boolean field |
| `0014` | `user_preferences.ticket_seen_map` (JSON) — server-side ticket badge tracking |
| `0015` | `dashboards.mode` (`classic`/`generative`), `dashboard_widgets.pinned`, `dashboard_widgets.hidden` — generative mode |
| `0016` | `alert_score_adjustments` — adaptive scoring (feedback loop, deltas with decay) |
| `0017` | `worklist_snapshots`, `ai_insight_cache` — AI worklist cache and alert insight cache |
| `0018` | `user_preferences.ui_theme` (`classic`/`holo`/`lcars`) — app-wide theme |
| `0019` | `dashboards.rationale`, `dashboards.generated_at` — generative dashboard with AI situation report |
| `0020` | `alert_collaboration` + `alert_comments` — collaborative alert handling (claim/status/timeline) |
| `0021` | `incidents` + `incident_members` — incident grouping (alert compressor + timeline) |

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

All other configuration (LLM URL, connector credentials, SearXNG, RAG) is stored Fernet-encrypted in the database and managed via the frontend.

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
