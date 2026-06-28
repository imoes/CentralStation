import {
  Component, OnInit, OnDestroy, AfterViewInit,
  ElementRef, ViewChild, inject, signal, ChangeDetectorRef,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatTabsModule } from '@angular/material/tabs';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSelectModule } from '@angular/material/select';
import { Subject, takeUntil } from 'rxjs';
import { ProjectsService, PlanGraph, StepNode, DepEdge } from '../../core/services/projects.service';
import { WebsocketService, WsMessage } from '../../core/services/websocket.service';
import { I18nService } from '../../core/services/i18n.service';

// Cytoscape type-only import for type safety
declare const require: (m: string) => any;

interface ContextMenu { x: number; y: number; stepId: string; step: StepNode; }
interface GanttRow { step: StepNode; barLeft: number; barWidth: number; }

const ISSUE_TYPE_COLORS: Record<string, string> = {
  epic: '#9B59B6', story: '#2ECC71', task: '#3498DB', subtask: '#1ABC9C', bug: '#E74C3C',
};
const STATUS_COLORS: Record<string, string> = {
  done: '#90EE90', in_progress: '#FFCC66', pending: '#606060',
};

@Component({
  selector: 'cs-project-detail',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatButtonModule, MatIconModule, MatTabsModule,
    MatProgressSpinnerModule, MatSnackBarModule, MatTooltipModule, MatSelectModule,
  ],
  template: `
    <div class="detail-container">
      <div class="detail-header lcars-header">
        <div class="header-elbow"></div>
        <div class="header-info">
          <button mat-icon-button (click)="back()"><mat-icon>arrow_back</mat-icon></button>
          <h2 class="project-name">{{ graph()?.project?.name ?? '…' }}</h2>
          <span class="status-chip" [style.color]="statusColor(graph()?.project?.status ?? '')">
            {{ i18n.t('projects.status.' + (graph()?.project?.status ?? 'planning')) }}
          </span>
        </div>
        <div class="header-actions">
          <button mat-button (click)="syncJira()" [disabled]="syncing()">
            <mat-icon>sync</mat-icon> Jira Sync
          </button>
          <button mat-raised-button (click)="openWorkbench()">
            <mat-icon>code</mat-icon> {{ i18n.t('projects.open_workbench') }}
          </button>
          <button mat-raised-button (click)="addStep()">
            <mat-icon>add</mat-icon> {{ i18n.t('projects.add_step') }}
          </button>
        </div>
      </div>

      @if (loading()) {
        <div class="spinner-center"><mat-spinner diameter="48"></mat-spinner></div>
      } @else {
        <mat-tab-group class="detail-tabs" (selectedTabChange)="onTabChange($event.index)">

          <!-- Tab: Netzplan (Cytoscape) -->
          <mat-tab label="{{ i18n.t('projects.tab.network') }}">
            <div class="cyto-container" #cytoEl></div>
          </mat-tab>

          <!-- Tab: Gantt -->
          <mat-tab label="{{ i18n.t('projects.tab.gantt') }}">
            <div class="gantt-container">
              @if (ganttRows().length === 0) {
                <div class="empty">{{ i18n.t('projects.no_steps') }}</div>
              } @else {
                <div class="gantt-header">
                  <div class="gantt-label-col">{{ i18n.t('projects.step') }}</div>
                  <div class="gantt-timeline">
                    @for (d of ganttDays(); track d) {
                      <div class="gantt-day">{{ d }}</div>
                    }
                  </div>
                </div>
                @for (row of ganttRows(); track row.step.id) {
                  <div class="gantt-row" (click)="selectStep(row.step)">
                    <div class="gantt-label" [title]="row.step.title">{{ row.step.title }}</div>
                    <div class="gantt-track">
                      <div
                        class="gantt-bar"
                        [style.left.%]="row.barLeft"
                        [style.width.%]="row.barWidth"
                        [style.background]="row.step.critical ? '#FFCC99' : statusBgColor(row.step.status)"
                        [matTooltip]="row.step.title + ' · ' + row.step.duration_days + 'd'"
                      ></div>
                    </div>
                  </div>
                }
              }
            </div>
          </mat-tab>

          <!-- Tab: Liste -->
          <mat-tab label="{{ i18n.t('projects.tab.list') }}">
            <div class="list-container">
              @if (graph()?.steps?.length === 0) {
                <div class="empty">{{ i18n.t('projects.no_steps') }}</div>
              } @else {
                <div class="step-list">
                  @for (s of graph()!.steps; track s.id) {
                    <div class="step-list-row" [class.critical]="s.critical">
                      <div class="step-type-dot" [style.background]="issueTypeColor(s.jira_issue_type)"
                           [matTooltip]="s.jira_issue_type">
                      </div>
                      <div class="step-main">
                        <div class="step-title">{{ s.title }}</div>
                        @if (s.description) {
                          <div class="step-desc">{{ s.description }}</div>
                        }
                        @if (s.jira_key) {
                          <span class="jira-badge">{{ s.jira_key }} · {{ s.jira_status }}</span>
                        }
                      </div>
                      <div class="step-meta">
                        <span class="step-status" [style.color]="statusColor(s.status)">{{ s.status }}</span>
                        <span class="step-dur">{{ s.duration_days }}d</span>
                        @if (s.slack != null) {
                          <span class="step-slack" [class.critical]="s.critical">
                            slack {{ s.slack }}d
                          </span>
                        }
                      </div>
                      <div class="step-actions">
                        <mat-select [(ngModel)]="stepStatusMap[s.id]" (ngModelChange)="setStatus(s, $event)" class="status-select">
                          <mat-option value="pending">{{ i18n.t('projects.step_status.pending') }}</mat-option>
                          <mat-option value="in_progress">{{ i18n.t('projects.step_status.in_progress') }}</mat-option>
                          <mat-option value="done">{{ i18n.t('projects.step_status.done') }}</mat-option>
                        </mat-select>
                        <button mat-icon-button [matTooltip]="'Ticket verknüpfen'" (click)="openAttachTicket(s)">
                          <mat-icon>link</mat-icon>
                        </button>
                        <button mat-icon-button [matTooltip]="'Ticket erstellen'" (click)="openCreateTicket(s)">
                          <mat-icon>add_task</mat-icon>
                        </button>
                        <button mat-icon-button [matTooltip]="'Löschen'" (click)="deleteStep(s)">
                          <mat-icon>delete</mat-icon>
                        </button>
                      </div>
                    </div>
                  }
                </div>
              }
            </div>
          </mat-tab>
        </mat-tab-group>
      }
    </div>

    <!-- Context menu -->
    @if (contextMenu()) {
      <div class="context-menu"
           [style.left.px]="contextMenu()!.x"
           [style.top.px]="contextMenu()!.y">
        <div class="ctx-title">{{ contextMenu()!.step.title }}</div>
        <button class="ctx-item" (click)="editStepTitle()">
          <mat-icon>edit</mat-icon> Titel bearbeiten
        </button>
        <button class="ctx-item" (click)="editStepDescription()">
          <mat-icon>description</mat-icon> Beschreibung bearbeiten
        </button>
        <button class="ctx-item" (click)="openAttachTicket(contextMenu()!.step); closeCtx()">
          <mat-icon>link</mat-icon> Ticket verknüpfen
        </button>
        <button class="ctx-item" (click)="openCreateTicket(contextMenu()!.step); closeCtx()">
          <mat-icon>add_task</mat-icon> Ticket erstellen
        </button>
        <button class="ctx-item danger" (click)="deleteStep(contextMenu()!.step); closeCtx()">
          <mat-icon>delete</mat-icon> Löschen
        </button>
        <button class="ctx-close" (click)="closeCtx()">✕</button>
      </div>
    }

    <!-- Edit step overlay -->
    @if (editDialog()) {
      <div class="dialog-overlay" (click)="editDialog.set(null)">
        <div class="edit-dialog" (click)="$event.stopPropagation()">
          <h3>{{ i18n.t('projects.edit_step') }}</h3>
          <label>Titel</label>
          <input class="dialog-input" [(ngModel)]="editTitle" />
          <label>Beschreibung</label>
          <textarea class="dialog-input" [(ngModel)]="editDescription" rows="4"></textarea>
          <label>Dauer (Tage)</label>
          <input class="dialog-input" type="number" [(ngModel)]="editDuration" min="1" />
          <div class="dialog-actions">
            <button mat-button (click)="editDialog.set(null)">Abbrechen</button>
            <button mat-raised-button (click)="saveStepEdit()">Speichern</button>
          </div>
        </div>
      </div>
    }

    <!-- Add step overlay -->
    @if (addDialog()) {
      <div class="dialog-overlay" (click)="addDialog.set(false)">
        <div class="edit-dialog" (click)="$event.stopPropagation()">
          <h3>{{ i18n.t('projects.add_step') }}</h3>
          <label>Titel</label>
          <input class="dialog-input" [(ngModel)]="newStepTitle" />
          <label>Beschreibung</label>
          <textarea class="dialog-input" [(ngModel)]="newStepDescription" rows="3"></textarea>
          <label>Typ</label>
          <select class="dialog-input" [(ngModel)]="newStepType">
            <option value="epic">Epic</option>
            <option value="story">Story</option>
            <option value="task" selected>Task</option>
            <option value="subtask">Subtask</option>
            <option value="bug">Bug</option>
          </select>
          <label>Dauer (Tage)</label>
          <input class="dialog-input" type="number" [(ngModel)]="newStepDuration" min="1" />
          <div class="dialog-actions">
            <button mat-button (click)="addDialog.set(false)">Abbrechen</button>
            <button mat-raised-button (click)="saveNewStep()">Hinzufügen</button>
          </div>
        </div>
      </div>
    }

    <!-- Attach ticket overlay -->
    @if (attachDialog()) {
      <div class="dialog-overlay" (click)="attachDialog.set(null)">
        <div class="edit-dialog" (click)="$event.stopPropagation()">
          <h3>Ticket verknüpfen</h3>
          <label>Connector</label>
          <select class="dialog-input" [(ngModel)]="attachConnector">
            <option value="jira">Jira</option>
            <option value="jira_sd">Jira Service Desk</option>
          </select>
          <label>Jira Key (z.B. IMIT-1234)</label>
          <input class="dialog-input" [(ngModel)]="attachKey" placeholder="IMIT-1234" />
          <div class="dialog-actions">
            <button mat-button (click)="attachDialog.set(null)">Abbrechen</button>
            <button mat-raised-button (click)="doAttachTicket()">Verknüpfen</button>
          </div>
        </div>
      </div>
    }

    <!-- Create ticket overlay -->
    @if (createTicketDialog()) {
      <div class="dialog-overlay" (click)="createTicketDialog.set(null)">
        <div class="edit-dialog" (click)="$event.stopPropagation()">
          <h3>Jira-Ticket erstellen</h3>
          <label>Connector</label>
          <select class="dialog-input" [(ngModel)]="attachConnector">
            <option value="jira">Jira</option>
            <option value="jira_sd">Jira Service Desk</option>
          </select>
          <label>Zusammenfassung</label>
          <input class="dialog-input" [(ngModel)]="newTicketSummary" />
          <label>Ticket-Typ</label>
          <select class="dialog-input" [(ngModel)]="newTicketType">
            <option value="Epic">Epic</option>
            <option value="Story">Story</option>
            <option value="Task" selected>Task</option>
            <option value="Sub-task">Sub-task</option>
            <option value="Bug">Bug</option>
          </select>
          <div class="dialog-actions">
            <button mat-button (click)="createTicketDialog.set(null)">Abbrechen</button>
            <button mat-raised-button (click)="doCreateTicket()">Erstellen</button>
          </div>
        </div>
      </div>
    }
  `,
  styles: [`
    .detail-container { display: flex; flex-direction: column; height: 100%; background: var(--cs-bg); position: relative; }

    .lcars-header { display: flex; align-items: center; gap: 0; padding: 0; flex-shrink: 0; }
    .header-elbow { width: 32px; height: 56px; border-top-left-radius: 24px; background: var(--cs-accent, #FFCC99); flex-shrink: 0; }
    .header-info { display: flex; align-items: center; gap: 12px; padding: 0 16px; flex: 1; }
    .project-name { margin: 0; font-size: 1.2rem; font-weight: 700; color: var(--cs-accent, #FFCC99); }
    .status-chip { font-size: 0.8rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; }
    .header-actions { display: flex; gap: 8px; padding-right: 16px; }

    .spinner-center { display: flex; justify-content: center; padding: 80px; }

    .detail-tabs { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
    ::ng-deep .detail-tabs .mat-mdc-tab-body-wrapper { flex: 1; overflow: hidden; }
    ::ng-deep .detail-tabs .mat-mdc-tab-body-content { height: 100%; overflow: hidden; }

    /* Cytoscape */
    .cyto-container { width: 100%; height: 100%; min-height: 500px; background: var(--cs-bg); }

    /* Gantt */
    .gantt-container { padding: 16px; overflow-x: auto; }
    .gantt-header { display: flex; align-items: center; font-size: 0.75rem; color: var(--cs-text-muted); margin-bottom: 4px; }
    .gantt-label-col { width: 220px; flex-shrink: 0; padding-right: 12px; font-weight: 600; }
    .gantt-timeline { flex: 1; display: flex; }
    .gantt-day { flex: 1; text-align: center; min-width: 20px; border-left: 1px solid var(--cs-border, #333); }
    .gantt-row { display: flex; align-items: center; margin-bottom: 4px; cursor: pointer; }
    .gantt-row:hover { background: var(--cs-surface, #1a1a2e); }
    .gantt-label { width: 220px; flex-shrink: 0; font-size: 0.85rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding-right: 12px; }
    .gantt-track { flex: 1; height: 24px; background: var(--cs-surface, #1a1a2e); border-radius: 2px; position: relative; }
    .gantt-bar { position: absolute; top: 2px; height: 20px; border-radius: 2px; min-width: 4px; transition: opacity 0.15s; }
    .gantt-bar:hover { opacity: 0.8; }
    .empty { color: var(--cs-text-muted); text-align: center; padding: 60px; }

    /* List */
    .list-container { padding: 16px; overflow-y: auto; height: 100%; }
    .step-list { display: flex; flex-direction: column; gap: 8px; }
    .step-list-row {
      display: flex; align-items: center; gap: 12px;
      background: var(--cs-surface, #1a1a2e); border: 1px solid var(--cs-border, #333);
      border-radius: 6px; padding: 10px 12px;
    }
    .step-list-row.critical { border-color: #FFCC99; }
    .step-type-dot { width: 12px; height: 12px; border-radius: 3px; flex-shrink: 0; }
    .step-main { flex: 1; }
    .step-title { font-size: 0.9rem; color: var(--cs-text); font-weight: 600; }
    .step-desc { font-size: 0.8rem; color: var(--cs-text-muted); margin-top: 2px; }
    .jira-badge { font-size: 0.75rem; background: var(--cs-bg); border: 1px solid var(--cs-border, #333); border-radius: 3px; padding: 1px 6px; margin-top: 4px; display: inline-block; }
    .step-meta { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; min-width: 80px; }
    .step-status { font-size: 0.75rem; font-weight: 600; }
    .step-dur { font-size: 0.75rem; color: var(--cs-text-muted); }
    .step-slack { font-size: 0.75rem; color: var(--cs-text-muted); }
    .step-slack.critical { color: #FFCC99; }
    .step-actions { display: flex; align-items: center; }
    .status-select { font-size: 0.8rem; width: 110px; }

    /* Context menu */
    .context-menu {
      position: fixed; z-index: 2000;
      background: var(--cs-surface, #1a1a2e); border: 1px solid var(--cs-accent, #FFCC99);
      border-radius: 6px; padding: 4px 0; min-width: 200px; box-shadow: 0 8px 24px rgba(0,0,0,0.5);
    }
    .ctx-title { padding: 8px 12px; font-size: 0.8rem; font-weight: 700; color: var(--cs-accent, #FFCC99); border-bottom: 1px solid var(--cs-border, #333); }
    .ctx-item {
      display: flex; align-items: center; gap: 8px; padding: 8px 12px; width: 100%;
      background: none; border: none; color: var(--cs-text); cursor: pointer; font-size: 0.9rem;
    }
    .ctx-item:hover { background: var(--cs-bg); }
    .ctx-item.danger { color: #CC4444; }
    .ctx-close { position: absolute; top: 4px; right: 8px; background: none; border: none; color: var(--cs-text-muted); cursor: pointer; font-size: 1rem; }

    /* Dialogs */
    .dialog-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; z-index: 1500; }
    .edit-dialog {
      background: var(--cs-surface, #1a1a2e); border: 1px solid var(--cs-border, #333);
      border-radius: 8px; padding: 24px; width: 440px; display: flex; flex-direction: column; gap: 10px;
    }
    .edit-dialog h3 { margin: 0; color: var(--cs-accent, #FFCC99); }
    .edit-dialog label { font-size: 0.8rem; color: var(--cs-text-muted); margin-bottom: -6px; }
    .dialog-input {
      background: var(--cs-bg); border: 1px solid var(--cs-border, #333); border-radius: 4px;
      padding: 8px 12px; color: var(--cs-text); font-size: 0.95rem; outline: none; resize: vertical; width: 100%; box-sizing: border-box;
    }
    .dialog-input:focus { border-color: var(--cs-accent, #FFCC99); }
    .dialog-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
  `],
})
export class ProjectDetailComponent implements OnInit, AfterViewInit, OnDestroy {
  @ViewChild('cytoEl') private cytoEl!: ElementRef<HTMLDivElement>;

