import { Component, Inject, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { MAT_DIALOG_DATA, MatDialogRef, MatDialogModule } from '@angular/material/dialog';
import { MatTabsModule } from '@angular/material/tabs';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatChipsModule } from '@angular/material/chips';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatDividerModule } from '@angular/material/divider';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatBadgeModule } from '@angular/material/badge';
import { environment } from '../../../environments/environment';

const CLOSURE_CODES = [
  { value: 'solved_permanently', label: 'Dauerlösung' },
  { value: 'solved_workaround', label: 'Workaround' },
  { value: 'no_fault_found', label: 'Kein Fehler gefunden' },
  { value: 'duplicate', label: 'Duplikat' },
  { value: 'user_error', label: 'Benutzerfehler' },
  { value: 'cancelled', label: 'Storniert' },
];

const STATUS_OPTIONS = [
  { value: 'in_progress', label: 'In Bearbeitung' },
  { value: 'pending', label: 'Ausstehend' },
  { value: 'resolved', label: 'Gelöst' },
  { value: 'closed', label: 'Geschlossen' },
];

const CATEGORIES = [
  'Hardware', 'Software', 'Netzwerk', 'Sicherheit',
  'E-Mail / Kommunikation', 'Berechtigungen / Zugang',
  'Backup / Storage', 'Monitoring / Alerting',
  'Server / Virtualisierung', 'Datenbank', 'Sonstiges',
];

const PRIORITY_META: Record<string, { color: string; label: string }> = {
  P1: { color: '#c62828', label: 'Kritisch' },
  P2: { color: '#ef6c00', label: 'Hoch' },
  P3: { color: '#f9a825', label: 'Mittel' },
  P4: { color: '#388e3c', label: 'Niedrig' },
};

