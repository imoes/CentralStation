import {
  Component, Input, Output, EventEmitter, OnChanges, SimpleChanges, inject, signal, computed,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { ProjectsService, StepNode } from '../../core/services/projects.service';

interface JiraSection {
  connector: string;
  key: string;
}

const PRIORITY_ICONS: Record<string, string> = {
  highest: '⬆⬆', high: '⬆', medium: '➡', low: '⬇', lowest: '⬇⬇',
};
const PRIORITY_COLORS: Record<string, string> = {
  highest: '#CC4444', high: '#FF8844', medium: '#FFCC99', low: '#88AACC', lowest: '#668899',
};

@Component({
  selector: 'cs-step-card',
  standalone: true,
  imports: [CommonModule, FormsModule, MatButtonModule, MatIconModule, MatTooltipModule, MatSnackBarModule],
  template: `
    <div class="card-panel" [class.open]="!!step" (click)="onBackdropClick($event)">
      <div class="card-inner" (click)="$event.stopPropagation()">
      @if (step) {
        <!-- Header -->
        <div class="card-header">
          <span class="issue-type-badge" [style.background]="issueTypeColor(editState.jira_issue_type)">
            {{ editState.jira_issue_type | uppercase }}
          </span>
          @if (step.jira_key) {
            <a class="jira-key" [href]="jiraUrl()" target="_blank">{{ step.jira_key }}</a>
          }
          <div class="card-header-actions">
            <button mat-icon-button [matTooltip]="'Speichern'" (click)="save()">
              <mat-icon>save</mat-icon>
            </button>
            <button mat-icon-button [matTooltip]="'Schließen'" (click)="close.emit()">
              <mat-icon>close</mat-icon>
            </button>
          </div>
        </div>

        <!-- Title -->
        <div class="card-section">
          <input class="card-title-input" [(ngModel)]="editState.title" placeholder="Titel" />
        </div>

        <!-- Meta row: Type · Priority · Status -->
        <div class="card-meta-row">
          <div class="meta-field">
            <label>Typ</label>
            <select class="meta-select" [(ngModel)]="editState.jira_issue_type">
              <option value="epic">Epic</option>
              <option value="story">Story</option>
              <option value="task">Task</option>
              <option value="subtask">Subtask</option>
              <option value="bug">Bug</option>
            </select>
          </div>
          <div class="meta-field">
            <label>Priorität</label>
            <select class="meta-select" [(ngModel)]="editState.priority" [style.color]="priorityColor(editState.priority)">
              <option value="highest">⬆⬆ Highest</option>
              <option value="high">⬆ High</option>
              <option value="medium">➡ Medium</option>
              <option value="low">⬇ Low</option>
              <option value="lowest">⬇⬇ Lowest</option>
            </select>
          </div>
          <div class="meta-field">
            <label>Status</label>
            <select class="meta-select" [(ngModel)]="editState.status">
              <option value="pending">Offen</option>
              <option value="in_progress">In Arbeit</option>
              <option value="done">Erledigt</option>
            </select>
          </div>
        </div>

        <!-- Assignee + Story Points + Due Date -->
        <div class="card-meta-row">
          <div class="meta-field flex2">
            <label>Zugewiesen an</label>
            <input class="meta-input" [(ngModel)]="editState.assignee" placeholder="Name oder E-Mail" />
          </div>
          <div class="meta-field">
            <label>Story Points</label>
            <input class="meta-input" type="number" [(ngModel)]="editState.story_points" placeholder="–" min="1" />
          </div>
          <div class="meta-field">
            <label>Dauer (Tage)</label>
            <input class="meta-input" type="number" [(ngModel)]="editState.duration_days" min="1" />
          </div>
        </div>

        <div class="card-meta-row">
          <div class="meta-field flex2">
            <label>Fälligkeitsdatum</label>
            <input class="meta-input" type="date" [(ngModel)]="editState.due_date" />
          </div>
          <div class="meta-field flex3">
            <label>Labels</label>
            <div class="labels-row">
              @for (lbl of labelList; track lbl) {
                <span class="label-chip">
                  {{ lbl }}
                  <button class="label-remove" (click)="removeLabel(lbl)">×</button>
                </span>
              }
              <input class="label-input" [(ngModel)]="newLabel" (keydown.enter)="addLabel()"
                     placeholder="Label + Enter" />
            </div>
          </div>
        </div>

        <!-- Description -->
        <div class="card-section">
          <label class="section-label">Beschreibung</label>
          <textarea class="card-textarea" [(ngModel)]="editState.description" rows="4"
                    placeholder="Beschreibung (Markdown)"></textarea>
        </div>

        <!-- Acceptance Criteria -->
        <div class="card-section">
          <label class="section-label">Akzeptanzkriterien</label>
          <textarea class="card-textarea" [(ngModel)]="editState.acceptance_criteria" rows="4"
                    placeholder="Definition of Done (Markdown)"></textarea>
        </div>

        <!-- Implementation Notes: code blocks + bash commands from planner -->
        @if (implNotes().code_blocks.length || implNotes().bash_commands.length) {
          <div class="card-section impl-section">
            <div class="section-label-row">
              <label class="section-label">Implementierungshinweise</label>
              <button class="impl-edit-btn" (click)="editingImplNotes.set(!editingImplNotes())" [title]="editingImplNotes() ? 'Vorschau' : 'Bearbeiten'">
                <mat-icon>{{ editingImplNotes() ? 'visibility' : 'edit' }}</mat-icon>
              </button>
            </div>

            @if (editingImplNotes()) {
              <textarea class="card-textarea" rows="10" [(ngModel)]="editState.implementation_notes"
                        placeholder='{"code_blocks":[...],"bash_commands":[...]}'></textarea>
            } @else {
              @for (cb of implNotes().code_blocks; track $index) {
                <div class="impl-code-block">
                  <div class="impl-code-header">
                    <span class="impl-lang">{{ cb.lang }}</span>
                    @if (cb.filename) { <span class="impl-filename">{{ cb.filename }}</span> }
                    <button class="impl-copy-btn" (click)="copy(cb.content)"><mat-icon>content_copy</mat-icon></button>
                  </div>
                  <pre class="impl-pre"><code>{{ cb.content }}</code></pre>
                </div>
              }
              @for (cmd of implNotes().bash_commands; track $index) {
                <div class="impl-bash-block">
                  <div class="impl-bash-header">
                    <mat-icon>terminal</mat-icon>
                    <span>{{ cmd.purpose }}</span>
                    <button class="impl-copy-btn" (click)="copy(cmd.command)"><mat-icon>content_copy</mat-icon></button>
                  </div>
                  <pre class="impl-bash-cmd">{{ cmd.command }}</pre>
                </div>
              }
            }
          </div>
        }

        <!-- CPM info (read-only) -->
        @if (step.est_start != null) {
          <div class="card-section cpm-section">
            <label class="section-label">Kritischer Pfad</label>
            <div class="cpm-row">
              <span>Frühstart: <b>Tag {{ step.est_start }}</b></span>
              <span>Frühende: <b>Tag {{ step.est_end }}</b></span>
              <span>Puffer: <b [style.color]="step.critical ? '#FFCC99' : ''">{{ step.slack }}d</b></span>
              @if (step.critical) { <span class="critical-badge">Kritisch</span> }
            </div>
          </div>
        }

        <!-- Save button -->
        <div class="card-section">
          <button mat-raised-button class="save-btn" (click)="save()" [disabled]="saving()">
            <mat-icon>save</mat-icon> Speichern
          </button>
        </div>

        <!-- Jira section -->
        <div class="jira-section">
          <div class="jira-section-title">
            <mat-icon>link</mat-icon> Jira
          </div>
          @if (step.jira_key) {
            <div class="jira-row">
              <span class="jira-status-badge" [class]="'cat-' + (step.jira_status_category ?? 'new')">
                {{ step.jira_status ?? 'Unbekannt' }}
              </span>
              <a class="jira-key-link" [href]="jiraUrl()" target="_blank">
                {{ step.jira_key }} öffnen
              </a>
              <button mat-icon-button [matTooltip]="'Von Jira aktualisieren'" (click)="pullFromJira()" [disabled]="syncing()">
                <mat-icon [class.spin]="syncing()">sync</mat-icon>
              </button>
              @if (syncOk() === true) { <span class="sync-ok">✓ Jira aktualisiert</span> }
              @if (syncOk() === false) { <span class="sync-err">✗ Sync fehlgeschlagen</span> }
              <button mat-stroked-button (click)="unlinkTicket()">
                <mat-icon>link_off</mat-icon> Entkoppeln
              </button>
            </div>
            <div class="jira-push-hint">Speichern überträgt Titel, Beschreibung, Status und Priorität automatisch nach Jira.</div>
          } @else {
            <div class="jira-row">
              <button mat-stroked-button (click)="showAttach.set(!showAttach())">
                <mat-icon>link</mat-icon> Ticket verknüpfen
              </button>
              <button mat-stroked-button (click)="showCreate.set(!showCreate())">
                <mat-icon>add_task</mat-icon> Ticket erstellen
              </button>
            </div>
          }

          @if (showAttach()) {
            <div class="jira-form">
              <select class="meta-select" [(ngModel)]="jiraAction.connector">
                <option value="jira">Jira</option>
                <option value="jira_sd">Jira Service Desk</option>
              </select>
              <input class="meta-input" [(ngModel)]="jiraAction.key" placeholder="IMIT-1234" />
              <button mat-raised-button (click)="doAttach()">Verknüpfen</button>
            </div>
          }

          @if (showCreate()) {
            <div class="jira-form">
              <select class="meta-select" [(ngModel)]="jiraAction.connector">
                <option value="jira">Jira</option>
                <option value="jira_sd">Jira Service Desk</option>
              </select>
              <input class="meta-input" [(ngModel)]="createSummary" [placeholder]="editState.title" />
              <button mat-raised-button (click)="doCreate()">Erstellen</button>
            </div>
          }
        </div>
      }
      </div><!-- /card-inner -->
    </div>
  `,
  styles: [`
    /* Backdrop overlay — only visible when .open */
    .card-panel {
      display: none;
      position: fixed; inset: 0; z-index: 1200;
      background: rgba(0,0,0,0.65);
      align-items: center; justify-content: center;
    }
    .card-panel.open { display: flex; }

    /* The actual modal card */
    .card-inner {
      background: var(--cs-surface, #1a1a2e);
      border: 1px solid var(--cs-accent, #FFCC99);
      border-radius: 8px;
      width: 680px; max-width: 96vw;
      max-height: 90vh; overflow-y: auto;
      display: flex; flex-direction: column;
      box-shadow: 0 24px 64px rgba(0,0,0,0.7);
    }

    /* Header */
    .card-header {
      display: flex; align-items: center; gap: 8px; padding: 10px 12px;
      border-bottom: 1px solid var(--cs-border, #333); flex-shrink: 0;
      position: sticky; top: 0; background: var(--cs-surface, #1a1a2e); z-index: 10;
    }
    .issue-type-badge {
      font-size: 0.7rem; font-weight: 700; padding: 2px 8px; border-radius: 3px;
      color: #fff; letter-spacing: 0.06em;
    }
    .jira-key { font-size: 0.8rem; color: #4CACFF; text-decoration: none; }
    .jira-key:hover { text-decoration: underline; }
    .card-header-actions { margin-left: auto; display: flex; }

    /* Title */
    .card-title-input {
      width: 100%; box-sizing: border-box;
      background: transparent; border: none; border-bottom: 1px solid var(--cs-border, #444);
      color: var(--cs-text); font-size: 1.05rem; font-weight: 600;
      padding: 6px 0; outline: none;
    }
    .card-title-input:focus { border-bottom-color: var(--cs-accent, #FFCC99); }

    /* Sections */
    .card-section { padding: 10px 14px; display: flex; flex-direction: column; gap: 6px; }
    .section-label { font-size: 0.75rem; color: var(--cs-text-muted); text-transform: uppercase; letter-spacing: 0.06em; }

    /* Meta row */
    .card-meta-row {
      display: flex; gap: 8px; padding: 8px 14px;
      border-bottom: 1px solid var(--cs-border, #333);
    }
    .meta-field { display: flex; flex-direction: column; gap: 3px; flex: 1; min-width: 0; }
    .meta-field.flex2 { flex: 2; }
    .meta-field.flex3 { flex: 3; }
    .meta-field label { font-size: 0.7rem; color: var(--cs-text-muted); }
    .meta-select, .meta-input {
      background: var(--cs-bg); border: 1px solid var(--cs-border, #333); border-radius: 3px;
      color: var(--cs-text); font-size: 0.85rem; padding: 4px 6px; outline: none; width: 100%;
      box-sizing: border-box;
    }
    .meta-select:focus, .meta-input:focus { border-color: var(--cs-accent, #FFCC99); }

    /* Labels */
    .labels-row { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; min-height: 28px; }
    .label-chip {
      display: flex; align-items: center; gap: 3px;
      background: var(--cs-bg); border: 1px solid var(--cs-border, #444);
      border-radius: 12px; padding: 2px 8px; font-size: 0.75rem; color: var(--cs-text);
    }
    .label-remove { background: none; border: none; color: var(--cs-text-muted); cursor: pointer; padding: 0; font-size: 0.9rem; line-height: 1; }
    .label-remove:hover { color: #CC4444; }
    .label-input {
      background: transparent; border: none; border-bottom: 1px solid var(--cs-border, #444);
      color: var(--cs-text); font-size: 0.8rem; padding: 2px 4px; outline: none; width: 120px;
    }
    .label-input:focus { border-bottom-color: var(--cs-accent, #FFCC99); }

    /* Textarea */
    .card-textarea {
      background: var(--cs-bg); border: 1px solid var(--cs-border, #333); border-radius: 4px;
      color: var(--cs-text); font-size: 0.85rem; padding: 8px; outline: none; resize: vertical;
      font-family: monospace; width: 100%; box-sizing: border-box;
    }
    .card-textarea:focus { border-color: var(--cs-accent, #FFCC99); }

    /* CPM */
    .cpm-section { background: color-mix(in srgb, var(--cs-bg) 60%, transparent); border-radius: 4px; }
    .cpm-row { display: flex; gap: 12px; flex-wrap: wrap; font-size: 0.8rem; color: var(--cs-text-muted); }
    .critical-badge {
      background: #FFCC99; color: #000; font-size: 0.7rem; font-weight: 700;
      padding: 1px 6px; border-radius: 3px; letter-spacing: 0.04em;
    }

    /* Save button */
    .save-btn { width: 100%; }

    /* Jira section */
    .jira-section {
      padding: 12px 14px; border-top: 2px solid var(--cs-border, #333);
      display: flex; flex-direction: column; gap: 10px;
    }
    .jira-section-title {
      display: flex; align-items: center; gap: 6px;
      font-size: 0.8rem; color: var(--cs-text-muted); text-transform: uppercase; letter-spacing: 0.06em;
    }
    .jira-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .jira-key-link { font-size: 0.85rem; color: #4CACFF; text-decoration: none; }
    .jira-key-link:hover { text-decoration: underline; }
    .jira-status-badge {
      font-size: 0.75rem; padding: 2px 8px; border-radius: 3px; font-weight: 600;
    }
    .jira-status-badge.cat-done { background: #2D6A2D; color: #90EE90; }
    .jira-status-badge.cat-indeterminate { background: #5A4A00; color: #FFCC66; }
    .jira-status-badge.cat-new { background: var(--cs-bg); color: var(--cs-text-muted); }
    .jira-form { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
    .jira-push-hint { font-size: 0.72rem; color: var(--cs-text-muted); font-style: italic; }
    .sync-ok { font-size: 0.75rem; color: #90EE90; }
    .sync-err { font-size: 0.75rem; color: #CC4444; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .spin { animation: spin 1s linear infinite; display: inline-block; }

    /* Implementation Notes */
    .impl-section { gap: 10px; }
    .section-label-row { display: flex; align-items: center; justify-content: space-between; }
    .impl-edit-btn { background: none; border: none; cursor: pointer; color: var(--cs-text-muted); padding: 0; display: flex; align-items: center; }
    .impl-edit-btn mat-icon { font-size: 16px; width: 16px; height: 16px; }
    .impl-edit-btn:hover { color: var(--cs-accent, #FFCC99); }

    .impl-code-block { border-radius: 6px; overflow: hidden; border: 1px solid var(--cs-border, #333); }
    .impl-code-header {
      display: flex; align-items: center; gap: 8px; padding: 4px 10px;
      background: color-mix(in srgb, var(--cs-bg) 80%, transparent);
      font-size: 0.72rem; font-weight: 700; letter-spacing: .05em;
    }
    .impl-lang { text-transform: uppercase; color: var(--cs-text-muted); }
    .impl-filename { font-family: 'Fira Code', monospace; color: var(--cs-text-muted); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .impl-copy-btn { margin-left: auto; background: none; border: none; cursor: pointer; color: var(--cs-text-muted); padding: 2px 4px; border-radius: 3px; display: flex; align-items: center; }
    .impl-copy-btn:hover { color: var(--cs-accent, #FFCC99); }
    .impl-copy-btn mat-icon { font-size: 13px; width: 13px; height: 13px; }
    .impl-pre { margin: 0; padding: 10px 12px; font-family: 'Fira Code', monospace; font-size: 0.78rem; line-height: 1.6; overflow-x: auto; white-space: pre; background: var(--cs-bg); color: var(--cs-text); }

    .impl-bash-block { border-radius: 6px; overflow: hidden; border: 1px solid var(--cs-border, #333); }
    .impl-bash-header {
      display: flex; align-items: center; gap: 6px; padding: 4px 10px;
      background: #1a1a1a; color: #90EE90; font-size: 0.72rem; font-weight: 700;
    }
    .impl-bash-header mat-icon { font-size: 13px; width: 13px; height: 13px; }
    .impl-bash-cmd { margin: 0; padding: 8px 12px; font-family: 'Fira Code', monospace; font-size: 0.8rem; background: #111; color: #90EE90; overflow-x: auto; white-space: pre; }
  `],
})
export class StepCardComponent implements OnChanges {
  @Input() step: StepNode | null = null;
  @Input() projectId = '';
  @Output() close = new EventEmitter<void>();
  @Output() saved = new EventEmitter<void>();
  @Output() ticketChanged = new EventEmitter<void>();

  private svc = inject(ProjectsService);
  private snack = inject(MatSnackBar);

  saving = signal(false);
  syncing = signal(false);
  syncOk = signal<boolean | null>(null);
  showAttach = signal(false);
  showCreate = signal(false);
  editingImplNotes = signal(false);

  private _implNotesRaw = signal<string>('');
  implNotes = computed(() => {
    try {
      const parsed = JSON.parse(this._implNotesRaw());
      return {
        code_blocks: Array.isArray(parsed.code_blocks) ? parsed.code_blocks : [],
        bash_commands: Array.isArray(parsed.bash_commands) ? parsed.bash_commands : [],
      };
    } catch {
      return { code_blocks: [], bash_commands: [] };
    }
  });

  editState = this.emptyState();
  labelList: string[] = [];
  newLabel = '';
  jiraAction: JiraSection = { connector: 'jira', key: '' };
  createSummary = '';

  ngOnChanges(changes: SimpleChanges) {
    if (changes['step'] && this.step) {
      const implNotes = this.step.implementation_notes ?? '';
      this.editState = {
        title: this.step.title,
        description: this.step.description ?? '',
        status: this.step.status,
        jira_issue_type: this.step.jira_issue_type,
        priority: this.step.priority ?? 'medium',
        duration_days: this.step.duration_days,
        story_points: this.step.story_points ?? null,
        assignee: this.step.assignee ?? '',
        due_date: this.step.due_date ?? '',
        acceptance_criteria: this.step.acceptance_criteria ?? '',
        implementation_notes: implNotes,
      };
      this._implNotesRaw.set(implNotes);
      try {
        this.labelList = this.step.labels ? JSON.parse(this.step.labels) : [];
      } catch {
        this.labelList = [];
      }
      this.showAttach.set(false);
      this.showCreate.set(false);
      this.editingImplNotes.set(false);
      this.createSummary = this.step.title;
    }
  }

  private emptyState() {
    return {
      title: '', description: '', status: 'pending', jira_issue_type: 'task',
      priority: 'medium', duration_days: 1, story_points: null as number | null,
      assignee: '', due_date: '', acceptance_criteria: '', implementation_notes: '',
    };
  }

  addLabel() {
    const lbl = this.newLabel.trim();
    if (lbl && !this.labelList.includes(lbl)) {
      this.labelList = [...this.labelList, lbl];
    }
    this.newLabel = '';
  }

  removeLabel(lbl: string) {
    this.labelList = this.labelList.filter(l => l !== lbl);
  }

  issueTypeColor(type: string): string {
    const colors: Record<string, string> = {
      epic: '#9B59B6', story: '#2ECC71', task: '#3498DB', subtask: '#1ABC9C', bug: '#E74C3C',
    };
    return colors[type] ?? '#444';
  }

  priorityColor(p: string): string { return PRIORITY_COLORS[p] ?? '#FFCC99'; }

  jiraUrl(): string {
    if (!this.step?.jira_key) return '#';
    return `https://servicedesk.example.com/browse/${this.step.jira_key}`;
  }

  save() {
    if (!this.step) return;
    this.saving.set(true);
    const payload: any = {
      title: this.editState.title,
      description: this.editState.description || null,
      status: this.editState.status,
      jira_issue_type: this.editState.jira_issue_type,
      priority: this.editState.priority,
      duration_days: Number(this.editState.duration_days) || 1,
      story_points: this.editState.story_points ? Number(this.editState.story_points) : null,
      assignee: this.editState.assignee || null,
      labels: this.labelList,
      due_date: this.editState.due_date || null,
      acceptance_criteria: this.editState.acceptance_criteria || null,
      implementation_notes: this.editState.implementation_notes || null,
    };
    this._implNotesRaw.set(this.editState.implementation_notes ?? '');
    this.svc.updateStep(this.projectId, this.step.id, payload).subscribe({
      next: () => {
        this.saving.set(false);
        this.saved.emit();
        this.snack.open('Gespeichert', undefined, { duration: 1500 });
      },
      error: () => {
        this.saving.set(false);
        this.snack.open('Fehler beim Speichern', 'OK', { duration: 3000 });
      },
    });
  }

  doAttach() {
    if (!this.step || !this.jiraAction.key.trim()) return;
    this.svc.attachTicket(this.projectId, this.step.id, this.jiraAction.connector, this.jiraAction.key.trim())
      .subscribe({
        next: () => { this.showAttach.set(false); this.jiraAction.key = ''; this.ticketChanged.emit(); },
        error: () => this.snack.open('Ticket nicht gefunden', 'OK', { duration: 3000 }),
      });
  }

  doCreate() {
    if (!this.step) return;
    this.svc.createTicket(this.projectId, this.step.id, {
      connector_type: this.jiraAction.connector,
      summary: this.createSummary || this.editState.title,
      description: this.editState.description,
      issue_type: this.editState.jira_issue_type.charAt(0).toUpperCase() + this.editState.jira_issue_type.slice(1),
    }).subscribe({
      next: () => { this.showCreate.set(false); this.ticketChanged.emit(); },
      error: () => this.snack.open('Fehler beim Erstellen', 'OK', { duration: 3000 }),
    });
  }

  copy(text: string) { navigator.clipboard.writeText(text).catch(() => {}); }

  onBackdropClick(e: MouseEvent) {
    this.close.emit();
  }

  pullFromJira() {
    if (!this.step?.jira_key) return;
    this.syncing.set(true);
    this.svc.pullStepFromJira(this.projectId, this.step.id).subscribe({
      next: () => { this.syncing.set(false); this.syncOk.set(true); this.saved.emit(); setTimeout(() => this.syncOk.set(null), 3000); },
      error: () => { this.syncing.set(false); this.syncOk.set(false); setTimeout(() => this.syncOk.set(null), 3000); },
    });
  }

  unlinkTicket() {
    if (!this.step) return;
    this.svc.updateStep(this.projectId, this.step.id, {
      jira_connector_type: null, jira_key: null, jira_issue_id: null,
    } as any).subscribe({ next: () => this.ticketChanged.emit() });
  }
}
