# LCARS-Designvorschriften

Verbindlich für jede eigene (nicht-Material) Ansicht in CentralStation. Grundlage sind
die tatsächlich implementierten Werte in `frontend/src/styles.scss` und
`frontend/src/app/features/bridge/bridge.component.ts`, nicht ein abstrakter Entwurf.

Vorher stand nichts davon an einer auffindbaren Stelle: die Palette als kommentierte
Tokens im Stylesheet, die Strukturmuster nur implizit in der Brücke, die
Schriftkonvention als verstreute Codekommentare. Wer eine neue Ansicht baute, musste
sie aus der Brücke herauslesen oder raten.

## Drei Themes, eine Pflicht

`ThemeService` (`frontend/src/app/core/services/theme.service.ts`) setzt eine Klasse
`cs-theme-{classic|holo|lcars}` an `<html>`. Material-Komponenten folgen automatisch
über die `--mat-sys-*`-Tokens.

**Eigene Ansichten müssen theme-aware sein.** Container-Klasse aus
`themeSvc.theme()` setzen (`[class.t-lcars]` / `[class.t-holo]` / `[class.t-classic]`),
dann je Theme die Farben. Ein generischer Dark-Look genügt nicht — er fällt sofort auf.

**Kanonische Referenz:** `bridge.component.ts` enthält alle drei Themes vollständig
inline. Beim Bau neuer Ansichten von dort übernehmen statt neu erfinden.
Beispielumsetzung: `features/problems/problems.component.ts`.

## Palette

Die offiziellen LCARS-Farben, definiert in `styles.scss` (Z. 146 ff. als Tokens,
Z. 370 ff. als Rotation für Dashboard-Widgets):

| Farbe | Hex | Verwendung |
|---|---|---|
| Neon Carrot | `#FF9933` | primäres Orange — Struktur, Caps, Rails |
| Golden Tanoi | `#FFCC66` | Gold — Schaltflächen, Beschriftungen |
| Tanoi / Butterscotch | `#FFCC99` | helles Cremeorange |
| Anakiwa | `#99CCFF` | Blauakzent, sparsam einsetzen |

Severity, wie in der Brücke verwendet:

| Zustand | Hex |
|---|---|
| CRIT | `#ff5544` (auch `#ff4433`) |
| WARN | `#ffcc00` |
| UNKNOWN | `#99CCFF` |
| OK / Info | `#66cc66` |

Text: `#ffe8a0` (Gold/Creme) als Vordergrund, `#e8a060` (Amber) für Sekundärtext.

**Kein Pink, kein Lila.** `#CC99CC` (Lilac/Mauve) ist ausdrücklich abgelehnt — es wirkt
nicht authentisch. Wo eine vierte Farbe in einer Rotation gebraucht wird, steht
Butterscotch `#FFCC99` an dieser Stelle (siehe `styles.scss:390`).

## Strukturelemente

Aus der Brücke, TNG-authentisch:

- **Grund:** schwarzer Hintergrund `#000`, Schrift `'Antonio','Eurostile',sans-serif`,
  `text-transform: uppercase`
- **Sweep-Bar mit Elbow-Caps:** `.cap { background:#FF9933; width:60px }`, Radien
  `.cap-tl{border-radius:46px 0 0 0}`, `.cap-tr{border-radius:0 46px 0 0; width:30px}`,
  analog `.cap-bl` / `.cap-br`. Im Classic-Theme `display:none`.
- **Pill-Sidebar:** `border-radius: 0 18px 18px 0`, Farben abwechselnd über
  `:nth-child(3n)` → Anakiwa, `:nth-child(3n+1)` → Neon Carrot
- **Panels:** linker Severity-Balken `border-left: 7px solid <farbe>` mit
  `border-radius: 0 8px 8px 0`
- **Zählerblöcke** (`num-cell`): große Zahl über kleinem Uppercase-Label

## Schriftkonvention

Die wichtigste Regel, weil sie am leichtesten übersehen wird:

**Die kondensierte Displayschrift `'Antonio','Eurostile'` nur für Struktur** — Titel,
Beschriftungen, Navigation, Zähler, alles in Großbuchstaben.

**Prosatexte immer in `Roboto,'Helvetica Neue',sans-serif` mit
`text-transform: none`:** Alert-Titel als Inhalt, KI-Analysen und Bewertungen,
Service-Plugin-Ausgaben, Log- und Newszeilen.

Grund: kondensierte Schrift macht längeren Fließtext schwer lesbar.

Global gilt bereits `body { font-family: Roboto }` (`styles.scss`), News Feed und
Dashboard-Widget erben das korrekt. Die Brücke ist der Sonderfall: `.t-lcars` setzt
Antonio auf den gesamten Container, deshalb müssen Prosaelemente dort einzeln
zurückgesetzt werden (`.ip-title`, `.ip-insight`, `.work-verdict`, `.cb-diagnosis`,
`.cb-root`, `.cb-rec`, `.log-line`). Bei neuen Ansichten daran denken.

## Prüfliste für eine neue LCARS-Ansicht

1. Container trägt `[class.t-lcars]` aus `themeSvc.theme()`, alle drei Themes bedient
2. Farben aus der Palette oben, kein Pink
3. Struktur in Antonio/uppercase, **Fließtext in Roboto mit `text-transform:none`**
4. Severity über den linken 7-px-Balken, nicht über Flächenfarbe
5. Gegen die Brücke gegenprüfen — sie ist die Referenz, nicht dieses Dokument