@Component({
  selector: 'cs-work-session-dialog',
  standalone: true,
  imports: [
    CommonModule, FormsModule, MatDialogModule, MatTabsModule,
    MatButtonModule, MatIconModule, MatFormFieldModule, MatInputModule,
    MatSelectModule, MatChipsModule, MatProgressSpinnerModule, MatDividerModule,
    MatExpansionModule, MatSnackBarModule, MatTooltipModule, MatBadgeModule,
  ],
  template: `
    <div class="dialog-root">
      <!-- Header -->
      <div class="dialog-header" [style.border-left-color]="priorityColor()">
        <div class="header-left">
          @if (session()?.jira_key) {
            <a class="jira-link" [href]="jiraUrl()" target="_blank">
              {{ session()?.jira_key }} <mat-icon inline>open_in_new</mat-icon>
            </a>
          }
          <span class="priority-badge" [style.background]="priorityColor()">
            {{ session()?.priority ?? '–' }}
          </span>
          <span class="status-badge">{{ statusLabel() }}</span>
        </div>
        <div class="header-right">
          <button mat-icon-button (click)="dialogRef.close()"><mat-icon>close</mat-icon></button>
        </div>
      </div>

      @if (loading()) {
        <div class="spinner-center"><mat-spinner diameter="40"></mat-spinner></div>
      } @else {

      <mat-tab-group animationDuration="200ms" class="session-tabs">

        <!-- ── Tab 1: Overview ── -->
        <mat-tab label="Übersicht">
          <div class="tab-content">
            <mat-form-field appearance="outline" class="full-width">
              <mat-label>Titel</mat-label>
              <input matInput [(ngModel)]="form.title">
            </mat-form-field>

            <div class="row-2">
              <mat-form-field appearance="outline">
                <mat-label>Kategorie</mat-label>
                <mat-select [(ngModel)]="form.category">
                  @for (c of categories; track c) { <mat-option [value]="c">{{ c }}</mat-option> }
                </mat-select>
              </mat-form-field>
              <mat-form-field appearance="outline">
                <mat-label>Unterkategorie</mat-label>
                <input matInput [(ngModel)]="form.subcategory">
              </mat-form-field>
            </div>

            <div class="row-3">
              <mat-form-field appearance="outline">
                <mat-label>Impact</mat-label>
                <mat-select [(ngModel)]="form.impact" (selectionChange)="onImpactUrgencyChange()">
                  <mat-option value="high">Hoch</mat-option>
                  <mat-option value="medium">Mittel</mat-option>
                  <mat-option value="low">Niedrig</mat-option>
                </mat-select>
              </mat-form-field>
              <mat-form-field appearance="outline">
                <mat-label>Urgency</mat-label>
                <mat-select [(ngModel)]="form.urgency" (selectionChange)="onImpactUrgencyChange()">
                  <mat-option value="high">Hoch</mat-option>
                  <mat-option value="medium">Mittel</mat-option>
                  <mat-option value="low">Niedrig</mat-option>
                </mat-select>
              </mat-form-field>
              <mat-form-field appearance="outline">
                <mat-label>Status</mat-label>
                <mat-select [(ngModel)]="form.status">
                  @for (s of statusOptions; track s.value) { <mat-option [value]="s.value">{{ s.label }}</mat-option> }
                </mat-select>
              </mat-form-field>
            </div>

            @if (form.status === 'resolved' || form.status === 'closed') {
              <div class="row-2">
                <mat-form-field appearance="outline">
                  <mat-label>Abschlusstyp</mat-label>
                  <mat-select [(ngModel)]="form.closure_code">
                    @for (c of closureCodes; track c.value) { <mat-option [value]="c.value">{{ c.label }}</mat-option> }
                  </mat-select>
                </mat-form-field>
                <mat-form-field appearance="outline">
                  <mat-label>Lösungstyp</mat-label>
                  <mat-select [(ngModel)]="form.resolution_type">
                    <mat-option value="permanent_fix">Dauerlösung</mat-option>
                    <mat-option value="workaround">Workaround</mat-option>
                  </mat-select>
                </mat-form-field>
              </div>
            }

            <!-- SLA Indicator -->
            @if (session()?.sla_response_at || session()?.sla_resolved_at) {
              <div class="sla-row">
                <mat-icon class="sla-icon">timer</mat-icon>
                <span class="sla-label">SLA Response:</span>
                <span [class.sla-breach]="isSlaBreached(session()?.sla_response_at)">
                  {{ session()?.sla_response_at | date:'dd.MM. HH:mm' }}
                </span>
                <mat-icon class="sla-icon" style="margin-left:12px">schedule</mat-icon>
                <span class="sla-label">Lösung:</span>
                <span [class.sla-breach]="isSlaBreached(session()?.sla_resolved_at)">
                  {{ session()?.sla_resolved_at | date:'dd.MM. HH:mm' }}
                </span>
              </div>
            }

            <!-- KI Auto-Kategorisierung -->
            <button mat-stroked-button (click)="autoCategorize()" [disabled]="aiLoading.categorize()">
              @if (aiLoading.categorize()) { <mat-spinner diameter="16"></mat-spinner> }
              @else { <mat-icon>psychology</mat-icon> }
              KI Auto-Kategorisierung
            </button>

            <div class="form-actions">
              <button mat-flat-button color="primary" (click)="saveOverview()">
                <mat-icon>save</mat-icon> Speichern
              </button>
            </div>
          </div>
        </mat-tab>

        <!-- ── Tab 2: Work Notes ── -->
        <mat-tab [label]="'Notizen (' + (session()?.work_notes?.length ?? 0) + ')'">
          <div class="tab-content">
            <div class="notes-log">
              @for (note of session()?.work_notes ?? []; track $index) {
                <div class="note-entry" [class.ai-note]="note.type === 'ai'">
                  <div class="note-meta">
                    <mat-icon class="note-icon">{{ note.type === 'ai' ? 'smart_toy' : 'person' }}</mat-icon>
                    <span class="note-author">{{ note.author }}</span>
                    <span class="note-time">{{ note.timestamp | date:'dd.MM.yyyy HH:mm' }}</span>
                  </div>
                  <pre class="note-content">{{ note.content }}</pre>
                </div>
              }
              @if (!session()?.work_notes?.length) {
                <div class="empty-notes">Noch keine Notizen.</div>
              }
            </div>

            <mat-divider></mat-divider>

            <div class="add-note">
              <mat-form-field appearance="outline" class="full-width">
                <mat-label>Neue Notiz</mat-label>
                <textarea matInput [(ngModel)]="newNote" rows="3" placeholder="Arbeitsschritt, Beobachtung, …"></textarea>
              </mat-form-field>
              <button mat-flat-button color="primary" (click)="addNote()" [disabled]="!newNote.trim()">
                <mat-icon>add_comment</mat-icon> Notiz hinzufügen
              </button>
            </div>
          </div>
        </mat-tab>

        <!-- ── Tab 3: KI-Kommentar ── -->
        <mat-tab label="KI-Assistent">
          <div class="tab-content">

            <!-- Comment Generator -->
            <mat-expansion-panel expanded>
              <mat-expansion-panel-header>
                <mat-panel-title><mat-icon>comment</mat-icon> Ticket-Kommentar generieren</mat-panel-title>
              </mat-expansion-panel-header>
              <div class="panel-body">
                <div class="comment-type-row">
                  @for (ct of commentTypes; track ct.value) {
                    <button mat-stroked-button
                      [class.selected]="selectedCommentType() === ct.value"
                      (click)="selectedCommentType.set(ct.value)">
                      {{ ct.label }}
                    </button>
                  }
                </div>
                <button mat-flat-button color="accent" (click)="generateComment()" [disabled]="aiLoading.comment()">
                  @if (aiLoading.comment()) { <mat-spinner diameter="16"></mat-spinner> Generiere… }
                  @else { <ng-container><mat-icon>auto_awesome</mat-icon> Kommentar erstellen</ng-container> }
                </button>
                @if (generatedComment()) {
                  <div class="ai-result">
                    <div class="ai-result-header">
                      <span>Generierter Kommentar</span>
                      <button mat-icon-button (click)="copyToClipboard(generatedComment()!)" matTooltip="Kopieren">
                        <mat-icon>content_copy</mat-icon>
                      </button>
                    </div>
                    <pre class="ai-text">{{ generatedComment() }}</pre>
                  </div>
                }
              </div>
            </mat-expansion-panel>

            <!-- Resolution Generator -->
            <mat-expansion-panel>
              <mat-expansion-panel-header>
                <mat-panel-title><mat-icon>task_alt</mat-icon> Abschluss-Dokumentation generieren</mat-panel-title>
              </mat-expansion-panel-header>
              <div class="panel-body">
                <mat-form-field appearance="outline" class="full-width">
                  <mat-label>Root Cause (optional)</mat-label>
                  <textarea matInput [(ngModel)]="form.root_cause" rows="2"></textarea>
                </mat-form-field>
                <div class="row-2">
                  <mat-form-field appearance="outline">
                    <mat-label>Abschlusstyp</mat-label>
                    <mat-select [(ngModel)]="form.closure_code">
                      @for (c of closureCodes; track c.value) { <mat-option [value]="c.value">{{ c.label }}</mat-option> }
                    </mat-select>
                  </mat-form-field>
                  <mat-form-field appearance="outline">
                    <mat-label>Lösungstyp</mat-label>
                    <mat-select [(ngModel)]="form.resolution_type">
                      <mat-option value="permanent_fix">Dauerlösung</mat-option>
                      <mat-option value="workaround">Workaround</mat-option>
                    </mat-select>
                  </mat-form-field>
                </div>
                <button mat-flat-button color="accent" (click)="generateResolution()" [disabled]="aiLoading.resolution()">
                  @if (aiLoading.resolution()) { <mat-spinner diameter="16"></mat-spinner> Generiere… }
                  @else { <ng-container><mat-icon>auto_awesome</mat-icon> Dokumentation erstellen</ng-container> }
                </button>
                @if (generatedResolution()) {
                  <div class="ai-result">
                    <div class="ai-result-header">
                      <span>Lösungsdokumentation</span>
                      <button mat-icon-button (click)="copyToClipboard(generatedResolution()!)" matTooltip="Kopieren">
                        <mat-icon>content_copy</mat-icon>
                      </button>
                    </div>
                    <pre class="ai-text">{{ generatedResolution() }}</pre>
                  </div>
                }
              </div>
            </mat-expansion-panel>

            <!-- Solution Suggester -->
            <mat-expansion-panel>
              <mat-expansion-panel-header>
                <mat-panel-title><mat-icon>search</mat-icon> Lösungsvorschläge (RAG + Web)</mat-panel-title>
              </mat-expansion-panel-header>
              <div class="panel-body">
                <button mat-flat-button color="accent" (click)="suggestSolution()" [disabled]="aiLoading.solution()">
                  @if (aiLoading.solution()) { <mat-spinner diameter="16"></mat-spinner> Suche… }
                  @else { <ng-container><mat-icon>travel_explore</mat-icon> Lösungen suchen</ng-container> }
                </button>
                @if (solutionData()) {
                  @if (solutionData()!.solution_steps?.length) {
                    <div class="solution-section">
                      <strong>Lösungsschritte</strong>
                      <ol>@for (step of solutionData()!.solution_steps; track $index) { <li>{{ step }}</li> }</ol>
                    </div>
                  }
                  @if (solutionData()!.possible_causes?.length) {
                    <div class="solution-section">
                      <strong>Mögliche Ursachen</strong>
                      <ul>@for (c of solutionData()!.possible_causes; track $index) { <li>{{ c }}</li> }</ul>
                    </div>
                  }
                  @if (solutionData()!.rag_results?.length) {
                    <div class="solution-section">
                      <strong>Wissensdatenbank</strong>
                      @for (r of solutionData()!.rag_results; track $index) {
                        <div class="rag-item"><mat-icon>article</mat-icon> {{ r.title ?? r }}</div>
                      }
                    </div>
                  }
                  @if (solutionData()!.web_results?.length) {
                    <div class="solution-section">
                      <strong>Web-Ergebnisse</strong>
                      @for (r of solutionData()!.web_results; track r.url) {
                        <div class="rag-item"><mat-icon>language</mat-icon>
                          <a [href]="r.url" target="_blank">{{ r.title }}</a>
                        </div>
                      }
                    </div>
                  }
                }
              </div>
            </mat-expansion-panel>

          </div>
        </mat-tab>

        <!-- ── Tab 4: 5-Why Analyse ── -->
        <mat-tab label="5-Why Analyse">
          <div class="tab-content">
            <p class="tab-desc">Die 5-Why-Analyse identifiziert die Kernursache eines Problems durch fünf iterative Warum-Fragen (ITIL Problem Management).</p>

            <mat-form-field appearance="outline" class="full-width">
              <mat-label>Problembeschreibung / Root Cause Hinweis</mat-label>
              <textarea matInput [(ngModel)]="form.root_cause" rows="3"></textarea>
            </mat-form-field>

            <button mat-flat-button color="accent" (click)="run5Why()" [disabled]="aiLoading.fiveWhy()">
              @if (aiLoading.fiveWhy()) { <mat-spinner diameter="16"></mat-spinner> Analysiere… }
              @else { <ng-container><mat-icon>psychology</mat-icon> 5-Why Analyse starten</ng-container> }
            </button>

            @if (fiveWhyData()) {
              <div class="fivewhy-result">
                @for (i of [1,2,3,4,5]; track i) {
                  @let key = 'why_' + i;
                  @if (fiveWhyData()![key]) {
                    <div class="why-step">
                      <div class="why-q"><span class="why-num">Warum {{ i }}</span> {{ fiveWhyData()![key].question }}</div>
                      <div class="why-a">→ {{ fiveWhyData()![key].answer }}</div>
                    </div>
                  }
                }
                @if (fiveWhyData()!.root_cause) {
                  <div class="root-cause-box">
                    <mat-icon>gps_fixed</mat-icon>
                    <div>
                      <strong>Kernursache:</strong> {{ fiveWhyData()!.root_cause }}
                    </div>
                  </div>
                }
                @if (fiveWhyData()!.corrective_action) {
                  <div class="corrective-box">
                    <mat-icon>build</mat-icon>
                    <div>
                      <strong>Empfohlene Maßnahme:</strong> {{ fiveWhyData()!.corrective_action }}
                    </div>
                  </div>
                }
                <div class="fivewhy-actions">
                  <button mat-stroked-button (click)="adopt5WhyRootCause()">
                    <mat-icon>check</mat-icon> Kernursache übernehmen
                  </button>
                </div>
              </div>
            }
          </div>
        </mat-tab>

      </mat-tab-group>
      }
    </div>
  `,
  styles: [`
    .dialog-root { display: flex; flex-direction: column; height: 100%; max-height: 85vh; }
    .dialog-header { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; border-left: 4px solid #ccc; background: var(--mat-sys-surface-variant); }
    .header-left { display: flex; align-items: center; gap: 8px; }
    .jira-link { font-family: monospace; font-size: 13px; color: var(--mat-sys-primary); text-decoration: none; display: flex; align-items: center; gap: 2px; }
    .priority-badge { font-size: 11px; font-weight: 700; color: #fff; padding: 2px 8px; border-radius: 12px; }
    .status-badge { font-size: 12px; color: var(--mat-sys-on-surface-variant); }
    .header-right { display: flex; gap: 4px; }
    .spinner-center { display: flex; justify-content: center; padding: 60px; }
    .session-tabs { flex: 1; overflow: hidden; }
    .tab-content { padding: 16px; display: flex; flex-direction: column; gap: 12px; max-height: calc(85vh - 120px); overflow-y: auto; }
    .tab-desc { color: var(--mat-sys-on-surface-variant); font-size: 13px; margin: 0; }
    .full-width { width: 100%; }
    .row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
    .sla-row { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--mat-sys-on-surface-variant); }
    .sla-icon { font-size: 16px; width: 16px; height: 16px; }
    .sla-label { font-weight: 500; }
    .sla-breach { color: #c62828; font-weight: 700; }
    .form-actions { display: flex; justify-content: flex-end; }
    /* Notes */
    .notes-log { display: flex; flex-direction: column; gap: 8px; max-height: 300px; overflow-y: auto; }
    .note-entry { border-radius: 8px; padding: 8px 12px; background: var(--mat-sys-surface-variant); }
    .note-entry.ai-note { background: #e3f2fd; border-left: 3px solid #1565c0; }
    .note-meta { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
    .note-icon { font-size: 16px; width: 16px; height: 16px; }
    .note-author { font-weight: 500; font-size: 12px; }
    .note-time { font-size: 11px; color: var(--mat-sys-on-surface-variant); margin-left: auto; }
    pre.note-content { margin: 0; font-size: 12px; white-space: pre-wrap; word-break: break-word; font-family: inherit; }
    .empty-notes { text-align: center; padding: 20px; color: var(--mat-sys-on-surface-variant); }
    .add-note { display: flex; flex-direction: column; gap: 8px; }
    /* AI Panels */
    mat-expansion-panel { margin-bottom: 4px; }
    mat-panel-title { display: flex; align-items: center; gap: 6px; }
    .panel-body { padding: 12px 0; display: flex; flex-direction: column; gap: 10px; }
    .comment-type-row { display: flex; gap: 6px; flex-wrap: wrap; }
    .comment-type-row button.selected { background: var(--mat-sys-primary-container); }
    .ai-result { background: var(--mat-sys-surface-variant); border-radius: 8px; padding: 12px; }
    .ai-result-header { display: flex; align-items: center; justify-content: space-between; font-weight: 500; font-size: 13px; margin-bottom: 6px; }
    pre.ai-text { margin: 0; font-size: 12px; white-space: pre-wrap; word-break: break-word; font-family: inherit; line-height: 1.5; }
    .solution-section { font-size: 13px; }
    .solution-section strong { display: block; margin-bottom: 4px; }
    .rag-item { display: flex; align-items: center; gap: 6px; font-size: 12px; padding: 3px 0; }
    .rag-item mat-icon { font-size: 14px; width: 14px; height: 14px; }
    /* 5-Why */
    .fivewhy-result { display: flex; flex-direction: column; gap: 10px; }
    .why-step { padding: 8px 12px; background: var(--mat-sys-surface-variant); border-radius: 6px; }
    .why-q { font-size: 13px; }
    .why-num { font-weight: 700; color: var(--mat-sys-primary); margin-right: 6px; }
    .why-a { font-size: 12px; color: var(--mat-sys-on-surface-variant); margin-top: 2px; padding-left: 12px; }
    .root-cause-box { display: flex; gap: 8px; align-items: flex-start; padding: 12px; background: #fff3e0; border-radius: 8px; border-left: 4px solid #ef6c00; font-size: 13px; }
    .corrective-box { display: flex; gap: 8px; align-items: flex-start; padding: 12px; background: #e8f5e9; border-radius: 8px; border-left: 4px solid #388e3c; font-size: 13px; }
    .fivewhy-actions { display: flex; gap: 8px; }
  `],
})
export class WorkSessionDialogComponent implements OnInit {
  session = signal<any | null>(null);
  loading = signal(true);

