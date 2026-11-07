import { CommonModule, DatePipe, DecimalPipe } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, computed, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import { environment } from '../../../environments/environment';
import { AuthService } from '../../core/auth/auth.service';

/**
 * Regel-Editor wie die Policy-Ansicht einer Palo-Alto-Firewall (Nutzer 07.11.2026).
 *
 * Eine Quelle der Wahrheit: die versionierte Definition (GET /log-watch/definition). Der Editor hält einen
 * Entwurf; Prüfen + Vorschau (POST /definition/pruefen) speichert nichts, Übernehmen (POST /definition) legt
 * eine neue Version mit Begründung an — nichts wird überschrieben. Treffer je Regel aus den Rasterläufen
 * (GET /regeln/treffer): entschieden, verdrängt (passte, eine frühere entschied), Testtreffer.
 */
interface Regel {
  id: string; kriterium: string; name: string; aktion: string; bedingung: any; modus: string; herkunft: string;
  stufe_block: number; filter: string; gruppe: string; fenster_min: number; mindestens: Record<string, number>;
  incident_art: string;
}
interface Treffer { treffer: number; verdraengt: number; test: number; letzter: string | null }

@Component({
  selector: 'app-regelwerk',
  standalone: true,
  imports: [CommonModule, FormsModule, DatePipe, DecimalPipe, MatIconModule, MatTooltipModule],
  template: `
  <div class="rw">
    <header class="rw-kopf">
      <h1>Regelwerk</h1>
      @if (def(); as d) {
        <span class="rw-meta">Definition v{{ d.version }} · {{ entwurf().length }} Regeln · {{ objektNamen().length }} Objekte ·
          Default: block (N0)</span>
      }
      <span class="sp"></span>
      <label class="rw-meta">Treffer der letzten
        <select [(ngModel)]="stunden" (ngModelChange)="ladeTreffer()">
          <option [ngValue]="24">24 h</option><option [ngValue]="168">7 Tage</option>
        </select></label>
    </header>

    <nav class="rw-tabs">
      @for (t of tabs; track t.id) {
        <button type="button" [class.an]="tab() === t.id" (click)="tab.set(t.id)">{{ t.label }}</button>
      }
    </nav>

    @if (fehler()) { <div class="rw-fehler"><mat-icon>error_outline</mat-icon>{{ fehler() }}</div> }

    @if (tab() === 'regeln') {
      <p class="rw-hinweis">Erste passende aktive Regel entscheidet (von oben nach unten). <b>Verdrängt</b> = passte, aber
        eine frühere Regel entschied. <b>Test</b> = hätte gepasst, entscheidet aber nie. Filtersprache wie in der Suche:
        <code>art:wert</code>, <code>!</code>, <code>OR</code>, Klammern, <code>level&gt;=7</code>, Objekte als <code>&#64;Name</code>.</p>
      <table class="rw-tab">
        <thead><tr>
          <th>#</th><th>ID</th><th>Name</th><th>Filter</th><th>Aktion</th><th>Kriterium</th><th>Modus</th>
          <th class="z">Treffer</th><th class="z">Verdrängt</th><th class="z">Test</th><th>Letzter</th><th></th>
        </tr></thead>
        <tbody>
          @for (r of entwurf(); track r.id; let i = $index) {
            <tr [class.geaendert]="geaendert(r)" [class.test]="r.modus === 'test'" [class.inc]="r.aktion === 'incident'">
              <td class="pos">
                <button type="button" (click)="schiebe(i, -1)" [disabled]="i === 0" aria-label="nach oben">▲</button>
                <button type="button" (click)="schiebe(i, 1)" [disabled]="i === entwurf().length - 1" aria-label="nach unten">▼</button>
              </td>
              <td class="id" [matTooltip]="'Herkunft: ' + r.herkunft">{{ r.id }}</td>
              <td><input [(ngModel)]="r.name" (ngModelChange)="beruehrt()"></td>
              <td class="filter"><input [(ngModel)]="r.filter" (ngModelChange)="beruehrt()" spellcheck="false">
                @if (r.aktion === 'incident') {
                  <div class="inc-info">Gruppe {{ r.gruppe }} · Fenster {{ r.fenster_min }} min · mindestens {{ mindestText(r) }} · Art {{ r.incident_art }}</div>
                }
              </td>
              <td>
                <select [(ngModel)]="r.aktion" (ngModelChange)="beruehrt()" [disabled]="r.aktion === 'incident'">
                  <option value="forward">forward</option><option value="block">block</option>
                  @if (r.aktion === 'incident') { <option value="incident">incident</option> }
                </select>
              </td>
              <td>
                <select [(ngModel)]="r.kriterium" (ngModelChange)="beruehrt()">
                  @for (k of kriterien(); track k.id) { <option [value]="k.id">{{ k.id }} {{ k.name }}</option> }
                </select>
              </td>
              <td>
                <button type="button" class="modus" [class.aktiv]="r.modus === 'aktiv'" (click)="r.modus = r.modus === 'aktiv' ? 'test' : 'aktiv'; beruehrt()"
                        [matTooltip]="r.modus === 'aktiv' ? 'entscheidet — Klick: nur zählen (Test)' : 'zählt nur — Klick: scharf schalten'">{{ r.modus }}</button>
              </td>
              <td class="z">{{ t(r.id).treffer | number }}</td>
              <td class="z" [class.warn]="t(r.id).verdraengt > 0">{{ t(r.id).verdraengt | number }}</td>
              <td class="z">{{ t(r.id).test | number }}</td>
              <td class="zeit">{{ t(r.id).letzter ? (t(r.id).letzter | date:'dd.MM. HH:mm') : '—' }}</td>
              <td><button type="button" class="weg" (click)="loesche(i)" [matTooltip]="'Regel ' + r.id + ' aus dem Entwurf entfernen'"><mat-icon>delete</mat-icon></button></td>
            </tr>
          }
        </tbody>
      </table>
      <button type="button" class="neu" (click)="neueRegel()"><mat-icon>add</mat-icon>Neue Regel (Testmodus)</button>
    }

    @if (tab() === 'objekte') {
      <p class="rw-hinweis">Objekte sind benannte Filter (wie Adress-/Applikations-Objekte einer Firewall); Regeln verweisen
        mit <code>&#64;Name</code> darauf. Ein Objekt, das eine Regel nutzt, lässt sich nicht löschen.</p>
      <table class="rw-tab">
        <thead><tr><th>Name</th><th>Filter</th><th>Verwendet in</th><th></th></tr></thead>
        <tbody>
          @for (n of objektNamen(); track n) {
            <tr [class.geaendert]="objekte()[n] !== original()?.objekte?.[n]">
              <td class="id">&#64;{{ n }}</td>
              <td class="filter"><input [ngModel]="objekte()[n]" (ngModelChange)="setzeObjekt(n, $event)" spellcheck="false"></td>
              <td>{{ verwendetIn(n).join(', ') || '—' }}</td>
              <td><button type="button" class="weg" (click)="loescheObjekt(n)" [disabled]="verwendetIn(n).length > 0"
                          [matTooltip]="verwendetIn(n).length ? 'verwendet in ' + verwendetIn(n).join(', ') : 'Objekt entfernen'"><mat-icon>delete</mat-icon></button></td>
            </tr>
          }
        </tbody>
      </table>
      <div class="neu-obj">
        <input [(ngModel)]="neuObjName" placeholder="Name, z. B. DOT1X-Rauschen">
        <input [(ngModel)]="neuObjFilter" placeholder="Filter, z. B. muster:(a OR b)" spellcheck="false">
        <button type="button" class="neu" (click)="neuesObjekt()" [disabled]="!neuObjName.trim() || !neuObjFilter.trim()"><mat-icon>add</mat-icon>Objekt</button>
      </div>
    }

    @if (tab() === 'versionen') {
      <table class="rw-tab">
        <thead><tr><th>Version</th><th>Von</th><th>Wann</th><th>Begründung</th></tr></thead>
        <tbody>
          @for (v of versionen(); track v.version) {
            <tr><td class="id">v{{ v.version }}</td><td>{{ v.erstellt_von }}</td><td class="zeit">{{ v.erstellt_at | date:'dd.MM.yy HH:mm' }}</td>
              <td class="begr">{{ v.begruendung }}</td></tr>
          }
        </tbody>
      </table>
    }

    <!-- Entwurf: prüfen (Vorschau) und als neue Version übernehmen -->
    <footer class="rw-entwurf" [class.an]="aenderungen() > 0">
      <span><b>{{ aenderungen() }}</b> Änderung(en) im Entwurf</span>
      <button type="button" (click)="pruefe()" [disabled]="pruefend()"><mat-icon>fact_check</mat-icon>Prüfen &amp; Vorschau</button>
      <button type="button" (click)="verwerfe()" [disabled]="aenderungen() === 0"><mat-icon>undo</mat-icon>Verwerfen</button>
      <input class="begr" [(ngModel)]="begruendung" placeholder="Begründung (Pflicht für eine neue Version)">
      <span [matTooltip]="uebernehmenGrund()">
        <button type="button" class="primaer" (click)="uebernehme()" [disabled]="!!uebernehmenGrund()">
          <mat-icon>publish</mat-icon>Als v{{ (def()?.version ?? 0) + 1 }} übernehmen</button>
      </span>
    </footer>
    @if (pruefung(); as p) {
      <section class="rw-vorschau">
        @if (p.fehler?.length) {
          <div class="rw-fehler"><mat-icon>error_outline</mat-icon>{{ p.fehler.length }} Fehler: {{ p.fehler.join(' · ') }}</div>
        } @else if (p.vorschau; as v) {
          <div><b>Vorschau</b> ({{ v.stunden }} h, {{ v.geprueft | number }} vom Regelwerk beurteilte Nachrichten@if (v.gekappt) {, gekappt}):
            <b>{{ v.geaendert | number }}</b> Urteil(e) würden sich ändern.
            @for (w of wechsel(v); track w[0]) { <span class="chip">{{ w[0] }}: {{ w[1] }}</span> }</div>
          @if (testTreffer(v).length) {
            <div>Regeln im Testmodus hätten getroffen:
              @for (w of testTreffer(v); track w[0]) { <span class="chip">{{ w[0] }}: {{ w[1] | number }}</span> }</div>
          }
          @for (b of v.beispiele; track b.id) {
            <div class="bsp">{{ b.vorher.aktion }} ({{ b.vorher.regel }}) → <b>{{ b.nachher.aktion }} ({{ b.nachher.regel }})</b> · {{ b.host }} · {{ b.text }}</div>
          }
          <div class="grenze">Grenze: {{ v.grenze }}</div>
        }
      </section>
    }
  </div>
  `,
  styles: [`
    .rw { padding: 16px 24px 96px; max-width: 1500px; }
    .rw-kopf { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
    .rw-kopf h1 { margin: 0; font-size: 22px; }
    .rw-meta { font-size: 12.5px; color: var(--mat-sys-on-surface-variant); }
    .sp { flex: 1; }
    .rw-tabs { display: flex; gap: 4px; margin: 12px 0; border-bottom: 1px solid var(--mat-sys-outline-variant); }
    .rw-tabs button { border: 0; background: none; padding: 8px 14px; cursor: pointer; color: inherit; border-bottom: 2px solid transparent; }
    .rw-tabs button.an { border-bottom-color: var(--mat-sys-primary); font-weight: 600; }
    .rw-hinweis { font-size: 12.5px; color: var(--mat-sys-on-surface-variant); margin: 4px 0 10px; }
    .rw-fehler { display: flex; gap: 6px; align-items: center; color: var(--mat-sys-error); font-size: 13px; margin: 8px 0; }
    .rw-tab { width: 100%; border-collapse: collapse; font-size: 12.5px; }
    .rw-tab th { text-align: left; font-weight: 600; padding: 6px 4px; border-bottom: 1px solid var(--mat-sys-outline-variant); white-space: nowrap; }
    .rw-tab td { padding: 3px 4px; border-bottom: 1px solid color-mix(in srgb, var(--mat-sys-outline-variant) 50%, transparent); vertical-align: top; }
    .rw-tab input, .rw-tab select, .neu-obj input, .begr { width: 100%; box-sizing: border-box; background: transparent; color: inherit;
      border: 1px solid transparent; border-radius: 4px; padding: 3px 5px; font: inherit; }
    .rw-tab input:hover, .rw-tab input:focus, .neu-obj input, .begr { border-color: var(--mat-sys-outline-variant); }
    .rw-tab select option { color: initial; }
    td.filter { min-width: 280px; font-family: monospace; }
    td:nth-child(3) { min-width: 170px; }
    td:nth-child(5) select { min-width: 92px; }
    td:nth-child(6) select { min-width: 64px; }
    td.id { font-family: monospace; white-space: nowrap; }
    td.z, th.z { text-align: right; font-variant-numeric: tabular-nums; }
    td.warn { color: var(--mat-sys-tertiary); font-weight: 600; }
    td.zeit { white-space: nowrap; color: var(--mat-sys-on-surface-variant); }
    td.begr { max-width: 700px; }
    td.pos { white-space: nowrap; }
    td.pos button, .weg { border: 0; background: none; cursor: pointer; color: inherit; padding: 0 2px; opacity: .7; }
    td.pos button:disabled, .weg:disabled { opacity: .2; cursor: default; }
    tr.geaendert { background: color-mix(in srgb, var(--mat-sys-primary) 10%, transparent); }
    tr.test td.id { font-style: italic; }
    .inc-info { font-size: 11px; color: var(--mat-sys-on-surface-variant); font-family: inherit; }
    .modus { border: 1px solid var(--mat-sys-outline-variant); background: none; color: inherit; border-radius: 10px; padding: 1px 8px; cursor: pointer; font-size: 11.5px; }
    .modus.aktiv { background: color-mix(in srgb, var(--mat-sys-primary) 20%, transparent); border-color: var(--mat-sys-primary); }
    .neu { display: inline-flex; align-items: center; gap: 4px; margin-top: 10px; border: 1px dashed var(--mat-sys-outline-variant);
      background: none; color: inherit; border-radius: 6px; padding: 4px 10px; cursor: pointer; }
    .neu-obj { display: grid; grid-template-columns: 220px 1fr auto; gap: 8px; margin-top: 10px; align-items: center; }
    .rw-entwurf { position: fixed; left: 240px; right: 0; bottom: 0; display: flex; gap: 8px; align-items: center; padding: 10px 24px;
      background: var(--mat-sys-surface-container, #f3f3f3); border-top: 1px solid var(--mat-sys-outline-variant); z-index: 20; }
    .rw-entwurf.an { border-top: 2px solid var(--mat-sys-primary); }
    .rw-entwurf button { display: inline-flex; align-items: center; gap: 4px; border: 1px solid var(--mat-sys-outline-variant);
      background: none; color: inherit; border-radius: 6px; padding: 5px 10px; cursor: pointer; white-space: nowrap; }
    .rw-entwurf button:disabled { opacity: .45; cursor: default; }
    .rw-entwurf button.primaer { background: var(--mat-sys-primary); color: var(--mat-sys-on-primary); border-color: transparent; }
    .rw-entwurf .begr { flex: 1; min-width: 200px; }
    .rw-vorschau { margin-top: 14px; font-size: 12.5px; }
    .rw-vorschau .chip { display: inline-block; margin: 2px 4px; padding: 1px 8px; border-radius: 10px;
      background: color-mix(in srgb, var(--mat-sys-primary) 14%, transparent); }
    .rw-vorschau .bsp { font-family: monospace; font-size: 11.5px; padding: 2px 0; }
    .rw-vorschau .grenze { color: var(--mat-sys-on-surface-variant); margin-top: 6px; font-size: 11.5px; }
    :host-context(html.cs-theme-lcars) .rw-entwurf { background: #1a1208; }
    :host-context(html.cs-theme-lcars) .rw-entwurf button.primaer { background: #ffcc66; color: #000; }
  `],
})
export class RegelwerkComponent implements OnInit {
  private http = inject(HttpClient);
  private auth = inject(AuthService);
  private api = `${environment.apiUrl}/log-watch`;

