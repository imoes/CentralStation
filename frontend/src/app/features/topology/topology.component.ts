import { Component, OnInit, OnDestroy, inject, signal, computed, NgZone } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { NgxEchartsDirective } from 'ngx-echarts';
import { environment } from '../../../environments/environment';
import { ThemeService } from '../../core/services/theme.service';
import { AuthService } from '../../core/auth/auth.service';
import { I18nService } from '../../core/services/i18n.service';

interface TopologyNode {
  id: string;
  label: string;
  type: 'site' | 'cluster' | 'host' | 'vm' | 'service';
  status: string;
  alert_count: number;
  inactive: boolean;
}

interface TopologyEdge {
  source: string;
  target: string;
  kind: string;
}

interface TopologyGraph {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  stats?: { sites: number; clusters: number; hosts: number; vms: number; alerts: number };
  error?: string;
  generated_at?: string;
}

const SEV_COLOR: Record<string, string> = {
  critical: '#b71c1c',
  high: '#e65100',
  medium: '#f9a825',
  low: '#1565c0',
  ok: '#2e7d32',
};

const NODE_SIZE: Record<string, number> = {
  site: 6, cluster: 4, host: 2, vm: 1, service: 2,
};

const CAT = ['site', 'cluster', 'host', 'vm', 'service'];