  private svc = inject(ProjectsService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private snack = inject(MatSnackBar);
  private ws = inject(WebsocketService);
  private cdr = inject(ChangeDetectorRef);
  i18n = inject(I18nService);

  graph = signal<PlanGraph | null>(null);
  loading = signal(true);
  syncing = signal(false);
  contextMenu = signal<ContextMenu | null>(null);
  editDialog = signal<StepNode | null>(null);
  addDialog = signal(false);
  attachDialog = signal<StepNode | null>(null);
  createTicketDialog = signal<StepNode | null>(null);
  ganttRows = signal<GanttRow[]>([]);
  ganttDays = signal<number[]>([]);

  // edit fields
  editTitle = ''; editDescription = ''; editDuration = 1;
  newStepTitle = ''; newStepDescription = ''; newStepType = 'task'; newStepDuration = 1;
  attachConnector = 'jira'; attachKey = '';
  newTicketSummary = ''; newTicketType = 'Task';
  stepStatusMap: Record<string, string> = {};

  private projectId = '';
  private cy: any = null;
  private destroyed$ = new Subject<void>();
  private activeTab = 0;

  ngOnInit() {
    this.projectId = this.route.snapshot.paramMap.get('id') ?? '';
    this.loadGraph();

    this.ws.messages().pipe(takeUntil(this.destroyed$)).subscribe((msg: WsMessage) => {
      if (msg.type === 'project_updated' && msg['project_id'] === this.projectId) {
        this.loadGraph(true);
      }
    });

    // Close context menu on outside click
    document.addEventListener('click', this.closeCtxBound);
  }

  ngAfterViewInit() {
    if (this.graph() && this.activeTab === 0) {
      this.initCytoscape();
    }
  }

  ngOnDestroy() {
    this.destroyed$.next();
    this.destroyed$.complete();
    document.removeEventListener('click', this.closeCtxBound);
    this.cy?.destroy();
  }

  private closeCtxBound = () => this.contextMenu.set(null);

  loadGraph(silent = false) {
    if (!silent) this.loading.set(true);
    this.svc.getGraph(this.projectId).subscribe({
      next: g => {
        this.graph.set(g);
        g.steps.forEach(s => { this.stepStatusMap[s.id] = s.status; });
        this.buildGantt(g);
        this.loading.set(false);
        if (this.cy && this.activeTab === 0) {
          this.updateCytoscape(g);
        } else if (!this.cy && this.activeTab === 0) {
          setTimeout(() => this.initCytoscape(), 100);
        }
        this.cdr.markForCheck();
      },
      error: () => this.loading.set(false),
    });
  }

  onTabChange(idx: number) {
    this.activeTab = idx;
    if (idx === 0 && !this.cy && this.graph()) {
      setTimeout(() => this.initCytoscape(), 100);
    }
  }

  // ── Cytoscape ────────────────────────────────────────────────────────────

  private async initCytoscape() {
    if (!this.cytoEl?.nativeElement || !this.graph()) return;
    const cytoscape = (await import('cytoscape')).default;
    const cytoscapeDagre = (await import('cytoscape-dagre')).default;
    cytoscape.use(cytoscapeDagre);

    const g = this.graph()!;
    const elements = this.buildCyElements(g);

    this.cy = cytoscape({
      container: this.cytoEl.nativeElement,
      elements,
      style: this.buildCyStyle(),
      layout: { name: 'dagre', rankDir: 'LR', padding: 40, rankSep: 80, nodeSep: 40 } as any,
      userZoomingEnabled: true,
      userPanningEnabled: true,
      boxSelectionEnabled: false,
    });

    // Right-click context menu
    this.cy.on('cxttap', 'node', (evt: any) => {
      evt.originalEvent.preventDefault();
      const stepId = evt.target.id();
      const step = g.steps.find(s => s.id === stepId);
      if (!step) return;
      this.contextMenu.set({
        x: evt.originalEvent.clientX,
        y: evt.originalEvent.clientY,
        stepId,
        step,
      });
      this.cdr.markForCheck();
    });

    // Save position on drag end
    this.cy.on('dragfree', 'node', (evt: any) => {
      const pos = evt.target.position();
      const stepId = evt.target.id();
      this.svc.updateStep(this.projectId, stepId, { pos_x: Math.round(pos.x), pos_y: Math.round(pos.y) }).subscribe();
    });
  }

  private buildCyElements(g: PlanGraph): any[] {
    const nodes = g.steps.map(s => ({
      data: {
        id: s.id,
        label: `${s.title}\n${s.jira_issue_type.toUpperCase()} · ${s.duration_days}d${s.jira_key ? '\n' + s.jira_key : ''}`,
        bgColor: s.critical ? '#FFCC99' : ISSUE_TYPE_COLORS[s.jira_issue_type] ?? '#444',
        textColor: s.critical ? '#000' : '#fff',
        borderColor: STATUS_COLORS[s.status] ?? '#888',
      },
      position: (s.pos_x != null && s.pos_y != null) ? { x: s.pos_x, y: s.pos_y } : undefined,
    }));
    const edges = g.deps.map(d => ({
      data: { id: d.id, source: d.depends_on_step_id, target: d.step_id },
    }));
    return [...nodes, ...edges];
  }

  private buildCyStyle(): any[] {
    return [
      {
        selector: 'node',
        style: {
          'shape': 'roundrectangle',
          'background-color': 'data(bgColor)',
          'border-color': 'data(borderColor)',
          'border-width': 3,
          'label': 'data(label)',
          'text-valign': 'center',
          'text-halign': 'center',
          'color': 'data(textColor)',
          'font-size': '11px',
          'text-wrap': 'wrap',
          'text-max-width': '120px',
          'width': 140,
          'height': 60,
          'padding': '8px',
        },
      },
      {
        selector: 'edge',
        style: {
          'width': 2,
          'line-color': '#555',
          'target-arrow-color': '#555',
          'target-arrow-shape': 'triangle',
          'curve-style': 'bezier',
        },
      },
      {
        selector: 'node:selected',
        style: {
          'border-color': '#FFCC99',
          'border-width': 4,
        },
      },
    ];
  }

  private updateCytoscape(g: PlanGraph) {
    if (!this.cy) return;
    this.cy.elements().remove();
    this.cy.add(this.buildCyElements(g));
    this.cy.layout({ name: 'dagre', rankDir: 'LR', padding: 40 } as any).run();
  }

  // ── Gantt ─────────────────────────────────────────────────────────────────

  private buildGantt(g: PlanGraph) {
    if (!g.steps.length) { this.ganttRows.set([]); this.ganttDays.set([]); return; }

    const hasEst = g.steps.some(s => s.est_start != null);
    let maxDay = 0;

    if (hasEst) {
      g.steps.forEach(s => { if (s.est_end != null && s.est_end > maxDay) maxDay = s.est_end; });
    } else {
      g.steps.forEach(s => { maxDay += s.duration_days; });
    }

    const days = Array.from({ length: Math.max(maxDay, 1) }, (_, i) => i + 1);
    this.ganttDays.set(days);

    let offset = 0;
    const rows = g.steps.map(s => {
      let start = 0;
      if (hasEst) {
        start = s.est_start ?? 0;
      } else {
        start = offset;
        offset += s.duration_days;
      }
      return {
        step: s,
        barLeft: maxDay > 0 ? (start / maxDay) * 100 : 0,
        barWidth: maxDay > 0 ? (s.duration_days / maxDay) * 100 : 100,
      };
    });
    this.ganttRows.set(rows);
  }

  // ── Actions ───────────────────────────────────────────────────────────────

  statusColor(status: string): string {
    return { planning: '#99CCFF', active: '#FFCC66', done: '#90EE90', archived: '#888', pending: '#888', in_progress: '#FFCC66' }[status] ?? '#888';
  }

  statusBgColor(status: string): string {
    return STATUS_COLORS[status] ?? '#606060';
  }

  issueTypeColor(type: string): string {
    return ISSUE_TYPE_COLORS[type] ?? '#444';
  }

  selectStep(step: StepNode) {
    this.contextMenu.set(null);
    this.editDialog.set(step);
    this.editTitle = step.title;
    this.editDescription = step.description ?? '';
    this.editDuration = step.duration_days;
  }

  editStepTitle() {
    const step = this.contextMenu()?.step;
    if (!step) return;
    this.closeCtx();
    this.selectStep(step);
  }

  editStepDescription() {
    this.editStepTitle();
  }

  closeCtx() { this.contextMenu.set(null); }

  saveStepEdit() {
    const step = this.editDialog();
    if (!step) return;
    this.svc.updateStep(this.projectId, step.id, {
      title: this.editTitle,
      description: this.editDescription || undefined,
      duration_days: this.editDuration,
    } as any).subscribe({
      next: () => { this.editDialog.set(null); this.loadGraph(true); },
      error: () => this.snack.open('Fehler beim Speichern', 'OK', { duration: 2000 }),
    });
  }

  addStep() { this.newStepTitle = ''; this.newStepDescription = ''; this.newStepType = 'task'; this.newStepDuration = 1; this.addDialog.set(true); }

  saveNewStep() {
    if (!this.newStepTitle.trim()) return;
    this.svc.addStep(this.projectId, {
      title: this.newStepTitle.trim(),
      description: this.newStepDescription.trim() || undefined,
      jira_issue_type: this.newStepType,
      duration_days: this.newStepDuration,
    }).subscribe({
      next: () => { this.addDialog.set(false); this.loadGraph(true); },
      error: () => this.snack.open('Fehler beim Hinzufügen', 'OK', { duration: 2000 }),
    });
  }

  deleteStep(step: StepNode) {
    if (!confirm(`Schritt "${step.title}" wirklich löschen?`)) return;
    this.svc.deleteStep(this.projectId, step.id).subscribe({
      next: () => this.loadGraph(true),
    });
  }

  setStatus(step: StepNode, status: string) {
    this.svc.updateStep(this.projectId, step.id, { status } as any).subscribe({
      next: () => this.loadGraph(true),
    });
  }

  openAttachTicket(step: StepNode) { this.attachKey = step.jira_key ?? ''; this.attachDialog.set(step); }
  openCreateTicket(step: StepNode) { this.newTicketSummary = step.title; this.createTicketDialog.set(step); }

  doAttachTicket() {
    const step = this.attachDialog();
    if (!step || !this.attachKey.trim()) return;
    this.svc.attachTicket(this.projectId, step.id, this.attachConnector, this.attachKey.trim()).subscribe({
      next: () => { this.attachDialog.set(null); this.loadGraph(true); this.snack.open('Ticket verknüpft', 'OK', { duration: 2000 }); },
      error: e => this.snack.open(`Fehler: ${e?.error?.detail ?? 'unbekannt'}`, 'OK', { duration: 3000 }),
    });
  }

  doCreateTicket() {
    const step = this.createTicketDialog();
    if (!step) return;
    this.svc.createTicket(this.projectId, step.id, {
      connector_type: this.attachConnector,
      summary: this.newTicketSummary || step.title,
      issue_type: this.newTicketType,
    }).subscribe({
      next: () => { this.createTicketDialog.set(null); this.loadGraph(true); this.snack.open('Ticket erstellt', 'OK', { duration: 2000 }); },
      error: e => this.snack.open(`Fehler: ${e?.error?.detail ?? 'unbekannt'}`, 'OK', { duration: 3000 }),
    });
  }

  syncJira() {
    this.syncing.set(true);
    this.svc.sync(this.projectId).subscribe({
      next: r => {
        this.syncing.set(false);
        this.snack.open(`${r.updated} Ticket(s) synchronisiert`, 'OK', { duration: 2000 });
        this.loadGraph(true);
      },
      error: () => { this.syncing.set(false); this.snack.open('Sync fehlgeschlagen', 'OK', { duration: 2000 }); },
    });
  }

  openWorkbench() {
    this.svc.openInWorkbench(this.projectId).subscribe({
      next: r => window.open(r.ide_url, '_blank'),
      error: () => this.snack.open('Werkbank konnte nicht geöffnet werden', 'OK', { duration: 3000 }),
    });
  }

  back() { this.router.navigate(['/projects']); }
}
