# CentralStation: Vorschläge für eine ruhigere Oberfläche

Ergänzung vom 17. September: Der [Logik-Audit mit Live-Prüfung](logik-audit.md) und der [überarbeitete Arbeitsbereich](arbeitsbereich.html) berücksichtigen nun Ticketübersicht, Kanban, Projektmanager und Werkbank einschließlich ihrer Redundanzen. Die folgende Recherche dokumentiert den ersten Entwurf.

Stand: 16. September 2026. Grundlage: Internetrecherche und Prüfung der Angular-Templates und Styles. Die produktive Oberfläche wurde nicht mit echten Nutzerdaten betrachtet; dies ist eine heuristische Einschätzung, kein Usability-Test. Die Screenshots zeigen neue Konzepte mit erfundenen Daten. Anwendungscode wurde nicht verändert.

## Befund im vorhandenen Frontend

- **Navigation:** `frontend/src/app/app.ts` definiert 13 gleichrangige Einträge, plus optional Maschinenraum. Die tatsächliche Anzahl hängt von Rolle und freigeschalteten Funktionen ab. Brücke, Problemboard und Server-Cockpit blenden die normale Navigation aus. Dieser Wechsel kann die Orientierung erschweren.
- **Brücke:** `bridge.component.ts` verteilt Quellen, Sektoren, Prioritäten, KI-Diagnosen, primären Incident, Incident-Gruppen, Prognosen, Metriken und Logs auf mehrere Bereiche. Einige erscheinen nur bei vorhandenen Daten. Besonders im Störungsfall können daher viele Elemente zugleich Aufmerksamkeit verlangen.
- **Gestaltung:** Die Brücke verwendet auch im Classic-Theme zahlreiche Karten und Schatten sowie eine pulsierende kritische Statusanzeige. Holo ergänzt Leuchteffekte; LCARS nutzt starke strukturelle Farben. Das sind unterschiedliche Gestaltungsschwerpunkte, keine pauschal schlechten Themes.
- **Vorhandene gute Ansätze:** Der Feed blendet erweiterte Filter standardmäßig aus. Das Dashboard besitzt einen separaten Konfigurationsmodus. Diese Mechanismen sollten erhalten und vereinheitlicht werden. Die Begründung des generativen Dashboards ist hingegen initial aufgeklappt.

## Empfehlungen, nach Nutzen und Eingriffstiefe

### 1. Visuelle Gewichtung vereinheitlichen — zuerst

Neutrale Flächen, wenige Schrifthierarchien, dezente Trennlinien und ein Akzent für aktive Navigation und Hauptaktionen. Rot und Gelb gezielt für Zustände einsetzen, stets mit Text oder Symbol. Dauerhaftes Pulsieren und dekorative Leuchteffekte reduzieren; Status, Schweregrad und Datenalter bleiben erkennbar. Sektionen durch Abstände gruppieren, statt jede Information mit einer eigenen kräftigen Karte auszuzeichnen.

