# Vorschlag: ein lernendes Relevanz-Modell neben dem Regelwerk

Stand 12.11.2026 · Status: **gebaut im Testbetrieb** (Nutzer 12.11.: „gleich implementieren“, siehe CHANGELOG 2026-11-12 und README „relevance model“; N1-MOD steht wie empfohlen nach R1) · Anlass: Auftrag des Nutzers über die
vi-mcp-Sitzung („KI soll auf die Fehlermeldungen trainiert werden … welche relevant sind und welche nur
Rauschen … Sie soll einen Vorschlag machen“)

## Kurzfassung

1. **Trainiert wird kein Sprachmodell.** Trainiert wird ein kleines, lesbares Modell (logistische
   Regression, ein Gewicht je Merkmal), wie es vi-mcp für seinen Router benutzt.
2. **Das Hauptproblem sind die Labels, nicht das Modell.** Unabhängige menschliche Urteile gibt es heute
   praktisch keine (siehe §2). Trainiert man auf die Urteile der Regeln, lernt das Modell nur die Regeln
   nach.
3. **Darum fängt der Plan mit dem Sammeln von Labels an.** Es gibt eine Stichprobe dessen, was die Regeln
   verwerfen, eine tägliche Prüfliste mit ca. 20 Fällen und einen Knopf „relevant / Rauschen“.
4. **Die Ausgabe des Modells wird ein Signal-Hashtag (`signal:modell-rauschen`), kein zweiter
   Entscheidungsweg.** Eine Regel im vorhandenen Testmodus des Regelwerks wertet es aus. Trefferzähler,
   Verdeckung, Commit-Vorschau und Freigabe durch den Admin gibt es dafür schon.
5. **Harte Untergrenzen bleiben.** Schaden (R5), Sicherheit (R6) und Störung in Produktion (R1) kann das
   Modell nie herabstufen.

## 1. Ist-Zustand: die Regeln heute

| Baustein | Ort | Umfang |
|---|---|---|
| Urteilsregeln (Firewall-Prinzip, erste passende entscheidet, Default BLOCK = N0) | `backend/app/services/log_watch/regelwerk.py:133-158` (`STANDARD_FILTER`) | 17 Regeln, davon 2 im Testmodus (N1-INV, N1-FLAT) |
| Incident-Regeln (Gruppe + Fenster + Mindestwerte) | `regelwerk.py:163-181` (`STANDARD_INCIDENT`) | 4 (I-AUSBRUCH, I-FLOTTE, I-KRITISCH, I-HOST) |
| Relevanz-Definition (Intension, versioniert) | `log_watch/definition.py:32-75` | 8 Kriterien „relevant“ (R1–R8), 7 „nicht relevant“ (N0–N6); Code-Version 11 |
| Statistische Signale je Drain-Muster (neu, Anstieg, Ausbreitung, anhaltend) | `error_clusters/rules.py` (`baseline`, `evaluate`, Poisson-Test) | 15-min-Fenster aus 5-min-Buckets |
| Kernel-Signaturen (Schaden) | `error_clusters/critical.py:42-60` | 7 Regex-Signaturen |
| Text-Regeln Rauschen/selbst erzeugt | `log_watch/raster.py:41-52` (`SELBST_APPS`, `TEXT_HASHTAGS`) | z. B. pam_env, ansible |
| Stufe 0 (Graylog-Pipeline, verwirft vor uns) | Graylog `[proc]routing` | nur gelesen (`log_watch/stufe0.py`) |
| KI-Gedächtnis „bekannter Fehler“ | OpenSearch `cs-log-bekannt` (`log_watch/bekannt.py`) | siehe §2 |

Die Regeln entscheiden je Nachricht bzw. je (Muster, Host, Fenster) und setzen `relevanz` 0–3. In die
Historie `cs-logs-*` kommt nur, was die Regeln weiterleiten (Relevanz ≥ 2). Die KI (Trichter,
`log_watch/trichter.py`) sieht nur diese weitergeleiteten Fälle.

## 2. Datenlage — gemessen

Gemessen am 12.11.2026 gegen 07:31 UTC, nur lesend, mit
`docker compose exec -T backend python - < labels.py` (Abfragen gegen PostgreSQL und OpenSearch des
Backends).

**Merkmale (reichlich):**

