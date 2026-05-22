import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatChipsModule } from '@angular/material/chips';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatDividerModule } from '@angular/material/divider';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { WorkSessionDialogComponent } from '../workflow/work-session-dialog.component';
import { environment } from '../../../environments/environment';

interface JqlQuery {
  id: string;
  name: string;
  jql: string;
  position: number;
  enabled: boolean;
  show_in_widget: boolean;
}

interface JiraIssue {
  key: string;
  fields: {
    summary: string;
    status: { name: string };
    priority: { name: string };
    assignee: { displayName: string } | null;
    updated: string;
    issuetype: { name: string };
  };
}

interface TicketGroup {
  id: string;
  name: string;
  jql: string;
  issues: JiraIssue[];
  error?: string;
}

const PRIORITY_COLOR: Record<string, string> = {
  Highest: '#c62828',
  High: '#ef6c00',
  Medium: '#f9a825',
  Low: '#388e3c',
  Lowest: '#757575',
};

const STATUS_COLOR: Record<string, string> = {
  'In Progress': '#1565c0',
  'In Bearbeitung': '#1565c0',
  Open: '#424242',
  Offen: '#424242',
  Done: '#2e7d32',
  Erledigt: '#2e7d32',
  Pending: '#e65100',
  Ausstehend: '#e65100',
};

