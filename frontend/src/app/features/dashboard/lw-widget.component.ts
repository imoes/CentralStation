import { CommonModule, DecimalPipe } from '@angular/common';
import { Component, computed, inject, input } from '@angular/core';
import { MatTooltipModule } from '@angular/material/tooltip';
import { ThemeService } from '../../core/services/theme.service';

/**
 * KI-Cockpit: Widgets der Log-Überwachung (lw_*). Daten aus
 * backend services/log_watch/cockpit.py — jede Zahl trägt Einheit und Zeitraum.
 * Gezeichnet mit CSS statt Diagrammbibliothek (Deference: Inhalt vor Chrom).
 * „Nicht verfügbar“ (z. B. Historie noch im Trockenlauf) zeigt der Rahmen
 * (dashboard-widget), nicht diese Komponente.
 */
const QUELLE: Record<string, string> = {
  graylog: 'Graylog', wazuh: 'Wazuh', checkmk: 'CheckMK', firewall: 'Firewall', switch: 'Switches',
};
const ZUSTAND: Record<string, string> = {
  ok: 'läuft', veraltet: 'veraltet', fehler: 'Fehler', 'nie gelaufen': 'nie gelaufen',
  unvollstaendig: 'unvollständig',
};

@Component({
  selector: 'cs-lw-widget',
  standalone: true,
  imports: [CommonModule, DecimalPipe, MatTooltipModule],
  template: `
    <div class="lw" [class.t-lcars]="theme() === 'lcars'" [class.t-holo]="theme() === 'holo'"
         [class.t-classic]="theme() === 'classic'">
      @switch (typ()) {
        @case ('lw_befunde') {
          <div class="lw-meta">KI-Befunde · {{ d().zeitraum_h }} h · Prüfung: {{ d().pruefung?.wartend ?? 0 }} wartend
            · KI {{ d().pruefung?.ki_aktiv ? 'an' : 'aus' }}</div>
          @if (!befunde().length) {
            <div class="lw-leer">{{ d().grund }}</div>
          }
          @for (b of befunde(); track b.id) {
            <details class="bef" [class.r3]="b.relevanz >= 3">
              <summary>
                <span class="bef-rel" [matTooltip]="'Relevanz ' + b.relevanz + ' von 3'">{{ b.relevanz }}</span>
                <span class="bef-titel">{{ b.titel }}</span>
                <span class="bef-host">{{ b.hostKurz }}</span>
                <span class="bef-zeit">{{ b.zeit | date:'HH:mm' }}</span>
              </summary>
              <p class="bef-text">{{ b.befund }}</p>
              @if (b.hypothesen.length) {
                <div class="bef-sub">Hypothesen</div>
                @for (h of b.hypothesen; track $index) {
                  <div class="hyp"><span class="hyp-st" [class]="'hyp-st st-' + h.status">{{ STATUS[h.status] ?? h.status }}</span>{{ h.text }}</div>
                }
              }
              <div class="bef-sub">Belege · {{ b.belege.length }} {{ b.belege.length === 1 ? 'geprüftes Zitat' : 'geprüfte Zitate' }}@if (b.verworfen) { · {{ b.verworfen }} verworfen }</div>
              @for (z of b.belege; track $index) {
                <div class="zitat" [matTooltip]="z.id">„{{ z.zitat }}“ <span class="zitat-q">{{ z.quelle }}</span></div>
              }
              <div class="bef-sub">Weg der Analyse</div>
              <div class="schritte">
                @for (s of b.schritte; track $index) {
                  <span class="schritt" [matTooltip]="s.filter || ''">{{ s.werkzeug.replace('lw_', '') }}@if (s.gesamt != null) { · {{ s.gesamt }} }</span>
                  <span class="pfeil">→</span>
                }
                <span class="schritt ende" [matTooltip]="b.modell ? 'Modell ' + b.modell : ''">Befund</span>
              </div>
              <div class="tags">
                @for (t of b.hashtags; track t) { <span class="tag-chip">#{{ t }}</span> }
                <span class="tag-chip krit">{{ b.kriterien.join(' ') }}</span>
              </div>
            </details>
          }
        }
        @case ('lw_ki') {
          <div class="lw-meta">KI-Analyse · {{ d().zeitraum_h }} h · {{ d().laeufe }} Läufe · KI {{ d().ki_aktiv ? 'an' : 'aus' }}
            · Kapazität jetzt {{ d().kapazitaet?.faelle_je_lauf }} Fälle je 10 min</div>
          @if (!d().laeufe) {
            <div class="lw-leer">Kein Lauf im Zeitraum{{ d().ki_aktiv ? '' : ' — KI-Trichter ist ausgeschaltet' }}</div>
          } @else {
            <div class="kpis">
              <div class="kpi"><b>{{ d().summe?.befunde ?? 0 }}</b><span>Befunde</span></div>
              <div class="kpi"><b>{{ d().summe?.analysiert ?? 0 }}</b><span>Fälle analysiert</span></div>
              <div class="kpi" [matTooltip]="'Analyse ohne gültigen Befund (z. B. Zeitbudget, kein Abschluss)'">
                <b>{{ d().summe?.ohne_befund ?? 0 }}</b><span>ohne Befund</span></div>
              <div class="kpi" [class.warn]="(d().summe?.verfallen ?? 0) > 0"
                   matTooltip="Auslösende Nachrichten, die das 2-h-Prüffenster ohne Analyse verlassen haben">
                <b>{{ d().summe?.verfallen ?? 0 }}</b><span>verfallen</span></div>
              <div class="kpi"><b>{{ d().modell_s_je_fall ?? '—' }}</b><span>s Modell je Fall</span></div>
            </div>
            <div class="ki-kurve">
              <span class="ki-lbl">Warteschlange<br><b>{{ d().jetzt?.offen }}</b> Nachr. · {{ d().jetzt?.faelle }} Fälle</span>
              <svg class="spark" viewBox="0 0 100 24" preserveAspectRatio="none">
                <polyline [attr.points]="kiSpark()" fill="none" stroke="currentColor" stroke-width="1.5"></polyline>
              </svg>
            </div>
            <div class="ki-urteil" [class.warn]="kiUrteil().warn">{{ kiUrteil().text }}</div>
          }
        }
        @case ('lw_kriterien') {
          <div class="lw-meta">Nachrichten · {{ d().zeitraum_h }} h · Definition v{{ d().definition_v }}</div>
          @if (!kriterienGruppen().length) {
            <div class="lw-leer">Noch keine Urteile im Zeitraum</div>
          }
          <!-- Je Gruppe eine eigene Skala: Routine (Millionen) drückte sonst jeden Grund für
               Relevanz (Hunderte) auf null — gemessen 06.11. -->
          @for (g of kriterienGruppen(); track g.art) {
            <div class="grp-head" [class.rel]="g.art === 'relevant'">
              <span>{{ g.art === 'relevant' ? 'Relevant' : 'Nicht relevant' }}</span>
              <span class="bar-num">{{ g.summe | number }}</span>
            </div>
            @for (k of g.kriterien; track k.id) {
              <div class="bar-row" [matTooltip]="k.tooltip">
                <span class="bar-id" [class.rel]="k.art === 'relevant'">{{ k.id }}</span>
                <span class="bar-label">{{ k.titel }}</span>
                <span class="bar-track"><span class="bar-fill" [class.rel]="k.art === 'relevant'"
                      [style.width.%]="k.anteil"></span></span>
                <span class="bar-num">{{ k.anzahl | number }}</span>
              </div>
            }
          }
        }
        @case ('lw_volumen') {
          <div class="lw-meta">je Quelle · letzte {{ d().zeitraum_h }} h · Fenster gesehen/erwartet</div>
          <div class="vol-head"><span></span><span>gelesen</span><span>relevant</span><span>Verlauf relevant</span><span>Fenster</span></div>
          @for (q of volumen(); track q.quelle) {
            <div class="vol-row">
              <span class="vol-q">{{ q.name }}</span>
              <span class="vol-n">{{ q.gelesen | number }}</span>
              <span class="vol-n rel">{{ q.relevant | number }}</span>
              <svg class="spark" viewBox="0 0 100 24" preserveAspectRatio="none">
                <polyline [attr.points]="q.spark" fill="none" stroke="currentColor" stroke-width="1.5"></polyline>
              </svg>
              <span class="vol-n" [class.warn]="q.luecke" [matTooltip]="q.fensterTip">
                {{ q.fenster }}/{{ q.erwartet }}@if (q.fehler) { · {{ q.fehler }}✗ }
              </span>
            </div>
          }
        }
        @case ('lw_hashtags') {
          <div class="lw-meta">{{ d().einheit }} · {{ d().zeitraum_h }} h · Faktor gegen die {{ d().zeitraum_h }} h davor</div>
          @if (!hashtags().length) {
            <div class="lw-leer">Keine relevanten Nachrichten im Zeitraum</div>
          }
          @for (h of hashtags(); track h.tag) {
            <div class="tag-row">
              <span class="tag">#{{ h.tag }}</span>
              <span class="tag-n">{{ h.anzahl | number }}</span>
              <span class="tag-trend" [class.neu]="h.vergleich === 'neu'" [class.hoch]="(h.faktor ?? 0) >= 2"
                    [matTooltip]="h.vergleich === 'kein_vergleich' ? 'Kein Vergleich: im Zeitraum davor keine Messung dieser Quelle' : h.vergleich === 'neu' ? 'Im Zeitraum davor gemessen, dort nicht vorgekommen' : 'vorher ' + h.vorher">
                {{ h.vergleich === 'faktor' ? '×' + h.faktor : h.vergleich === 'neu' ? 'neu' : '—' }}
              </span>
            </div>
          }
        }
        @case ('lw_korpus') {
          <div class="korpus">
            @for (q of laeufe(); track q.quelle) {
              <span class="chip" [class]="'chip z-' + q.klasse" [matTooltip]="q.tip">{{ q.name }} · {{ q.zustand }}</span>
            }
            @if (stufe0(); as s) {
              <span class="chip" [class.z-fehler]="s.zustand === 'fehler'" [matTooltip]="s.tip">
                Stufe 0 · {{ s.text }}
              </span>
            }
            @for (n of nichtZugeordnet(); track n.grund) {
              <span class="chip z-info" matTooltip="Quellnamen ohne CheckMK-Host (Grund benannt, nicht geraten)">
                Hosts {{ n.grund }}: {{ n.anzahl }}
              </span>
            }
            <span class="chip z-info">Definition v{{ d().definition_v }}</span>
            <span class="chip" [class.z-info]="!d().historie_aktiv" [class.z-ok]="d().historie_aktiv">
              Historie {{ d().historie_aktiv ? 'aktiv' : 'Trockenlauf' }}
            </span>
          </div>
        }
      }
    </div>
  `,
  styles: [`
    .lw { display: flex; flex-direction: column; gap: 4px; font-size: 12px; height: 100%; overflow: auto; }
    .lw-meta { font-size: 10px; opacity: .65; text-transform: uppercase; letter-spacing: .04em; margin-bottom: 2px; }
    .lw-leer { opacity: .6; padding: 12px 0; }
    .grp-head { display: flex; justify-content: space-between; font-size: 11px; font-weight: 700;
                text-transform: uppercase; margin-top: 4px; opacity: .8; }
    .grp-head.rel { opacity: 1; }
    .bar-row { display: grid; grid-template-columns: 30px minmax(90px, 1fr) 2fr 64px; gap: 6px; align-items: center; }
    .bar-id { font-weight: 700; opacity: .7; }
    .bar-label { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .bar-track { height: 8px; background: rgba(127,127,127,.15); border-radius: 4px; overflow: hidden; }
    .bar-fill { display: block; height: 100%; background: rgba(127,127,127,.55); border-radius: 4px; }
    .bar-num, .vol-n, .tag-n { text-align: right; font-variant-numeric: tabular-nums; }
    .vol-head, .vol-row { display: grid; grid-template-columns: 72px 90px 70px 1fr 70px; gap: 6px; align-items: center; }
    .vol-head { font-size: 10px; opacity: .55; text-transform: uppercase; }
    .vol-head span:not(:first-child) { text-align: right; }
    .vol-head span:nth-child(4) { text-align: left; }
    .spark { width: 100%; height: 22px; }
    .vol-n.warn { color: #ffcc00; }
    .tag-row { display: grid; grid-template-columns: 1fr 70px 48px; gap: 6px; align-items: center; }
    .tag { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .tag-trend { text-align: right; opacity: .75; }
    .tag-trend.hoch { color: #ff9933; opacity: 1; font-weight: 700; }
    .tag-trend.neu { color: #99ccff; opacity: 1; }
    .bef { border-left: 3px solid rgba(127,127,127,.4); padding: 4px 8px; background: rgba(127,127,127,.06); }
    .bef.r3 { border-left-color: #ff9933; }
    .bef summary { display: grid; grid-template-columns: 22px 1fr auto 40px; gap: 8px; align-items: center;
                   cursor: pointer; list-style: none; }
    .bef summary::-webkit-details-marker { display: none; }
    .bef-rel { font-weight: 700; text-align: center; border-radius: 4px; background: rgba(127,127,127,.25); }
    .bef.r3 .bef-rel { background: #ff9933; color: #000; }
    .bef-titel { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .bef-host, .bef-zeit { opacity: .7; font-variant-numeric: tabular-nums; white-space: nowrap; }
    .bef-text { margin: 6px 0; line-height: 1.4; }
    .bef-sub { font-size: 10px; opacity: .6; text-transform: uppercase; letter-spacing: .04em; margin-top: 6px; }
    .hyp { display: flex; gap: 6px; align-items: baseline; }
    .hyp-st { font-size: 10px; padding: 0 6px; border-radius: 8px; background: rgba(127,127,127,.25); white-space: nowrap; }
    .hyp-st.st-gestuetzt { background: rgba(102,204,102,.3); }
    .hyp-st.st-widerlegt { background: rgba(255,85,68,.3); text-decoration: line-through; }
    .zitat { font-family: ui-monospace, monospace; font-size: 11px; opacity: .9; overflow-wrap: anywhere; }
    .zitat-q { opacity: .55; font-family: inherit; }
    .schritte { display: flex; flex-wrap: wrap; gap: 4px; }
    .schritt { font-size: 11px; padding: 1px 8px; border-radius: 10px; background: rgba(153,204,255,.18); }
    .pfeil { opacity: .45; font-size: 11px; }
    .schritt.ende { background: rgba(255,153,51,.25); }
    .tags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
    .tag-chip { font-size: 10px; padding: 1px 6px; border-radius: 8px; background: rgba(127,127,127,.15); }
    .tag-chip.krit { background: rgba(255,153,51,.2); }
    .t-lcars .bef-titel, .t-lcars .bef-sub { font-family: 'Antonio','Eurostile',sans-serif; text-transform: uppercase; color: #FFCC99; }
    .t-lcars .bef { border-radius: 0 12px 12px 0; }
    .t-lcars .schritt { color: #99CCFF; }
    .t-holo .bef.r3 { border-left-color: #4fd6ff; }
    .t-holo .bef.r3 .bef-rel { background: #4fd6ff; }
    .t-classic .bef.r3 { border-left-color: #f59e0b; }
    .kpis { display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px; }
    .kpi { display: flex; flex-direction: column; align-items: flex-start; padding: 4px 6px;
           background: rgba(127,127,127,.08); border-radius: 4px; min-width: 0; }
    .kpi b { font-size: 18px; font-variant-numeric: tabular-nums; }
    .kpi span { font-size: 10px; opacity: .65; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
    .kpi.warn b { color: #ffcc00; }
    .ki-kurve { display: grid; grid-template-columns: 130px 1fr; gap: 8px; align-items: center; margin-top: 4px; }
    .ki-lbl { font-size: 10px; opacity: .75; line-height: 1.3; }
    .ki-urteil { font-size: 11px; opacity: .8; }
    .ki-urteil.warn { color: #ffcc00; opacity: 1; }
    .t-lcars .kpi b, .t-lcars .ki-kurve .spark { color: #FF9933; }
    .t-lcars .kpi span, .t-lcars .ki-lbl { font-family: 'Antonio','Eurostile',sans-serif; text-transform: uppercase; color: #FFCC66; }
    .t-holo .kpi b, .t-holo .ki-kurve .spark { color: #4fd6ff; }
    .t-classic .ki-kurve .spark { color: #60a5fa; }
    .korpus { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
    .chip { padding: 3px 10px; border-radius: 12px; font-size: 11px; background: rgba(127,127,127,.18); }
    .chip.z-ok { background: rgba(102,204,102,.25); }
    .chip.z-veraltet { background: rgba(255,204,0,.28); }
    .chip.z-fehler { background: rgba(255,85,68,.32); }
    .chip.z-nie { background: rgba(127,127,127,.3); text-decoration: line-through; }
    .chip.z-info { background: rgba(153,204,255,.2); }

    /* LCARS: Struktur in Antonio/uppercase, Zahlen und Tags lesbar */
    .t-lcars .bar-fill.rel, .t-lcars .bar-id.rel { background: #FF9933; color: #FF9933; }
    .t-lcars .bar-id.rel { background: none; }
    .t-lcars .bar-fill:not(.rel) { background: #FFCC99; opacity: .55; }
    .t-lcars .vol-q, .t-lcars .bar-label, .t-lcars .lw-meta { font-family: 'Antonio','Eurostile',sans-serif; text-transform: uppercase; color: #FFCC66; }
    .t-lcars .vol-n.rel, .t-lcars .spark { color: #FF9933; }
    .t-lcars .chip { font-family: 'Antonio','Eurostile',sans-serif; text-transform: uppercase; border-radius: 0 12px 12px 0; }
    .t-lcars .tag { color: #99CCFF; }
    /* Holo */
    .t-holo .bar-fill.rel, .t-holo .spark, .t-holo .vol-n.rel { background: #4fd6ff; color: #4fd6ff; }
    .t-holo .spark { background: none; }
    .t-holo .bar-id.rel { color: #4fd6ff; }
    /* Classic */
    .t-classic .bar-fill.rel { background: #60a5fa; }
    .t-classic .bar-id.rel, .t-classic .vol-n.rel, .t-classic .spark { color: #60a5fa; }
  `],
})
export class LwWidgetComponent {
  readonly typ = input.required<string>();
  readonly daten = input.required<unknown>();
  private readonly themeSvc = inject(ThemeService);
  readonly theme = computed(() => this.themeSvc.theme());

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  readonly d = computed(() => (this.daten() ?? {}) as any);

