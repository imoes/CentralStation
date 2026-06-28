import { Component, OnInit, OnDestroy, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import {
  CdkDragDrop, DragDropModule, moveItemInArray, transferArrayItem,
} from '@angular/cdk/drag-drop';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatChipsModule } from '@angular/material/chips';
import { MatDialogModule, MatDialog } from '@angular/material/dialog';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatBadgeModule } from '@angular/material/badge';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { Subject, takeUntil } from 'rxjs';
import { RouterModule } from '@angular/router';
import { KanbanService } from '../../core/services/kanban.service';
import { ProjectsService, ReadyStep } from '../../core/services/projects.service';
import { WebsocketService, WsMessage } from '../../core/services/websocket.service';
import { KanbanCard, KanbanColumn, KanbanStatus } from '../../core/models/kanban.model';
import { KanbanCardDialogComponent } from './kanban-card-dialog.component';
import { I18nService } from '../../core/services/i18n.service';

const COLUMNS: { id: KanbanStatus; label: string; color: string }[] = [
  { id: 'backlog',     label: 'Backlog',     color: '#607d8b' },
  { id: 'todo',        label: 'To Do',       color: '#1976d2' },
  { id: 'in_progress', label: 'In Arbeit',   color: '#f57c00' },
  { id: 'review',      label: 'Review',      color: '#7b1fa2' },
  { id: 'done',        label: 'Erledigt',    color: '#388e3c' },
];

// LCARS-authentic palette — same colors used in Bridge + Dashboard widgets
const PRIORITY_COLORS: Record<string, string> = {
  critical: '#CC4444',   // LCARS red
  high:     '#FF9933',   // Neon Carrot — primary LCARS orange
  medium:   '#FFCC66',   // Golden Tanoi
  low:      '#99CCFF',   // Anakiwa blue
};

