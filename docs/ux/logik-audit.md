# Logik-Audit: Tickets, Projekte und Werkbank

Stand: 17. September 2026. Angewandter Skill: `/home/mutkluge/skills/logik.md`.

## Prüfziel und Stand der Live-Prüfung

Geprüft: Ticketübersicht, Kanban, Projektmanager, Arbeitsdialog und Werkbank einschließlich ihrer APIs und Datenmodelle. Zusätzlich Brücke und Navigation, soweit sie dieselben Aufgaben und Zustände darstellen.

Die Anmeldung unter `https://hal.example.com` war mit der nachgereichten E-Mail-Adresse erfolgreich. Im angemeldeten Browser wurden Dashboard, Meine Tickets, Kanban, Projektübersicht, ein Projekt mit Netzplan und Listenansicht sowie die Werkbank mit eingebetteter VS-Code-Oberfläche betrachtet. Das Konto sieht zwölf Hauptnavigationseinträge und verwendet das LCARS-Theme. Zugangsdaten wurden nicht in dieses Dokument oder den Entwurf übernommen.

Die Arbeit konzentrierte sich auf Anzeige und Navigation. Der eigene IDE-Arbeitsplatz wurde über dessen normalen Einstieg geöffnet. Keine Ticketänderungen, Projektänderungen, Jira-Synchronisationen, KI-Aufträge oder Terminalbefehle wurden ausdrücklich ausgelöst. Das Kanban-Backend führt laut Code beim normalen Abruf einen automatischen Jira-Import durch. Der Arbeitsdialog wurde nicht durch Öffnen eines echten Tickets erzeugt, da sein Code dabei bereits eine neue Sitzung anlegt. Fehler- und Schreibpfade sind daher Codebefunde, keine absichtlich ausgelösten Produktionsfehler. Die installierte Version wurde nicht gegen einen Deployment-Commit abgeglichen.

**Live bestätigt:** überlappende Ticketlisten; ein Ticket mit Originalstatus „Blocked“ erscheint im Kanban unter „TO DO“; mehrere nicht unmittelbar vergleichbare Alert-Zahlen auf demselben Dashboard; Projektplanung mit Netzplan/Gantt/Liste; Werkbank als vollwertiger VS-Code-Arbeitsplatz. Rohscreenshots mit Betriebsdaten liegen nur temporär außerhalb des Repositories. Die teilbaren Konzeptbilder verwenden erfundene Daten.

## Begriffe und Zuständigkeiten

| Fachlicher Begriff | Code/API heute | Empfohlene Bedeutung im UI |
|---|---|---|
| Jira-Ticket | JiraIssue, `/jira-view`, `jira_key` | Vorgang mit Jira als Quelle für bestätigten Ticketstatus |
| Lokale Aufgabe | KanbanCard ohne `jira_key` | Eigenständiger Vorgang mit CentralStation als Quelle |
| Board-Karte | KanbanCard, `/kanban` | Darstellung eines Vorgangs, bei Jira-Verknüpfung keine zweite fachliche Aufgabe |
| Projekt | Project, `/projects` | Planung, Struktur, Zeitplan und Abhängigkeiten |
| Projektschritt | ProjectStep, `/projects/.../steps` | Planungsschritt; bei 1:1-Ticketverknüpfung verweist er auf den Vorgang |
| Arbeitssitzung | WorkSession, `/workflow` | Notizen, Arbeitskontext, Branch, MR und technische Durchführung |
| Werkbank | `/workbench`, `/workbench/:id`, Web-IDE | Ausführungsumgebung einer Arbeitssitzung oder freier Arbeitsplatz |
| Assistent | ComputerService, ComputerSession, Projekt-KI | Gemeinsamer Einstieg mit nachvollziehbarem Kontext; spezialisierte Fähigkeiten bleiben erhalten |

Ein Projekt, ein Vorgang und eine Sitzung sind verschiedene Entitäten. Ihre Zusammenhänge sollen durch Referenzen sichtbar werden. Identische Ticketdaten müssen nicht in jeder Ansicht unabhängig bearbeitet und abgeglichen werden. Caches sind zulässig, benötigen aber Quelle, Aktualität und einen kenntlichen Synchronisationszustand. Mehrere passende Zugänge zu demselben Vorgang sind sinnvoll, wenn sie denselben Kontext öffnen.