  readonly kiSpark = computed(() => {
    const r: { offen: number }[] = this.d().reihe ?? [];
    const max = Math.max(1, ...r.map(x => x.offen));
    return r.map((x, i) => `${r.length > 1 ? (i / (r.length - 1)) * 100 : 50},${24 - (x.offen / max) * 22}`).join(' ');
  });

  /** Lesart der Warteschlange: wächst sie, kommt die KI nicht nach — Grund und Zahl genannt. */
  readonly kiUrteil = computed(() => {
    const d = this.d();
    const v = d.summe?.verfallen ?? 0;
    const t = d.trend_offen;
    if (v > 0) return { warn: true, text: `${v} auslösende Nachrichten verfallen ohne Analyse — die KI kommt nicht nach` };
    if (t != null && t > 0) return { warn: true, text: `Warteschlange +${t} Nachrichten im Zeitraum — wächst` };
    if (t != null) return { warn: false, text: `Warteschlange ${t} Nachrichten im Zeitraum — die KI kommt nach` };
    return { warn: false, text: 'Noch zu wenige Läufe für einen Trend' };
  });

  readonly STATUS: Record<string, string> = { offen: 'offen', gestuetzt: 'gestützt', widerlegt: 'widerlegt' };

  readonly befunde = computed(() =>
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ((this.d().befunde ?? []) as any[]).map(b => ({
      ...b, hostKurz: String(b.host ?? '').split('.')[0],
      hashtags: (b.hashtags ?? []).filter((t: string) => !t.startsWith('befund:') && t !== 'quelle:ki'),
    })));

