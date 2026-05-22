# CentralStation

Zentrales IT-Operations-Dashboard für Linux-Systemadministratoren.  
Aggregiert Alerts aus Wazuh, Graylog und CheckMK, synchronisiert Jira-Tickets und
unterstützt mit KI bei der gesamten ITIL-konformen Arbeitsdokumentation.

---

## Features

| Bereich | Funktionen |
|---------|------------|
| **Alert-Aggregation** | CheckMK, Graylog, Wazuh — zentrale Timeline, Acknowledge, Severity-Filter |
| **Kanban-Board** | Drag-Drop, bidirektionaler Jira-/ServiceDesk-Sync, automatische Jira-Importe, AI-erstellte Cards |
| **Meine Tickets** | Per-User JQL-Filter-Verwaltung, KI-JQL-Generator (Freitext → JQL), Live-Jira-Ergebnisse |
| **Arbeitsdokumentation** | ITIL Work Sessions: Impact/Urgency/Priorität P1–P4, SLA-Tracking, Arbeitsnotizen |
| **KI-Kommentare** | Fortschritt, Pending, Eskalation, Übergabe — per KI generiert, direkt in Jira kopierbar |
| **Abschlussdokumentation** | KI-generierte Lösungsdokumentation mit Root Cause, Maßnahmen, Closure Code |
| **5-Why-Analyse** | ITIL Problem Management — KI führt 5-Why-Analyse durch, schlägt Kernursache vor |
| **Lösungssuche** | RAG-Suche in it-aikb Wissensdatenbank + SearXNG Web-Suche, HyDE-Pattern |
| **Mail-Analyse** | O365 E-Mail → strukturierte Ticket-Informationen per KI |
| **Netzwerk-Modul** | Switch-Alerts (NSA/NSS/NSC), Standort-Zuordnung (ID-Generator), Vendor-Erkennung |
| **KI-Insights** | LangGraph Agenten (SysAdmin + Network), alle 10 Min., Jira Auto-Create |
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
              ├── LangGraph (SysAdmin + Network AI Agents)
              └── Externe Systeme (CheckMK, Graylog, Wazuh, Jira, O365, ...)
```

---

## KI-Templates (workflow_ai.py)

Alle KI-Funktionen nutzen OpenAI-kompatible Endpunkte (konfigurierbar über Frontend → Einstellungen → KI).

---

## Persönliche Konnektoren

Neben globalen Admin-Konnektoren unterstützt CentralStation benutzerspezifische Konnektoren für:

- `checkmk`
- `graylog`
- `wazuh`
- `jira`
- `jira_sd`
- `o365`
- `teams`

Diese werden im Setup-Wizard unter **Meine Konnektoren** gepflegt und gelten nur für das jeweilige Benutzerkonto.

Verwendung:

- `checkmk`, `graylog`, `wazuh`: persönliche Zugänge für Monitoring- und Log-Zugriff
- `jira`, `jira_sd`: persönliche Ticket-Sicht und Kanban-Sync
- `o365`: persönlicher Microsoft-Graph-Zugriff plus Postfachzuordnung
- `teams`: persönlicher Microsoft-Graph-Zugriff plus kanalbezogener Feed

Für O365- und Teams-Feeds werden persönliche Konnektoren bevorzugt; nur wenn keine vorhanden sind, fällt das System auf globale Konnektoren zurück.

| Template / Funktion | Endpunkt | Beschreibung |
|---------------------|----------|--------------|
| `generate_comment` | `POST /api/workflow/{id}/generate-comment` | Jira-Kommentar (Fortschritt / Pending / Eskalation / Übergabe) |
| `generate_resolution` | `POST /api/workflow/{id}/generate-resolution` | Lösungsdokumentation mit Root Cause und Maßnahmen |
| `auto_categorize` | `POST /api/workflow/{id}/auto-categorize` | Kategorie, Unterkategorie, Impact, Urgency aus Titelbeschreibung |
| `suggest_solution` | `POST /api/workflow/{id}/suggest-solution` | Lösungsschritte + RAG (it-aikb) + SearXNG-Websuche |
| `analyze_mail` | `POST /api/workflow/analyze-mail` | O365 E-Mail → strukturiertes JSON (Zusammenfassung, Dringlichkeit, Ticket-Key) |
| `run_5why_analysis` | `POST /api/workflow/{id}/5why` | 5-Why Root Cause Analysis (ITIL Problem Management) |
| `generate_jql` | `POST /api/preferences/jira-queries/generate` | **KI JQL-Generator**: Freitext-Beschreibung → valide Jira JQL-Query + Name |

### KI JQL-Generator

Beschreiben Sie den gewünschten Ticket-Filter auf Deutsch — das KI-Modell übersetzt automatisch in valide JQL:

```
Eingabe:  "meine offenen Bugs mit hoher Priorität aus dieser Woche"
Ausgabe:  { "jql": "assignee = currentUser() AND issuetype = Bug AND priority in (Highest, High) AND created >= -7d AND status != Done ORDER BY priority ASC, updated DESC",
            "name": "Meine Bugs (hoch, diese Woche)" }