@Component({
  selector: 'cs-my-tickets',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatCardModule, MatButtonModule, MatIconModule,
    MatFormFieldModule, MatInputModule, MatChipsModule,
    MatProgressSpinnerModule, MatDividerModule, MatDialogModule,
    MatSnackBarModule, MatTooltipModule, MatSlideToggleModule,
  ],
  template: `
    <div class="page-container">
      <div class="page-header">
        <h2>Meine Tickets</h2>
        <div class="header-actions">
          <button mat-stroked-button (click)="loadTickets()">
            <mat-icon>refresh</mat-icon> Aktualisieren
          </button>
          <button mat-flat-button color="primary" (click)="openQueryManager()">
            <mat-icon>tune</mat-icon> Filter verwalten
          </button>
        </div>
      </div>

      @if (loadingTickets()) {
        <div class="spinner-center"><mat-spinner diameter="40"></mat-spinner></div>
      } @else {
        @for (group of ticketGroups(); track group.id) {
          <mat-card class="group-card">
            <mat-card-header>
              <mat-card-title>
                <span>{{ group.name }}</span>
                <mat-chip class="count-chip">{{ group.issues.length }}</mat-chip>
              </mat-card-title>
              <mat-card-subtitle class="jql-subtitle">{{ group.jql }}</mat-card-subtitle>
            </mat-card-header>
            <mat-divider></mat-divider>

            @if (group.error) {
              <div class="group-error"><mat-icon>error</mat-icon> {{ group.error }}</div>
            } @else if (group.issues.length === 0) {
              <div class="group-empty">Keine Tickets gefunden.</div>
            } @else {
              <div class="issue-list">
                @for (issue of group.issues; track issue.key) {
                  <div class="issue-row" (click)="openSession(issue)">
                    <span class="issue-key">{{ issue.key }}</span>
                    <mat-icon class="issue-type-icon" [matTooltip]="issue.fields.issuetype?.name">
                      {{ issueTypeIcon(issue.fields.issuetype?.name) }}
                    </mat-icon>
                    <span class="issue-summary">{{ issue.fields.summary }}</span>
                    <div class="issue-meta">
                      <span class="priority-dot"
                        [style.background]="priorityColor(issue.fields.priority?.name)"
                        [matTooltip]="issue.fields.priority?.name"></span>
                      <span class="status-badge"
                        [style.background]="statusBg(issue.fields.status?.name)"
                        [style.color]="statusColor(issue.fields.status?.name)">
                        {{ issue.fields.status?.name }}
                      </span>
                      <span class="updated-label">{{ issue.fields.updated | date:'dd.MM. HH:mm' }}</span>
                    </div>
                  </div>
                }
              </div>
            }
          </mat-card>
        }

        @if (ticketGroups().length === 0) {
          <mat-card class="empty-card">
            <mat-icon>inbox</mat-icon>
            <p>Keine Ticket-Filter konfiguriert.</p>
            <button mat-flat-button color="primary" (click)="openQueryManager()">
              Filter einrichten
            </button>
          </mat-card>
        }
      }

      <!-- Query Manager Panel -->
      @if (showQueryManager()) {
        <div class="side-panel-backdrop" (click)="closeQueryManager()"></div>
        <div class="side-panel">
          <div class="panel-header">
            <h3>Ticket-Filter</h3>
            <button mat-icon-button (click)="closeQueryManager()"><mat-icon>close</mat-icon></button>
          </div>

          <div class="query-list">
            @for (q of queries(); track q.id; let i = $index) {
              <div class="query-item" [class.disabled]="!q.enabled">
                <mat-icon class="drag-icon">drag_indicator</mat-icon>
                <div class="query-info">
                  @if (editingQueryId() === q.id) {
                    <input class="edit-name" [(ngModel)]="q.name">
                    <input class="edit-jql" [(ngModel)]="q.jql">
                    <div class="edit-actions">
                      <button mat-stroked-button (click)="saveQuery(q)">Speichern</button>
                      <button mat-button (click)="cancelEdit()">Abbrechen</button>
                    </div>
                  } @else {
                    <span class="q-name">{{ q.name }}</span>
                    <span class="q-jql">{{ q.jql }}</span>
                  }
                </div>
                <div class="query-actions">
                  <mat-slide-toggle [checked]="q.enabled" (change)="toggleQuery(q, $event.checked)"></mat-slide-toggle>
                  <button mat-icon-button (click)="editQuery(q)"><mat-icon>edit</mat-icon></button>
                  <button mat-icon-button color="warn" (click)="deleteQuery(q)"><mat-icon>delete</mat-icon></button>
                </div>
              </div>
            }
          </div>

          <div class="panel-actions">
            <button mat-stroked-button (click)="addQuery()">
              <mat-icon>add</mat-icon> Filter hinzufügen
            </button>
            <button mat-stroked-button [disabled]="!hasLlm()" (click)="showAiInput.set(!showAiInput())">
              <mat-icon>psychology</mat-icon> KI erstellen
            </button>
          </div>

          @if (showAiInput()) {
            <div class="ai-input-box">
              <mat-form-field appearance="outline" class="full-width">
                <mat-label>Beschreiben Sie den gewünschten Filter</mat-label>
                <input matInput [(ngModel)]="aiDesc" placeholder="z.B. meine Bugs mit hoher Priorität">
              </mat-form-field>
              <button mat-flat-button color="accent" (click)="generateAiQuery()" [disabled]="aiGenerating()">
                @if (aiGenerating()) { <mat-spinner diameter="16"></mat-spinner> Generiere… }
                @else { <ng-container><mat-icon>auto_awesome</mat-icon> Generieren</ng-container> }
              </button>
            </div>
          }
        </div>
      }
    </div>
  `,
  styles: [`
    .page-container { padding: 24px; max-width: 1100px; position: relative; }
    .page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; flex-wrap: wrap; gap: 8px; }
    .page-header h2 { margin: 0; }
    .header-actions { display: flex; gap: 8px; }
    .spinner-center { display: flex; justify-content: center; padding: 60px; }
    .group-card { margin-bottom: 16px; }
    mat-card-title { display: flex; align-items: center; gap: 8px; }
    .count-chip { font-size: 11px; min-height: 20px; background: var(--mat-sys-primary-container); }
    .jql-subtitle { font-family: monospace; font-size: 11px; margin-top: 2px !important; }
    .group-error { padding: 12px 16px; display: flex; align-items: center; gap: 6px; color: #c62828; }
    .group-empty { padding: 16px; text-align: center; color: var(--mat-sys-on-surface-variant); font-size: 13px; }
    .issue-list { display: flex; flex-direction: column; }
    .issue-row { display: flex; align-items: center; gap: 8px; padding: 8px 16px; cursor: pointer; border-bottom: 1px solid var(--mat-sys-outline-variant); transition: background .15s; }
    .issue-row:hover { background: var(--mat-sys-surface-variant); }
    .issue-row:last-child { border-bottom: none; }
    .issue-key { font-family: monospace; font-size: 12px; color: var(--mat-sys-primary); min-width: 80px; font-weight: 500; }
    .issue-type-icon { font-size: 16px; width: 16px; height: 16px; color: var(--mat-sys-on-surface-variant); }
    .issue-summary { flex: 1; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .issue-meta { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
    .priority-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
    .status-badge { font-size: 10px; padding: 2px 6px; border-radius: 10px; font-weight: 500; }
    .updated-label { font-size: 11px; color: var(--mat-sys-on-surface-variant); min-width: 80px; text-align: right; }
    .empty-card { display: flex; flex-direction: column; align-items: center; padding: 40px; gap: 12px; }
    .empty-card mat-icon { font-size: 48px; width: 48px; height: 48px; color: var(--mat-sys-on-surface-variant); }
    /* Side Panel */
    .side-panel-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,.3); z-index: 99; }
    .side-panel { position: fixed; right: 0; top: 0; bottom: 0; width: 420px; background: var(--mat-sys-surface); box-shadow: -4px 0 16px rgba(0,0,0,.2); z-index: 100; display: flex; flex-direction: column; overflow-y: auto; padding: 16px; gap: 12px; }
    .panel-header { display: flex; align-items: center; justify-content: space-between; }
    .panel-header h3 { margin: 0; }
    .query-list { display: flex; flex-direction: column; gap: 6px; }
    .query-item { display: flex; align-items: flex-start; gap: 8px; padding: 8px; border: 1px solid var(--mat-sys-outline-variant); border-radius: 8px; }
    .query-item.disabled { opacity: 0.5; }
    .drag-icon { color: var(--mat-sys-on-surface-variant); cursor: grab; flex-shrink: 0; margin-top: 2px; }
    .query-info { flex: 1; display: flex; flex-direction: column; gap: 2px; }
    .q-name { font-weight: 500; font-size: 13px; }
    .q-jql { font-family: monospace; font-size: 11px; color: var(--mat-sys-on-surface-variant); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .edit-name { border: 1px solid var(--mat-sys-outline); border-radius: 4px; padding: 2px 6px; font-weight: 500; font-size: 13px; width: 100%; margin-bottom: 4px; }
    .edit-jql { border: 1px solid var(--mat-sys-outline); border-radius: 4px; padding: 2px 6px; font-family: monospace; font-size: 11px; width: 100%; }
    .edit-actions { display: flex; gap: 4px; margin-top: 6px; }
    .query-actions { display: flex; align-items: center; gap: 2px; flex-shrink: 0; }
    .panel-actions { display: flex; gap: 8px; }
    .ai-input-box { display: flex; flex-direction: column; gap: 8px; padding: 12px; background: var(--mat-sys-surface-variant); border-radius: 8px; }
    .full-width { width: 100%; }
  `],
})
export class MyTicketsComponent implements OnInit {
  ticketGroups = signal<TicketGroup[]>([]);
  queries = signal<JqlQuery[]>([]);
  loadingTickets = signal(true);
  showQueryManager = signal(false);
  editingQueryId = signal<string | null>(null);
  showAiInput = signal(false);
  aiGenerating = signal(false);
  aiDesc = '';
  hasLlm = signal(false);