  readonly kriterienGruppen = computed(() => {
    const ks: { id: string; titel: string; art: string; anzahl: number; je_quelle: Record<string, number> }[] =
      this.d().kriterien ?? [];
    return (['relevant', 'nicht_relevant'] as const)
      .map(art => {
        const teil = ks.filter(k => k.art === art);
        const max = Math.max(1, ...teil.map(k => k.anzahl));
        return {
          art, summe: teil.reduce((s, k) => s + k.anzahl, 0),
          kriterien: teil.map(k => ({
            ...k,
            anteil: Math.max(1, Math.round((k.anzahl / max) * 100)),
            tooltip: Object.entries(k.je_quelle).map(([q, n]) => `${QUELLE[q] ?? q} ${n}`).join(', '),
          })),
        };
      })
      .filter(g => g.kriterien.length);
  });

  readonly volumen = computed(() => {
    const qs = this.d().quellen ?? {};
    return Object.keys(QUELLE).filter(q => qs[q]).map(q => {
      const x = qs[q];
      const reihe: { relevant?: number }[] = x.reihe ?? [];
      const werte = reihe.map(r => r.relevant ?? 0);
      const max = Math.max(1, ...werte);
      const spark = werte.map((v, i) =>
        `${werte.length > 1 ? (i / (werte.length - 1)) * 100 : 50},${24 - (v / max) * 22}`).join(' ');
      return {
        quelle: q, name: QUELLE[q], gelesen: x.summe?.gelesen ?? 0, relevant: x.summe?.relevant ?? 0,
        spark, fenster: x.fenster, erwartet: x.fenster_erwartet, fehler: x.fehler,
        luecke: x.fenster < x.fenster_erwartet,
        fensterTip: `${x.fenster} von ${x.fenster_erwartet} 5-min-Fenstern ausgewertet` +
          (x.fehler ? `, ${x.fehler} mit Fehler` : '') + (x.alte_fassung ? `, ${x.alte_fassung} alte Fassung` : ''),
      };
    });
  });