Begründung: Kontrast, Größe und Gruppierung lenken Aufmerksamkeit. Wenn fast alles hervorsticht, wird die Rangfolge schwerer erkennbar. [NN/g: Visual Hierarchy](https://www.nngroup.com/articles/visual-hierarchy-ux-definition/).

### 2. Einen klaren Einstieg schaffen

Als Ausgangsentwurf: drei arbeitsrelevante Kennzahlen, priorisierte Probleme, eigene Arbeit und ein kompakter Datenquellenstatus. Drei ist eine Designentscheidung für diesen Entwurf, keine allgemeine UX-Regel. Die Auswahl sollte anhand realer Aufgaben validiert werden.

Brücke als explizite Vollbild-/Leitstandansicht erreichbar halten. Im normalen Arbeitsfluss eine konsistente Navigation verwenden. Die Verbindung zwischen Datenquelle und CentralStation getrennt vom Gesundheitszustand überwachter Hosts darstellen.

Begründung: Inhalte auf den Zweck einer Ansicht ausrichten und konkurrierende, nachrangige Informationen reduzieren. [NN/g: Aesthetic and Minimalist Design](https://www.nngroup.com/articles/aesthetic-minimalist-design/).

### 3. Details beim ausgewählten Problem zeigen

Liste: Priorität, Problem, Host, Alter, Bearbeitung und eine erkennbare Detailaktion. Diagnose, Verlauf, korrelierte Meldungen, Logs und Metriken im Detailbereich. Auf breiten Bildschirmen bleibt die Liste daneben sichtbar; auf schmalen Ansichten folgen Details darunter oder auf einer Detailseite mit erhaltener Listenposition.

Häufig benötigte Funktionen sichtbar lassen. Selten benötigte Optionen unter verständlich benannten Einstiegen anbieten. Kritische Vorfälle und ausgefallene oder veraltete Datenquellen gehören weiterhin in die Übersicht.

Begründung: Progressive Disclosure priorisiert häufig benötigte Informationen. Für komplexe Anwendungen sollte der Wechsel zu Details den Arbeitskontext möglichst erhalten. [NN/g: Progressive Disclosure](https://www.nngroup.com/articles/progressive-disclosure/), [NN/g: Complex Applications](https://www.nngroup.com/articles/complex-application-design/).

### 4. Navigation nach Aufgaben bündeln — anschließend testen

| Einstieg | Bestehende Funktionen |
|---|---|
| Übersicht | Dashboard; Brücke als Leitstandansicht |
| Probleme | Problemboard, Incidents; passende KI-Einschätzungen im Kontext |
| Ereignisse | Feed; administrative Alert-Ansicht als Unteransicht |
| Meine Arbeit | Meine Tickets, Kanban, Projekte |
| Infrastruktur | Topologie, Einstieg in Host-Cockpits |
| Werkzeuge | Werkbank, Maschinenraum; vollständige KI-Analysen weiter erreichbar |
| Einstellungen / Hilfe | Im unteren Navigationsbereich |

Dies ist eine zu prüfende Informationsarchitektur. Bestehende Funktionen, Berechtigungen und Direktlinks erhalten. Fachbegriffe ergänzen: beispielsweise „Maschinenraum · Automatisierung“, wenn die Metapher allein neuen Nutzern zu wenig Orientierung gibt.

### 5. Filter und Aktionen konsolidieren

Eine gemeinsame Werkzeugleiste für Suche, Schweregrad und weitere Filter. Aktive Filter als entfernbare Chips anzeigen. Quellenfarben im Feed zurücknehmen, damit sie nicht mit Schweregraden konkurrieren. Sammelaktionen erst nach Auswahl von Einträgen anbieten. Für lange, vergleichbare Datensätze kompakte Tabellen statt vieler Einzelkarten verwenden.

Begründung: Das Carbon Design System beschreibt gemeinsame Tabellenwerkzeugleisten, ausklappbare Details und kontextuelle Sammelaktionen. [Carbon: Data Table](https://carbondesignsystem.com/components/data-table/usage/).

## Entwürfe

- [A: Ruhige Übersicht](entwurf-a.png): klarer Einstieg und priorisierte Problemliste.
- [B: Fokussierte Arbeitsansicht](entwurf-b.png): Liste mit ausgewähltem Problem und Detailbereich; dunkle Darstellung als optionale Präferenz.
- [Interaktive Vorschau](konzepte.html): lokal im Browser öffnen. Links oben rechts wechseln die Ansichten. Suche, Schweregrad, Quellenfilter und Details sind bedienbar. Weitere Navigationsbereiche sind nur dargestellt; Aktionen werden nicht an ein Backend gesendet.

Layout und Farbschema sind unabhängig: Beide Ansichten könnten hell oder dunkel angeboten werden. Die Entwürfe enthalten keine neuen Logos oder Bildassets, sondern direkt gerenderte HTML/CSS-Oberflächen. Beide Desktopansichten wurden in Chromium gerendert und visuell geprüft; ein vollständiger Interaktions- oder Barrierefreiheitstest war nicht Teil dieser Recherche.

## Überprüfung mit dem Team

Aktuelle Oberfläche und Entwurf mit denselben Beispieldaten vergleichen: dringendstes unzugewiesenes Problem finden, betroffenen Host und Ursache untersuchen, eigenes Ticket wiederfinden. Bearbeitungszeit, Fehlklicks, Rücksprünge und übersehene Warnungen beobachten. Insbesondere erfahrene Admins einbeziehen, damit die ruhigere Darstellung ihre täglichen Abläufe tatsächlich unterstützt.

Erster Umsetzungsschritt: Farben, Schrifthierarchie und Gruppierung beruhigen; anschließend Navigation und Detailansicht erproben. Die vorhandenen Filter- und Konfigurationsmodi bieten dafür eine gute Grundlage.