  readonly tabs = [{ id: 'regeln', label: 'Regeln' }, { id: 'objekte', label: 'Objekte' }, { id: 'versionen', label: 'Versionen' }] as const;
  tab = signal<'regeln' | 'objekte' | 'versionen'>('regeln');
  stunden = 24;

  def = signal<any | null>(null);
  original = signal<{ regeln: Regel[]; objekte: Record<string, string> } | null>(null);
  entwurf = signal<Regel[]>([]);
  objekte = signal<Record<string, string>>({});
  treffer = signal<Record<string, Treffer>>({});
  versionen = signal<any[]>([]);
  pruefung = signal<any | null>(null);
  pruefend = signal(false);
  fehler = signal('');
  private stand = signal(0);                       // zählt Änderungen, damit computed() neu rechnet
  begruendung = '';
  neuObjName = '';
  neuObjFilter = '';

  readonly kriterien = computed(() => this.def()?.kriterien ?? []);
  readonly objektNamen = computed(() => Object.keys(this.objekte()).sort());
  readonly istAdmin = computed(() => this.auth.userRole() === 'admin');
  /** neue, entfernte, inhaltlich geänderte Regeln + 1, wenn sich die Reihenfolge der BESTEHENDEN ändert
   *  (eine eingefügte Regel verschiebt die übrigen — das ist keine Änderung an ihnen) + geänderte Objekte */
  readonly aenderungen = computed(() => {
    this.stand();
    const o = this.original();
    if (!o) return 0;
    const alt = new Map(o.regeln.map(r => [r.id, r]));
    const neuIds = new Set(this.entwurf().map(r => r.id));
    let n = 0;
    for (const r of this.entwurf()) {
      const a = alt.get(r.id);
      if (!a || JSON.stringify(this.vergleich(a)) !== JSON.stringify(this.vergleich(r))) n++;
    }
    n += o.regeln.filter(r => !neuIds.has(r.id)).length;
    const reihe = (l: Regel[]) => l.map(r => r.id).filter(id => alt.has(id) && neuIds.has(id)).join(',');
    if (reihe(o.regeln) !== reihe(this.entwurf())) n++;
    const ob = this.objekte();
    for (const k of new Set([...Object.keys(ob), ...Object.keys(o.objekte)])) if (ob[k] !== o.objekte[k]) n++;
    return n;
  });

