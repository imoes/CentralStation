import { CommonModule, DatePipe, DecimalPipe } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, computed, effect, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import { NgxEchartsDirective } from 'ngx-echarts';
import { environment } from '../../../environments/environment';
import { ThemeService } from '../../core/services/theme.service';

/**
 * Fehler-Netzplan (Nutzer 07.11.: „wie im Recherche-Tool — auf einen Blick sehen, wo die Probleme sind;
 * jeder Knoten ist ein Objekt, also Host oder Tag“).
 *
 * Alles ist EINE Suche über die Historie der relevanten Nachrichten (backend log_watch/suche.netzplan,
 * sicht=fehler): Knoten = Hashtag-Objekt (host:, befund:, thema:, dienst:, regel: …), Kante = beide an
 * derselben Nachricht (nie bloß zeitliche Nähe).
 *   Farbe = Problemzustand   rot: dringend und unbearbeitet · orange: dringend, untersucht · gelb: relevant
 *   Form  = Art des Objekts  Kreis: Host · Raute: KI-Befund · Rechteck: Tag
 * Wie im Recherche-Tool bedeutet ein Klick überall dasselbe: das Objekt wird zur Auswahl → rechts sein
 * Steckbrief, seine Befunde, verknüpfte Objekte und Nachrichten. Filter setzen/ausschließen sind
 * eigene Knöpfe; die Suche steht in der URL.
 */
interface Knoten {
  id: string; art: string; wert: string; anzahl: number; fokus: boolean;
  stufe: 'dringend_offen' | 'dringend' | 'auffaellig'; dringend: number; dringend_offen: number; offen: number;
  befunde: string[]; zuletzt?: string; titel?: string; incident?: { kind?: string; status?: string };
}
interface Kante { a: string; b: string; anzahl: number }
interface Netz {
  gesamt: number; knoten: Knoten[]; kanten: Kante[]; satz?: string; fehler?: string; ausgeblendet_naben?: string[];
}
interface Treffer { id: string; zeit: string; quelle: string; host: string; relevanz: number; text: string; hashtags: string[] }

const STUFE_FARBE: Record<string, Record<string, string>> = {
  lcars:   { dringend_offen: '#FF5544', dringend: '#FF9933', auffaellig: '#FFCC66' },
  default: { dringend_offen: '#d32f2f', dringend: '#f57c00', auffaellig: '#fbc02d' },
};
const STUFE_TEXT: Record<string, string> = {
  dringend_offen: 'dringend und unbearbeitet', dringend: 'dringend, untersucht', auffaellig: 'relevant',
};
const FORM: Record<string, string> = { host: 'circle', befund: 'diamond', incident: 'triangle' };
const INCIDENT_ART: Record<string, string> = { log_cluster: 'Log-Ausbruch', fleet: 'Flotten-Ausfall', critical: 'Kritisch', host: 'Host' };