```

Aufruf über die UI: **Meine Tickets → Filter verwalten → KI erstellen**

---

## Kanban und Jira

Das Kanban-Board arbeitet bidirektional mit Jira bzw. Jira ServiceDesk:

- offene, dem aktuellen Benutzer zugewiesene Jira-/ServiceDesk-Tickets werden automatisch als Kanban-Karten importiert
- lokale Karten können per Aktion als Jira-Ticket erstellt werden
- Änderungen an Titel, Beschreibung und Priorität von Jira-verknüpften Karten werden nach Jira zurückgeschrieben
- Statuswechsel per Drag-and-Drop lösen passende Jira-Transitions aus

Für den Statusabgleich nutzt CentralStation folgende Zuordnung:

- `backlog` → `Backlog`, `Open`, `Selected for Development`
- `todo` → `To Do`, `Open`, `Ready`
- `in_progress` → `In Progress`, `Doing`, `Implementing`, `In Bearbeitung`
- `review` → `Review`, `In Review`, `Testing`, `QA`
- `done` → `Done`, `Resolved`, `Closed`, `Erledigt`

Wenn im Jira-Projekt keine passende Transition vorhanden ist, bleibt die Karte lokal unverändert und die API liefert einen Fehler zurück.

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

# Datenbank-Migrationen
docker compose exec backend alembic upgrade head

# Frontend Build (Produktion)
docker compose exec frontend ng build --configuration production
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

## API-Übersicht

| Pfad | Methode | Beschreibung |
|------|---------|--------------|
| `/api/auth/login` | POST | Login (rate-limited: 10/min) |
| `/api/auth/refresh` | POST | Token-Refresh (HttpOnly Cookie) |
| `/api/preferences` | GET, PATCH | Benutzer-Präferenzen + Setup-Status |
| `/api/preferences/jira-queries` | GET, POST | JQL-Filter verwalten |
| `/api/preferences/jira-queries/generate` | POST | **KI JQL-Generator** |
| `/api/jira-view/my-tickets` | GET | Jira-Tickets nach aktiven JQL-Filtern |
| `/api/connectors/my` | GET | Persönliche Konnektoren des aktuellen Benutzers |
| `/api/connectors/my/{type}` | PUT | Persönlichen Konnektor anlegen/aktualisieren |
| `/api/connectors/my/{type}/test` | POST | Persönlichen Konnektor testen |
| `/api/workflow` | GET, POST | Work Sessions (ITIL) |
| `/api/workflow/{id}` | GET, PATCH, DELETE | Work Session CRUD |
| `/api/workflow/{id}/notes` | POST | Arbeitsnotiz hinzufügen |
| `/api/workflow/{id}/generate-comment` | POST | KI-Ticket-Kommentar |
| `/api/workflow/{id}/generate-resolution` | POST | KI-Lösungsdokumentation |
| `/api/workflow/{id}/5why` | POST | 5-Why Root Cause Analyse |
| `/api/workflow/{id}/suggest-solution` | POST | RAG + Web Lösungssuche |
| `/api/workflow/{id}/auto-categorize` | POST | KI Auto-Kategorisierung |
| `/api/workflow/analyze-mail` | POST | E-Mail-Analyse |
| `/api/alerts` | GET | Aggregierte Alerts |
| `/api/kanban` | GET, POST, PATCH | Kanban-Karten inkl. Jira-Import |
| `/api/ai/trigger/{type}` | POST | KI-Agent manuell triggern |
| `/api/settings` | GET, PATCH | Globale Einstellungen (Admin) |
| `/api/audit` | GET | Audit-Log (Admin) |
| `/ws/{user_id}` | WS | Real-Time Push |

---

## Verbindungstypen (Konnektoren)

| Typ | Auth | Beschreibung |
|-----|------|--------------|
| `checkmk` | Bearer Token | CheckMK REST API (Monitoring) |
| `graylog` | Basic Auth | Graylog (Log-Aggregation) |
| `wazuh` | JWT (eigene Auth) | Wazuh SIEM |
| `jira` | Bearer PAT | Jira Tickets + Kanban-Sync |
| `jira_sd` | Bearer PAT | Jira ServiceDesk Tickets + Kanban-Sync |
| `o365` | OAuth2 client_credentials | O365 Microsoft Graph (Mail) |
| `teams` | OAuth2 client_credentials | Microsoft Teams / Graph Feed |
| `prometheus` | Optional Basic/Bearer | Metriken |
| `netbox` | Bearer Token | IP-Inventar, VMs |
| `id_generator` | Basic Auth (READ-ONLY) | Standort-Stammdaten (ippen.media) |
| `it_aikb` | Bearer Token | RAG-Wissensdatenbank (it-aikb) |

Alle Zugangsdaten werden mit Fernet (AES-128-CBC) verschlüsselt in der Datenbank gespeichert.

---

## Rollen

| Rolle | Zugang |
|-------|--------|
| `admin` | Vollzugriff inkl. Benutzer, Konnektoren, Audit-Log |
| `sysadmin` | Alerts, Kanban, Meine Tickets, KI-Insights, Workflow |
| `network_technician` | Switch-Alerts, Netzwerk-Kanban |
| `viewer` | Lesen (rollenbasiert) |

---

## Entwicklung

```bash
# Backend (dev)
cd backend
python -m uvicorn app.main:app --reload --port 8000

# Frontend (dev)
cd frontend
npm install
ng serve --port 4200
```