## Codebefunde und Abgleich mit der laufenden Oberfläche

### 1. [Widerspruchsfreiheit] Projektschritt gespeichert, Jira-Übertragung gescheitert

**Beleg:** `backend/app/services/project_service.py:242` speichert lokal vor dem Jira-Push; `:250` protokolliert einen Push-Fehler, gibt aber den gespeicherten Schritt zurück. `frontend/src/app/features/projects/step-card.component.ts:217` verspricht automatische Übertragung beim Speichern. Zusätzlich übergibt `project_service.py:265` nur ein Connector-Objekt an `JiraConnector`, während der geerbte Konstruktor `backend/app/services/connectors/base.py:9` URL und Credentials erwartet.

**Problem:** Der Speichervorgang kann einen erfolgreichen lokalen Zustand zeigen, der nicht dem bestätigten Jira-Zustand entspricht; der vorliegende Connector-Aufruf ist zudem inkonsistent zur Signatur.

**Kleinster Fix:** Connector mit entschlüsselten Credentials und URL wie im Kanban-Pfad aufbauen. Jira-Ergebnis explizit zurückgeben und als „Abgleich ausstehend/fehlgeschlagen“ darstellen. Bestätigten Ticketstatus und noch nicht bestätigte lokale Änderung getrennt halten. Auch eine nicht gefundene Transition auswerten. Reproduktion durch fehlgeschlagenen Push mit anschließendem Vergleich beider Ansichten.

### 2. [Ausgeschlossenes Drittes / gültige Ableitung] Fehlende oder gefilterte Daten ergeben „alle Systeme nominal“

**Beleg:** `frontend/src/app/features/bridge/bridge.component.ts:623` gibt für jeden Zustand außer rot/gelb „ALLE SYSTEME NOMINAL“ zurück, auch bei `null`. `:652` beendet bei Ladefehler lediglich den Ladezustand. `:211` zeigt denselben globalen Text bei leerer gefilterter Arbeitsliste; die Filterung erfolgt in `:613`.

**Problem:** Unbekannter Zustand und leere Teilmenge werden als gesicherte globale Entwarnung ausgelegt.

**Kleinster Fix:** Laden, Fehler, unbekannt, veraltet und bestätigter Zustand ausdrücklich unterscheiden. Für gefilterte Leere „Keine Probleme für diesen Filter“. Globale Entwarnung nur bei entsprechend vollständiger und aktueller Datenlage. Gegenprobe: erster Abruf schlägt fehl; eine Quelle hat keine Treffer, eine andere kritische Probleme.

### 3. [Widerspruchsfreiheit / Identität] Arbeitsstatus wirkt wie Ticketstatus

**Beleg:** `frontend/src/app/features/workflow/work-session-dialog.component.ts:77` zeigt den WorkSession-Status im Kopf neben dem Jira-Key. Im Ticket-Tab wird bei `:116` der Jira-Status separat angezeigt. `backend/app/api/workflow.py:287` aktualisiert die lokale Sitzung; dieser Update-Pfad überträgt keine Jira-Transition.

**Problem:** „Closed“ im Arbeitsdialog kann eine abgeschlossene Sitzung bezeichnen, während das verknüpfte Ticket offen bleibt; die Zustandsobjekte sind nicht klar benannt.

**Kleinster Fix:** „Arbeitsstatus“ und „Ticketstatus · Jira“ explizit beschriften. „Ticket abschließen“ als eigene, bestätigte Jira-Transition behandeln. Ein beendeter Arbeitsplatz darf kein erledigtes Ticket suggerieren.

### 4. [Ausgeschlossenes Drittes / Identität] Unbekannte Jira-Status werden zu „To do“

**Beleg:** `backend/app/api/kanban.py:39` ordnet nach Statusnamen zu; `:51` macht aus allen unbekannten Namen `todo`. „Zu erledigen“ steht bei `:41` und `:43` in zwei verschiedenen Gruppen; erreichbar ist nur die erste. `backend/app/services/project_service.py:581` verwendet eine andere Zuordnung, mit `new` als Fallback. `backend/app/api/kanban.py:230` verschluckt Importfehler und liefert vorhandene Karten weiter.