  ngOnInit() { this.lade(); }

  lade() {
    this.http.get<any>(`${this.api}/definition`).subscribe({
      next: d => {
        this.def.set(d);
        const regeln: Regel[] = (d.regeln ?? []).map((r: Regel) => ({ ...r, mindestens: { ...(r.mindestens ?? {}) } }));
        this.original.set({ regeln: regeln.map(r => ({ ...r })), objekte: { ...(d.objekte ?? {}) } });
        this.entwurf.set(regeln.map(r => ({ ...r })));
        this.objekte.set({ ...(d.objekte ?? {}) });
        this.pruefung.set(null);
        this.beruehrt();
      },
      error: e => this.fehler.set(e?.error?.detail ?? 'Definition nicht lesbar'),
    });
    this.ladeTreffer();
    this.http.get<any[]>(`${this.api}/definition`, { params: { alle: true } }).subscribe({ next: v => this.versionen.set(v) });
  }

  ladeTreffer() {
    this.http.get<any>(`${this.api}/regeln/treffer`, { params: { stunden: this.stunden } }).subscribe({
      next: r => this.treffer.set(r.regeln ?? {}),
      error: e => this.fehler.set(e?.error?.detail ?? 'Treffer nicht lesbar'),
    });
  }

  t(id: string): Treffer { return this.treffer()[id] ?? { treffer: 0, verdraengt: 0, test: 0, letzter: null }; }
  mindestText(r: Regel): string { return Object.entries(r.mindestens ?? {}).map(([k, v]) => `${v} ${k}`).join(', ') || '—'; }
  wechsel(v: any): [string, number][] { return Object.entries(v.je_wechsel ?? {}) as [string, number][]; }
  testTreffer(v: any): [string, number][] { return Object.entries(v.test_treffer ?? {}) as [string, number][]; }