| Quelle | Menge |
|---|---|
| `log_templates` (Drain-Muster) | 62 506 Muster seit 30.09.2026, 0 stumm geschaltet |
| `log_template_buckets` (5-min-Zähler je Muster) | 1 195 700 Zeilen, 34 220 Muster, 28.10.–12.11. |
| `log_template_hourly` | 509 570 Zeilen, 60 557 Muster, ab 14.10. |
| `raster_lauf` (Zähler je Fenster und Quelle) | je Quelle ca. 1 500 Fenster seit 06.11. |
| `cs-logs-*` (Historie, nur Relevanz ≥ 2) | 56 492 Dokumente: wazuh 21 028, graylog 14 234, switch 8 664, firewall 8 292, checkmk 2 925, ki 1 349 |

**Labels, die nicht von den Regeln stammen (knapp):**

| Labelquelle | Menge | Brauchbar? |
|---|---|---|
| Mensch: `alert_feedback` (quittiert, ausgeblendet, ticket …) | **0 Zeilen** | Die Tabelle gibt es, sie wird aber nicht befüllt. |
| Mensch: Incident mit Ticket (`causal_context` type ticket) | **0** von 27 892 Incidents | nein |
| Mensch: Incident „übernommen“ | **1** | nein |
| Mensch: stumm geschaltete Muster (`log_templates.muted`) | **0** | nein |
| KI-Befunde (`cs-logs-*`, `quelle:ki`) | 1 349 (Relevanz 3: 743, 2: 422, **1: 184**), 06.–12.11. | ja, als Labels zweiter Güte (siehe unten) |
| KI-Gedächtnis `cs-log-bekannt` | 1 935 Fehlerschlüssel (Relevanz 3: 897, 2: 782, 1: 256), alle `aktiv`, **0 widerlegt** | dito |
| Incidents (27 892, davon host 26 901) | entstehen aus den Regeln | **nein als Label** (Zirkelschluss), ja als Merkmal |
| CheckMK-Zustand (CRIT/Recovery) am selben Host | laufend | ja, als Folge-Label (siehe unten) |

**Was daraus folgt:**

- **Ohne neue menschliche Labels ist jedes Modell eine Kopie der Regeln oder der KI.** Das ist die
  wichtigste Aussage dieses Vorschlags.
- **Die 184 KI-Befunde mit Relevanz 1 sind die interessantesten Fälle.** Bei ihnen hat die KI einer
  Weiterleitung durch die Regeln widersprochen (13,6 % der Befunde). Sie sind aber ein LLM-Urteil, nicht
  die Wahrheit.
- **Auswahlverzerrung:** Was die Regeln verwerfen, wird nicht gespeichert, nur in `raster_lauf`
  gezählt. Das Modell sähe also kaum Beispiele für Rauschen, und gerade das soll es lernen.

## 3. Labels: woher, mit welchem Gewicht

Die Labels haben drei Güten. Die Herkunft wird an jedem Label gespeichert, und der Vorrang aus der
Definition gilt auch hier: Mensch vor KI vor Regel.

1. **Gold — Mensch.**
   - Ein Knopf „relevant / Rauschen“ an jeder Nachricht in Feed, Historie und Incident.
   - Er schreibt in die vorhandene `alert_feedback`, mit zwei neuen Arten, z. B. `relevant` und
     `rauschen`.
   - Dazu eine **tägliche Prüfliste mit ca. 20 Fällen**, die das System nach dem größten
     Informationsgewinn auswählt (aktives Lernen):
     - Modell und Regel widersprechen sich;
     - das Modell ist unsicher (P ≈ 0,5);
     - die KI hat herabgestuft.
   - Bei 20 Fällen am Tag wären die 50 Fälle je Klasse, die die Mindestmenge verlangt, nach ca.
     **1 Woche** erreicht (Schätzung).
2. **Silber — KI.**
   - Quellen: KI-Befunde (Relevanz 1 = Rauschen, ≥ 2 = relevant) und `cs-log-bekannt`.
   - Sie bekommen ein kleineres Stichprobengewicht, z. B. 0,3.
   - Sie entscheiden nie allein über „aktiv“. Die Bewertung (§5) läuft **nur gegen Gold**.
3. **Folge — CheckMK.**
   - Label „relevant“: Auf demselben Host geht innerhalb von 60 min nach dem Fenster ein Dienst auf
     CRIT, oder es entsteht ein Incident, der länger als 1 h offen bleibt.
   - Das ist **Vorhersage, keine Ursache**: Benannt wird es als „ging einem Problem voraus“, nie als
     „verursachte“ (kein post hoc).
   - CheckMK ist ein unabhängiges System. Für Graylog-, Switch- und Firewall-Muster ist das Label daher
     kein Zirkelschluss. Für CheckMK-Nachrichten selbst ist es ausgeschlossen.