**Problem:** Unbekannte oder veraltete Daten erhalten einen regulären Arbeitszustand ohne Kennzeichnung.

**Live-Abgleich:** Derselbe Jira-Key wurde in der Ticketübersicht als „Blocked“ und im anschließend geöffneten Board unter „TO DO“ angezeigt. Das bestätigt die praktische Relevanz der verlustbehafteten Zuordnung. Auch „Approved“ und „Voranalyse“ kamen in der Ticketübersicht vor und werden von der angegebenen Namensliste nicht explizit behandelt.

**Kleinster Fix:** Originalstatus und Jira-Statuskategorie erhalten, Namen zentral normalisieren, unbekannte Zuordnungen gesondert anzeigen. Backlog/To do und Review nicht allein aus den drei Jira-Kategorien erraten. Letzten erfolgreichen Abgleich und Abgleichfehler sichtbar machen.

### 5. [Identität / Parsimonie] Wiederholtes Öffnen erzeugt neue Arbeitssitzungen

**Beleg:** `frontend/src/app/features/my-tickets/my-tickets.component.ts:569` übergibt beim Öffnen keinen Sitzungsbezug. `frontend/src/app/features/workflow/work-session-dialog.component.ts:682` erstellt ohne ID eine Sitzung. `backend/app/api/workflow.py:241` legt unconditionally eine neue WorkSession an.

**Problem:** Derselbe Ticketzugang erzeugt wiederholt unabhängige Arbeitskontexte, in denen Notizen und Git-Bezüge auseinanderlaufen können.

**Kleinster Fix:** Beim Anzeigen nur laden. Beim Arbeitsbeginn bestehende aktive Sitzung für Benutzer und Vorgang fortsetzen; eine zusätzliche Sitzung bewusst anlegen. Wiederholung und parallele Aufrufe serverseitig idempotent behandeln. Geschlossene Sitzungen als Historie erhalten. Gegenprobe: dasselbe Ticket zweimal öffnen; derselbe aktive Kontext bleibt bestehen.

### 6. [Parsimonie / Intension und Extension] Projektaufgaben und Jira-Karten konkurrieren im Board

**Beleg:** `frontend/src/app/features/kanban/kanban.component.ts:77` rendert Projektaufgaben zusätzlich zu den Karten bei `:107`, ohne gemeinsamen Abgleich. Der Spaltenzähler bei `:74` zählt nur Karten. `backend/app/services/project_service.py:606` liefert alle nicht erledigten Schritte und `:646` prüft ihre Abhängigkeiten; auch ein Schritt „in_progress“ kann daher in der To-do-Sonderliste erscheinen.

**Problem:** Derselbe verknüpfte Jira-Vorgang kann doppelt auftauchen; „ausführbar“ wird außerdem mit „To do“ vermischt, und Zähler erfassen nicht alle sichtbaren Einträge.

**Kleinster Fix:** Bestehende Karten und verknüpfte Schritte vor dem Rendern zu einer Vorgangsdarstellung zusammenführen. Projekt als Kontext/Badge anzeigen. Arbeitsstatus bestimmt die Spalte, Abhängigkeiten bestimmen die zusätzliche Kennzeichnung „blockiert/ausführbar“. Lokale, unverknüpfte Schritte bleiben eigenständig. Zähler auf dieselbe sichtbare Ergebnismenge beziehen.

### 7. [Identität] Ticket-Key ohne Quelle ist keine hinreichende Identität

**Beleg:** `backend/app/api/kanban.py:97` dedupliziert über `seen_keys`; bei `:131` wird nach `KanbanCard.jira_key` gesucht. `backend/app/models/kanban.py:20` hat keinen zugehörigen Connector-Verweis. Projektschritte speichern bei `backend/app/models/projects.py:70` nur den Connector-Typ.

**Problem:** Bei mehreren unabhängigen Jira-Instanzen können identische Keys verschiedene Tickets bezeichnen; Auswahl eines „bevorzugten“ Connectors kann dann die falsche Quelle treffen. Das Risiko ist bedingt durch eine solche Konfiguration, nicht live nachgewiesen.