  constructor(
    private http: HttpClient,
    private dialog: MatDialog,
    private snackBar: MatSnackBar,
  ) {}

  ngOnInit() {
    this.loadTickets();
    this.loadQueries();
    this.checkLlm();
  }

  loadTickets() {
    this.loadingTickets.set(true);
    this.http.get<TicketGroup[]>(`${environment.apiUrl}/jira-view/my-tickets`).subscribe({
      next: data => { this.ticketGroups.set(data); this.loadingTickets.set(false); },
      error: () => this.loadingTickets.set(false),
    });
  }

  loadQueries() {
    this.http.get<JqlQuery[]>(`${environment.apiUrl}/preferences/jira-queries`).subscribe({
      next: data => this.queries.set(data),
    });
  }

  checkLlm() {
    this.http.get<{ configured: boolean }>(`${environment.apiUrl}/settings/llm-status`).subscribe({
      next: data => this.hasLlm.set(!!data?.configured),
    });
  }

  openQueryManager() { this.showQueryManager.set(true); }
  closeQueryManager() { this.showQueryManager.set(false); this.loadTickets(); }

  addQuery() {
    const q: JqlQuery = { id: crypto.randomUUID(), name: 'Neuer Filter', jql: 'assignee = currentUser() ORDER BY updated DESC', position: this.queries().length, enabled: true, show_in_widget: true };
    this.http.post<any>(`${environment.apiUrl}/preferences/jira-queries`, { name: q.name, jql: q.jql }).subscribe({
      next: res => { this.queries.update(qs => [...qs, { ...q, id: res.id }]); this.editingQueryId.set(res.id); },
    });
  }