**Stichprobe gegen die Auswahlverzerrung:**
- Von jeder BLOCK-Entscheidung wird 1 % zufällig gespeichert, in einem eigenen Index
  `cs-logs-stichprobe-*`, mit Regel-ID, Kriterium und allen Feldern.
- Die Prüfliste zieht auch daraus.
- Erst damit kann das Modell lernen, wo die Regeln zu viel wegwerfen.
- Das Volumen wird vor dem Bau einen Tag lang im Trockenlauf gezählt; Grundlage sind die Zähler in
  `raster_lauf`.

## 4. Beobachtung, Merkmale, Modell

**Beobachtung** = ein Fehlerschlüssel (Drain-Muster bzw. Wazuh-Regel, CheckMK-Dienst, Firewall-Art) × Host ×
15-min-Fenster. Das ist dieselbe Einheit, die `rules.py` (WINDOW_BUCKETS = 3) und `cs-log-bekannt`
(`schluessel`) schon benutzen.

**Merkmale.** Alle stammen aus vorhandenen Daten; nichts wird neu abgefragt.

| Gruppe | Merkmale |
|---|---|
| Identität | `muster:`, `quelle:`, `app:`, `dienst:`, Level, Wazuh-`regel:` |
| Host | `kritikalitaet:`, `ort:`, `hg:` (Hostgruppe), Host selbst (mit Audit, siehe §6) |
| Häufigkeit | Anzahl im Fenster ÷ eigene Basis (`rules.baseline`), Anzahl Hosts (`n_entities`), Tage seit `first_seen` (Neuheit) |
| Umfeld | Anzahl anderer Muster am selben Host im Fenster, CheckMK-Zustand des Hosts im Fenster, Quellen am Host (R7-artig) |
| **Regel-Entscheidung** | entscheidende Regel-ID, Kriterien, `signal:*`, Relevanz der Regel |
| Zeit | Stunde, Wochentag (nur mit Audit, siehe §6) |

Die Regel-Entscheidung ist ein **Merkmal, kein Label**. Damit zeigen die Gewichte, wo das Modell den
Regeln widerspricht. Beispiel: ein stark negatives Gewicht für „regel:R2 und muster:X“ heißt: „R2 leitet
hier weiter, die Menschen nennen es Rauschen.“

**Modell:**
- Logistische Regression mit L2-Regularisierung und Klassengewichten.
- Merkmale erst ab 10 Vorkommen, mindestens 50 Gold-Fälle je Klasse; beides wie vi-mcp
  `learning.py`.
- Training nachts als Job (APScheduler wie die übrigen Jobs), nicht live.
- Die Gewichte liegen als JSON in der DB, z. B. als Tabelle `relevanz_modell` (Version, Gewichte,
  Kennzahlen, Modus).
- Die Vorhersage ist eine Sigmoid-Summe in reinem Python; sklearn wird nur zum Trainieren gebraucht.
- Gradient Boosting erst später und nur, wenn die Regression messbar zu wenig erklärt. Es ist schwerer
  zu lesen.

## 5. Bewertung: wann darf das Modell etwas entscheiden

- **Holdout:** der zeitlich jüngste Teil der Gold-Labels, nie zufällig gezogen. Er wächst, bis beide
  Klassen genug Fälle haben.
- **Kennzahlen:**
  - AUC auf dem Holdout > 0,7.
  - **Recall relevant ≥ 99 %** an der gewählten Schwelle: Von dem, was ein Mensch relevant nannte, darf
    das Modell höchstens 1 % als Rauschen einstufen. Das ist die Kennzahl, auf die es ankommt.
  - Daneben steht der Nutzen: der Anteil der Regel-Weiterleitungen, die das Modell zusätzlich als
    Rauschen erkennt, z. B. „−30 % KI-Aufrufe und Mails bei 99 % Recall“.
- **Modus:**
  - `zu wenig Daten`, wenn die Mindestmengen fehlen.
  - `test`, wenn die Kennzahlen nicht reichen. Das Modell urteilt dann mit, hat aber keine Wirkung.
  - `aktiv` erst nach Freigabe durch einen Admin im Regel-Editor, nicht automatisch.
  - Jeder Zustand nennt seinen Grund.

## 6. Wie Mensch und KI die Gewichte lesen