**Kleinster Fix:** Eine stabile Identität aus Jira-Instanz und Issue-ID für verknüpfte Tickets führen; konkrete Connector-Zuordnung separat für den Zugriff. Der angezeigte Key bleibt ein lesbarer Alias. Bei Migration Quellen klären, nicht pauschal gleichnamige Tickets zusammenführen.

### 8. [Zureichender Grund] Rangfolge ist nicht vollständig nachvollziehbar

**Beleg:** `frontend/src/app/features/bridge/bridge.component.ts:122` beschreibt die Reihenfolge als KI-vorsortiert. Das WorkItem-Modell führt `score`; die Darstellung bei `:196` zeigt Rang und bei `:203` einen optionalen Text, aber keine verlässliche Aufschlüsselung der Ranggründe.

**Problem:** Ohne zusätzliche Erklärung lässt sich nicht durchgängig beurteilen, warum ein Problem vor einem anderen steht.

**Kleinster Fix:** „Warum priorisiert?“ mit tatsächlich verwendeten Faktoren, Bezugszeit und Datenquelle anbieten. Fehlende Begründung ausdrücklich benennen; keine Erklärung nachträglich erfinden. Auf dem Hauptscreen genügt ein kurzer Grund.

### 9. [Identität] Ticketlink im Projekt führt auf einen Platzhalter

**Beleg:** `frontend/src/app/features/projects/step-card.component.ts:503` baut Ticketlinks mit `https://servicedesk.example.com/browse/...`.

**Problem:** Der angezeigte Ticket-Key verweist nicht zuverlässig auf seine wirkliche Quelle.

**Kleinster Fix:** Die aufgelöste Ticket-URL vom Backend aus der passenden Jira-Instanz liefern; bei unbekannter Quelle keinen irreführenden Link anbieten.

### 10. [Parsimonie] Überlappende Ticketansichten verlängern die gesamte Seite

**Beleg:** Live unter `/my-tickets`: derselbe Vorgang erscheint unter „Hohe Priorität“ und „Meine offenen Tickets“, weitere Vorgänge unter „Von mir geöffnete Tickets“ und „Meine offenen Tickets“. `frontend/src/app/features/my-tickets/my-tickets.component.ts:165` stellt jede konfigurierte Gruppe untereinander dar; bei `:183` werden die Treffer jeder Gruppe separat gerendert.

**Problem:** Nützliche Perspektiven auf dieselben Vorgänge erzeugen wiederholte Zeilen und lange Scrollwege. Dies belegt Präsentationsredundanz, nicht doppelt angelegte Jira-Tickets.

**Kleinster Fix:** Gespeicherte Ansichten als Auswahl oder Tabs; Liste/Board bleiben ein davon unabhängiger Darstellungswechsel. Optional eine gemeinsame deduplizierte Gesamtansicht. JQL auf Abruf bearbeiten, nicht unter jeder Überschrift permanent ausgeben. Teamansichten und eigene Ansichten klar kennzeichnen.

### 11. [Zureichender Grund / Widerspruchsfreiheit] Alert-Zahlen haben keinen klar gemeinsamen Bezug

**Beleg:** Live zeigte das KI-Lagebild „2 critical und 10 high“, daneben standen Kennzahlen von 14 Critical und 2384 High. Das Lagebild war als 21 Minuten alt gekennzeichnet. `frontend/src/app/features/dashboard/dashboard.component.ts:191` zeigt das Alter des Lagebilds; `frontend/src/app/features/dashboard/dashboard-widget.component.ts:77` rendert die Statistikzahl ohne deren Bezugszeitraum direkt am Wert. Zusätzlich erschien im KI-Streifen der technische Wert „NONE“; `dashboard.component.ts:173` gibt den Severity-Wert direkt in Großbuchstaben aus.

**Problem:** Nutzer können nicht sicher beurteilen, ob die Zahlen andere Zeiträume, Filter, Zähleinheiten oder Aktualisierungsstände meinen. Ein tatsächlicher Datenwiderspruch ist damit nicht bewiesen. „NONE“ benennt ebenfalls keinen verständlichen Fachzustand.

