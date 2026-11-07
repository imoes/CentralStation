import { CommonModule, DatePipe } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { MatTooltipModule } from '@angular/material/tooltip';
import { NgxEchartsDirective } from 'ngx-echarts';
import { environment } from '../../../environments/environment';
import { ThemeService } from '../../core/services/theme.service';

/**
 * KI-Cockpit: Netzplan der Log-Überwachung (lw_netzplan). Alles ist eine Suche: Graph,
 * Filterkette und rechte Liste zeigen dasselbe Ergebnis (backend log_watch/suche.py).
 * Knoten = Hashtag, Kante = beide an derselben Nachricht (nie bloß zeitliche Nähe).
 * Klick auf einen Knoten ergänzt den Filter; ein Chip nimmt ihn wieder heraus.
 */
interface Knoten { id: string; art: string; wert: string; anzahl: number; fokus: boolean }
interface Kante { a: string; b: string; anzahl: number }
interface Netz { gesamt: number; knoten: Knoten[]; kanten: Kante[]; zeitraum_h?: number; fehler?: string; ausgeblendet_naben?: string[] }
interface Treffer { id: string; zeit: string; quelle: string; host: string; relevanz: number; text: string; hashtags: string[] }

const ART_FARBE: Record<string, Record<string, string>> = {
  lcars:   { host: '#FF9933', befund: '#FFCC66', thema: '#99CCFF', dienst: '#9999FF', regel: '#CC6666',
             mitre: '#CC6666', zustand: '#FF5544', kritikalitaet: '#FFCC99', signal: '#FFAA90',
             quelle: '#7788AA', app: '#99CCFF', ort: '#66CC99', gegenstelle: '#CC6666' },
  default: { host: '#3b82f6', befund: '#f59e0b', thema: '#10b981', dienst: '#8b5cf6', regel: '#ef4444',
             mitre: '#dc2626', zustand: '#f43f5e', kritikalitaet: '#64748b', signal: '#f97316',
             quelle: '#94a3b8', app: '#06b6d4', ort: '#22c55e', gegenstelle: '#b91c1c' },
};

@Component({
  selector: 'cs-lw-netzplan',
  standalone: true,
  imports: [CommonModule, DatePipe, MatTooltipModule, NgxEchartsDirective],
  template: `
    <div class="np">
      <div class="np-kette">
        <span class="np-meta">{{ netz()?.gesamt ?? '…' }} Nachrichten · {{ stunden() }} h</span>
        @if (basis()) { <span class="chip basis" matTooltip="Filter der Kachel">{{ basis() }}</span> }
        @for (f of auswahl(); track f) {
          <button class="chip" (click)="entferne(f, $event)" [matTooltip]="'Filter entfernen'">{{ f }} ✕</button>
        }
        @if (!auswahl().length) { <span class="np-hinweis">Knoten anklicken: wird zum Filter</span> }
      </div>
      @if (fehler()) {
        <div class="np-leer">{{ fehler() }}</div>
      } @else if (netz() && !netz()!.knoten.length) {
        <div class="np-leer">Keine relevanten Nachrichten zu diesem Filter im Zeitraum</div>
      } @else {
        <div class="np-body">
          <div echarts class="np-graph" [options]="optionen()" (chartClick)="klick($event)"></div>
          <div class="np-seite">
            <div class="np-sub">Nachrichten{{ treffer().length ? ' · ' + gesamtTreffer() : '' }}</div>
            @for (t of treffer(); track t.id) {
              <div class="np-msg" [class.r3]="t.relevanz >= 3" [matTooltip]="t.hashtags.join(' ')">
                <span class="np-msg-kopf">{{ t.zeit | date:'dd.MM. HH:mm' }} · {{ t.quelle }} · {{ kurz(t.host) }}</span>
                <span class="np-msg-text">{{ t.text }}</span>
              </div>
            }
            @if (!treffer().length) { <div class="np-hinweis">—</div> }
            <div class="np-sub">Legende</div>
            <div class="np-legende">
              @for (a of arten(); track a) { <span class="leg"><i [style.background]="farbe(a)"></i>{{ a }}</span> }
            </div>
            <div class="np-regel">Kante = beide Hashtags an derselben Nachricht · Zahl = Nachrichten · keine Ursache</div>
            @if (naben().length) {
              <div class="np-regel" [matTooltip]="naben().join(', ')">Ausgeblendet (an über 80 % der Nachrichten, sagt nichts über Zusammenhänge): {{ naben().length }}</div>
            }
          </div>
        </div>
      }
    </div>
  `,
  styles: [`
    .np { display: flex; flex-direction: column; height: 100%; gap: 4px; font-size: 12px; }
    .np-kette { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
    .np-meta { font-size: 10px; opacity: .65; text-transform: uppercase; letter-spacing: .04em; margin-right: 4px; }
    .chip { font: inherit; font-size: 11px; padding: 1px 8px; border-radius: 10px; border: 0; cursor: pointer;
            background: rgba(153,204,255,.25); color: inherit; }
    .chip.basis { cursor: default; background: rgba(127,127,127,.18); }
    .np-hinweis { opacity: .55; font-size: 11px; }
    .np-leer { opacity: .6; padding: 12px 0; }
    .np-body { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 3fr) minmax(160px, 2fr); gap: 8px; }
    .np-graph { width: 100%; height: 100%; min-height: 200px; }
    .np-seite { overflow: auto; display: flex; flex-direction: column; gap: 4px; min-width: 0; }
    .np-sub { font-size: 10px; opacity: .6; text-transform: uppercase; letter-spacing: .04em; margin-top: 4px; }
    .np-msg { display: flex; flex-direction: column; border-left: 2px solid rgba(127,127,127,.4); padding-left: 6px; }
    .np-msg.r3 { border-left-color: #ff9933; }
    .np-msg-kopf { font-size: 10px; opacity: .65; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .np-msg-text { overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
                   overflow-wrap: anywhere; }
    .np-legende { display: flex; flex-wrap: wrap; gap: 6px; }
    .leg { display: inline-flex; align-items: center; gap: 3px; font-size: 10px; opacity: .8; }
    .leg i { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
    .np-regel { font-size: 10px; opacity: .5; margin-top: 4px; }
  `],
})
export class LwNetzplanComponent {
  readonly daten = input.required<unknown>();
  readonly config = input<Record<string, unknown>>({});
  private readonly http = inject(HttpClient);
  private readonly themeSvc = inject(ThemeService);