@Component({
  selector: 'cs-kanban',
  standalone: true,
  imports: [
    CommonModule, FormsModule, DragDropModule,
    MatCardModule, MatButtonModule, MatIconModule,
    MatChipsModule, MatDialogModule, MatProgressSpinnerModule,
    MatTooltipModule, MatBadgeModule, MatSnackBarModule,
    RouterModule,
  ],
  template: `
    <div class="board-container">
      <div class="board-header">
        <h2>Kanban Board</h2>
        <button mat-raised-button color="primary" (click)="openCreate()">
          <mat-icon>add</mat-icon> {{ i18n.t('kanban.new_card') }}
        </button>
      </div>

      @if (loading()) {
        <div class="spinner-center"><mat-spinner diameter="40"></mat-spinner></div>
      } @else {
        <div class="board">
          @for (col of columns; track col.id) {
            <div class="column">
              <div class="column-header" [style.border-top-color]="col.color">
                <span class="col-title">{{ col.label }}</span>
                <span class="col-count">{{ getColumn(col.id).length }}</span>
              </div>
              <!-- Ready project steps (only in Todo column) -->
              @if (col.id === 'todo' && readySteps().length > 0) {
                <div class="ready-steps-section">
                  <div class="ready-steps-header">
                    <mat-icon style="font-size:12px;width:12px;height:12px">folder_open</mat-icon>
                    Projektaufgaben
                  </div>
                  @for (step of readySteps(); track step.step_id) {
                    <div class="ready-step-card" [attr.data-type]="step.jira_issue_type">
                      <div class="ready-step-header">
                        <span class="ready-step-type">{{ step.jira_issue_type.toUpperCase() }}</span>
                        @if (step.jira_key) {
                          <span class="ready-step-jira">{{ step.jira_key }}</span>
                        }
                        <a class="ready-step-project" [routerLink]="['/projects', step.project_id]">{{ step.project_name }}</a>
                      </div>
                      <div class="ready-step-title">{{ step.title }}</div>
                      <button class="ready-step-done" (click)="markStepDone(step)">
                        <mat-icon style="font-size:13px;width:13px;height:13px">check</mat-icon> Erledigt
                      </button>
                    </div>
                  }
                </div>
              }
              <div
                cdkDropList
                [id]="col.id"
                [cdkDropListData]="getColumn(col.id)"
                [cdkDropListConnectedTo]="columnIds"
                (cdkDropListDropped)="onDrop($event)"
                class="column-drop-zone">
                @for (card of getColumn(col.id); track card.id) {
                  <div cdkDrag [cdkDragData]="card" class="kanban-card" [class.ai-card]="card.ai_generated"
                       [attr.data-priority]="card.priority"
                       [style.--card-color]="priorityColor(card.priority)"
                       (click)="openEdit(card)">
                    <!-- LCARS header bar: priority + jira key -->
                    <div class="card-header-bar">
                      <span class="card-priority-label">{{ card.priority | uppercase }}</span>
                      @if (card.jira_key) {
                        <span class="card-jira-key">{{ card.jira_key }}</span>
                      }
                      @if (card.ai_generated) {
                        <mat-icon class="ai-icon" [matTooltip]="i18n.t('kanban.ai_generated')">smart_toy</mat-icon>
                      }
                      <div class="card-actions">
                        <button mat-icon-button (click)="$event.stopPropagation(); openEdit(card)">
                          <mat-icon>edit</mat-icon>
                        </button>
                        <button mat-icon-button color="warn" (click)="$event.stopPropagation(); deleteCard(card)">
                          <mat-icon>delete</mat-icon>
                        </button>
                      </div>
                    </div>
                    <!-- Card body -->
                    <div class="card-body">
                      <div class="card-title">{{ card.title }}</div>
                      @if (card.description) {
                        <div class="card-desc">{{ card.description | slice:0:80 }}{{ card.description!.length > 80 ? '…' : '' }}</div>
                      }
                    </div>
                  </div>
                }
                @if (getColumn(col.id).length === 0) {
                  <div class="empty-column">Keine Karten</div>
                }
              </div>
            </div>
          }
        </div>
      }
    </div>
  `,
  styles: [`
    .board-container { padding: 16px; height: calc(100vh - 64px); overflow: hidden; display: flex; flex-direction: column; }
    .board-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; flex-shrink: 0; }
    .board-header h2 { margin: 0; }
    .board { display: flex; gap: 12px; flex: 1; overflow-x: auto; align-items: flex-start; }
    .column { width: 240px; min-width: 240px; display: flex; flex-direction: column; }
    /* Classic column header */
    .column-header {
      display: flex; align-items: center; justify-content: space-between;
      padding: 8px 12px;
      background: var(--mat-sys-surface-container);
      border-top: 3px solid;
      border-radius: 4px 4px 0 0;
    }
    .col-title { font-weight: 600; font-size: 13px; }
    .col-count { background: var(--mat-sys-surface-variant); color: var(--mat-sys-on-surface-variant); border-radius: 10px; padding: 1px 7px; font-size: 11px; font-weight: 700; }
    .column-drop-zone { min-height: 200px; padding: 8px; background: var(--mat-sys-surface-container-low); border-radius: 0 0 4px 4px; flex: 1; }

    /* LCARS column header */
    :host-context(html.cs-theme-lcars) .column-header {
      background: #FF9933; color: #000;
      border-top: none; border-radius: 0 6px 0 0;
      font-family: 'Antonio', 'Eurostile', sans-serif;
    }
    :host-context(html.cs-theme-lcars) .col-title { font-weight: 900; font-size: 12px; text-transform: uppercase; letter-spacing: .1em; }
    :host-context(html.cs-theme-lcars) .col-count { background: #000; color: #FF9933; font-weight: 900; }
    :host-context(html.cs-theme-lcars) .column-drop-zone { background: #0a0804; }
    /* ── Classic/default Kanban card ── */
    .kanban-card {
      display: flex; flex-direction: column;
      background: var(--mat-sys-surface);
      border: none;
      border-left: 4px solid var(--card-color, var(--mat-sys-primary));
      border-radius: 4px;
      margin-bottom: 8px; cursor: pointer; overflow: hidden;
      box-shadow: 0 1px 3px rgba(0,0,0,.2);
      transition: box-shadow .2s, transform .2s;
      color: var(--mat-sys-on-surface);
    }
    .kanban-card:hover { box-shadow: 0 4px 8px rgba(0,0,0,.3); transform: translateY(-1px); }
    .kanban-card.ai-card { outline: 1px solid var(--mat-sys-primary); }

    /* Classic header bar */
    .card-header-bar {
      background: color-mix(in srgb, var(--card-color, var(--mat-sys-primary)) 18%, var(--mat-sys-surface-container));
      color: var(--mat-sys-on-surface);
      display: flex; align-items: center; gap: 6px;
      padding: 5px 10px; min-height: 32px; flex-shrink: 0;
    }
    .card-priority-label { font-size: 10px; font-weight: 700; letter-spacing: .04em; flex-shrink: 0; }
    .card-jira-key {
      font-size: 10px; font-weight: 700; font-family: 'Fira Code', monospace;
      background: rgba(0,82,204,.15); color: #0052cc;
      padding: 1px 5px; border-radius: 3px; flex-shrink: 0;
    }
    .ai-icon { font-size: 13px; width: 13px; height: 13px; flex-shrink: 0; }
    .card-actions { display: flex; margin-left: auto; }
    .card-actions button { width: 24px; height: 24px; line-height: 24px; }
    .card-actions mat-icon { font-size: 14px; width: 14px; height: 14px; }
    .card-body { padding: 8px 10px; flex: 1; min-width: 0; }
    .card-title { font-size: 13px; font-weight: 600; margin-bottom: 4px; word-break: break-word; line-height: 1.4; }
    .card-desc { font-size: 11px; color: var(--mat-sys-on-surface-variant); margin-bottom: 2px; }

    /* ── LCARS overrides ── */
    :host-context(html.cs-theme-lcars) .kanban-card {
      background: #000;
      border-left: 18px solid var(--card-color, #FF9933);
      border-radius: 18px 6px 6px 18px;
      box-shadow: none;
      color: #ffe8a0;
    }
    :host-context(html.cs-theme-lcars) .kanban-card:hover { filter: brightness(1.1); transform: none; box-shadow: none; }
    :host-context(html.cs-theme-lcars) .kanban-card.ai-card { outline: 1px solid #FFCC66; }
    :host-context(html.cs-theme-lcars) .card-header-bar {
      background: var(--card-color, #FF9933);
      color: #000;
      font-family: 'Antonio', 'Eurostile', sans-serif;
    }
    :host-context(html.cs-theme-lcars) .card-priority-label { font-weight: 900; letter-spacing: .08em; }
    :host-context(html.cs-theme-lcars) .card-jira-key { background: rgba(0,0,0,.22); color: #000; }
    :host-context(html.cs-theme-lcars) .card-title { color: #ffe8a0; }
    :host-context(html.cs-theme-lcars) .card-desc { color: #e8a060; }
    .empty-column { text-align: center; padding: 16px; color: var(--mat-sys-on-surface-variant); font-size: 12px; }
    .spinner-center { display: flex; justify-content: center; padding: 40px; }

    /* Ready Project Steps */
    .ready-steps-section { margin-bottom: 8px; }
    .ready-steps-header {
      display: flex; align-items: center; gap: 4px;
      font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em;
      color: var(--mat-sys-on-surface-variant); padding: 4px 8px 6px;
    }
    .ready-step-card {
      background: var(--mat-sys-surface); border-left: 4px solid #1976d2;
      border-radius: 4px; padding: 8px 10px; margin-bottom: 6px;
      font-size: 12px;
    }
    .ready-step-header { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; flex-wrap: wrap; }
    .ready-step-type {
      font-size: 10px; font-weight: 700; background: #1976d233; color: #1976d2;
      padding: 1px 5px; border-radius: 3px;
    }
    .ready-step-jira {
      font-size: 10px; font-weight: 700; font-family: 'Fira Code', monospace;
      background: rgba(0,82,204,.15); color: #0052cc; padding: 1px 5px; border-radius: 3px;
    }
    .ready-step-project {
      font-size: 10px; color: var(--mat-sys-on-surface-variant); text-decoration: none; margin-left: auto;
    }
    .ready-step-project:hover { text-decoration: underline; }
    .ready-step-title { font-weight: 600; font-size: 12px; margin-bottom: 6px; line-height: 1.3; }
    .ready-step-done {
      display: flex; align-items: center; gap: 4px;
      background: #388e3c22; border: 1px solid #388e3c; color: #388e3c;
      border-radius: 3px; padding: 2px 8px; font-size: 11px; cursor: pointer;
      transition: background .15s;
    }
    .ready-step-done:hover { background: #388e3c44; }
    :host-context(html.cs-theme-lcars) .ready-step-card { background: #0a0804; border-left-color: #FFCC99; }
    :host-context(html.cs-theme-lcars) .ready-step-type { background: #FFCC9933; color: #FFCC99; }
    :host-context(html.cs-theme-lcars) .ready-step-title { color: #ffe8a0; }
    .cdk-drag-preview { box-shadow: 0 8px 16px rgba(0,0,0,.4); }
    .cdk-drag-placeholder { opacity: 0.3; }
    .cdk-drag-animating { transition: transform 200ms; }
    .cdk-drop-list-dragging .kanban-card:not(.cdk-drag-placeholder) { transition: transform 200ms; }
  `],
})
export class KanbanComponent implements OnInit, OnDestroy {
  readonly i18n = inject(I18nService);
  private projectsSvc = inject(ProjectsService);

