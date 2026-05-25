import {
  AfterViewInit,
  Component,
  ElementRef,
  OnDestroy,
  ViewChild,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { forkJoin } from 'rxjs';
import { GridItemHTMLElement, GridStack } from 'gridstack';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { environment } from '../../../environments/environment';
import { AddWidgetDialogComponent } from './add-widget-dialog.component';
import { DashboardWidgetComponent } from './dashboard-widget.component';
import {
  DashboardWidget,
  DashboardWidgetCreate,
  WidgetData,
} from './dashboard-widget.model';

@Component({
  selector: 'cs-dashboard',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatCardModule,
    MatDialogModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatSnackBarModule,
    DashboardWidgetComponent,
  ],
  template: `
    <div class="dashboard-shell">
      <section class="hero">
        <div>
          <p class="eyebrow">CentralStation</p>
          <h1>Operations Cockpit</h1>
          <p class="subtitle">
            Gespeicherte Suchen, Live-Listen und Metriken als frei arrangierbare Widgets.
          </p>
        </div>
        <div class="hero-actions">
          @if (configMode()) {
            <button mat-flat-button color="primary" (click)="addWidget()">
              <mat-icon>add</mat-icon>
              Widget hinzufügen
            </button>
          }
          <button mat-stroked-button [color]="configMode() ? 'warn' : 'primary'" (click)="toggleConfigMode()">
            <mat-icon>{{ configMode() ? 'done' : 'dashboard_customize' }}</mat-icon>
            {{ configMode() ? 'Layout speichern' : 'Dashboard anpassen' }}
          </button>
          <button mat-icon-button (click)="refreshAll()" [disabled]="loading()" title="Aktualisieren">
            <mat-icon>refresh</mat-icon>
          </button>
        </div>
      </section>

      @if (loading()) {
        <mat-card class="loading-card">
          <mat-spinner diameter="32"></mat-spinner>
          <span>Lade Dashboard...</span>
        </mat-card>
      }

      <div #grid class="grid-stack" [class.config-mode]="configMode()">
        @for (widget of widgets(); track widget.id) {
          <div class="grid-stack-item"
               [attr.gs-id]="widget.id"
               [attr.gs-x]="widget.gs_x"
               [attr.gs-y]="widget.gs_y"
               [attr.gs-w]="widget.gs_w"
               [attr.gs-h]="widget.gs_h">
            <div class="grid-stack-item-content">
              <cs-dashboard-widget
                [widget]="widget"
                [data]="widgetData()[widget.id]"
                [editMode]="configMode()"
                (click)="openWidget(widget)"
                (remove)="deleteWidget(widget.id)" />
            </div>
          </div>
        }
      </div>
    </div>
  `,
  styles: [`
    .dashboard-shell {
      min-height: 100%;
      padding: 24px;
      background:
        radial-gradient(circle at 12% 8%, color-mix(in srgb, var(--mat-sys-primary) 15%, transparent), transparent 26rem),
        linear-gradient(145deg, color-mix(in srgb, var(--mat-sys-surface-container) 70%, #eef7f2), var(--mat-sys-surface));
    }
    .hero {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 20px;
      margin-bottom: 18px;
    }
    .eyebrow {
      margin: 0 0 4px;
      color: var(--mat-sys-primary);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: .14em;
      text-transform: uppercase;
    }
    h1 {
      margin: 0;
      font-size: clamp(28px, 5vw, 52px);
      line-height: .98;
      letter-spacing: -.06em;
      font-weight: 900;
    }
    .subtitle {
      margin: 10px 0 0;
      max-width: 680px;
      color: var(--mat-sys-on-surface-variant);
      font-size: 14px;
    }
    .hero-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }
    .loading-card {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 14px 16px;
      margin-bottom: 16px;
      color: var(--mat-sys-on-surface-variant);
    }
    .grid-stack { min-height: 520px; }
    .grid-stack.config-mode {
      background-image:
        linear-gradient(var(--mat-sys-outline-variant) 1px, transparent 1px),
        linear-gradient(90deg, var(--mat-sys-outline-variant) 1px, transparent 1px);
      background-size: 80px 80px;
      border-radius: 18px;
      padding-bottom: 12px;
    }
    .grid-stack-item-content { inset: 4px !important; overflow: visible !important; }

    @media (max-width: 820px) {
      .dashboard-shell { padding: 16px; }
      .hero { align-items: flex-start; flex-direction: column; }
      .hero-actions { justify-content: flex-start; }
    }
  `],
})
export class DashboardComponent implements AfterViewInit, OnDestroy {
  @ViewChild('grid') private gridEl!: ElementRef<HTMLElement>;

  widgets = signal<DashboardWidget[]>([]);
  widgetData = signal<Record<string, WidgetData>>({});
  configMode = signal(false);
  loading = signal(true);
  private grid?: GridStack;