  readonly auswahl = signal<string[]>([]);
  readonly geladen = signal<Netz | null>(null);
  readonly treffer = signal<Treffer[]>([]);
  readonly gesamtTreffer = signal(0);
  readonly fehler = signal<string>('');

  readonly basis = computed(() => String(this.config()?.['filter'] ?? ''));
  readonly stunden = computed(() => Number(this.config()?.['hours'] ?? 24));
  /** Ohne Auswahl gilt die Antwort des Widget-Datenendpunkts, danach die eigene Suche. */
  readonly netz = computed<Netz | null>(() => this.auswahl().length ? this.geladen() : (this.daten() as Netz));
  readonly filter = computed(() => [this.basis(), ...this.auswahl()].filter(Boolean).join(' '));
  readonly naben = computed(() => this.netz()?.ausgeblendet_naben ?? []);
  readonly arten = computed(() => [...new Set((this.netz()?.knoten ?? []).map(k => k.art))]);

  constructor() {
    // Jede Änderung der Filterkette: Netzplan und Liste aus derselben Suche neu laden.
    effect(() => {
      const f = this.filter();
      const h = this.stunden();
      const params = { filter: f, stunden: h };
      if (this.auswahl().length) {
        this.http.get<Netz>(`${environment.apiUrl}/log-watch/netzplan`, { params: { ...params, knoten: 15 } })
          .subscribe({ next: n => { this.fehler.set(n.fehler ?? ''); this.geladen.set(n); },
                       error: e => this.fehler.set(`Netzplan nicht geladen: ${e.status ?? ''}`) });
      }
      this.http.get<{ gesamt: number; treffer: Treffer[]; fehler?: string }>(
        `${environment.apiUrl}/log-watch/suche`, { params: { ...params, limit: 8 } })
        .subscribe({ next: r => { this.treffer.set(r.treffer ?? []); this.gesamtTreffer.set(r.gesamt ?? 0); },
                     error: () => this.treffer.set([]) });
    });
  }

  farbe(art: string): string {
    const p = ART_FARBE[this.themeSvc.theme() === 'lcars' ? 'lcars' : 'default'];
    return p[art] ?? '#999';
  }

  kurz(host: string): string { return String(host ?? '').split('.')[0]; }

  readonly optionen = computed(() => {
    const n = this.netz();
    const knoten = n?.knoten ?? [];
    const max = Math.max(1, ...knoten.map(k => k.anzahl));
    const maxK = Math.max(1, ...(n?.kanten ?? []).map(k => k.anzahl));
    const dunkel = this.themeSvc.theme() !== 'classic';
    return {
      animation: false,
      tooltip: {
        formatter: (p: { dataType: string; data: { id?: string; anzahl: number; source?: string; target?: string } }) =>
          p.dataType === 'edge'
            ? `${p.data.source} — ${p.data.target}<br>gemeinsam in ${p.data.anzahl} Nachrichten`
            : `${(p.data as any).titel ? '<b>' + (p.data as any).titel + '</b><br>' : ''}${p.data.id}<br>${p.data.anzahl} Nachrichten`,
      },
      series: [{
        type: 'graph', layout: 'force', roam: true, draggable: true,
        // Schwerkraft hält unverbundene Teilgraphen im Bild (gemessen: 0,08 schob CheckMK unter die Liste)
        force: { repulsion: 140, edgeLength: [40, 110], gravity: 0.25, layoutAnimation: false },
        // lesbarer Name zuerst (Regel-Beschreibung, Technik, Thema, Muster-Text …), sonst der Wert
        label: { show: true, fontSize: 10, color: dunkel ? '#ddd' : '#333', formatter: (p: { data: { wert: string; titel?: string } }) => {
          const w = p.data.titel || p.data.wert;
          return w.length > 22 ? w.slice(0, 21) + '…' : w; } },
        data: knoten.map(k => ({
          id: k.id, name: k.id, wert: k.wert, titel: (k as any).titel, anzahl: k.anzahl,
          symbolSize: 8 + 26 * Math.sqrt(k.anzahl / max),
          itemStyle: { color: this.farbe(k.art), borderColor: k.fokus ? (dunkel ? '#fff' : '#111') : 'transparent',
                      borderWidth: k.fokus ? 2 : 0 },
        })),
        edges: (n?.kanten ?? []).map(k => ({
          source: k.a, target: k.b, anzahl: k.anzahl,
          lineStyle: { width: 0.5 + 4 * (k.anzahl / maxK), opacity: 0.45, color: dunkel ? '#8aa' : '#999' },
        })),
      }],
    };
  });

  klick(e: { dataType?: string; data?: unknown }) {
    const id = (e.data as { id?: string } | undefined)?.id;
    if (e.dataType !== 'node' || !id) return;
    if (!this.auswahl().includes(id) && !this.basis().split(' ').includes(id)) {
      this.auswahl.update(a => [...a, id]);
    }
  }

  entferne(f: string, ev: Event) {
    ev.stopPropagation();
    this.auswahl.update(a => a.filter(x => x !== f));
  }
}