  columns = COLUMNS;
  columnIds = COLUMNS.map(c => c.id);
  loading = signal(true);
  readySteps = signal<ReadyStep[]>([]);

  private cards = signal<KanbanCard[]>([]);
  private destroy$ = new Subject<void>();

  constructor(
    private svc: KanbanService,
    private ws: WebsocketService,
    private dialog: MatDialog,
    private snack: MatSnackBar,
  ) {}

  ngOnInit() {
    this.load();
    this.loadReadySteps();
    this.ws.messages().pipe(takeUntil(this.destroy$)).subscribe((msg: WsMessage) => {
      if (msg.type === 'kanban_update' || msg.type === 'kanban_move') {
        this.load();
      }
      if (msg.type === 'project_updated') {
        this.loadReadySteps();
      }
    });
  }

  ngOnDestroy() { this.destroy$.next(); this.destroy$.complete(); }

  loadReadySteps() {
    this.projectsSvc.getReadySteps().subscribe({
      next: steps => this.readySteps.set(steps),
      error: () => {},
    });
  }

  markStepDone(step: ReadyStep) {
    this.projectsSvc.updateStep(step.project_id, step.step_id, { status: 'done' } as any).subscribe({
      next: () => {
        this.snack.open(`"${step.title}" als erledigt markiert`, 'OK', { duration: 2000 });
        this.loadReadySteps();
      },
      error: () => this.snack.open('Fehler beim Aktualisieren', 'OK', { duration: 2000 }),
    });
  }

