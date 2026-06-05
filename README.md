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
6. [Server Cockpit (Host-Detail-Fenster)](#server-cockpit-host-detail-fenster)
7. [News Feed](#news-feed)
8. [OpenSearch-Suchen (FeedSearches)](#opensearch-suchen-feedsearches)
9. [Alert-Aggregation und Enrichment](#alert-aggregation-und-enrichment)
10. [Incident-Korrelation](#incident-korrelation)
11. [Kanban und Jira](#kanban-und-jira)
12. [KI-Funktionen](#ki-funktionen)
13. [Prometheus-Metriken & PromQL](#prometheus-metriken--promql)
14. [Konnektoren](#konnektoren)
15. [Benutzerverwaltung und RBAC](#benutzerverwaltung-und-rbac)
16. [Einstellungen und Präferenzen](#einstellungen-und-präferenzen)
17. [API-Referenz](#api-referenz)
18. [Datenbankmigrationen](#datenbankmigrationen)
19. [Deployment](#deployment)

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
| **Operations Cockpit** | Dual-Mode Dashboard (Klassisch/Generativ), Widget-Typen: Stat, Liste, Donut, Balken, Zeitreihe, Forecast, KI-Lagebericht, Top-Hosts, War Room; klickbare Charts; Pin/Reset im generativen Modus; Hostname in Top-Hosts klickbar |
| **Generatives Dashboard** | KI komponiert situativ ein maßgeschneidertes Dashboard — analysiert Findings, Worklist, Vitals + Forecast-Kandidaten; Rationale als Lage-Briefing; Neu-Generieren-Button + WS-Eskalation-Trigger; CUE-Produktionshosts priorisiert |
| **Server Cockpit** | Klick auf Hostnamen im News Feed öffnet `/cockpit/:hostname` in neuem Fenster; LCARS-Design; Hero-Gauges (CPU/RAM/Disk) + Sparklines; **Voll-Service-Liste** aller CheckMK-Checks mit Status + Summary; hierarchische Filter-Chips (FEHLER/WARN/CRIT/UNKNOWN); Klick auf Service → on-demand 24h-Graph; Font Roboto; Navbar wird ausgeblendet |
| **Brücke (Bridge)** | Star-Trek-LCARS-Cockpit unter `/bridge`; drei Themes (Classic/Holo/LCARS); Prioritäten-Worklist, Fleet-Vitals, Forecasts, Sektoren, Live-Logs; Primärer-Incident-Panel; Font Roboto |
| **Adaptives Alert-Scoring** | Deterministisches Basis-Scoring (Severity/Novelty/Alter/Flapping/Cross-Source); adaptiver Lern-Feedback-Loop (Jira-Tickets, Acks, Ignorieren → `alert_score_adjustments`); Score-Delta-Verfall |
| **Incident-Korrelation** | Automatische Gruppierung zusammengehöriger Alerts zu Incidents; nur FQDNs als Hosts (Docker-Container-IDs werden abgelehnt); 30-Minuten-Zeitfenster für Incident-Wiederverwendung; Minimum 2 Alerts oder Cross-Source für neuen Incident |
| **Alert-Aggregation** | CheckMK, Graylog, Wazuh — zentrale Timeline, Acknowledge, Severity-Filter; Graylog: Python-Loglevel-Erkennung (INFO→low, ERROR→high, verhindert Docker-GELF-Fehleinstufung) |
| **News Feed** | Unified OpenSearch Feed, gespeicherte Suchen (Lucene), Last-Seen-Divider, KI-Anreicherung, KI-Ignorieren; Hostname anklickbar → Feed-Filter; Severity-Filter ignoriert aktive Saved-Searches korrekt |
| **KI-Insights** | Befunde + zugehörige Empfehlungen direkt zusammen (kein getrenntes Panel); Datenquelle-Badge je Befund; Hostname/Feed-Links; Empfehlungen fließen in generatives Dashboard ein |
| **AI War Room** | Blast-Radius-Analyse bei Critical/High; Ko-VMs, Ko-lokalisierte Hosts; Empfehlungen mit Ein-Klick-Jira |
| **CheckMK Metriken** | Collector schreibt CPU/RAM/Disk/Agent-Zeit in `cs-metrics-checkmk`; Bridge zeigt Fleet-Vitals + Forecasts (lineare Regression); stabile Metriken (< 90 % ohne Trend) werden aus generativem Kontext gefiltert |
| **OpenAI Codex OAuth** | Browser-initiierter Device-Code-Flow (kein CLI nötig); Provider umschaltbar zwischen lokalem LLM und OpenAI Codex (GPT-5.x); Token verschlüsselt in DB, automatischer Refresh |
| **3 App-weite Themes** | **Classic** (hell, blauer Schleier), **Holo** (dunkelblau/cyan), **LCARS** (schwarz/orange — offizielles Neon Carrot + Golden Tanoi + Anakiwa + Lilac Palette); in Einstellungen wählbar |
| **Kanban-Board** | Drag-Drop, bidirektionaler Jira-/ServiceDesk-Sync, automatische Jira-Importe, AI-erstellte Cards |
| **Meine Tickets** | Per-User Jira-Sicht, JQL-Filter-Verwaltung, KI-JQL-Generator; Unread-Badge; roter Punkt bei Aktivität |
| **Arbeitsdokumentation** | ITIL Work Sessions: Impact/Urgency/Priorität P1–P4, SLA-Tracking, Arbeitsnotizen |
| **KI-Kommentare** | Fortschritt, Pending, Eskalation, Übergabe — per KI generiert, direkt in Jira kopierbar |
| **Abschlussdokumentation** | KI-generierte Lösungsdokumentation mit Root Cause, Maßnahmen, Closure Code |
| **5-Why-Analyse** | ITIL Problem Management — KI führt 5-Why-Analyse durch, schlägt Kernursache vor |
| **Lösungssuche** | RAG-Suche in it-aikb Wissensdatenbank + SearXNG Web-Suche, HyDE-Pattern |
| **Netzwerk-Modul** | Switch-Alerts (NSA/NSS/NSC), Standort-Zuordnung (ID-Generator), Vendor-Erkennung |
| **RBAC** | Admin / SysAdmin / Network-Technician / Viewer — rollenbasierte UI und API |
| **Audit-Log** | Protokollierung aller schreibenden Operationen |

---

## Operations Cockpit (Dashboard)

Das Dashboard besteht aus frei konfigurierbaren GridStack-Widgets. Jedes Widget ist unabhängig skalierbar und verschiebbar.

### Widget-Typen

| Typ | Beschreibung | Datenquelle | Pflicht-Config |
|-----|--------------|-------------|----------------|
| `stat` | Einzelne Zahl (Alert-Count) | OpenSearch count query | `severity` oder `search_id` |
| `list` | Alert-Liste mit Severity-Dot + Host/Container | OpenSearch query | `sources`, `limit` (default 10) |
| `donut` | Severity-Verteilung als Donut-Chart (ECharts) | OpenSearch aggregation | `sources` |
| `bar` | Balkendiagramm über ein Aggregations-Feld (ECharts) | OpenSearch terms-aggregation | `agg_field`, `limit` (default 10) |
| `top_hosts` | Hosts mit den meisten Alerts | OpenSearch aggregation | `sources`, `limit` (default 5) |
| `ai_summary` | Letzter KI-Lagebericht (Findings + Empfehlungen) | PostgreSQL `ai_analyses` | *(keine)* |
| `timeseries` | Zeitreihen-Liniendiagramm (ECharts) | Prometheus PromQL / CheckMK RRD | `promql`, `step`, `hours` |
| `forecast` | CheckMK RRD-Historie + lineare Trendprojektion + ±1σ-Konfidenzband | CheckMK `get_forecast_data()` | `host`, `service`, `metric_id`, `history_hours` (default 72), `horizon_hours` (default 24) |
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

// bar — agg_field: severity | source | metadata.host.keyword | metadata.hostgroups.keyword
{ "index_pattern": "cs-feed-*", "query_string": "", "agg_field": "severity", "limit": 10 }

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

### Dual-Mode: Klassisch ↔ Generativ

Toggle-Button im Dashboard-Header wechselt zwischen zwei Modi:

**Klassisch** (Standard): manuelles Drag-Drop-Layout, `saveLayout` beim Verlassen des Konfigurationsmodus — wie bisher.

**Generativ**: Die KI-Layout-Engine passt das Dashboard situativ an:
- Beim Aktivieren + bei jedem WebSocket-Event `ai_insight` wird `POST /dashboard-widgets/dashboards/{id}/suggest-layout` aufgerufen
- Die Engine scoret Widgets nach KI-Analyse (severity_summary, Findings-Sources) + Live OpenSearch-Counts
- Relevante Widgets wandern nach oben/werden größer; ruhige Widgets schrumpfen/werden ausgeblendet
- **Pin-Button** je Widget (NASA-Override-Regel): gepinnte Widgets werden von der KI nie bewegt
- **Reset-Button** stellt das Standard-Layout in einem Klick wieder her
- Der Modus wird in `Dashboard.mode` gespeichert und beim nächsten Besuch wiederhergestellt
- **Klick auf Widget (Hintergrund)**: Öffnet den News Feed mit den passenden Filtern des Widgets
- **Klick auf Donut-Segment**: Öffnet den Feed gefiltert auf die angeklickte Severity
- **Klick auf Balken (`bar`)**: Öffnet den Feed gefiltert auf den Feld-Wert des Balkens
- **KI-Findings anklicken**: Direktlink aus dem `ai_summary`-Widget-Finding in die **KI-Insights** (`/ai-insights?analysis=<id>`) — die zugehörige Analyse wird hervorgehoben und das Befunde-Panel aufgeklappt
- **Interne Klicks vs. Widget-Klick**: Dedizierte Klicks (Donut/Bar/Finding/Item) haben Vorrang; der Hintergrund-Klick (`openWidget`) wird per Suppression-Flag unterdrückt, damit der Filter nicht überschrieben wird

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

## Server Cockpit (Host-Detail-Fenster)

Ein Klick auf einen Servernamen im **News Feed** öffnet das Server Cockpit in einem eigenständigen Browser-Fenster (`window.open`, Route `/cockpit/:hostname`). Das Fenster hat kein Navigationsmenü — es ist ein vollbildiges LCARS-Dashboard für genau einen Host.

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

### Performance-Block

- **Hero-Gauges**: CPU / RAM / Disk als ECharts-Gauge-Charts (140px), Farbe nach Level: crit `#ff4433` / high `#ffcc00` / ok `#66cc66`
- **Sparklines**: Kompakte 52px-Liniencharts direkt unterhalb der Gauges (24h-Historie)
- **Zweistufiges Laden**: Zuerst gecachte Werte aus `cs-metrics-checkmk` (sofort), dann Live-Refresh via CheckMK RRD (`?live=true`, ~1-2s)
- **LIVE-Badge**: wechselt von `cached` auf `LIVE ●` sobald die aktuellen Werte eintreffen

### Service-Liste

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

### API-Endpunkte (Backend)

| Endpunkt | Beschreibung |
|----------|-------------|
| `GET /api/hosts/{hostname}/health` | Performance-Vitals (gecacht + `?live=true`) + Host-Alerts |
| `GET /api/hosts/{hostname}/services` | Alle CheckMK-Services mit `state_label` + `summary`; sortiert CRIT→WARN→UNKNOWN→OK |
| `GET /api/hosts/{hostname}/graph` | 24h-Zeitreihe: `?service=<name>&metric=<id>` → `{series, title, unit}` |

### Technische Details

- **Navbar-Ausblendung**: `ngOnInit` setzt `document.body.classList.add('cockpit-active')`; `styles.scss` versteckt `.sidenav` und entfernt `mat-sidenav-content`-Margin (analog zu `bridge-active`)
- **Font**: Roboto (identisch mit News Feed und Alerts)
- **Auth**: Verwendet denselben `cs_access_token` aus `localStorage` — kein erneuter Login nötig
- **Mehrere Tabs**: `window.open` mit `target='cockpit-{hostname}'` — pro Host ein Fenster, kein Duplikat

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
- Beim Schließen des WorkSession-Dialogs wird die Ticket-Liste neu geladen, sodass der Punkt anhand der frischen `updated`-Zeit korrekt erscheint
- Tracking basiert **serverseitig** auf `user_preferences.ticket_seen_map` (JSON, `{jira_key: ISO-Zeit}`) — geräte- und browserübergreifend persistent (kein localStorage mehr)
- Geladen/geschrieben über `GET`/`PATCH /api/preferences` (Feld `ticket_seen_map`)
- Beim ersten Besuch der Seite werden alle sichtbaren offenen Tickets als „gesehen" markiert (keine Dots beim Erstbesuch)
- **Geschlossene Tickets** (`statusCategory = done`) werden aus der Map entfernt; wird ein Ticket wieder geöffnet, startet das Tracking neu
- Der Nav-Badge wird live im 60-Sekunden-Polling neu berechnet (Tickets + `ticket_seen_map`), unabhängig davon, ob die „Meine Tickets"-Seite geöffnet ist

### Ignorieren-Button (KI-Ausschluss)

- Jede Feed-Karte hat einen **Ignorieren**-Button
- Klick ruft `POST /api/feed/{id}/ignore` auf: die KI generiert aus dem Item eine OpenSearch-Lucene-Ausschluss-Query (charakteristische Phrase aus `title`/`body`, ggf. Container)
- Die Query wird als **System-Ausschluss-Suche** (`is_system=true, is_exclusion=true`) gespeichert → ähnliche Meldungen verschwinden dauerhaft aus dem Feed
- Das angeklickte Item wird sofort lokal aus der Liste entfernt

### KI-Analyse mit Websuche

- Button **KI Analyse** je Alert ruft `POST /api/feed/{id}/enrich` auf
- Bei aktivem `workflow.web_search` (Default an) ergänzt eine SearXNG-Websuche den Kontext zusätzlich zur it-aikb-RAG-Suche (HyDE)
- Steuerung der automatischen Anreicherung über `agent.auto_enrich`

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

## Incident-Korrelation

CentralStation gruppiert zusammengehörige Alerts automatisch zu **Incidents** (`incidents` + `incident_members`-Tabellen).

### Korrelationsregeln

Ein neuer Incident wird nur angelegt wenn **alle** Bedingungen erfüllt sind:

1. **Severity-Schwelle**: Nur `critical`/`high` Alerts können einen Incident auslösen oder erweitern — `low`/`info` werden ignoriert
2. **Mindestgröße**: Neuer Incident erfordert ≥ 2 korrelierte Alerts (gleicher Host, 30-Min-Fenster) **oder** Cross-Source-Evidenz (gleicher Host, ≥ 2 Quellen)
3. **Host-Validierung**: Nur FQDNs werden als Hosts akzeptiert (muss mindestens einen Punkt enthalten). Docker-Container-Short-IDs (z.B. `5086bbde056b`) und Container-Namen werden abgelehnt

### Zeitfenster

Offene Incidents werden nur wiederverwendet, wenn `updated_at >= jetzt - 30 Minuten`. Ein älterer Incident wird **nicht** verlängert — stattdessen wird ein neuer Incident angelegt. Das verhindert, dass zeitlich weit auseinanderliegende Alerts fälschlicherweise in denselben Incident gepackt werden.

### Incident-Lifecycle

```
Neuer Alert (critical/high) mit FQDN
    │
    ├── Offener Incident für diesen Host? (updated_at < 30 Min alt)
    │       → Ja: Incident erweitern (Member hinzufügen, Severity eskalieren, updated_at aktualisieren)
    │       → Nein: neuen Incident anlegen (wenn ≥ 2 Alerts oder Cross-Source)
    │
    └── Housekeeping-Job (alle 2h): Incidents ohne neue Member → resolved
```

### Datenmodell

| Tabelle | Felder |
|---------|--------|
| `incidents` | `id`, `title` (z.B. „host.example.com: 4 Alerts [checkmk/graylog]"), `primary_host`, `severity`, `status` (open/investigating/resolved), `created_at`, `updated_at`, `resolved_at` |
| `incident_members` | `incident_id`, `external_id`, `source`, `added_at` |

### API-Endpunkte

| Endpunkt | Beschreibung |
|----------|-------------|
| `GET /api/feed/incidents` | Offene Incidents (status: open/investigating) |
| `GET /api/feed/incidents/{id}/timeline` | Chronologische Timeline (Alerts + Kommentare + KI-Diagnosen) |

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
| `llm.provider` | Aktiver LLM-Provider: `custom` (lokaler Endpunkt) oder `openai-codex` (OAuth) | `custom` |
| `llm.base_url` | OpenAI-kompatibler Endpunkt (nur für `custom`) | — |
| `llm.model` | Modell-ID für `custom`-Provider (z.B. `qwen3next-79b`) | — |
| `llm.api_key` | API-Key (optional, nur für `custom`) | — |
| `llm.codex_model` | Modell-ID für `openai-codex`-Provider (z.B. `gpt-5.5`) | `gpt-4o` |
| `llm.codex_timeout_seconds` | Timeout für Codex-Anfragen | `60` |
| `llm.vision_model_url` | Vision-Modell Endpunkt | — |
| `llm.vision_model` | Vision-Modell-ID | — |
| `llm.thinking_mode` | Extended Thinking aktivieren (nur `custom`) | `false` |
| `agent.auto_enrich` | KI-Anreicherung automatisch nach Aggregation (aus = On-Demand) | `true` |
| `agent.rag_enabled` | Wissensdatenbank-Suche (RAG/it-aikb) im KI-Agenten | `true` |
| `workflow.web_search` | Websuche (SearXNG) bei KI-Analyse von Feed/Alerts | `true` |
| `agent.interval_minutes` | Intervall für Hintergrund-Agenten (Minuten) | `10` |
| `agent.auto_jira` | Jira-Tickets automatisch anlegen | `true` |
| `agent.jira_severity_threshold` | Ab welcher Severity Tickets anlegen | `critical` |
| `rag.base_url` | it-aikb RAG API URL | — |
| `rag.api_token` | it-aikb Bearer Token | — |
| `searxng.base_url` | SearXNG Web-Suche URL | — |

### OpenAI Codex OAuth-Provider

CentralStation kann optional OpenAI Codex (GPT-5.x) als LLM-Provider nutzen — ohne bezahlten API-Key, nur mit einem normalen ChatGPT-Account.

**Einrichten (einmalig, im Browser):**
1. Einstellungen → KI → Karte **„OpenAI Codex — Anmeldung"**
2. Button **„Mit OpenAI anmelden"** → Browser-Code wird angezeigt (z.B. `28UF-4FKHV`)
3. `https://auth.openai.com/codex/device` im Browser öffnen, Code eingeben, mit ChatGPT-Account einloggen
4. CentralStation erkennt den erfolgreichen Login automatisch (Polling alle 5 Sekunden)
5. Token wird Fernet-verschlüsselt in der Datenbank gespeichert und automatisch per Refresh-Token erneuert

**Provider umschalten:**
- Einstellungen → KI → **LLM Konfiguration** → „LLM Provider" auf `OpenAI Codex (OAuth)` stellen
- Modell eintragen (z.B. `gpt-5.5`) → Speichern
- Verbindung testen → zeigt `Verbindung OK — OpenAI Codex / Modell 'gpt-5.5' antwortet`

**Technischer Hintergrund:**
- Endpoint: `https://chatgpt.com/backend-api/codex/responses` (Responses API, nicht Chat-Completions)
- Zwingend Streaming (`stream: true`) — Endpunkt akzeptiert keine nicht-streamenden Anfragen
- `max_output_tokens` wird vom Endpunkt nicht unterstützt
- OAuth-Flow: Device-Code + PKCE (RFC 8628), kopiert aus Hermes-Quellcode
- API-Endpunkte: `GET/DELETE /api/oauth/openai-codex/status|logout`, `POST /api/oauth/openai-codex/start|poll/{session_id}`

**KI-Ausgabe-Verhalten:**
- Der SysAdmin-Agent gibt alle Textfelder (Befunde, Empfehlungen) **auf Deutsch** aus — auch wenn RAG-/Web-Kontext auf Englisch vorliegt
- **Halluzinations-Verbot**: Fehlt Kontext, nennt die KI das explizit (`„Kein Kontext aus Wissensdatenbank verfügbar…"`) statt Ursachen zu erfinden
- it-aikb-Aufrufe (Standard + DeepSearch) haben ein Timeout von **300 s** (DeepSearch dauert ~2 min)

---

## CheckMK Metriken-Collector

### Architektur

Alle 5 Minuten läuft ein APScheduler-Job (`run_metrics_collection`):
1. Findet alle Hosts mit aktiven WARN/CRIT-Problemen via CheckMK `get_problems()`
2. Fetcht Standard-Metriken (CPU load, Memory, Disk, cmk_time_agent) pro Host via `get_graph_data()`
3. Schreibt den jeweils neuesten Datenpunkt als OpenSearch-Dokument in `cs-metrics-checkmk`

**Index:** `cs-metrics-checkmk` — Felder: `host`, `service`, `metric`, `value`, `unit`, `timestamp`

### KI-Korrelation

Der KI-Agent liest beim `rag_lookup`-Step aktuelle Metrik-Punkte der betroffenen Hosts aus `cs-metrics-checkmk`. Damit kann das LLM Muster wie *„CPU war 94% → 5 Min. später OOM-Kill → CheckMK-Alert"* in einem Kontext sehen statt sie getrennt suchen zu müssen.

### AI War Room: Blast-Radius

Bei Critical/High-Alerts wird automatisch eine Blast-Radius-Analyse gestartet (`blast_radius.py`):
- **Standort** des Hosts via ID-Generator (`resolve_host_to_location()`, nutzt `virt_servers`-SQL)
- **Ko-VMs** auf demselben physischen Host via NetBox (`get_vm_host()`)
- **Ko-lokalisierte Hosts** am selben Standort via `cs-metrics-checkmk`
- Der Blast-Radius wird als Kontext an das LLM übergeben → kausale Narrative möglich

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

### CheckMK-Metriken — Integrationsrichtung

> **Wichtig:** CheckMK **exportiert nicht** nach Prometheus. Die native Prometheus-Integration ist einseitig — CheckMK *scrapt* Prometheus (CheckMK als Konsument). Nativer Metrik-Export geht nur nach InfluxDB/Graphite. Bestätigt: `monitoring.ippen.media` läuft als **CheckMK 2.3.0 CEE** (Commercial Enterprise Edition).

Daher zwei reale Wege, CheckMK-Performance-Daten in CentralStation zu bekommen:

**Option A – CheckMK RRD via REST-API (genutzt):**
Der `CheckMKConnector.get_graph_data()` zieht RRD-Zeitreihen über `/domain-types/metric/actions/get/invoke`. Das `timeseries`-Widget kann mit `data_source: "checkmk"` direkt darauf zugreifen — **kein Prometheus nötig**.

**Option B – node_exporter → Prometheus (optional, für Host-Metriken):**
```bash
# Ansible-Deploy auf allen Hosts:
ansible all -m apt -a "name=prometheus-node-exporter state=present" -b
ansible all -m service -a "name=prometheus-node-exporter enabled=yes state=started" -b
```
Danach scrapt Prometheus die node_exporter und das `timeseries`-Widget nutzt `data_source: "prometheus"` mit PromQL.

**Forecast:** CheckMK CEE hat **keinen** Forecast-REST-Endpoint (nur GUI-Dashlet). CentralStation implementiert daher eine eigene lineare Regression auf den historischen RRD-Daten: `get_forecast_data()` holt 72h History, projiziert via linearer Regression + berechnet ±1σ Konfidenzband. Ergebnis: `series_history`, `series_forecast`, `confidence_band`.

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

## Eigene Konnektoren schreiben (Connector-SDK)

CentralStation ist um eine kleine, klar definierte Connector-Schnittstelle gebaut. Ein neuer
Konnektor — egal ob Monitoring, Ticketing oder Inventar — braucht nur **4 Schritte**. Dieser
Abschnitt ist als **LLM-Skill** gedacht: Ein KI-Agent (Claude CLI, Codex, eigener Agent) kann
ihn als Kontext laden und einen lauffähigen Konnektor in einem Durchgang erzeugen.

### Architektur in einem Bild

```
ConnectorConfig (DB, Fernet-verschlüsselt: base_url + credentials)
      │
      ▼
get_connector(type, base_url, credentials)      # Factory  (connectors/__init__.py)
      │
      ▼
class MyConnector(BaseConnector)                # deine Klasse (connectors/my.py)
   ├── test_connection() -> ConnectorTestResult # Pflicht: Erreichbarkeit + Auth
   └── get_problems() / get_alerts() / ...      # Datenmethode(n)
      │
      ▼
collect_my(connector, time_range_minutes)       # Mapping → Feed-Alert-Dicts (alert_aggregator.py)
      │
      ▼
cs-feed-{source} (OpenSearch)  →  Feed · Brücke · Dashboard · KI-Agent
```

### Schritt 1 — Connector-Klasse

`backend/app/services/connectors/<type>.py`. Erbt von `BaseConnector` (liefert `self.base_url`,
`self.credentials`, `self._client()` mit `verify=False` für Self-Signed-Certs).

```python
from app.schemas.connector import ConnectorTestResult
from app.services.connectors.base import BaseConnector


class MyConnector(BaseConnector):
    def _headers(self) -> dict:
        # Auth aus den (entschlüsselten) credentials bauen
        token = self.credentials.get("api_token", "")
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    async def test_connection(self) -> ConnectorTestResult:
        """PFLICHT: prüft Erreichbarkeit + Auth. Wird vom 'Verbindung testen'-Button genutzt."""
        try:
            async with self._client() as client:
                r = await client.get(f"{self.base_url}/api/status", headers=self._headers())
            r.raise_for_status()
            return ConnectorTestResult(success=True, message="MySystem erreichbar")
        except Exception as e:
            return ConnectorTestResult(success=False, message=str(e))

    async def get_problems(self) -> list[dict]:
        """Datenmethode: liefert offene Probleme im EINHEITLICHEN Schema (s.u.)."""
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

**Einheitliches Problem-Schema** (so erwartet es der Aggregator):

| Feld | Typ | Pflicht | Bedeutung |
|------|-----|---------|-----------|
| `severity` | `critical\|high\|medium\|low\|info` | ✓ | normalisierte Severity |
| `host` | str | ✓ | Hostname (Korrelations-/Filter-Schlüssel) |
| `service` | str | ✓ | betroffener Dienst/Check |
| `output` | str | – | Plugin-/Status-Text |
| `acknowledged` | bool | – | quittiert? |
| `last_state_change` | epoch/ISO | – | Zeitpunkt des Statuswechsels |
| `host_address` | str | – | IP |
| `metadata` | dict | – | `os`, `location`, `criticality`, `ve` … (CheckMK-Filter greifen darauf) |

### Schritt 2 — In der Factory registrieren

`backend/app/services/connectors/__init__.py` → Import + Mapping-Eintrag:

```python
from app.services.connectors.my import MyConnector
mapping = { ..., "my": MyConnector }
```

`backend/app/api/connectors.py` → `VALID_TYPES` ergänzen (`"my"`). Optional in
`USER_MANAGED_TYPES`, wenn jeder User (statt nur Admin) den Konnektor anlegen darf.

### Schritt 3 — Collector im Aggregator

`backend/app/services/alert_aggregator.py` — mappt die Connector-Ausgabe auf Feed-Alert-Dicts:

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
        "external_id": f"my:{i['host']}:{i['service']}",   # STABILER Dedup-Key!
        "external_url": f"{connector.base_url}/host/{i['host']}",
        "metadata": {**(i.get("metadata") or {}), "host": i["host"], "service": i["service"]},
    } for i in items]

# weiter unten in der _COLLECTORS-Map:
_COLLECTORS = { ..., "my": collect_my }
```

**`external_id` ist der wichtigste Wert**: stabiler, deterministischer Dedup-Key über alle Läufe
(z.B. `my:host:service`). Daran hängen Deduplizierung, Incident-Korrelation, Claim/Status und Timeline.

### Schritt 4 — Frontend-Formularfelder (optional)

`frontend/src/app/features/settings/connectors/connector-form/` → `CRED_FIELDS` um die Felder
des neuen Typs ergänzen (z.B. `api_token`, `username`/`password`). Ohne Eintrag erscheint der
Konnektor nicht im Anlege-Dialog (lässt sich aber per API/Seed anlegen).

### Checkliste

- [ ] `MyConnector(BaseConnector)` mit `test_connection()` + Datenmethode
- [ ] In `get_connector()`-Factory + `VALID_TYPES` registriert
- [ ] `collect_my()` + `_COLLECTORS`-Eintrag im Aggregator
- [ ] `external_id` ist stabil und deterministisch
- [ ] Severity auf `critical|high|medium|low|info` normalisiert
- [ ] (optional) Frontend-`CRED_FIELDS`
- [ ] `POST /api/connectors/{id}/test` grün

> **Referenz-Implementierungen:** `checkmk.py` (Bearer, Monitoring → `get_problems`),
> `wazuh.py` (JWT-Login, Security → `get_alerts`), `graylog.py` (Views-API, Logs).
> Ein vollständiger Beispiel-Konnektor für **Icinga2** liegt im Fork
> `github.com/imoes/CentralStation` und ist exakt nach dieser Anleitung gebaut.

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
| `ticket_seen_map` | JSON `{jira_key: ISO-Zeit}` — Ticket-Badge-Tracking (ersetzt localStorage) |
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
- `llm.provider` — `custom` (lokaler Endpunkt, default) oder `openai-codex` (OAuth, kein API-Key nötig)
- `llm.base_url`, `llm.model`, `llm.api_key` — für `custom`-Provider
- `llm.codex_model` — für `openai-codex`-Provider (z.B. `gpt-5.5`)
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
| `/api/feed/{item_id}/enrich` | POST | KI-Anreicherung (it-aikb RAG + optional SearXNG) für einzelnes Item |
| `/api/feed/{item_id}/ignore` | POST | KI generiert OpenSearch-Ausschluss-Query → als System-Exclusion-Suche speichern |
| `/api/feed/incidents` | GET | Offene Incidents (status: open/investigating) |
| `/api/feed/incidents/{id}/timeline` | GET | Chronologische Timeline eines Incidents (Alerts + Kommentare + KI-Diagnosen) |

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
| `/api/dashboard-widgets/dashboards/{id}/suggest-layout` | POST | Generativen Layout-Vorschlag berechnen (schreibt nicht selbst) |
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
| `/api/ai/trigger/{agent_type}` | POST | Agent manuell auslösen (`sysadmin` / `network`) |
| `/api/ai/analyses` | GET | Letzte KI-Analysen (ai_analyses) |
| `/api/ai/analyses/{analysis_id}` | GET | Einzelne Analyse (Deep-Link aus KI-Summary-Widget: `/ai-insights?analysis=<id>`) |

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
| `/api/workflow/{id}/generate-comment` | POST | KI-Jira-Kommentar generieren (it-aikb DeepSearch-Kontext, 300 s Timeout) |
| `/api/workflow/{id}/post-comment` | POST | Kommentar in Jira posten (`{"comment": "..."}`) — KI-generiert oder manuell |
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
| `0014` | `user_preferences.ticket_seen_map` (JSON) — serverseitiges Ticket-Badge-Tracking |
| `0015` | `dashboards.mode` (`classic`/`generative`), `dashboard_widgets.pinned`, `dashboard_widgets.hidden` — Generativer Modus |
| `0016` | `alert_score_adjustments` — adaptives Scoring (Feedback-Loop, Deltas mit Verfall) |
| `0017` | `worklist_snapshots`, `ai_insight_cache` — KI-Worklist-Cache und Alert-Insight-Cache |
| `0018` | `user_preferences.ui_theme` (`classic`/`holo`/`lcars`) — app-weites Theme |
| `0019` | `dashboards.rationale`, `dashboards.generated_at` — Generatives Dashboard mit KI-Lagebild |
| `0020` | `alert_collaboration` + `alert_comments` — kollaboratives Alert-Handling (Claim/Status/Timeline) |
| `0021` | `incidents` + `incident_members` — automatische Incident-Korrelation (FQDN-only, 30-Min-Zeitfenster, Cross-Source) |

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
