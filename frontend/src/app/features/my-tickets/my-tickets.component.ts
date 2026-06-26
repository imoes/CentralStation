import { Component, OnInit, OnDestroy, signal, Inject, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { TextFieldModule } from '@angular/cdk/text-field';
import { CdkDragDrop, DragDropModule, moveItemInArray } from '@angular/cdk/drag-drop';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatChipsModule } from '@angular/material/chips';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatDividerModule } from '@angular/material/divider';
import {
  MatDialog, MatDialogModule, MatDialogRef, MAT_DIALOG_DATA,
} from '@angular/material/dialog';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { WorkSessionDialogComponent } from '../workflow/work-session-dialog.component';
import { environment } from '../../../environments/environment';
import { I18nService } from '../../core/services/i18n.service';

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
    status: { name: string; statusCategory?: { key: string } };
    priority: { name: string };
    assignee: { displayName: string } | null;
    updated: string;
    issuetype: { name: string };
    comment?: { total: number };
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

// ── JQL Edit Dialog ────────────────────────────────────────────────────────────

@Component({
  selector: 'cs-jql-query-dialog',
  standalone: true,
  imports: [
    CommonModule, FormsModule, TextFieldModule,
    MatDialogModule, MatButtonModule, MatFormFieldModule,
    MatInputModule, MatIconModule,
  ],
  template: `
    <h2 mat-dialog-title>{{ data.title }}</h2>
    <mat-dialog-content>
      <mat-form-field appearance="outline" class="full-width">
        <mat-label>Name</mat-label>
        <input matInput [(ngModel)]="name" placeholder="My open tickets" autofocus>
      </mat-form-field>
      <mat-form-field appearance="outline" class="full-width">
        <mat-label>JQL query</mat-label>
        <textarea matInput [(ngModel)]="jql"
                  cdkTextareaAutosize cdkAutosizeMinRows="4" cdkAutosizeMaxRows="10"
                  placeholder="assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC"
                  spellcheck="false"></textarea>
        <mat-hint>Tip: <code>statusCategory != Done</code> filters out all resolved statuses.</mat-hint>
      </mat-form-field>
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button (click)="dialogRef.close()">Cancel</button>
      <button mat-flat-button color="primary" (click)="save()" [disabled]="!name.trim() || !jql.trim()">
        <mat-icon>save</mat-icon> Save
      </button>
    </mat-dialog-actions>
  `,
  styles: [`
    mat-dialog-content { display: flex; flex-direction: column; gap: 16px; padding-top: 8px; min-width: 520px; }
    .full-width { width: 100%; }
    code { font-family: monospace; background: var(--mat-sys-surface-variant); padding: 1px 4px; border-radius: 3px; }
  `],
})
export class JqlQueryDialogComponent {
  name: string;
  jql: string;

  constructor(
    public dialogRef: MatDialogRef<JqlQueryDialogComponent>,
    @Inject(MAT_DIALOG_DATA) public data: { title: string; name: string; jql: string },
  ) {
    this.name = data.name;
    this.jql = data.jql;
  }

  save() {
    if (this.name.trim() && this.jql.trim()) {
      this.dialogRef.close({ name: this.name.trim(), jql: this.jql.trim() });
    }
  }
}

// ── Main Component ──────────────────────────────────────────────────────────────