  form: any = {
    title: '', category: null, subcategory: '', impact: null, urgency: null,
    status: 'in_progress', closure_code: 'solved_permanently', resolution_type: 'permanent_fix',
    root_cause: '',
  };

  newNote = '';
  selectedCommentType = signal('progress');
  generatedComment = signal<string | null>(null);
  generatedResolution = signal<string | null>(null);
  solutionData = signal<any | null>(null);
  fiveWhyData = signal<any | null>(null);

  aiLoading = {
    comment: signal(false),
    resolution: signal(false),
    solution: signal(false),
    fiveWhy: signal(false),
    categorize: signal(false),
  };

  readonly categories = CATEGORIES;
  readonly closureCodes = CLOSURE_CODES;
  readonly statusOptions = STATUS_OPTIONS;
  readonly commentTypes = [
    { value: 'progress', label: 'Fortschritt' },
    { value: 'pending', label: 'Pending' },
    { value: 'escalation', label: 'Eskalation' },
    { value: 'handoff', label: 'Übergabe' },
  ];

  private sessionId: string | null = null;

  constructor(
    public dialogRef: MatDialogRef<WorkSessionDialogComponent>,
    @Inject(MAT_DIALOG_DATA) public dialogData: any,
    private http: HttpClient,
    private snackBar: MatSnackBar,
  ) {}