@Component({
  selector: 'app-topology',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatIconModule,
    MatButtonModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    MatSnackBarModule,
    NgxEchartsDirective,
  ],
  template: `
    <div class="topo-shell">
      <div class="topo-topbar">
        <h2 class="topo-title">
          <mat-icon>account_tree</mat-icon>
          {{ i18n.t('topology.title') }}
        </h2>

        <div class="topo-sources">
          @for (src of sources; track src.value) {
            <button class="src-chip"
              [class.src-chip--active]="activeSource() === src.value"
              (click)="setSource(src.value)"
              [matTooltip]="src.label + ' Alerts anzeigen'">
              {{ src.label }}
            </button>
          }
        </div>

        @if (graph()?.stats; as s) {
          <div class="topo-stats">
            <span class="stat-chip">{{ s.hosts }} Hosts</span>
            <span class="stat-chip">{{ s.vms }} VMs</span>
            @if (s.alerts > 0) {
              <span class="stat-chip stat-chip--alert">{{ s.alerts }} Alerts</span>
            }
          </div>
        }

        <div class="topo-spacer"></div>

        <input
          class="topo-search"
          type="text"
          [(ngModel)]="searchTerm"
          (ngModelChange)="searchFilter.set($event.toLowerCase())"
          placeholder="Nodes suchen…"
          matTooltip="Nodes filtern — nicht passende werden ausgeblendet"
        />

        @if (isSysAdmin()) {
          <button mat-stroked-button class="topo-kbsync" (click)="triggerKbSync()" [disabled]="syncing()">
            @if (syncing()) { <mat-spinner diameter="16"></mat-spinner> }
            @else { <mat-icon>sync</mat-icon> }
            KB Sync
          </button>
        }

        <button mat-flat-button class="topo-refresh" (click)="load(false)" [disabled]="loading()">
          @if (loading()) { <mat-spinner diameter="18"></mat-spinner> }
          @else { <mat-icon>refresh</mat-icon> }
          Aktualisieren
        </button>
      </div>

      @if (graph()?.error) {
        <div class="topo-empty">
          <mat-icon>lan</mat-icon>
          <p>{{ graph()!.error }}</p>
          <p class="topo-hint">NetBox-Connector konfigurieren unter Einstellungen → Connectors</p>
        </div>
      } @else if (loading() && !graph()) {
        <div class="topo-loading">
          <mat-spinner diameter="48"></mat-spinner>
          <p>Lade Infrastrukturdaten…</p>
        </div>
      } @else if (graph()?.nodes?.length === 0) {
        <div class="topo-empty">
          <mat-icon>hub</mat-icon>
          <p>{{ i18n.t('topology.empty') }}</p>
        </div>
      } @else {
        <div
          echarts
          [options]="chartOptions()"
          [theme]="chartTheme()"
          class="topo-chart"
          (chartInit)="onChartInit($event)"
          (chartClick)="onNodeClick($event)"
        ></div>
      }
    </div>
  `,
  styles: [`
    :host { display: block; }

    .topo-shell {
      display: flex; flex-direction: column;
      height: 100vh;
      background: var(--mat-sys-surface);
      color: var(--mat-sys-on-surface);
      overflow: hidden;
    }

    .topo-topbar {
      display: flex; align-items: center; gap: 8px;
      padding: 8px 16px; flex-wrap: wrap; flex-shrink: 0;
      border-bottom: 1px solid var(--mat-sys-outline-variant);
    }

    .topo-title {
      display: flex; align-items: center; gap: 6px;
      margin: 0; font-size: 1.1rem; font-weight: 600;
    }

    .topo-sources { display: flex; gap: 4px; flex-wrap: wrap; }

    .src-chip {
      padding: 2px 10px; border-radius: 12px; font-size: 0.75rem; cursor: pointer;
      border: 1px solid var(--mat-sys-outline);
      background: transparent;
      color: var(--mat-sys-on-surface-variant);
      transition: background 0.15s, color 0.15s;
      &:hover { background: var(--mat-sys-surface-variant); }
    }
    .src-chip--active {
      background: var(--mat-sys-primary);
      color: var(--mat-sys-on-primary);
      border-color: var(--mat-sys-primary);
    }

    .topo-stats { display: flex; gap: 6px; flex-wrap: wrap; }

    .stat-chip {
      padding: 2px 10px; border-radius: 12px; font-size: 0.75rem;
      background: var(--mat-sys-surface-variant);
      color: var(--mat-sys-on-surface-variant);
    }
    .stat-chip--alert { background: #b71c1c; color: #fff; }

    .topo-spacer { flex: 1; }

    .topo-search {
      padding: 4px 10px; border-radius: 6px; font-size: 0.85rem;
      border: 1px solid var(--mat-sys-outline);
      background: var(--mat-sys-surface-container);
      color: var(--mat-sys-on-surface);
      width: 200px;
      &:focus { outline: 2px solid var(--mat-sys-primary); }
    }

    .topo-refresh mat-spinner, .topo-kbsync mat-spinner { display: inline-flex; }

    .topo-chart { flex: 1; min-height: 0; width: 100%; }

    .topo-loading, .topo-empty {
      flex: 1; min-height: 0; display: flex; flex-direction: column;
      align-items: center; justify-content: center; gap: 12px;
      color: var(--mat-sys-on-surface-variant);
    }
    .topo-empty mat-icon { font-size: 48px; width: 48px; height: 48px; opacity: 0.4; }
    .topo-hint { font-size: 0.8rem; opacity: 0.7; }

    /* ── LCARS ─────────────────────────────────────────────────────────── */
    :host-context(html.cs-theme-lcars) .topo-topbar {
      background: #000; border-color: #FF9933;
    }
    :host-context(html.cs-theme-lcars) .topo-title { color: #FFCC66; }
    :host-context(html.cs-theme-lcars) .stat-chip {
      background: #1a0e00; color: #FFCC99; border: 1px solid #FF9933;
    }
    :host-context(html.cs-theme-lcars) .src-chip {
      border-color: #FF9933; color: #FFCC99;
      &:hover { background: #1a0e00; }
    }
    :host-context(html.cs-theme-lcars) .src-chip--active {
      background: #FF9933; color: #000; border-color: #FF9933;
    }
    :host-context(html.cs-theme-lcars) .topo-search {
      background: #0d0700; color: #FFCC99; border-color: #FF9933;
      &:focus { outline-color: #FF9933; }
    }
    :host-context(html.cs-theme-lcars) .topo-shell { background: #0a0600; color: #FFCC99; }

    /* ── Holo ──────────────────────────────────────────────────────────── */
    :host-context(html.cs-theme-holo) .topo-topbar {
      background: #040e1a; border-color: #1a3a5c;
    }
    :host-context(html.cs-theme-holo) .stat-chip {
      background: #071420; color: #5fc8ee; border: 1px solid #1a3a5c;
    }
    :host-context(html.cs-theme-holo) .src-chip {
      border-color: #1a3a5c; color: #5fc8ee;
      &:hover { background: #071420; }
    }
    :host-context(html.cs-theme-holo) .src-chip--active {
      background: #4fd6ff; color: #000; border-color: #4fd6ff;
    }
    :host-context(html.cs-theme-holo) .topo-search {
      background: #040e1a; color: #5fc8ee; border-color: #1a3a5c;
    }
  `],
})
export class TopologyComponent implements OnInit, OnDestroy {
  private http = inject(HttpClient);
  private themeSvc = inject(ThemeService);
  private auth = inject(AuthService);
  readonly i18n = inject(I18nService);
  private snack = inject(MatSnackBar);
  private zone = inject(NgZone);

  private _echart: any = null;
  private _resizeObs: ResizeObserver | null = null;

  readonly sources = [
    { value: null,        label: 'Alle' },
    { value: 'checkmk',  label: 'CheckMK' },
    { value: 'graylog',  label: 'Graylog' },
    { value: 'wazuh',    label: 'Wazuh' },
    { value: 'icinga2',  label: 'Icinga2' },
    { value: 'coroot',   label: 'Coroot' },
  ] as const;

  graph = signal<TopologyGraph | null>(null);
  loading = signal(true);
  syncing = signal(false);
  activeSource = signal<string | null>(null);
  searchTerm = '';
  searchFilter = signal('');

  isSysAdmin = computed(() => {
    const role = this.auth.userRole();
    return role === 'admin' || role === 'sysadmin';
  });

  chartTheme = computed(() => '');