- **Je Urteil:** An jeder Nachricht stehen die drei Gewichte, die das Urteil tragen, z. B. „Rauschen
  0,91: muster:ab12 (−2,1), stunde:03 (−0,6), regel:R2 (+0,4)“. Sie stehen im vorhandenen Feld
  `gruende`, so wie heute die Regel-ID dort steht.
- **Je Modell:** Eine Seite und ein MCP-Werkzeug `lw_modell` zeigen:
  - Zustand, AUC, Recall, Schwelle;
  - die stärksten Gewichte;
  - die Widerspruchsliste Modell ↔ Regel, sortiert nach Anzahl.

  Das entspricht `vi_model` / `vi_why` in vi-mcp.
- **Gewichts-Audit mit festen Regeln, kein LLM** (wie vi-mcp `weight_audit.py`). Abschließende Klassen:
  - `dominant`: Ein Merkmal trägt mehr als 50 % der Summe.
  - `host`: Ein einzelner Host steht unter den stärksten Gewichten. Dann wird ein Hostzustand gelernt,
    kein Muster.
  - `stunde`: Die Uhrzeit steht unter den stärksten Gewichten. Uhrzeit als Ursache wäre post hoc.
  - `widerspricht`: Das Vorzeichen des Gewichts passt nicht zur gemessenen Trefferquote.

  Ein Audit-Befund hält das Modell im Testmodus, bis ein Mensch es freigibt.
- **Warum feste Regeln:** Ein LLM, das Gewichte prüft, liest auch Logtexte und kann von ihnen gesteuert
  werden. Ein LLM darf hier nur verschärfen (zur Prüfung vorlegen), nie freigeben.

## 7. Einbau: ein Weg, nicht zwei

Das Modell setzt ein **Signal**. Es ist keine eigene Entscheidung:

```
Raster ─► Hashtags/Signale ─► Modell bewertet ─► + signal:modell-rauschen (P ≥ Schwelle)
                                            └─► + signal:modell-relevant (optional, später)
        ─► Regelwerk (erste passende Regel entscheidet)
             … N6, N2, N4, R5, R6, R6-FW, N3, N5,
             N1-MOD  „Modell: Rauschen“  BLOCK  signal:modell-rauschen  [modus test]   ← neu
             N1-TAG, N1-FLAT, R1, R2, R3, R4, R8, N1, Default N0
```

- **Position:** N1-MOD steht nach R5, R6 und N3/N5, aber **vor R1–R4**. So kann das Modell Weiterleitungen
  wegen „neu“ oder „Anstieg“ herabstufen, aber nie Schaden oder Sicherheit; das sind die harten
  Untergrenzen.
- R1 (CRIT in Produktion) steht heute nach N1-TAG. Ob das Modell auch R1 herabstufen darf, entscheidest
  du (siehe §9). Mein Vorschlag ist nein: N1-MOD käme dann nach R1.
- **Vorhanden und wiederverwendet:**
  - Testmodus („hätte N Treffer“), Verdeckung und Trefferzähler je Regel in `raster_lauf`;
  - Commit-Vorschau, Versionierung der Definition, Audit.

  Ein zweiter Entscheidungsweg neben dem Regelwerk wäre ein Logikfehler (Parsimonie).
- **Nichts verschwindet still:** Was N1-MOD blockt, wird mit den tragenden Gewichten gezählt. Ein Teil
  davon kommt in die Stichprobe (§3) und bleibt in der Suche auffindbar (`kriterium:N1 regel:N1-MOD`).
- **Erkundung gegen die Rückkopplung:** 5 % dessen, was N1-MOD blocken würde, wird trotzdem
  weitergeleitet und als `signal:modell-erkundung` markiert. Sonst bekäme das Modell für das, was es
  wegwirft, nie wieder Labels.
- **Nebennutzen:** Heute geht jedes unbekannte Muster mit Relevanz 3 an die KI (`raster_lauf` „ki“: 2 374
  Läufe seit 06.11.). Muster, die das Modell sicher als Rauschen einstuft, brauchen keinen LLM-Aufruf mehr.

## 8. Stufenplan