/** Hashtag → Ausdruck der Filtersprache (Werte mit Leerzeichen/Klammern in Anführungszeichen). */
export function tagFilter(tag: string, ausschliessen = false): string {
  const i = tag.indexOf(':');
  const art = tag.slice(0, i), wert = tag.slice(i + 1);
  const t = /[\s()"]/.test(wert) || wert.endsWith('*') ? `${art}:"${wert.replace(/"/g, '')}"` : tag;
  return (ausschliessen ? '!' : '') + t;
}

@Component({
  selector: 'cs-fehlernetz',
  standalone: true,
  imports: [CommonModule, FormsModule, DatePipe, DecimalPipe, MatIconModule, MatTooltipModule, NgxEchartsDirective],
  template: `
    <div class="fn">
      <div class="fn-kopf">
        <div class="fn-kette">
          @for (f of filter(); track f) {
            <button class="chip" [class.aus]="f.startsWith('!')" (click)="entferne(f)" matTooltip="Filter entfernen">{{ f }} ✕</button>
          }
          <input class="fn-eingabe" [(ngModel)]="eingabe" (keydown.enter)="ergaenze()"
                 placeholder="Filter, z. B. host:cue0148* · thema:oom · !regel:533 — Enter" autocomplete="off">
        </div>
        <select class="fn-zeit" [ngModel]="stunden()" (ngModelChange)="setzeStunden($event)">
          @for (z of ZEITEN; track z.h) { <option [ngValue]="z.h">{{ z.label }}</option> }
        </select>
      </div>
      <!-- Ein Satz, der das Bild in Worten sagt (Recherche-Tool) -->
      <div class="fn-satz">{{ fehler() || netz()?.satz || 'Lade …' }}</div>

      <div class="fn-body">
        <div class="fn-graph-rahmen">
          @if (netz() && !netz()!.knoten.length && !fehler()) {
            <div class="fn-leer">Keine relevanten Nachrichten zu diesem Filter im Zeitraum.</div>
          } @else {
            <div echarts class="fn-graph" [options]="optionen()" (chartClick)="klick($event)"></div>
          }
          <div class="fn-legende">
            @for (s of STUFEN; track s) { <span class="leg"><i [style.background]="stufenFarbe(s)"></i>{{ STUFE_TEXT[s] }}</span> }
            <span class="leg">● Host</span><span class="leg">▲ Incident</span><span class="leg">◆ KI-Befund</span><span class="leg">▬ Tag</span>
            <span class="leg dim">Kante = an derselben Nachricht · keine Ursache</span>
          </div>
        </div>

        <aside class="fn-seite">
          @if (auswahl(); as k) {
            <div class="st-kopf">
              <span class="st-art">{{ k.art }}</span>
              <span class="st-name" [title]="k.id">{{ k.titel || k.wert }}</span>
            </div>
            <div class="st-stufe" [style.border-color]="stufenFarbe(k.stufe)">{{ STUFE_TEXT[k.stufe] }}</div>
            @if (k.incident) {
              <div class="st-meta">Incident · {{ INCIDENT_ART[k.incident.kind ?? ''] || k.incident.kind }} ·
                {{ k.incident.status === 'resolved' ? 'gelöst' : 'offen' }}</div>
            }
            <div class="st-zahlen">
              <span><b>{{ k.anzahl | number }}</b> Nachrichten</span>
              <span><b>{{ k.dringend | number }}</b> dringend</span>
              <span [class.rot]="k.dringend_offen"><b>{{ k.dringend_offen | number }}</b> dringend ohne Befund</span>
              <span><b>{{ k.offen | number }}</b> ohne Befund</span>
            </div>
            @if (k.zuletzt) { <div class="st-meta">zuletzt {{ k.zuletzt | date:'dd.MM. HH:mm' }} · {{ stunden() }} h</div> }
            <div class="st-knoepfe">
              <button (click)="alsFilter(k.id, false)" matTooltip="Nur Nachrichten mit diesem Objekt"><mat-icon>filter_alt</mat-icon> Filtern</button>
              <button (click)="alsFilter(k.id, true)" matTooltip="Dieses Objekt ausschließen"><mat-icon>filter_alt_off</mat-icon> Ausschließen</button>
              @if (k.art === 'host') {
                <button (click)="cockpit(k.wert)" matTooltip="Host-Cockpit öffnen"><mat-icon>dns</mat-icon> Cockpit</button>
              }
              @if (k.art === 'incident') {
                <button (click)="incidentOeffnen(k.wert)" matTooltip="Incident mit Zeitachse im Feed öffnen"><mat-icon>open_in_new</mat-icon> Incident</button>
              }
            </div>
            @if (befundeVon(k).length) {
              <div class="st-sub">KI-Befunde</div>
              @for (b of befundeVon(k); track b.id) {
                <button class="st-zeile" (click)="waehle(b.id)">◆ {{ b.titel || b.id }}</button>
              }
            }
            <div class="st-sub">Verknüpft (an denselben Nachrichten)</div>
            @for (v of nachbarn(k); track v.id) {
              <button class="st-zeile" (click)="waehle(v.id)">
                <i class="punkt" [style.background]="stufenFarbe(v.stufe)"></i>{{ v.id }} <span class="dim">{{ v.anzahl }}</span>
              </button>
            }
            @if (!nachbarn(k).length) { <div class="dim">—</div> }
            <div class="st-sub">Nachrichten{{ treffer().length ? ' · ' + (gesamtTreffer() | number) : '' }}</div>
            @for (t of treffer(); track t.id) {
              <div class="msg" [class.r3]="t.relevanz >= 3" [class.offen]="objekt()?.id === t.id" (click)="oeffne(t.id)">
                <span class="msg-kopf">{{ t.zeit | date:'dd.MM. HH:mm' }} · {{ t.quelle }} · {{ kurz(t.host) }}</span>
                <span class="msg-text">{{ t.text }}</span>
              </div>
              <!-- Die Nachricht als Objekt (backend log_watch/nachricht.py): feste Felder, Urteil, Hashtags,
                   Geschichte; Änderungen nur über Methoden, jede mit Grund -->
              @if (objekt(); as o) { @if (o.id === t.id) {
                <div class="obj">
                  <div class="obj-zeile"><b>{{ o.aenderbar.urteil === 'relevant' ? 'relevant' : 'nicht relevant' }}</b>
                    · Relevanz {{ o.aenderbar.relevanz }} · {{ (o.aenderbar.kriterien || []).join(' ') }}
                    · von {{ o.aenderbar.herkunft || 'regel' }}</div>
                  <div class="obj-tags">
                    @for (h of o.aenderbar.hashtags || []; track h) {
                      <span class="obj-tag">{{ h }}@if (!fest(h)) { <button (click)="tagWeg(h)" title="entfernen">✕</button> }</span>
                    }
                  </div>
                  <div class="obj-form">
                    <input [(ngModel)]="neuerTag" placeholder="art:wert hinzufügen" (keydown.enter)="tagHinzu()">
                  </div>
                  <div class="obj-form">
                    <select [(ngModel)]="urteilForm.urteil">
                      <option value="relevant">relevant</option><option value="nicht_relevant">nicht relevant</option>
                    </select>
                    <select [(ngModel)]="urteilForm.relevanz">
                      @for (r of [0, 1, 2, 3]; track r) { <option [ngValue]="r">Relevanz {{ r }}</option> }
                    </select>
                    <select [(ngModel)]="urteilForm.kriterium">
                      @for (k of kriterienFuer(urteilForm.urteil); track k.id) { <option [value]="k.id">{{ k.id }} {{ k.titel }}</option> }
                    </select>
                  </div>
                  <div class="obj-form">
                    <input [(ngModel)]="grund" placeholder="Grund (Pflicht) — steht an der Nachricht">
                    <button (click)="urteilen()" [disabled]="!grund.trim()">Urteil setzen</button>
                  </div>
                  @if (objektFehler()) { <div class="obj-fehler">{{ objektFehler() }}</div> }
                  <div class="obj-sub">Geschichte</div>
                  @for (e of o.aenderungen; track $index) {
                    <div class="obj-gesch">{{ e.zeit | date:'dd.MM. HH:mm' }} · <b>{{ e.wer }}</b> · {{ e.was }} — {{ e.grund }}</div>
                  }
                  @if (!o.aenderungen.length) { <div class="obj-gesch dim">noch keine Änderung — so, wie das Raster es schrieb</div> }
                </div>
              } }
            }
          } @else {
            @if (objekteDerArt('incident').length) {
              <div class="st-sub">Incidents</div>
              @for (k of objekteDerArt('incident'); track k.id) {
                <button class="st-zeile" (click)="waehle(k.id)">
                  <i class="punkt" [style.background]="stufenFarbe(k.stufe)"></i>▲ {{ k.titel || k.id }}
                  <span class="dim">{{ k.incident?.status === 'resolved' ? 'gelöst' : 'offen' }} · {{ k.anzahl }}</span>
                </button>
              }
            }
            @if (objekteDerArt('befund').length) {
              <div class="st-sub">KI-Befunde</div>
              @for (k of objekteDerArt('befund'); track k.id) {
                <button class="st-zeile" (click)="waehle(k.id)">
                  <i class="punkt" [style.background]="stufenFarbe(k.stufe)"></i>◆ {{ k.titel || k.id }}
                  <span class="dim">{{ k.anzahl }}</span>
                </button>
              }
            }
            <div class="st-sub">Wo sind die Probleme?</div>
            @for (k of brennpunkte(); track k.id) {
              <button class="st-zeile" (click)="waehle(k.id)">
                <i class="punkt" [style.background]="stufenFarbe(k.stufe)"></i>{{ k.titel || k.id }}
                <span class="dim">{{ k.dringend_offen ? k.dringend_offen + ' unbearbeitet' : k.dringend + ' dringend' }}</span>
              </button>
            }
            <div class="dim hinweis">Klick auf einen Knoten: Steckbrief, Befunde, Verknüpfungen und Nachrichten.</div>
          }
        </aside>
      </div>
    </div>
  `,
  styles: [`
    :host { display: block; height: 100%; }
    .fn { display: flex; flex-direction: column; height: 100%; gap: 6px; font-size: 13px; }
    .fn-kopf { display: flex; gap: 8px; align-items: center; }
    .fn-kette { flex: 1; display: flex; flex-wrap: wrap; gap: 4px; align-items: center; padding: 4px 8px; border-radius: 8px;
      background: var(--mat-sys-surface-container, rgba(127,127,127,.08)); }
    .fn-eingabe { flex: 1; min-width: 220px; border: 0; outline: 0; background: transparent; color: inherit; font: inherit; padding: 4px; }
    .fn-zeit { padding: 5px 8px; border-radius: 6px; border: 1px solid rgba(127,127,127,.4); background: transparent; color: inherit; }
    .chip { font: inherit; font-size: 12px; padding: 1px 9px; border-radius: 10px; border: 0; cursor: pointer;
      background: rgba(153,204,255,.28); color: inherit; }
    .chip.aus { background: rgba(255,85,68,.22); }
    .fn-satz { font-size: 14px; font-weight: 600; padding: 2px 2px 4px; }
    .fn-body { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 3fr) minmax(280px, 1.2fr); gap: 10px; }
    .fn-graph-rahmen { display: flex; flex-direction: column; min-height: 0; }
    .fn-graph { flex: 1; min-height: 420px; width: 100%; }
    .fn-leer { padding: 24px; opacity: .65; }
    .fn-legende { display: flex; flex-wrap: wrap; gap: 10px; font-size: 11px; opacity: .85; padding-top: 4px; }
    .leg { display: inline-flex; align-items: center; gap: 4px; }
    .leg i, .punkt { width: 10px; height: 10px; border-radius: 50%; display: inline-block; flex: none; }
    .dim { opacity: .6; }
    .fn-seite { overflow: auto; display: flex; flex-direction: column; gap: 4px; min-width: 0; padding-right: 4px; }
    .st-kopf { display: flex; flex-direction: column; gap: 2px; }
    .st-art { font-size: 10px; text-transform: uppercase; letter-spacing: .05em; opacity: .6; }
    .st-name { font-size: 15px; font-weight: 600; overflow-wrap: anywhere; }
    .st-stufe { border-left: 4px solid; padding-left: 6px; font-size: 12px; }
    .st-zahlen { display: grid; grid-template-columns: 1fr 1fr; gap: 2px 10px; font-size: 12px; margin-top: 4px; }
    .st-zahlen .rot b { color: #d32f2f; }
    .st-meta { font-size: 11px; opacity: .6; }
    .st-knoepfe { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0; }
    .st-knoepfe button { display: inline-flex; align-items: center; gap: 3px; font: inherit; font-size: 12px; cursor: pointer;
      padding: 3px 8px; border-radius: 6px; border: 1px solid rgba(127,127,127,.4); background: transparent; color: inherit; }
    .st-knoepfe mat-icon { font-size: 16px; height: 16px; width: 16px; }
    .st-sub { font-size: 10px; opacity: .6; text-transform: uppercase; letter-spacing: .05em; margin-top: 8px; }
    .st-zeile { display: flex; align-items: center; gap: 6px; text-align: left; font: inherit; font-size: 12px; cursor: pointer;
      border: 0; background: transparent; color: inherit; padding: 2px 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
    .st-zeile:hover { text-decoration: underline; }
    .st-zeile .dim { margin-left: auto; padding-left: 6px; }
    .msg { display: flex; flex-direction: column; border-left: 2px solid rgba(127,127,127,.4); padding-left: 6px; margin-top: 2px; }
    .msg.r3 { border-left-color: #f57c00; }
    .msg-kopf { font-size: 10.5px; opacity: .65; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .msg-text { font-size: 12px; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow-wrap: anywhere; }
    .hinweis { margin-top: 10px; font-size: 12px; }
    .msg { cursor: pointer; }
    .msg.offen { border-left-color: #5b9bd5; }
    .obj { margin: 2px 0 8px 8px; padding: 6px 8px; border-radius: 6px; font-size: 12px;
      background: var(--mat-sys-surface-container, rgba(127,127,127,.08)); display: flex; flex-direction: column; gap: 4px; }
    .obj-tags { display: flex; flex-wrap: wrap; gap: 3px; }
    .obj-tag { font-size: 11px; padding: 0 6px; border-radius: 8px; background: rgba(127,127,127,.2); }
    .obj-tag button { border: 0; background: none; color: inherit; cursor: pointer; padding: 0 0 0 3px; }
    .obj-form { display: flex; gap: 4px; flex-wrap: wrap; }
    .obj-form input { flex: 1; min-width: 140px; font: inherit; padding: 3px 6px; border-radius: 5px;
      border: 1px solid rgba(127,127,127,.4); background: transparent; color: inherit; }
    .obj-form select, .obj-form button { font: inherit; font-size: 12px; padding: 3px 6px; border-radius: 5px;
      border: 1px solid rgba(127,127,127,.4); background: transparent; color: inherit; cursor: pointer; }
    .obj-fehler { color: #d32f2f; }
    .obj-sub { font-size: 10px; opacity: .6; text-transform: uppercase; letter-spacing: .05em; margin-top: 4px; }
    .obj-gesch { font-size: 11px; overflow-wrap: anywhere; }
  `],
})
export class FehlernetzComponent implements OnInit {
  private readonly http = inject(HttpClient);
  private readonly themeSvc = inject(ThemeService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  readonly ZEITEN = [{ h: 1, label: '1 Stunde' }, { h: 6, label: '6 Stunden' }, { h: 24, label: '24 Stunden' },
                     { h: 72, label: '3 Tage' }, { h: 168, label: '7 Tage' }];
  readonly STUFEN = ['dringend_offen', 'dringend', 'auffaellig'];
  readonly STUFE_TEXT = STUFE_TEXT;
  readonly INCIDENT_ART = INCIDENT_ART;

  readonly filter = signal<string[]>([]);
  readonly stunden = signal(24);
  readonly netz = signal<Netz | null>(null);
  readonly fehler = signal('');
  readonly auswahlId = signal<string | null>(null);
  readonly treffer = signal<Treffer[]>([]);
  /** die geöffnete Nachricht als Objekt (GET /log-watch/nachrichten/{id}) */
  readonly objekt = signal<any | null>(null);
  readonly objektFehler = signal('');
  readonly kriterien = signal<{ id: string; art: string; titel: string }[]>([]);
  neuerTag = '';
  grund = '';
  urteilForm: { urteil: string; relevanz: number; kriterium: string } = { urteil: 'nicht_relevant', relevanz: 1, kriterium: 'N1' };
  readonly gesamtTreffer = signal(0);
  eingabe = '';

  readonly auswahl = computed(() => (this.netz()?.knoten ?? []).find(k => k.id === this.auswahlId()) ?? null);
  /** ohne Auswahl rechts: die Problemobjekte, rot zuerst — die Antwort auf „wo sind die Probleme?“ */
  objekteDerArt(art: string): Knoten[] { return (this.netz()?.knoten ?? []).filter(k => k.art === art); }

  readonly brennpunkte = computed(() => (this.netz()?.knoten ?? [])
    .filter(k => k.stufe !== 'auffaellig' && k.art !== 'incident' && k.art !== 'befund')
    .sort((a, b) => b.dringend_offen - a.dringend_offen || b.dringend - a.dringend).slice(0, 12));

  constructor() {
    // Filter oder Zeitraum geändert → EINE Suche lädt den Netzplan neu; die URL hält die Suche
    effect(() => {
      const f = this.filter().join(' ');
      const h = this.stunden();
      this.router.navigate([], { queryParams: { modus: 'fehler', filter: f || null, stunden: h }, queryParamsHandling: 'merge',
                                  replaceUrl: true });
      this.http.get<Netz>(`${environment.apiUrl}/log-watch/netzplan`, { params: { filter: f, stunden: h, knoten: 30, sicht: 'fehler' } })
        .subscribe({
          next: n => { this.fehler.set(n.fehler ? `Filter: ${n.fehler}` : ''); this.netz.set(n); },
          error: e => this.fehler.set(`Netzplan nicht geladen (${e.status ?? '?'})`),
        });
    });
    // Auswahl → Nachrichten dieses Objekts unter demselben Filter
    effect(() => {
      const id = this.auswahlId();
      if (!id) { this.treffer.set([]); return; }
      const f = [...this.filter(), tagFilter(id)].join(' ');
      this.http.get<{ gesamt: number; treffer: Treffer[] }>(`${environment.apiUrl}/log-watch/suche`,
        { params: { filter: f, stunden: this.stunden(), limit: 8 } })
        .subscribe({ next: r => { this.treffer.set(r.treffer ?? []); this.gesamtTreffer.set(r.gesamt ?? 0); },
                     error: () => this.treffer.set([]) });
    });
  }

  /** quelle: und host: spiegeln feste Felder der Nachricht — nicht änderbar (backend nachricht.FESTE_TAG_ARTEN) */
  fest(tag: string): boolean { return /^(quelle|host):/.test(tag); }

  kriterienFuer(urteil: string) { return this.kriterien().filter(k => (urteil === 'relevant') === (k.art === 'relevant')); }

  oeffne(id: string) {
    if (this.objekt()?.id === id) { this.objekt.set(null); return; }
    this.objektFehler.set('');
    if (!this.kriterien().length) {
      this.http.get<any>(`${environment.apiUrl}/log-watch/definition`).subscribe(d => this.kriterien.set(d.kriterien ?? []));
    }
    this.http.get<any>(`${environment.apiUrl}/log-watch/nachrichten/${encodeURIComponent(id)}`).subscribe({
      next: o => {
        this.objekt.set(o);
        const a = o.aenderbar ?? {};
        this.urteilForm = { urteil: a.urteil ?? 'nicht_relevant', relevanz: a.relevanz ?? 1,
                            kriterium: (a.kriterien ?? [])[0] ?? 'N1' };
      },
      error: e => this.objektFehler.set(`Nachricht nicht lesbar (${e.status})`),
    });
  }

  private aendere(pfad: string, body: any) {
    const o = this.objekt();
    if (!o) return;
    this.http.post<any>(`${environment.apiUrl}/log-watch/nachrichten/${encodeURIComponent(o.id)}/${pfad}`, body).subscribe({
      next: r => { this.objekt.set(r.nachricht); this.objektFehler.set(''); this.grund = ''; this.neuerTag = ''; },
      error: e => this.objektFehler.set(e?.error?.detail ?? `Änderung abgelehnt (${e.status})`),
    });
  }

  tagHinzu() {
    const t = this.neuerTag.trim();
    if (!t) return;
    if (!this.grund.trim()) { this.objektFehler.set('Grund ist Pflicht — bitte unten eintragen'); return; }
    this.aendere('hashtags', { hinzu: [t], weg: [], grund: this.grund });
  }

  tagWeg(t: string) {
    if (!this.grund.trim()) { this.objektFehler.set('Grund ist Pflicht — bitte unten eintragen'); return; }
    this.aendere('hashtags', { hinzu: [], weg: [t], grund: this.grund });
  }

  urteilen() {
    const f = this.urteilForm;
    this.aendere('urteil', { urteil: f.urteil, relevanz: Number(f.relevanz), kriterien: [f.kriterium], grund: this.grund });
  }

  ngOnInit() {
    const p = this.route.snapshot.queryParamMap;
    const f = p.get('filter');
    if (f) this.filter.set(f.match(/!?[\w.-]+:(?:"[^"]*"|\S+)|\S+/g) ?? []);
    const h = Number(p.get('stunden'));
    if (h) this.stunden.set(h);
  }

  stufenFarbe(stufe: string): string {
    return STUFE_FARBE[this.themeSvc.theme() === 'lcars' ? 'lcars' : 'default'][stufe] ?? '#999';
  }

  kurz(host: string): string { return String(host ?? '').split('.')[0]; }

  befundeVon(k: Knoten): { id: string; titel?: string }[] {
    const titel = new Map((this.netz()?.knoten ?? []).filter(x => x.art === 'befund').map(x => [x.id, x.titel]));
    return k.befunde.map(id => ({ id, titel: titel.get(id) }));
  }

  nachbarn(k: Knoten): Knoten[] {
    const kn = new Map((this.netz()?.knoten ?? []).map(x => [x.id, x]));
    return (this.netz()?.kanten ?? [])
      .filter(e => e.a === k.id || e.b === k.id)
      .map(e => ({ ...(kn.get(e.a === k.id ? e.b : e.a) as Knoten), anzahl: e.anzahl }))
      .filter(x => x.id && x.art !== 'befund');
  }

  readonly optionen = computed(() => {
    const n = this.netz();
    const knoten = n?.knoten ?? [];
    const max = Math.max(1, ...knoten.map(k => k.anzahl));
    const maxK = Math.max(1, ...(n?.kanten ?? []).map(k => k.anzahl));
    const dunkel = this.themeSvc.theme() !== 'classic';
    const sel = this.auswahlId();
    return {
      animation: false,
      tooltip: {
        formatter: (p: { dataType: string; data: any }) => p.dataType === 'edge'
          ? `${p.data.source} — ${p.data.target}<br>gemeinsam in ${p.data.anzahl} Nachrichten`
          : `<b>${p.data.titel || p.data.id}</b><br>${STUFE_TEXT[p.data.stufe]}<br>${p.data.anzahl} Nachrichten · `
            + `${p.data.dringend} dringend · ${p.data.dringend_offen} davon ohne Befund`,
      },
      series: [{
        type: 'graph', layout: 'force', roam: true, draggable: true,
        force: { repulsion: 420, edgeLength: [70, 200], gravity: 0.12, layoutAnimation: false },
        label: {
          show: true, fontSize: 11, color: dunkel ? '#eee' : '#222',
          formatter: (p: { data: any }) => {
            const w = (p.data.art === 'befund' || p.data.art === 'incident') ? (p.data.titel || p.data.wert) : p.data.wert;
            const t = w.length > 26 ? w.slice(0, 25) + '…' : w;
            return p.data.dringend_offen ? `${t}  ⚠ ${p.data.dringend_offen}` : t;
          },
        },
        emphasis: { focus: 'adjacency' },
        data: knoten.map(k => ({
          ...k, name: k.id,
          symbol: FORM[k.art] ?? 'roundRect',
          symbolSize: k.art === 'host' || k.art === 'befund' || k.art === 'incident'
            ? 14 + 34 * Math.sqrt(k.anzahl / max) : [22 + 30 * Math.sqrt(k.anzahl / max), 14],
          itemStyle: {
            color: this.stufenFarbe(k.stufe),
            borderColor: k.id === sel ? (dunkel ? '#fff' : '#000') : k.fokus ? '#5b9bd5' : 'transparent',
            borderWidth: k.id === sel || k.fokus ? 3 : 0,
          },
        })),
        edges: (n?.kanten ?? []).map(k => ({
          source: k.a, target: k.b, anzahl: k.anzahl,
          lineStyle: { width: 0.5 + 4 * (k.anzahl / maxK), opacity: 0.4, color: dunkel ? '#8aa' : '#999' },
        })),
      }],
    };
  });

  klick(e: { dataType?: string; data?: unknown }) {
    const id = (e.data as { id?: string } | undefined)?.id;
    if (e.dataType === 'node' && id) this.waehle(id);
  }

  waehle(id: string) { this.auswahlId.set(id); }

  alsFilter(id: string, ausschliessen: boolean) {
    const t = tagFilter(id, ausschliessen);
    if (!this.filter().includes(t)) this.filter.update(f => [...f, t]);
    if (ausschliessen) this.auswahlId.set(null);
  }

  ergaenze() {
    const t = this.eingabe.trim();
    if (t && !this.filter().includes(t)) this.filter.update(f => [...f, t]);
    this.eingabe = '';
  }

  entferne(f: string) { this.filter.update(a => a.filter(x => x !== f)); }

  setzeStunden(h: number) { this.stunden.set(Number(h)); }

  incidentOeffnen(id: string) { this.router.navigate(['/feed'], { queryParams: { incident: id } }); }

  cockpit(host: string) {
    window.open('/cockpit/' + encodeURIComponent(host), 'cockpit-' + host,
      'width=1300,height=820,menubar=no,toolbar=no,location=no,status=no');
  }
}