  private vergleich(r: Regel) {
    return { name: r.name, filter: r.filter, aktion: r.aktion, kriterium: r.kriterium, modus: r.modus };
  }

  geaendert(r: Regel): boolean {
    this.stand();
    const a = this.original()?.regeln.find(x => x.id === r.id);
    return !a || JSON.stringify(this.vergleich(a)) !== JSON.stringify(this.vergleich(r));
  }

  beruehrt() { this.stand.update(n => n + 1); this.pruefung.set(null); }

  schiebe(i: number, d: number) {
    const l = [...this.entwurf()];
    [l[i], l[i + d]] = [l[i + d], l[i]];
    this.entwurf.set(l);
    this.beruehrt();
  }

  loesche(i: number) { this.entwurf.set(this.entwurf().filter((_, j) => j !== i)); this.beruehrt(); }

  neueRegel() {
    const ids = new Set(this.entwurf().map(r => r.id));
    let n = 1;
    while (ids.has(`A${n}`)) n++;
    const neu: Regel = { id: `A${n}`, kriterium: 'N4', name: 'Neue Regel', aktion: 'block', bedingung: {}, modus: 'test',
      herkunft: 'admin', stufe_block: 0, filter: '', gruppe: '', fenster_min: 0, mindestens: {}, incident_art: '' };
    // vor die erste FORWARD-Regel: neue Blockregeln wirken sonst nie (verdeckt)
    const l = [...this.entwurf()];
    const pos = Math.max(0, l.findIndex(r => r.aktion === 'forward'));
    l.splice(pos, 0, neu);
    this.entwurf.set(l);
    this.beruehrt();
  }