  readonly hashtags = computed(() =>
    (this.d().hashtags ?? []) as { tag: string; anzahl: number; vorher: number; faktor: number | null; vergleich: string }[]);

  readonly laeufe = computed(() => {
    const l = this.d().laeufe ?? {};
    return Object.keys(QUELLE).filter(q => l[q]).map(q => {
      const x = l[q];
      return {
        quelle: q, name: QUELLE[q], zustand: ZUSTAND[x.zustand] ?? x.zustand,
        klasse: x.zustand === 'nie gelaufen' ? 'nie' : x.zustand,
        tip: x.fehler ? `Fehler: ${x.fehler}` : x.alter_min != null ? `letztes Fenster vor ${x.alter_min} min` : '',
      };
    });
  });

  readonly stufe0 = computed(() => {
    const s = this.d().stufe0;
    if (!s) return null;
    if (s.zustand === 'fehler') return { zustand: 'fehler', text: 'Fehler', tip: s.fehler };
    const n = Math.round((s.verworfen_pro_stunde ?? 0) / 1000);
    return {
      zustand: s.zustand,
      text: `${n.toLocaleString('de-DE')} Tsd./h verworfen` + (s.zustand === 'unvollstaendig' ? ' (unvollständig)' : ''),
      tip: 'An der Quelle (Graylog-Pipeline) verworfen — sieht keine Auswertung: ' +
        (s.regeln ?? []).map((r: { titel: string; pro_stunde: number }) => `${r.titel} ${r.pro_stunde}/h`).join(', '),
    };
  });

  readonly nichtZugeordnet = computed(() =>
    Object.entries(this.d().nicht_zugeordnet ?? {}).map(([grund, anzahl]) => ({ grund, anzahl })));
}