@Component({
  selector: 'cs-my-tickets',
  standalone: true,
  imports: [
    CommonModule, FormsModule, DragDropModule,
    MatCardModule, MatButtonModule, MatIconModule,
    MatFormFieldModule, MatInputModule, MatChipsModule,
    MatProgressSpinnerModule, MatDividerModule, MatDialogModule,
    MatSnackBarModule, MatTooltipModule, MatSlideToggleModule,
  ],
  template: `
    <div class="page-container">
      <div class="page-header">
        <h2>{{ i18n.t('app.nav.myTickets') }}</h2>
        <div class="header-actions">
          <button mat-stroked-button (click)="loadTickets()">
            <mat-icon>refresh</mat-icon> Refresh
          </button>
          <button mat-flat-button color="primary" (click)="openQueryManager()">
            <mat-icon>tune</mat-icon> Manage filters
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
              <div class="group-empty">{{ i18n.t('my_tickets.no_tickets') }}</div>
            } @else {
              <div class="issue-list">
                @for (issue of group.issues; track issue.key) {
                  <div class="issue-row" (click)="openSession(issue)">
                    @if (hasUnread(issue)) {
                      <span class="unread-dot" matTooltip="New activity since your last visit"></span>
                    }
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
            <p>No ticket filters configured.</p>
            <button mat-flat-button color="primary" (click)="openQueryManager()">
              Set up filters
            </button>
          </mat-card>
        }
      }

      <!-- Query Manager Panel -->
      @if (showQueryManager()) {
        <div class="side-panel-backdrop" (click)="closeQueryManager()"></div>
        <div class="side-panel">
          <div class="panel-header">
            <h3>Ticket filters</h3>
            <button mat-icon-button (click)="closeQueryManager()"><mat-icon>close</mat-icon></button>
          </div>

          <div class="query-list" cdkDropList (cdkDropListDropped)="onDrop($event)">
            @for (q of queries(); track q.id) {
              <div class="query-item" cdkDrag [class.disabled]="!q.enabled">
                <mat-icon class="drag-icon" cdkDragHandle>drag_indicator</mat-icon>
                <div class="query-info">
                  <span class="q-name">{{ q.name }}</span>
                  <span class="q-jql">{{ q.jql }}</span>
                </div>
                <div class="query-actions">
                  <mat-slide-toggle [checked]="q.enabled" (change)="toggleQuery(q, $event.checked)"></mat-slide-toggle>
                  <button mat-icon-button [matTooltip]="i18n.t('common.edit')" (click)="editQuery(q)">
                    <mat-icon>edit</mat-icon>
                  </button>
                  <button mat-icon-button color="warn" [matTooltip]="i18n.t('common.delete')" (click)="deleteQuery(q)">
                    <mat-icon>delete</mat-icon>
                  </button>
                </div>
              </div>
            }
          </div>

          <div class="panel-actions">
            <button mat-stroked-button (click)="addQuery()">
              <mat-icon>add</mat-icon> {{ i18n.t('common.add') }} filter
            </button>
            <button mat-stroked-button [disabled]="!hasLlm()" (click)="showAiInput.set(!showAiInput())">
              <mat-icon>psychology</mat-icon> Create with AI
            </button>
          </div>

          @if (showAiInput()) {
            <div class="ai-input-box">
              <mat-form-field appearance="outline" class="full-width">
                <mat-label>Describe the desired filter</mat-label>
                <input matInput [(ngModel)]="aiDesc" placeholder="e.g. my high-priority bugs">
              </mat-form-field>
              <button mat-flat-button color="accent" (click)="generateAiQuery()" [disabled]="aiGenerating()">
                @if (aiGenerating()) { <mat-spinner diameter="16"></mat-spinner> Generating… }
                @else { <ng-container><mat-icon>auto_awesome</mat-icon> Generate</ng-container> }
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
    .unread-dot { width: 8px; height: 8px; border-radius: 50%; background: #d32f2f; flex-shrink: 0; }
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
    .query-item { display: flex; align-items: center; gap: 8px; padding: 8px; border: 1px solid var(--mat-sys-outline-variant); border-radius: 8px; }
    .query-item.disabled { opacity: 0.5; }
    .drag-icon { color: var(--mat-sys-on-surface-variant); cursor: grab; flex-shrink: 0; }
    .cdk-drag-preview { background: var(--mat-sys-surface); border: 1px solid var(--mat-sys-primary); border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,.3); opacity: .95; display: flex; align-items: center; gap: 8px; padding: 8px; }
    .cdk-drag-placeholder { opacity: 0.3; background: var(--mat-sys-surface-variant); border-radius: 8px; }
    .cdk-drag-animating { transition: transform 200ms ease; }
    .cdk-drop-list-dragging .query-item:not(.cdk-drag-placeholder) { transition: transform 200ms ease; }
    .query-info { flex: 1; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
    .q-name { font-weight: 500; font-size: 13px; }
    .q-jql { font-family: monospace; font-size: 11px; color: var(--mat-sys-on-surface-variant); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .query-actions { display: flex; align-items: center; gap: 2px; flex-shrink: 0; }
    .panel-actions { display: flex; gap: 8px; }
    .ai-input-box { display: flex; flex-direction: column; gap: 8px; padding: 12px; background: var(--mat-sys-surface-variant); border-radius: 8px; }
    .full-width { width: 100%; }
  `],
})
export class MyTicketsComponent implements OnInit, OnDestroy {
  readonly i18n = inject(I18nService);
  ticketGroups = signal<TicketGroup[]>([]);
  queries = signal<JqlQuery[]>([]);
  loadingTickets = signal(true);
  showQueryManager = signal(false);
  showAiInput = signal(false);
  aiGenerating = signal(false);
  aiDesc = '';
  hasLlm = signal(false);