  private get _chartText() {
    const t = this.themeSvc.theme();
    return t === 'lcars' ? '#e8a060' : t === 'holo' ? '#5fc8ee' : '#94a3b8';
  }
  private get _chartGrid() {
    const t = this.themeSvc.theme();
    return t === 'lcars' ? '#2a1d0a' : t === 'holo' ? '#0e2236' : '#334155';
  }

  readonly chartOptions = computed(() => {
    const g = this.graph();
    if (!g || !g.nodes?.length) return {};
    const term = this.searchFilter();
    const txt = this._chartText;
    const grid = this._chartGrid;

    const large = g.nodes.length >= 600;

    return {
      backgroundColor: 'transparent',
      // animation + continuous force simulation freezes the browser. Disable
      // both above the threshold so the graph paints once and stays interactive.
      animation: !large,
      tooltip: {
        formatter: (p: any) =>
          p.dataType === 'node'
            ? `<b>${p.data.name}</b><br/>${p.data.nodeType} · ${p.data.status} · ${p.data.alertCount} Alerts`
            : `${p.data.source} → ${p.data.target}`,
      },
      legend: {
        data: CAT,
        textStyle: { color: txt },
        bottom: 4,
      },
      series: [{
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        categories: CAT.map(c => ({ name: c })),
        // Only used in the fallback path (no precomputed coordinates).
        force: {
          repulsion: 200,
          edgeLength: [30, 100],
          gravity: 0.05,
          layoutAnimation: g.nodes.length < 600,
        },
        label: {
          show: true,
          position: 'right',
          fontSize: 10,
          color: txt,
          formatter: (p: any) =>
            p.data.nodeType === 'vm' ? '' : p.data.name.split('.')[0],
        },
        emphasis: { focus: 'adjacency', label: { show: true } },
        lineStyle: { color: grid, width: 1, curveness: 0.1 },
        data: g.nodes.map(n => ({
          id: n.id,
          name: n.label,
          category: CAT.indexOf(n.type),
          nodeType: n.type,
          status: n.status,
          alertCount: n.alert_count,
          symbolSize: (NODE_SIZE[n.type] ?? 2) + Math.min(n.alert_count * 0.5, 3),
          itemStyle: {
            color: SEV_COLOR[n.status] ?? SEV_COLOR['ok'],
            opacity: term && !n.id.includes(term) ? 0.12 : (n.inactive ? 0.45 : 1),
          },
        })),
        links: g.edges.map(e => ({
          source: e.source,
          target: e.target,
          lineStyle: e.kind === 'depends_on'
            ? { type: 'dashed', color: '#FF9933' }
            : {},
        })),
      }],
    };
  });

  ngOnInit(): void {
    this.load(false);
  }

  onChartInit(ec: any): void {
    this._echart = ec;
    // ECharts reads container size at init; if flex layout isn't settled yet the
    // canvas is undersized and roam only works in the small centre area. Force a
    // resize on the next frame so we always get the real container dimensions.
    this.zone.runOutsideAngular(() => {
      setTimeout(() => ec.resize(), 0);
      if (typeof ResizeObserver !== 'undefined') {
        const el = ec.getDom() as HTMLElement;
        this._resizeObs?.disconnect();
        this._resizeObs = new ResizeObserver(() => ec.resize());
        this._resizeObs.observe(el);
      }
    });
  }

  ngOnDestroy(): void {
    this._resizeObs?.disconnect();
  }

  setSource(src: string | null): void {
    this.activeSource.set(src);
    this.load(false);
  }

  load(refresh: boolean): void {
    this.loading.set(true);
    const params: string[] = [];
    if (refresh) params.push('refresh=true');
    const src = this.activeSource();
    if (src) params.push(`source=${src}`);
    const qs = params.length ? '?' + params.join('&') : '';
    this.http.get<TopologyGraph>(`${environment.apiUrl}/topology/graph${qs}`).subscribe({
      next: g => {
        this.graph.set(g);
        this.loading.set(false);
        // The echarts div only appears after graph is set; give Angular one tick
        // to render it, then resize so the canvas fills the full flex container.
        setTimeout(() => this._echart?.resize(), 50);
      },
      error: () => { this.loading.set(false); },
    });
  }

  onNodeClick(event: any): void {
    if (event.dataType !== 'node') return;
    const type = event.data?.nodeType;
    if (type === 'host' || type === 'vm') {
      const host = event.data.id;
      window.open(
        '/cockpit/' + encodeURIComponent(host),
        'cockpit-' + host,
        'width=1300,height=820,menubar=no,toolbar=no,location=no,status=no',
      );
    }
  }

  triggerKbSync(): void {
    this.syncing.set(true);
    this.http.post(`${environment.apiUrl}/topology/extract-kb`, {}).subscribe({
      next: () => {
        this.snack.open('KB-Extraktion gestartet', 'OK', { duration: 3000 });
        this.syncing.set(false);
      },
      error: () => {
        this.snack.open('Fehler beim Starten der KB-Extraktion', 'OK', { duration: 4000 });
        this.syncing.set(false);
      },
    });
  }
}