  setzeObjekt(n: string, text: string) { this.objekte.set({ ...this.objekte(), [n]: text }); this.beruehrt(); }

  neuesObjekt() {
    const n = this.neuObjName.trim().replace(/^@/, '');
    if (!n || this.objekte()[n] !== undefined) { this.fehler.set(`Objekt @${n} gibt es schon`); return; }
    this.setzeObjekt(n, this.neuObjFilter.trim());
    this.neuObjName = ''; this.neuObjFilter = '';
  }

  loescheObjekt(n: string) {
    const o = { ...this.objekte() };
    delete o[n];
    this.objekte.set(o);
    this.beruehrt();
  }

  verwendetIn(n: string): string[] {
    const re = new RegExp(`@${n.replace(/[.*+?^${}()|[\]\\-]/g, '\\$&')}(?![\\w-])`);
    return [...this.entwurf().filter(r => re.test(r.filter ?? '')).map(r => r.id),
            ...Object.entries(this.objekte()).filter(([k, v]) => k !== n && re.test(v)).map(([k]) => '@' + k)];
  }

  private body(begruendung = '') {
    const d = this.def();
    return { kriterien: d.kriterien, parameter: d.parameter, begruendung,
             regeln: this.entwurf().map(r => ({ ...r, bedingung: r.filter ? {} : r.bedingung })), objekte: this.objekte() };
  }

