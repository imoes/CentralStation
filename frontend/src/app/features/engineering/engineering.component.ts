import { Component, OnInit, OnDestroy, signal, computed, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import { RouterLink } from '@angular/router';
import { environment } from '../../../environments/environment';

interface Remediation {
  id: string;
  host: string | null;
  finding_title: string;
  rationale: string | null;
  awx_template_name: string | null;
  awx_template_id: number | null;
  extra_vars: Record<string, unknown>;
  risk: string;
  status: string;
  awx_job_id: number | null;
  stdout: string | null;
  approved_at: string | null;
  created_at: string;
}

interface PlaybookDraft {
  id: string;
  title: string;
  yaml: string;
  target: string | null;
  description: string | null;
  status: string;
  awx_template_id: number | null;
  created_at: string;
}

@Component({
  selector: 'cs-engineering',
  standalone: true,
  imports: [
    CommonModule, FormsModule, RouterLink,
    MatButtonModule, MatIconModule, MatSnackBarModule, MatTooltipModule,
  ],
  template: `
    <div class="eng-root">

      <!-- ── LCARS Header ────────────────────────────────────────── -->
      <div class="eng-header">
        <div class="cap-tl"></div>
        <div class="header-bar">
          <span class="header-title">MASCHINENRAUM</span>
          <span class="header-sub">AWX Remediation &amp; Playbooks</span>
        </div>
        <div class="cap-tr"></div>
      </div>

      <!-- ── Tabs ───────────────────────────────────────────────── -->
      <div class="tab-bar">
        @for (t of tabs; track t.id) {
          <button class="tab-btn" [class.active]="activeTab() === t.id" (click)="activeTab.set(t.id)">
            {{ t.label }}
            @if (t.id === 'pending' && pendingCount() > 0) {
              <span class="tab-badge">{{ pendingCount() }}</span>
            }
            @if (t.id === 'active' && activeCount() > 0) {
              <span class="tab-badge">{{ activeCount() }}</span>
            }
            @if (t.id === 'playbooks' && draftCount() > 0) {
              <span class="tab-badge">{{ draftCount() }}</span>
            }
          </button>
        }
      </div>

      <!-- ── Pending Remediations ───────────────────────────────── -->
      @if (activeTab() === 'pending') {
        <div class="panel">
          <div class="panel-head">
            AUSSTEHENDE REMEDIATIONS
            <button class="refresh-btn" (click)="loadAll()" title="Aktualisieren">↻</button>
          </div>
          @if (loading()) {
            <div class="status-row">Lade…</div>
          } @else if (pendingItems().length === 0) {
            <div class="status-row muted">Keine ausstehenden Remediations.</div>
          } @else {
            @for (r of pendingItems(); track r.id) {
              <div class="remedy-card" [class.risk-high]="r.risk === 'high'" [class.risk-critical]="r.risk === 'critical'">
                <div class="remedy-row1">
                  <span class="risk-badge" [class]="'risk-' + r.risk">{{ r.risk.toUpperCase() }}</span>
                  <span class="remedy-host">{{ r.host ?? '—' }}</span>
                  <span class="remedy-time muted">{{ r.created_at | date:'dd.MM HH:mm' }}</span>
                </div>
                <div class="remedy-finding">{{ r.finding_title }}</div>
                @if (r.rationale) {
                  <div class="remedy-rationale muted">{{ r.rationale | slice:0:200 }}</div>
                }
                <div class="remedy-template">
                  <mat-icon class="inline-icon">smart_toy</mat-icon>
                  Template: <strong>{{ r.awx_template_name ?? r.awx_template_id }}</strong>
                  @if (r.extra_vars && objectKeys(r.extra_vars).length > 0) {
                    <span class="extra-vars"> · Vars: {{ r.extra_vars | json }}</span>
                  }
                </div>
                <div class="remedy-actions">
                  <button mat-flat-button color="primary"
                          (click)="approve(r)"
                          [disabled]="approving().has(r.id)"
                          matTooltip="AWX-Job starten">
                    <mat-icon>play_arrow</mat-icon>
                    Genehmigen &amp; Ausführen
                  </button>
                  <button mat-stroked-button
                          (click)="reject(r)"
                          [disabled]="approving().has(r.id)"
                          matTooltip="Vorschlag ablehnen">
                    <mat-icon>close</mat-icon>
                    Ablehnen
                  </button>
                </div>
              </div>
            }
          }
        </div>
      }

      <!-- ── Active Jobs ────────────────────────────────────────── -->
      @if (activeTab() === 'active') {
        <div class="panel">
          <div class="panel-head">
            LAUFENDE JOBS
            <button class="refresh-btn" (click)="loadAll()" title="Aktualisieren">↻</button>
          </div>
          @if (activeItems().length === 0) {
            <div class="status-row muted">Keine laufenden Jobs.</div>
          } @else {
            @for (r of activeItems(); track r.id) {
              <div class="remedy-card">
                <div class="remedy-row1">
                  <span class="status-pill" [class]="'st-' + r.status">{{ r.status }}</span>
                  <span class="remedy-host">{{ r.host ?? '—' }}</span>
                  @if (r.awx_job_id) {
                    <span class="muted"># Job {{ r.awx_job_id }}</span>
                  }
                </div>
                <div class="remedy-finding">{{ r.finding_title }}</div>
                <div class="remedy-template">
                  Template: <strong>{{ r.awx_template_name }}</strong>
                </div>
                @if (r.stdout) {
                  <pre class="stdout">{{ r.stdout | slice:0:600 }}</pre>
                }
              </div>
            }
          }
        </div>
      }

      <!-- ── History ────────────────────────────────────────────── -->
      @if (activeTab() === 'history') {
        <div class="panel">
          <div class="panel-head">
            VERLAUF
            <button class="refresh-btn" (click)="loadHistory()" title="Aktualisieren">↻</button>
          </div>
          @if (historyItems().length === 0) {
            <div class="status-row muted">Keine abgeschlossenen Remediations.</div>
          } @else {
            @for (r of historyItems(); track r.id) {
              <div class="remedy-card compact">
                <div class="remedy-row1">
                  <span class="status-pill" [class]="'st-' + r.status">{{ r.status }}</span>
                  <span class="remedy-host">{{ r.host ?? '—' }}</span>
                  <span class="remedy-time muted">{{ r.approved_at ?? r.created_at | date:'dd.MM HH:mm' }}</span>
                </div>
                <div class="remedy-finding">{{ r.finding_title }}</div>
                <div class="remedy-template muted">Template: {{ r.awx_template_name }}</div>
              </div>
            }
          }
        </div>
      }

      <!-- ── Playbook Drafts ─────────────────────────────────────── -->
      @if (activeTab() === 'playbooks') {
        <div class="panel">
          <div class="panel-head">
            PLAYBOOK DRAFTS
            <button class="refresh-btn" (click)="loadDrafts()" title="Aktualisieren">↻</button>
          </div>

          <!-- Neuen Draft anfordern -->
          <div class="author-form">
            <input class="author-input" [(ngModel)]="newTaskDesc"
                   placeholder="Aufgabe beschreiben (z.B. 'Nginx-Log rotation einrichten')"
                   (keydown.enter)="authorPlaybook()" />
            <input class="author-input" [(ngModel)]="newTaskCtx"
                   placeholder="Kontext (optional: Betriebssystem, Umgebung, …)" />
            <button mat-flat-button color="primary" (click)="authorPlaybook()"
                    [disabled]="!newTaskDesc.trim() || authoringInProgress()">
              <mat-icon>auto_awesome</mat-icon>
              KI-Playbook generieren
            </button>
            @if (authoringInProgress()) {
              <span class="muted">Generiere…</span>
            }
          </div>

          @if (drafts().length === 0) {
            <div class="status-row muted">Keine Playbook-Drafts vorhanden.</div>
          } @else {
            @for (d of drafts(); track d.id) {
              <div class="draft-card" [class.draft-published]="d.status === 'published'">
                <div class="remedy-row1">
                  <span class="status-pill" [class]="'st-draft-' + d.status">{{ d.status.toUpperCase() }}</span>
                  <span class="remedy-host">{{ d.title }}</span>
                  @if (d.target) { <span class="muted">· {{ d.target }}</span> }
                  <span class="remedy-time muted">{{ d.created_at | date:'dd.MM HH:mm' }}</span>
                </div>
                @if (d.description) {
                  <div class="remedy-rationale muted">{{ d.description }}</div>
                }
                <!-- YAML Vorschau -->
                <details class="yaml-details">
                  <summary class="yaml-summary">YAML anzeigen</summary>
                  <pre class="yaml-preview">{{ d.yaml }}</pre>
                </details>
                @if (d.status === 'drafted') {
                  <div class="remedy-actions">
                    <button mat-flat-button color="primary"
                            (click)="approveDraft(d)"
                            [disabled]="publishingDraft().has(d.id)"
                            matTooltip="In GitLab committen &amp; AWX-Template anlegen">
                      <mat-icon>upload</mat-icon>
                      Genehmigen &amp; Publizieren
                    </button>
                    <button mat-stroked-button (click)="rejectDraft(d)">
                      <mat-icon>close</mat-icon>
                      Ablehnen
                    </button>
                  </div>
                }
                @if (d.awx_template_id) {
                  <div class="remedy-template">
                    <mat-icon class="inline-icon">check_circle</mat-icon>
                    AWX-Template #{{ d.awx_template_id }} angelegt
                  </div>
                }
              </div>
            }
          }
        </div>
      }

      <!-- ── Template Catalog ───────────────────────────────────── -->
      @if (activeTab() === 'catalog') {
        <div class="panel">
          <div class="panel-head">
            AWX TEMPLATE-KATALOG
            <button class="refresh-btn" (click)="loadTemplates()" title="Aktualisieren">↻</button>
          </div>
          @if (templates().length === 0) {
            <div class="status-row muted">Kein AWX-Connector konfiguriert oder keine Templates.</div>
          } @else {
            @for (t of templates(); track t.id) {
              <div class="tmpl-card">
                <span class="tmpl-id">#{{ t.id }}</span>
                <span class="tmpl-name">{{ t.name }}</span>
                @if (t.description) {
                  <span class="tmpl-desc muted">{{ t.description }}</span>
                }
              </div>
            }
          }
        </div>
      }

      <!-- ── LCARS Bottom ───────────────────────────────────────── -->
      <div class="eng-footer">
        <div class="cap-bl"></div>
        <span class="foot-cell">{{ allItems().length }} EINTRÄGE</span>
        <span class="foot-cell">{{ pendingCount() }} AUSSTEHEND</span>
        <span class="foot-cell">{{ draftCount() }} DRAFTS</span>
        <div class="cap-br"></div>
      </div>

    </div>
  `,
  styles: [`
    :host { display: flex; flex-direction: column; height: 100vh; background: #111; color: #ffcc99; font-family: 'Antonio', 'Roboto', sans-serif; }

    /* ── LCARS Header ── */
    .eng-header { display: flex; align-items: stretch; height: 52px; flex-shrink: 0; }
    .cap-tl { width: 18px; background: #ff9933; border-radius: 18px 0 0 0; }
    .cap-tr { width: 18px; background: #ff9933; border-radius: 0 18px 0 0; }
    .cap-bl { width: 18px; background: #ff9933; border-radius: 0 0 0 18px; }
    .cap-br { width: 18px; background: #ff9933; border-radius: 0 0 18px 0; }
    .header-bar { flex: 1; background: #ff9933; display: flex; align-items: baseline; gap: 16px; padding: 0 16px; }
    .header-title { font-size: 1.4rem; font-weight: 700; letter-spacing: 0.12em; color: #111; }
    .header-sub { font-size: 0.78rem; color: #333; letter-spacing: 0.06em; }

    /* ── Tabs ── */
    .tab-bar { display: flex; gap: 4px; padding: 8px 12px; background: #1a1a1a; flex-shrink: 0; }
    .tab-btn { background: #2a2a2a; color: #ffcc99; border: 1px solid #333; padding: 5px 14px; font-family: inherit; font-size: 0.75rem; letter-spacing: 0.08em; cursor: pointer; transition: background 0.15s; position: relative; }
    .tab-btn.active { background: #ff9933; color: #111; border-color: #ff9933; font-weight: 700; }
    .tab-btn:hover:not(.active) { background: #3a3a3a; }
    .tab-badge { background: #cc4433; color: #fff; font-size: 0.65rem; border-radius: 8px; padding: 1px 5px; margin-left: 5px; font-weight: 700; }

    /* ── Panel ── */
    .panel { flex: 1; overflow-y: auto; padding: 12px 16px; display: flex; flex-direction: column; gap: 10px; }
    .panel-head { font-size: 0.75rem; letter-spacing: 0.12em; color: #ff9933; font-weight: 700; padding-bottom: 6px; border-bottom: 1px solid #333; display: flex; align-items: center; gap: 8px; }
    .refresh-btn { background: none; border: 1px solid #333; color: #ffcc99; padding: 1px 6px; cursor: pointer; font-size: 0.9rem; }
    .refresh-btn:hover { border-color: #ff9933; color: #ff9933; }
    .status-row { padding: 16px 0; font-size: 0.85rem; }
    .muted { color: #888; font-size: 0.8rem; }

    /* ── Remedy Card ── */
    .remedy-card { background: #1e1e1e; border: 1px solid #333; border-left: 4px solid #ff9933; padding: 10px 12px; display: flex; flex-direction: column; gap: 5px; }
    .remedy-card.risk-high { border-left-color: #cc7700; }
    .remedy-card.risk-critical { border-left-color: #cc2200; }
    .remedy-card.compact { padding: 7px 10px; gap: 3px; }
    .remedy-row1 { display: flex; align-items: center; gap: 10px; }
    .remedy-host { font-size: 0.85rem; font-weight: 600; color: #ffcc99; }
    .remedy-time { font-size: 0.75rem; margin-left: auto; }
    .remedy-finding { font-size: 0.9rem; font-weight: 600; color: #fff; }
    .remedy-rationale { font-size: 0.78rem; line-height: 1.4; }
    .remedy-template { font-size: 0.8rem; display: flex; align-items: center; gap: 4px; }
    .extra-vars { font-size: 0.72rem; color: #888; }
    .remedy-actions { display: flex; gap: 8px; padding-top: 4px; }
    .inline-icon { font-size: 14px; width: 14px; height: 14px; vertical-align: middle; }

    /* Risk badges */
    .risk-badge { font-size: 0.68rem; font-weight: 700; padding: 2px 6px; border-radius: 4px; }
    .risk-low { background: #1a4a1a; color: #66cc66; }
    .risk-medium { background: #3a3a00; color: #cccc44; }
    .risk-high { background: #4a2200; color: #ff8844; }
    .risk-critical { background: #4a0000; color: #ff4444; }

    /* Status pills */
    .status-pill { font-size: 0.72rem; font-weight: 700; padding: 2px 7px; border-radius: 4px; }
    .st-proposed   { background: #333; color: #ffcc99; }
    .st-running    { background: #003366; color: #66aaff; }
    .st-succeeded  { background: #1a4a1a; color: #66cc66; }
    .st-failed, .st-error { background: #4a0000; color: #ff6666; }
    .st-rejected   { background: #2a2a2a; color: #888; }
    .st-canceled   { background: #2a2a2a; color: #888; }

    /* Draft status pills */
    .st-draft-drafted   { background: #1a3355; color: #88aaff; }
    .st-draft-published { background: #1a4a1a; color: #66cc66; }
    .st-draft-rejected  { background: #2a2a2a; color: #888; }

    /* stdout */
    .stdout { background: #0a0a0a; border: 1px solid #222; padding: 8px; font-size: 0.72rem; color: #aaffaa; overflow-x: auto; white-space: pre-wrap; max-height: 200px; overflow-y: auto; margin: 0; }

    /* Template catalog */
    .tmpl-card { display: flex; align-items: baseline; gap: 10px; padding: 6px 8px; border-bottom: 1px solid #222; font-size: 0.82rem; }
    .tmpl-id { color: #888; font-size: 0.72rem; min-width: 32px; }
    .tmpl-name { font-weight: 600; color: #ffcc99; }
    .tmpl-desc { color: #888; }

    /* ── Playbook Drafts ── */
    .author-form { display: flex; flex-wrap: wrap; gap: 8px; padding: 8px 0; border-bottom: 1px solid #222; margin-bottom: 4px; }
    .author-input { background: #1a1a1a; border: 1px solid #444; color: #ffcc99; padding: 6px 10px; font-family: inherit; font-size: 0.82rem; flex: 1; min-width: 220px; }
    .author-input::placeholder { color: #555; }
    .author-input:focus { outline: none; border-color: #ff9933; }
    .draft-card { background: #1e1e1e; border: 1px solid #333; border-left: 4px solid #5588aa; padding: 10px 12px; display: flex; flex-direction: column; gap: 5px; }
    .draft-card.draft-published { border-left-color: #44aa44; }
    .yaml-details { margin-top: 4px; }
    .yaml-summary { font-size: 0.75rem; color: #888; cursor: pointer; list-style: none; }
    .yaml-summary:hover { color: #ff9933; }
    .yaml-preview { background: #0a0a0a; border: 1px solid #222; padding: 8px; font-size: 0.72rem; color: #aaccff; white-space: pre; overflow-x: auto; max-height: 300px; overflow-y: auto; margin: 4px 0 0; }

    /* ── Footer ── */
    .eng-footer { display: flex; align-items: stretch; height: 28px; flex-shrink: 0; }
    .foot-cell { background: #ff9933; color: #111; font-size: 0.7rem; font-weight: 700; letter-spacing: 0.1em; padding: 0 12px; display: flex; align-items: center; margin-right: 4px; }
  `],
})
export class EngineeringComponent implements OnInit, OnDestroy {
  private http = inject(HttpClient);
  private snack = inject(MatSnackBar);

  readonly tabs = [
    { id: 'pending',   label: 'AUSSTEHEND' },
    { id: 'active',    label: 'LAUFEND' },
    { id: 'history',   label: 'VERLAUF' },
    { id: 'playbooks', label: 'PLAYBOOKS' },
    { id: 'catalog',   label: 'TEMPLATES' },
  ];
  activeTab = signal<string>('pending');

  loading = signal(false);
  allItems = signal<Remediation[]>([]);
  templates = signal<Array<{id: number; name: string; description: string}>>([]);
  approving = signal<Set<string>>(new Set());

  drafts = signal<PlaybookDraft[]>([]);
  publishingDraft = signal<Set<string>>(new Set());
  authoringInProgress = signal(false);
  newTaskDesc = '';
  newTaskCtx = '';

  pendingItems = computed(() => this.allItems().filter(r => r.status === 'proposed'));
  activeItems  = computed(() => this.allItems().filter(r => r.status === 'running'));
  historyItems = computed(() => this.allItems().filter(r => !['proposed','running'].includes(r.status)));
  pendingCount = computed(() => this.pendingItems().length);
  activeCount  = computed(() => this.activeItems().length);
  draftCount   = computed(() => this.drafts().filter(d => d.status === 'drafted').length);

  private _pollInterval?: ReturnType<typeof setInterval>;

  objectKeys = Object.keys;

  ngOnInit(): void {
    this.loadAll();
    this.loadTemplates();
    this.loadDrafts();
    this._pollInterval = setInterval(() => {
      if (this.activeItems().length > 0) this.loadAll();
    }, 10_000);
  }

  ngOnDestroy(): void {
    clearInterval(this._pollInterval);
  }

  loadAll(): void {
    this.loading.set(true);
    this.http.get<Remediation[]>(`${environment.apiUrl}/remediations`)
      .subscribe({
        next: items => { this.allItems.set(items); this.loading.set(false); },
        error: () => this.loading.set(false),
      });
  }

  loadHistory(): void { this.loadAll(); }

  loadTemplates(): void {
    this.http.get<{templates: Array<{id: number; name: string; description: string}>}>(`${environment.apiUrl}/remediations/templates`)
      .subscribe({ next: d => this.templates.set(d?.templates ?? []), error: () => {} });
  }

  loadDrafts(): void {
    this.http.get<PlaybookDraft[]>(`${environment.apiUrl}/remediations/playbooks`)
      .subscribe({ next: d => this.drafts.set(d), error: () => {} });
  }

  approve(r: Remediation): void {
    const next = new Set(this.approving());
    next.add(r.id);
    this.approving.set(next);
    this.http.post<any>(`${environment.apiUrl}/remediations/${r.id}/approve`, {}).subscribe({
      next: res => {
        this.snack.open(`Job gestartet — AWX #${res?.awx_job_id ?? '…'}`, 'OK', { duration: 3000 });
        this.loadAll();
        this.approving.update(s => { const n = new Set(s); n.delete(r.id); return n; });
      },
      error: e => {
        this.snack.open('Fehler: ' + (e?.error?.detail ?? e.message), 'OK', { duration: 4000 });
        this.approving.update(s => { const n = new Set(s); n.delete(r.id); return n; });
      },
    });
  }

  reject(r: Remediation): void {
    this.http.post<any>(`${environment.apiUrl}/remediations/${r.id}/reject`, {}).subscribe({
      next: () => { this.snack.open('Abgelehnt', '', { duration: 2000 }); this.loadAll(); },
      error: () => this.snack.open('Fehler beim Ablehnen', '', { duration: 2000 }),
    });
  }

  authorPlaybook(): void {
    if (!this.newTaskDesc.trim()) return;
    this.authoringInProgress.set(true);
    this.http.post<PlaybookDraft>(`${environment.apiUrl}/remediations/playbooks`, {
      task_description: this.newTaskDesc.trim(),
      context: this.newTaskCtx.trim(),
    }).subscribe({
      next: d => {
        this.snack.open(`Playbook "${d.title}" erstellt`, 'OK', { duration: 3000 });
        this.drafts.update(ds => [d, ...ds]);
        this.newTaskDesc = '';
        this.newTaskCtx = '';
        this.authoringInProgress.set(false);
      },
      error: e => {
        this.snack.open('Fehler: ' + (e?.error?.detail ?? e.message), 'OK', { duration: 4000 });
        this.authoringInProgress.set(false);
      },
    });
  }

  approveDraft(d: PlaybookDraft): void {
    this.publishingDraft.update(s => { const n = new Set(s); n.add(d.id); return n; });
    this.http.post<any>(`${environment.apiUrl}/remediations/playbooks/${d.id}/approve`, {}).subscribe({
      next: res => {
        const tmpl = res?.awx_template_id ? ` → AWX-Template #${res.awx_template_id}` : '';
        this.snack.open(`Publiziert${tmpl}`, 'OK', { duration: 4000 });
        this.loadDrafts();
        this.loadTemplates();
        this.publishingDraft.update(s => { const n = new Set(s); n.delete(d.id); return n; });
      },
      error: e => {
        this.snack.open('Fehler: ' + (e?.error?.detail ?? e.message), 'OK', { duration: 4000 });
        this.publishingDraft.update(s => { const n = new Set(s); n.delete(d.id); return n; });
      },
    });
  }

  rejectDraft(d: PlaybookDraft): void {
    this.http.post<any>(`${environment.apiUrl}/remediations/playbooks/${d.id}/reject`, {}).subscribe({
      next: () => { this.snack.open('Draft abgelehnt', '', { duration: 2000 }); this.loadDrafts(); },
      error: () => {},
    });
  }
}