**Kleinster Fix:** Zahlen mit Einheit, Scope, Zeitraum und Abgleichzeit auszeichnen; etwa „offene Probleme“ versus „Ereignisse im Zeitraum“. KI-Snapshot als solchen kennzeichnen und möglichst auf denselben Scope beziehen. „NONE“ anhand seiner tatsächlichen Semantik fachlich übersetzen oder als unbekannten Zustand behandeln, niemals pauschal grün darstellen.

### 12. [Identität / Widerspruchsfreiheit] Direkte URL und Navigation führen unterschiedlich zum Ziel

**Beleg:** Im Browser führte ein direkter Neuaufruf von Ticket- bzw. Projekt-URL nach Anmeldung zurück zum Dashboard; der anschließende Klick auf denselben Menüeintrag funktionierte. `frontend/src/app/core/auth/auth.guard.ts:11` lässt einen gespeicherten Token unmittelbar passieren; `:27` prüft die Rolle synchron, obwohl `auth.service.ts` das Profil noch nachlädt.

**Problem:** Die Berechtigung wird beim direkten Einstieg zeitweise aus einem noch nicht geladenen Profil abgeleitet. Diese Codefolge ist eine plausible Erklärung für die beobachtete Umleitung; eine gesonderte Ablaufverfolgung steht aus.

**Kleinster Fix:** Authentifizierung einschließlich Benutzerprofil vor der Rollenprüfung abwarten; Ziel-URL erhalten. Mit kaltem Seitenstart und langsamer Profilantwort prüfen. Das ist besonders für Rücksprünge aus Werkbank, Projekten und externen Ticketlinks relevant.

## Ergebnis der sechs Prüfblöcke

| Prüfblock | Ergebnis |
|---|---|
| Identität | Quellenidentität, Ticket-/Arbeitsstatus und Fortsetzung der Sitzung präzisieren; Befunde 3–5, 7, 9. |
| Widerspruchsfreiheit | Bestätigte Jira-Zustände und lokale Änderungen können auseinanderlaufen; Befunde 1, 3. |
| Ausgeschlossenes Drittes | Unbekannt, veraltet und Ladefehler dürfen keinen regulären Erfolgszustand erhalten; Befunde 2, 4. |
| Zureichender Grund | Rangfolge und Synchronisationsstand benötigen überprüfbare Herkunft; Befunde 1, 4, 8. |
| Gültige Ableitung | Aus einer leeren gefilterten Liste folgt keine globale Systemgesundheit; Befund 2. KI-Ursachendiagnosen wurden nicht live validiert. |
| Intension/Extension und Parsimonie | Plan/Abhängigkeit, tatsächlicher Ticketstatus und Sitzung getrennt führen; redundante Karten/Sitzungen vermeiden; Befunde 3, 5, 6. |

## Vollständigerer Funktionszuschnitt

| Neuer Einstieg | Bestehende Funktionen und Zielort |
|---|---|
| Übersicht | Dashboard mit Widgets und optionaler generativer Zusammenfassung; Brücke als explizite Leitstandansicht |
| Probleme | Problemboard, korrelierte Incidents, Zuständigkeit, Diagnose, Verlauf, Ticketverknüpfung |
| Ereignisse | Feed, Quellfilter, gespeicherte Suchen; administrative Roh-Alerts als berechtigte Unteransicht |
| Tickets & Aufgaben | Meine Tickets, JQL-Ansichten, lokale Aufgaben; Liste und Kanban als Ansichtswechsel derselben Vorgänge |
| Gemeinsames Vorgangsdetail | Beschreibung, Originalstatus, Projektbezüge, Arbeitsnotizen, Historie, ITIL-Felder, Abschlussdokumentation, KI-Unterstützung und Git-Verknüpfungen |
| Projekte | Übersicht, KI-Planer, Hierarchie/Epics, Aufgaben, Gantt, Netzplan, Abhängigkeiten, kritischer Pfad und Jira-Verknüpfungen |
| Werkbank | Freier Arbeitsplatz oder vorhandene Arbeitssitzung mit IDE, Terminal, Dateien, Git/Branch/MR; Rückkehr zu Vorgang und Projekt |
| Hosts & Topologie | Topologie, Netzwerkanalyse, Server-Cockpit, Services, Metriken, Logs |
| Automatisierung | Maschinenraum, Ansible/AWX, Ausführungen und Ergebnisse |
| Gemeinsamer Assistent | Hermes/Computer mit fortsetzbarer Unterhaltung; KI-Insights als Analysehistorie; spezialisierte Projektplanung aus Projekten erreichbar |
| Einstellungen/Hilfe | Connectors, Benutzer, KI, Audit, Feed, Skills, Konsole, persönliche Einstellungen, Einrichtung und Hilfe |