  pruefe() {
    this.pruefend.set(true);
    this.http.post<any>(`${this.api}/definition/pruefen`, this.body(), { params: { stunden: this.stunden > 168 ? 168 : this.stunden } }).subscribe({
      next: r => { this.pruefung.set(r); this.pruefend.set(false); },
      error: e => { this.pruefung.set({ fehler: [e?.error?.detail ?? 'Prüfung fehlgeschlagen'] }); this.pruefend.set(false); },
    });
  }

  /** Grund, warum Übernehmen nicht geht — leer heißt: geht (ausgegraut statt hinterher melden) */
  uebernehmenGrund(): string {
    if (!this.istAdmin()) return 'Neue Versionen legt nur die Rolle admin an (deine Rolle: ' + (this.auth.userRole() ?? '—') + ')';
    if (this.aenderungen() === 0) return 'Keine Änderung im Entwurf';
    const p = this.pruefung();
    if (!p) return 'Erst „Prüfen & Vorschau“';
    if (p.fehler?.length) return 'Entwurf hat Fehler';
    if (!this.begruendung.trim()) return 'Begründung fehlt';
    return '';
  }

  uebernehme() {
    if (this.uebernehmenGrund()) return;
    this.http.post<any>(`${this.api}/definition`, this.body(this.begruendung.trim())).subscribe({
      next: () => { this.begruendung = ''; this.lade(); },
      error: e => this.fehler.set(e?.error?.detail ?? 'Übernehmen fehlgeschlagen'),
    });
  }

  verwerfe() {
    const o = this.original();
    if (!o) return;
    this.entwurf.set(o.regeln.map(r => ({ ...r })));
    this.objekte.set({ ...o.objekte });
    this.beruehrt();
  }
}