  private seenMap: Record<string, string> = {};

  constructor(
    private http: HttpClient,
    private dialog: MatDialog,
    private snackBar: MatSnackBar,
  ) {}

  ngOnInit() {
    this.http.get<{ ticket_seen_map: Record<string, string> }>(`${environment.apiUrl}/preferences`)
      .subscribe({
        next: prefs => {
          this.seenMap = prefs.ticket_seen_map ?? {};
          this.loadTickets();
        },
        error: () => this.loadTickets(),
      });
    this.loadQueries();
    this.checkLlm();
  }

  ngOnDestroy() {}

  private _persistSeenMap() {
    this.http.patch(`${environment.apiUrl}/preferences`, { ticket_seen_map: this.seenMap }).subscribe();
  }

  hasUnread(issue: JiraIssue): boolean {
    const seen = this.seenMap[issue.key];
    if (!seen) return false;
    return new Date(issue.fields.updated) > new Date(seen);
  }

  markSeen(issue: JiraIssue) {
    this.seenMap[issue.key] = new Date().toISOString();
    this._persistSeenMap();
  }

  loadTickets() {
    this.loadingTickets.set(true);
    this.http.get<TicketGroup[]>(`${environment.apiUrl}/jira-view/my-tickets`).subscribe({
      next: data => {
        const now = new Date().toISOString();
        let changed = false;
        const allKeys = new Set<string>();
        for (const group of data) {
          for (const issue of group.issues) {
            allKeys.add(issue.key);
            const isDone = issue.fields.status?.statusCategory?.key === 'done';
            if (isDone) {
              // Closed tickets: remove from seen map
              if (issue.key in this.seenMap) {
                delete this.seenMap[issue.key];
                changed = true;
              }
            } else {
              // Open tickets: add to seen map if not tracked yet
              if (!(issue.key in this.seenMap)) {
                this.seenMap[issue.key] = now;
                changed = true;
              }
            }
          }
        }
        if (changed) this._persistSeenMap();
        this.ticketGroups.set(data);
        this.loadingTickets.set(false);
      },
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

  onDrop(event: CdkDragDrop<JqlQuery[]>) {
    if (event.previousIndex === event.currentIndex) return;
    const qs = [...this.queries()];
    moveItemInArray(qs, event.previousIndex, event.currentIndex);
    qs.forEach((q, idx) => { q.position = idx; });
    this.queries.set(qs);
    qs.forEach((q, idx) => {
      this.http.patch(`${environment.apiUrl}/preferences/jira-queries/${q.id}`, { position: idx }).subscribe();
    });
  }

  addQuery() {
    const ref = this.dialog.open(JqlQueryDialogComponent, {
      width: '580px',
      data: {
        title: 'Add filter',
        name: 'New filter',
        jql: 'assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC',
      },
    });
    ref.afterClosed().subscribe((result: { name: string; jql: string } | undefined) => {
      if (!result) return;
      this.http.post<{ id: string; name: string; jql: string }>(
        `${environment.apiUrl}/preferences/jira-queries`,
        { name: result.name, jql: result.jql },
      ).subscribe({
        next: res => {
          this.queries.update(qs => [...qs, {
            id: res.id, name: result.name, jql: result.jql,
            position: qs.length, enabled: true, show_in_widget: true,
          }]);
          this.snackBar.open('Filter saved', '', { duration: 2000 });
        },
      });
    });
  }

  editQuery(q: JqlQuery) {
    const ref = this.dialog.open(JqlQueryDialogComponent, {
      width: '580px',
      data: { title: 'Edit filter', name: q.name, jql: q.jql },
    });
    ref.afterClosed().subscribe((result: { name: string; jql: string } | undefined) => {
      if (!result) return;
      this.http.patch(`${environment.apiUrl}/preferences/jira-queries/${q.id}`, result).subscribe({
        next: () => {
          this.queries.update(qs => qs.map(x => x.id === q.id ? { ...x, ...result } : x));
          this.snackBar.open('Filter saved', '', { duration: 2000 });
        },
      });
    });
  }

  toggleQuery(q: JqlQuery, enabled: boolean) {
    this.http.patch(`${environment.apiUrl}/preferences/jira-queries/${q.id}`, { enabled }).subscribe({
      next: () => this.queries.update(qs => qs.map(x => x.id === q.id ? { ...x, enabled } : x)),
    });
  }

  deleteQuery(q: JqlQuery) {
    if (!confirm(`Delete filter "${q.name}"?`)) return;
    this.http.delete(`${environment.apiUrl}/preferences/jira-queries/${q.id}`).subscribe({
      next: () => {
        this.queries.update(qs => qs.filter(x => x.id !== q.id));
        this.snackBar.open('Filter deleted', '', { duration: 2000 });
      },
    });
  }

  generateAiQuery() {
    if (!this.aiDesc.trim()) return;
    this.aiGenerating.set(true);
    this.http.post<{ name: string; jql: string }>(
      `${environment.apiUrl}/preferences/jira-queries/generate`,
      { description: this.aiDesc },
    ).subscribe({
      next: result => {
        this.http.post<{ id: string }>(
          `${environment.apiUrl}/preferences/jira-queries`,
          { name: result.name, jql: result.jql },
        ).subscribe({
          next: res => {
            this.queries.update(qs => [...qs, {
              id: res.id, name: result.name, jql: result.jql,
              position: qs.length, enabled: true, show_in_widget: true,
            }]);
            this.snackBar.open(`AI filter created: "${result.name}"`, '', { duration: 3000 });
            this.aiDesc = '';
            this.showAiInput.set(false);
            this.aiGenerating.set(false);
          },
        });
      },
      error: () => { this.aiGenerating.set(false); this.snackBar.open('Error generating filter', '', { duration: 3000 }); },
    });
  }

  openSession(issue: JiraIssue) {
    this.markSeen(issue);
    const ref = this.dialog.open(WorkSessionDialogComponent, {
      width: '820px',
      maxWidth: '95vw',
      data: {
        jira_key: issue.key,
        title: issue.fields.summary,
        jira_issue_id: issue.key,
      },
    });
    // After closing, the ticket may have new activity (e.g. a comment just posted) →
    // reload so updated timestamps refresh; hasUnread then shows the dot against the
    // seen-time recorded when the dialog was opened.
    ref.afterClosed().subscribe(() => this.loadTickets());
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