| Stufe | Inhalt | Wirkung auf Mails/Feed | Abschluss, wenn … |
|---|---|---|---|
| 0 | Labels sammeln: Knopf, Prüfliste, 1 %-Stichprobe der BLOCKs, KI-Befunde als Silber übernehmen, CheckMK-Folgelabel berechnen | keine | ≥ 50 Gold-Labels je Klasse |
| 1 | Training nachts, Modell im Modus `test`, Urteil steht an jeder Nachricht, Seite + `lw_modell` | keine | Holdout-Kennzahlen erreicht, Audit ohne Befund |
| 2 | Widerspruchsliste Modell ↔ Regel an dich; daraus ggf. **neue Regeln** (oft besser als das Modell: lesbar, sofort wirksam) | keine | du hast die Liste gesehen |
| 3 | N1-MOD im Regel-Editor scharf schalten (Commit-Vorschau: „hätte in 24 h N Urteile geändert“) | ja, nur Herabstufung | deine Freigabe |
| 4 | optional: `signal:modell-relevant` hebt Fälle an, die die Regeln verwerfen (N0) | ja | eigene Freigabe |

Stufe 2 ist absichtlich ein Ausgang: Oft ist das Ergebnis nicht „das Modell entscheidet“, sondern „das
Modell hat gezeigt, welche Regel fehlt“. Die neue Regel ist dann lesbar und prüfbar, und das Modell bleibt
im Test.

## 9. Risiken und woran man es merkt

| Risiko | Beobachtungspunkt (Anzeige oder Test) |
|---|---|
| Zirkelschluss: Das Modell lernt nur die Regeln nach | Übereinstimmung Modell ↔ Regel > 95 % **und** die Gewichte der Regel-Merkmale dominieren → kein Mehrwert, im Test lassen |
| Das Modell lernt die KI statt der Menschen | Bewertung nur gegen Gold; AUC getrennt gegen Gold und Silber ausweisen |
| Rückkopplung: was weggeworfen wird, bekommt nie wieder Labels | Erkundungsanteil 5 %; Recall auf den Erkundungsfällen wöchentlich |
| Host statt Muster gelernt | Audit-Klasse `host` |
| Uhrzeit als Ursache | Audit-Klasse `stunde` |
| Drift: Drain-Muster ändern sich, neue Hosts | Anteil Beobachtungen mit unbekanntem Muster bzw. Host; über 30 % → Modus `test` mit Grund |
| Sicherheitsereignis wird herabgestuft | strukturell unmöglich (Position nach R5/R6); Test, der das für alle Standardregeln beweist |
| Zu wenige Gold-Labels, weil niemand klickt | Zähler „Gold-Labels letzte 7 Tage“ im KI-Cockpit; bei < 50 je Klasse bleibt `zu wenig Daten` stehen, sichtbar |

## 10. Was du entscheiden müsstest

1. **Labels:** Knopf „relevant / Rauschen“ und eine tägliche Prüfliste mit ca. 20 Fällen. Ohne sie gibt es
   kein unabhängiges Modell (siehe §2).
2. **Stichprobe der verworfenen Nachrichten:** 1 % in `cs-logs-stichprobe-*`. Das Volumen messe ich
   vorher einen Tag im Trockenlauf.
3. **Folge-Label CheckMK** („ging einem CRIT am selben Host voraus“) zulassen, ja oder nein.
4. **Reichweite:** Darf das Modell nur R2–R4 herabstufen (mein Vorschlag) oder auch R1?
5. **Stufe 4** (Hochstufen von N0-Fällen): jetzt einplanen oder erst nach Stufe 3 entscheiden.

Zum Aufwand nur eine **Schätzung**: Stufe 0 und 1 sind je ein Baublock (Backend und eine Oberfläche);
Stufe 2 und 3 nutzen weitgehend den Regel-Editor, den es schon gibt.

## Quellen

- Regeln:
  - `backend/app/services/log_watch/regelwerk.py:133-181`
  - `log_watch/definition.py:32-140`
  - `error_clusters/rules.py`, `error_clusters/critical.py:42-60`
  - `log_watch/raster.py:41-52`
- Labeltabellen:
  - `backend/app/models/workflow.py:399` (`alert_feedback`, Arten in `services/alert_feedback.py:26`)
  - `models/error_cluster.py`, `models/log_watch.py`
- Messung: Skripte in der Sitzung (nur lesend), 12.11.2026 ca. 07:31 UTC, Backend-Container auf dem
  CentralStation-Host.
- vi-mcp-Verfahren (`learning.py`, `bandit.py`, `weight_audit.py`, Kennzahlen von prod 12.11.2026
  07:28 UTC: 13 932 Beobachtungen, AUC 0,879): Beschreibung der Sitzung bewertung-weight-space-quellen;
  Code unter `/home/mutkluge/Dev/code/vi-mcp`. Hier nicht selbst nachgeprüft.