  ngOnInit() {
    if (this.dialogData?.id) {
      this.loadSession(this.dialogData.id);
    } else {
      this.createSession();
    }
  }

  private createSession() {
    this.http.post<any>(`${environment.apiUrl}/workflow`, {
      title: this.dialogData.title,
      jira_key: this.dialogData.jira_key,
      jira_issue_id: this.dialogData.jira_issue_id,
      alert_id: this.dialogData.alert_id,
    }).subscribe({
      next: s => { this.setSession(s); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }

  private loadSession(id: string) {
    this.http.get<any>(`${environment.apiUrl}/workflow/${id}`).subscribe({
      next: s => { this.setSession(s); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }

  private setSession(s: any) {
    this.session.set(s);
    this.sessionId = s.id;
    this.form = {
      title: s.title,
      category: s.category,
      subcategory: s.subcategory,
      impact: s.impact,
      urgency: s.urgency,
      status: s.status,
      closure_code: s.closure_code ?? 'solved_permanently',
      resolution_type: s.resolution_type ?? 'permanent_fix',
      root_cause: s.root_cause ?? '',
    };
  }

  saveOverview() {
    this.http.patch(`${environment.apiUrl}/workflow/${this.sessionId}`, {
      title: this.form.title,
      category: this.form.category,
      subcategory: this.form.subcategory,
      impact: this.form.impact,
      urgency: this.form.urgency,
      status: this.form.status,
      closure_code: this.form.closure_code,
      resolution_type: this.form.resolution_type,
      root_cause: this.form.root_cause,
    }).subscribe({
      next: () => { this.snackBar.open('Gespeichert', '', { duration: 2000 }); this.loadSession(this.sessionId!); },
    });
  }

  onImpactUrgencyChange() {
    if (this.form.impact && this.form.urgency) {
      this.http.patch(`${environment.apiUrl}/workflow/${this.sessionId}`, { impact: this.form.impact, urgency: this.form.urgency }).subscribe({
        next: () => this.loadSession(this.sessionId!),
      });
    }
  }

  addNote() {
    if (!this.newNote.trim()) return;
    this.http.post<any>(`${environment.apiUrl}/workflow/${this.sessionId}/notes`, { content: this.newNote }).subscribe({
      next: res => { this.session.update(s => ({ ...s, work_notes: res.notes })); this.newNote = ''; },
    });
  }

  generateComment() {
    this.aiLoading.comment.set(true);
    this.http.post<any>(`${environment.apiUrl}/workflow/${this.sessionId}/generate-comment`, { comment_type: this.selectedCommentType() }).subscribe({
      next: res => { this.generatedComment.set(res.comment); this.aiLoading.comment.set(false); this.loadSession(this.sessionId!); },
      error: () => { this.aiLoading.comment.set(false); this.snackBar.open('Fehler beim Generieren', '', { duration: 3000 }); },
    });
  }

  generateResolution() {
    this.aiLoading.resolution.set(true);
    this.http.post<any>(`${environment.apiUrl}/workflow/${this.sessionId}/generate-resolution`, {
      root_cause: this.form.root_cause || null,
      resolution_type: this.form.resolution_type,
      closure_code: this.form.closure_code,
    }).subscribe({
      next: res => { this.generatedResolution.set(res.resolution); this.aiLoading.resolution.set(false); this.loadSession(this.sessionId!); },
      error: () => { this.aiLoading.resolution.set(false); this.snackBar.open('Fehler beim Generieren', '', { duration: 3000 }); },
    });
  }

  suggestSolution() {
    this.aiLoading.solution.set(true);
    this.http.post<any>(`${environment.apiUrl}/workflow/${this.sessionId}/suggest-solution`, { use_rag: true, use_web: true }).subscribe({
      next: res => { this.solutionData.set(res); this.aiLoading.solution.set(false); },
      error: () => { this.aiLoading.solution.set(false); this.snackBar.open('Fehler bei Lösungssuche', '', { duration: 3000 }); },
    });
  }

  run5Why() {
    this.aiLoading.fiveWhy.set(true);
    if (this.form.root_cause) {
      this.http.patch(`${environment.apiUrl}/workflow/${this.sessionId}`, { root_cause: this.form.root_cause }).subscribe();
    }
    this.http.post<any>(`${environment.apiUrl}/workflow/${this.sessionId}/5why`, {}).subscribe({
      next: res => { this.fiveWhyData.set(res); this.aiLoading.fiveWhy.set(false); },
      error: () => { this.aiLoading.fiveWhy.set(false); this.snackBar.open('Fehler bei 5-Why-Analyse', '', { duration: 3000 }); },
    });
  }

  adopt5WhyRootCause() {
    if (this.fiveWhyData()?.root_cause) {
      this.form.root_cause = this.fiveWhyData()!.root_cause;
      this.saveOverview();
    }
  }

  autoCategorize() {
    this.aiLoading.categorize.set(true);
    this.http.post<any>(`${environment.apiUrl}/workflow/${this.sessionId}/auto-categorize`, {}).subscribe({
      next: res => {
        this.form.category = res.category ?? this.form.category;
        this.form.subcategory = res.subcategory ?? this.form.subcategory;
        this.form.impact = res.impact ?? this.form.impact;
        this.form.urgency = res.urgency ?? this.form.urgency;
        this.aiLoading.categorize.set(false);
        this.loadSession(this.sessionId!);
        this.snackBar.open('Kategorisierung übernommen', '', { duration: 2000 });
      },
      error: () => this.aiLoading.categorize.set(false),
    });
  }

  copyToClipboard(text: string) {
    navigator.clipboard.writeText(text).then(() => this.snackBar.open('In Zwischenablage kopiert', '', { duration: 2000 }));
  }

  priorityColor() {
    return PRIORITY_META[this.session()?.priority ?? '']?.color ?? '#ccc';
  }

  statusLabel() {
    return STATUS_OPTIONS.find(s => s.value === this.session()?.status)?.label ?? this.session()?.status ?? '';
  }

  jiraUrl() {
    return this.session()?.jira_browse_url ?? null;
  }

  isSlaBreached(iso: string | null | undefined): boolean {
    if (!iso) return false;
    return new Date(iso) < new Date();
  }
}