  constructor(
    private http: HttpClient,
    private router: Router,
    private dialog: MatDialog,
    private snackBar: MatSnackBar,
  ) {}

  ngAfterViewInit() {
    this.loadWidgets();
  }

  ngOnDestroy() {
    this.grid?.destroy(false);
  }

  loadWidgets() {
    this.loading.set(true);
    this.http.get<DashboardWidget[]>(`${environment.apiUrl}/dashboard-widgets/`).subscribe({
      next: widgets => {
        this.widgets.set(widgets);
        this.loading.set(false);
        this.rebuildGrid();
        widgets.forEach(w => this.loadWidgetData(w.id));
      },
      error: () => {
        this.loading.set(false);
        this.snackBar.open('Dashboard konnte nicht geladen werden', 'OK', { duration: 4000 });
      },
    });
  }

  rebuildGrid() {
    setTimeout(() => {
      this.grid?.destroy(false);
      this.grid = GridStack.init({
        cellHeight: 80,
        minRow: 4,
        margin: 8,
        float: false,
        disableDrag: !this.configMode(),
        disableResize: !this.configMode(),
      }, this.gridEl.nativeElement);
    });
  }

  refreshAll() {
    this.widgets().forEach(w => this.loadWidgetData(w.id));
  }

  toggleConfigMode() {
    const next = !this.configMode();
    this.configMode.set(next);
    if (next) {
      this.grid?.enable();
    } else {
      this.grid?.disable();
      this.saveLayout();
    }
  }

  saveLayout() {
    const items = this.grid?.getGridItems() ?? [];
    const updates = items
      .map(el => this.layoutPatch(el))
      .filter((patch): patch is { id: string; body: Record<string, number> } => !!patch)
      .map(patch => this.http.patch(`${environment.apiUrl}/dashboard-widgets/${patch.id}`, patch.body));

    if (!updates.length) return;
    forkJoin(updates).subscribe({
      next: () => this.snackBar.open('Dashboard-Layout gespeichert', '', { duration: 2000 }),
      error: () => this.snackBar.open('Layout konnte nicht gespeichert werden', 'OK', { duration: 4000 }),
    });
  }

  private layoutPatch(el: GridItemHTMLElement): { id: string; body: Record<string, number> } | null {
    const id = el.getAttribute('gs-id');
    const n = el.gridstackNode;
    if (!id || !n) return null;
    return {
      id,
      body: {
        gs_x: n.x ?? 0,
        gs_y: n.y ?? 0,
        gs_w: n.w ?? 4,
        gs_h: n.h ?? 3,
      },
    };
  }

  loadWidgetData(widgetId: string) {
    this.http.get<WidgetData>(`${environment.apiUrl}/dashboard-widgets/${widgetId}/data`).subscribe({
      next: data => this.widgetData.update(m => ({ ...m, [widgetId]: data })),
      error: () => this.widgetData.update(m => ({ ...m, [widgetId]: { error: 'Daten konnten nicht geladen werden', series: [] } })),
    });
  }

  addWidget() {
    const ref = this.dialog.open<AddWidgetDialogComponent, unknown, DashboardWidgetCreate>(
      AddWidgetDialogComponent,
      { width: '680px' },
    );
    ref.afterClosed().subscribe(payload => {
      if (!payload) return;
      this.http.post<DashboardWidget>(`${environment.apiUrl}/dashboard-widgets/`, payload).subscribe({
        next: widget => {
          this.widgets.update(ws => [...ws, widget]);
          this.rebuildGrid();
          this.loadWidgetData(widget.id);
        },
        error: () => this.snackBar.open('Widget konnte nicht angelegt werden', 'OK', { duration: 4000 }),
      });
    });
  }

  deleteWidget(widgetId: string) {
    this.http.delete(`${environment.apiUrl}/dashboard-widgets/${widgetId}`).subscribe({
      next: () => {
        this.widgets.update(ws => ws.filter(w => w.id !== widgetId));
        this.widgetData.update(data => {
          const next = { ...data };
          delete next[widgetId];
          return next;
        });
        this.rebuildGrid();
      },
      error: () => this.snackBar.open('Widget konnte nicht gelöscht werden', 'OK', { duration: 4000 }),
    });
  }

  openWidget(widget: DashboardWidget) {
    if (this.configMode()) return;
    if (widget.widget_type === 'grafana_panel') return;

    const cfg = widget.config;
    this.router.navigate(['/feed'], {
      queryParams: {
        source: Array.isArray(cfg['sources']) ? cfg['sources'].join(',') : undefined,
        severity: typeof cfg['severity'] === 'string' ? cfg['severity'] : undefined,
        search_id: typeof cfg['search_id'] === 'string' ? cfg['search_id'] : undefined,
        q: typeof cfg['query_string'] === 'string' ? cfg['query_string'] : undefined,
        index: typeof cfg['index_pattern'] === 'string' ? cfg['index_pattern'] : undefined,
      },
    });
  }
}
