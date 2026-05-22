import { Component, OnInit, OnDestroy, signal } from '@angular/core';
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
import { KanbanService } from '../../core/services/kanban.service';
import { WebsocketService, WsMessage } from '../../core/services/websocket.service';
import { KanbanCard, KanbanColumn, KanbanStatus } from '../../core/models/kanban.model';
import { KanbanCardDialogComponent } from './kanban-card-dialog.component';

const COLUMNS: { id: KanbanStatus; label: string; color: string }[] = [
  { id: 'backlog',     label: 'Backlog',     color: '#607d8b' },
  { id: 'todo',        label: 'To Do',       color: '#1976d2' },
  { id: 'in_progress', label: 'In Arbeit',   color: '#f57c00' },
  { id: 'review',      label: 'Review',      color: '#7b1fa2' },
  { id: 'done',        label: 'Erledigt',    color: '#388e3c' },
];

const PRIORITY_COLORS: Record<string, string> = {
  critical: '#d32f2f',
  high:     '#f57c00',
  medium:   '#1976d2',
  low:      '#388e3c',
};

@Component({
  selector: 'cs-kanban',
  standalone: true,
  imports: [
    CommonModule, FormsModule, DragDropModule,
    MatCardModule, MatButtonModule, MatIconModule,
    MatChipsModule, MatDialogModule, MatProgressSpinnerModule,
    MatTooltipModule, MatBadgeModule, MatSnackBarModule,
  ],
  template: `
    <div class="board-container">
      <div class="board-header">
        <h2>Kanban Board</h2>
        <button mat-raised-button color="primary" (click)="openCreate()">
          <mat-icon>add</mat-icon> Neue Karte
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
              <div
                cdkDropList
                [id]="col.id"
                [cdkDropListData]="getColumn(col.id)"
                [cdkDropListConnectedTo]="columnIds"
                (cdkDropListDropped)="onDrop($event)"
                class="column-drop-zone">
                @for (card of getColumn(col.id); track card.id) {
                  <div cdkDrag class="kanban-card" [class.ai-card]="card.ai_generated">
                    <div class="card-priority-bar"
                         [style.background-color]="priorityColor(card.priority)">
                    </div>
                    <div class="card-body">
                      <div class="card-title">{{ card.title }}</div>
                      @if (card.description) {
                        <div class="card-desc">{{ card.description | slice:0:80 }}{{ card.description!.length > 80 ? '…' : '' }}</div>
                      }
                      <div class="card-footer">
                        @if (card.jira_key) {
                          <mat-chip class="jira-chip">{{ card.jira_key }}</mat-chip>
                        }
                        @if (card.ai_generated) {
                          <mat-icon class="ai-icon" matTooltip="KI-generiert">smart_toy</mat-icon>
                        }
                        <span class="priority-label" [style.color]="priorityColor(card.priority)">
                          {{ card.priority }}
                        </span>
                        <div class="card-actions">
                          <button mat-icon-button (click)="openEdit(card)">
                            <mat-icon>edit</mat-icon>
                          </button>
                          <button mat-icon-button color="warn" (click)="deleteCard(card)">
                            <mat-icon>delete</mat-icon>
                          </button>
                        </div>
                      </div>
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
    .column-header { display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; background: var(--mat-sys-surface-container); border-top: 3px solid; border-radius: 4px 4px 0 0; }
    .col-title { font-weight: 600; font-size: 13px; }
    .col-count { background: var(--mat-sys-surface-variant); border-radius: 10px; padding: 1px 7px; font-size: 11px; }
    .column-drop-zone { min-height: 200px; padding: 8px; background: var(--mat-sys-surface-container-low); border-radius: 0 0 4px 4px; flex: 1; }
    .kanban-card { display: flex; background: var(--mat-sys-surface); border-radius: 4px; margin-bottom: 8px; cursor: grab; box-shadow: 0 1px 3px rgba(0,0,0,.2); transition: box-shadow .2s; }
    .kanban-card:hover { box-shadow: 0 4px 8px rgba(0,0,0,.3); }
    .kanban-card.ai-card { border: 1px solid var(--mat-sys-primary); }
    .card-priority-bar { width: 4px; border-radius: 4px 0 0 4px; flex-shrink: 0; }
    .card-body { padding: 8px 10px; flex: 1; min-width: 0; }
    .card-title { font-size: 13px; font-weight: 500; margin-bottom: 4px; word-break: break-word; }
    .card-desc { font-size: 11px; color: var(--mat-sys-on-surface-variant); margin-bottom: 6px; }
    .card-footer { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
    .jira-chip { font-size: 10px; min-height: 18px; background: #0052cc20; color: #0052cc; }
    .ai-icon { font-size: 14px; width: 14px; height: 14px; color: var(--mat-sys-primary); }
    .priority-label { font-size: 10px; text-transform: uppercase; font-weight: 600; margin-left: auto; }
    .card-actions { display: flex; margin-left: auto; }
    .card-actions button { width: 24px; height: 24px; line-height: 24px; }
    .card-actions mat-icon { font-size: 14px; width: 14px; height: 14px; }
    .empty-column { text-align: center; padding: 16px; color: var(--mat-sys-on-surface-variant); font-size: 12px; }
    .spinner-center { display: flex; justify-content: center; padding: 40px; }
    .cdk-drag-preview { box-shadow: 0 8px 16px rgba(0,0,0,.4); }
    .cdk-drag-placeholder { opacity: 0.3; }
    .cdk-drag-animating { transition: transform 200ms; }
    .cdk-drop-list-dragging .kanban-card:not(.cdk-drag-placeholder) { transition: transform 200ms; }
  `],
})
export class KanbanComponent implements OnInit, OnDestroy {
  columns = COLUMNS;
  columnIds = COLUMNS.map(c => c.id);
  loading = signal(true);

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
    this.ws.messages().pipe(takeUntil(this.destroy$)).subscribe((msg: WsMessage) => {
      if (msg.type === 'kanban_update' || msg.type === 'kanban_move') {
        this.load();
      }
    });
  }

  ngOnDestroy() { this.destroy$.next(); this.destroy$.complete(); }

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
    const newStatus = event.container.id as KanbanStatus;
    const newPosition = event.currentIndex;

    if (event.previousContainer === event.container) {
      this.svc.move(card.id, { status: newStatus, position: newPosition }).subscribe({
        error: (err) => {
          this.snack.open(err?.error?.detail ?? 'Jira-Sync beim Verschieben fehlgeschlagen', 'OK', { duration: 4000 });
          this.load();
        },
      });
    } else {
      this.svc.move(card.id, { status: newStatus, position: newPosition }).subscribe({
        next: updated => {
          this.cards.update(list => list.map(c => c.id === updated.id ? updated : c));
        },
        error: (err) => {
          this.snack.open(err?.error?.detail ?? 'Jira-Sync beim Verschieben fehlgeschlagen', 'OK', { duration: 4000 });
          this.load();
        },
      });
    }
  }

  priorityColor(priority: string): string {
    return PRIORITY_COLORS[priority] ?? '#607d8b';
  }

  openCreate() {
    const ref = this.dialog.open(KanbanCardDialogComponent, { width: '500px' });
    ref.afterClosed().subscribe(result => { if (result) this.load(); });
  }

  openEdit(card: KanbanCard) {
    const ref = this.dialog.open(KanbanCardDialogComponent, {
      width: '500px',
      data: { card },
    });
    ref.afterClosed().subscribe(result => { if (result) this.load(); });
  }

  deleteCard(card: KanbanCard) {
    if (!confirm(`Karte "${card.title}" löschen?`)) return;
    this.svc.delete(card.id).subscribe({ next: () => this.load() });
  }
}