Die Zusammenführung erhält Rollen und Berechtigungen. Unterschiedliche Datenmengen bleiben als benannte Ansichten unterscheidbar („Mir zugewiesen“, „Team“, „Projekt“). JQL-Gruppen dürfen bewusst überlappen; ein Gesamtzähler zählt eindeutige Vorgänge. Ticket-Erstellung nutzt bereits eine geteilte Dialogkomponente in mehreren Bereichen — solche kontextuellen Zugänge müssen nicht entfernt werden.

## Überarbeiteter Entwurf

[Interaktive Vorschau](arbeitsbereich.html), [Ticketübersicht](entwurf-c-tickets.png), [Projektmanager](entwurf-c-projects.png), [Werkbank](entwurf-c-workbench.png).

Der Entwurf zeigt dieselbe Beispielaufgabe CS-142 in drei passenden Kontexten. Ticketliste und Board teilen ihre Daten und gespeicherten Ansichten; Projekte ergänzen Planung; die Werkbank setzt WS-24 fort. Zeitplan ist ausdrücklich Planung. Alle Daten sind erfunden. Drei Desktopansichten wurden gerendert und visuell geprüft; Liste/Board-Wechsel, gespeicherte Ansichten, Suche, Projektauswahl, Zeitplan und Abhängigkeiten wurden mit Playwright geprüft. Es handelt sich um einen Ausschnitt, nicht um eine vollständige Nachbildung aller Funktionen aus der Tabelle. Der IDE-Inhalt ist nur eine Vorschau.

## Gestaffelter Vorschlag zur Entscheidung

**Rangfolge:** zuerst Zustandswahrheit und Ladezustände (1–4, 11), dann verlässliche Zugänge (9, 12), anschließend doppelte Sitzungen und Aufgabenidentität (5–7), danach Darstellung und Erklärbarkeit (8, 10). Die gespeicherten Ansichten aus Befund 10 eignen sich schon vorher als kleiner, unabhängiger UX-Schritt.

1. **Zustände verlässlich machen:** Befunde 1–4 und Ticketlink korrigieren; Abgleichfehler und unbekannte Zustände sichtbar machen. Bestehende Navigation zunächst erhalten.
2. **Redundanz an der Quelle reduzieren:** Sitzungen wiederverwenden; verknüpfte Aufgaben eindeutig identifizieren; Board-Einträge und Zähler zusammenführen. Bestehende Daten erst nach geklärter Zuordnung migrieren.
3. **Arbeitsablauf verbinden:** Gemeinsames Vorgangsdetail, Tickets/Board als Ansichten, Projekte und Werkbank mit erhaltenem Kontext. Notizen und Abschluss nicht mehr in konkurrierenden Dialogen pflegen.
4. **Oberfläche beruhigen:** Einheitliche Navigation, zurückhaltende Gestaltung und sekundäre Details auf Abruf. Mit denselben Aufgaben und Daten gegen die aktuelle Oberfläche prüfen.

Akzeptanzkriterien: zweimaliges Öffnen erzeugt keine unbeabsichtigte Sitzung; ein verknüpfter Vorgang erscheint pro Boardansicht einmal; Jira-Ausfall zeigt keinen bestätigten Abschluss; Rückkehr aus Werkbank erhält Vorgang und Listenfilter; Planung und tatsächlicher Bearbeitungsstand sind unterscheidbar.

Es wurden ausschließlich Audit- und Konzeptdateien angelegt. Keine Anwendungskomponenten, Datenmodelle oder APIs wurden geändert.