  load() {
    this.svc.list().subscribe({
      next: cards => { this.cards.set(cards); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }

  getColumn(status: KanbanStatus): KanbanCard[] {
    return this.cards().filter(c => c.status === status)
      .sort((a, b) => a.position - b.position);
  }

  onDrop(event: CdkDragDrop<KanbanCard[]>) {
    const card: KanbanCard = event.item.data;
    if (!card) return;
    const newStatus = event.container.id as KanbanStatus;
    const newPosition = event.currentIndex;
    const oldStatus = card.status;

    // Optimistic update so the card stays in the new column immediately
    // without snapping back while the API call is in flight.
    this.cards.update(list =>
      list.map(c => c.id === card.id ? { ...c, status: newStatus, position: newPosition } : c)
    );

    this.svc.move(card.id, { status: newStatus, position: newPosition }).subscribe({
      next: updated => {
        this.cards.update(list => list.map(c => c.id === updated.id ? updated : c));
      },
      error: (err) => {
        // Revert on failure
        this.cards.update(list =>
          list.map(c => c.id === card.id ? { ...c, status: oldStatus, position: card.position } : c)
        );
        this.snack.open(err?.error?.detail ?? 'Jira-Sync beim Verschieben fehlgeschlagen', 'OK', { duration: 4000 });
      },
    });
  }

  priorityColor(priority: string): string {
    return PRIORITY_COLORS[priority] ?? '#607d8b';
  }

  openCreate() {
    const ref = this.dialog.open(KanbanCardDialogComponent, {
      width: '680px', maxWidth: '95vw',
    });
    ref.afterClosed().subscribe(result => { if (result) this.load(); });
  }

  openEdit(card: KanbanCard) {
    const ref = this.dialog.open(KanbanCardDialogComponent, {
      width: '720px', maxWidth: '95vw',
      data: { card },
    });
    ref.afterClosed().subscribe(result => { if (result) this.load(); });
  }

  deleteCard(card: KanbanCard) {
    if (!confirm(`Karte "${card.title}" löschen?`)) return;
    this.svc.delete(card.id).subscribe({ next: () => this.load() });
  }
}