  editQuery(q: JqlQuery) { this.editingQueryId.set(q.id); }
  cancelEdit() { this.editingQueryId.set(null); }

  saveQuery(q: JqlQuery) {
    this.http.patch(`${environment.apiUrl}/preferences/jira-queries/${q.id}`, { name: q.name, jql: q.jql }).subscribe({
      next: () => { this.editingQueryId.set(null); this.snackBar.open('Filter gespeichert', '', { duration: 2000 }); },
    });
  }

  toggleQuery(q: JqlQuery, enabled: boolean) {
    this.http.patch(`${environment.apiUrl}/preferences/jira-queries/${q.id}`, { enabled }).subscribe({
      next: () => this.queries.update(qs => qs.map(x => x.id === q.id ? { ...x, enabled } : x)),
    });
  }

  deleteQuery(q: JqlQuery) {
    this.http.delete(`${environment.apiUrl}/preferences/jira-queries/${q.id}`).subscribe({
      next: () => { this.queries.update(qs => qs.filter(x => x.id !== q.id)); this.snackBar.open('Filter gelöscht', '', { duration: 2000 }); },
    });
  }

  generateAiQuery() {
    if (!this.aiDesc.trim()) return;
    this.aiGenerating.set(true);
    this.http.post<any>(`${environment.apiUrl}/preferences/jira-queries/generate`, { description: this.aiDesc }).subscribe({
      next: result => {
        this.http.post<any>(`${environment.apiUrl}/preferences/jira-queries`, { name: result.name, jql: result.jql }).subscribe({
          next: res => {
            this.queries.update(qs => [...qs, { id: res.id, name: result.name, jql: result.jql, position: qs.length, enabled: true, show_in_widget: true }]);
            this.snackBar.open(`KI-Filter erstellt: "${result.name}"`, '', { duration: 3000 });
            this.aiDesc = '';
            this.showAiInput.set(false);
            this.aiGenerating.set(false);
          },
        });
      },
      error: () => { this.aiGenerating.set(false); this.snackBar.open('Fehler beim Generieren', '', { duration: 3000 }); },
    });
  }

  openSession(issue: JiraIssue) {
    this.dialog.open(WorkSessionDialogComponent, {
      width: '820px',
      maxWidth: '95vw',
      data: {
        jira_key: issue.key,
        title: issue.fields.summary,
        jira_issue_id: issue.key,
      },
    });
  }

  priorityColor(name: string = '') { return PRIORITY_COLOR[name] ?? '#757575'; }
  statusBg(name: string = '') { return (STATUS_COLOR[name] ?? '#424242') + '22'; }
  statusColor(name: string = '') { return STATUS_COLOR[name] ?? '#424242'; }
  issueTypeIcon(type: string = '') {
    if (type.toLowerCase().includes('bug')) return 'bug_report';
    if (type.toLowerCase().includes('task')) return 'task_alt';
    if (type.toLowerCase().includes('story')) return 'auto_stories';
    return 'confirmation_number';
  }
}
