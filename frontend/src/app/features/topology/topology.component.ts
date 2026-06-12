import { Component, OnInit, inject, signal, computed } from '@angular/core';
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
  site: 30, cluster: 22, host: 14, vm: 9, service: 12,
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
          Infrastructure Map
        </h2>

        @if (graph()?.stats; as s) {
          <div class="topo-stats">
            <span class="stat-chip">{{ s.sites }} Sites</span>
            <span class="stat-chip">{{ s.clusters }} Clusters</span>
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
          placeholder="Search nodes…"
          matTooltip="Filter nodes — non-matching nodes are dimmed"
        />

        @if (isSysAdmin()) {
          <button mat-stroked-button class="topo-kbsync" (click)="triggerKbSync()" [disabled]="syncing()">
            @if (syncing()) { <mat-spinner diameter="16"></mat-spinner> }
            @else { <mat-icon>sync</mat-icon> }
            KB Sync
          </button>
        }

        <button mat-flat-button class="topo-refresh" (click)="load(true)" [disabled]="loading()">
          @if (loading()) { <mat-spinner diameter="18"></mat-spinner> }
          @else { <mat-icon>refresh</mat-icon> }
          Refresh
        </button>
      </div>

      @if (graph()?.error) {
        <div class="topo-empty">
          <mat-icon>lan</mat-icon>
          <p>{{ graph()!.error }}</p>
          <p class="topo-hint">Configure the NetBox connector under Settings → Connectors</p>
        </div>
      } @else if (loading() && !graph()) {
        <div class="topo-loading">
          <mat-spinner diameter="48"></mat-spinner>
          <p>Loading infrastructure data…</p>
        </div>
      } @else if (graph()?.nodes?.length === 0) {
        <div class="topo-empty">
          <mat-icon>hub</mat-icon>
          <p>No nodes found — is NetBox data available?</p>
        </div>
      } @else {
        <div
          echarts
          [options]="chartOptions()"
          class="topo-chart"
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
    :host-context(html.cs-theme-holo) .topo-search {
      background: #040e1a; color: #5fc8ee; border-color: #1a3a5c;
    }
  `],
})
export class TopologyComponent implements OnInit {
  private http = inject(HttpClient);
  private themeSvc = inject(ThemeService);
  private auth = inject(AuthService);
  private snack = inject(MatSnackBar);

  graph = signal<TopologyGraph | null>(null);
  loading = signal(true);
  syncing = signal(false);
  searchTerm = '';
  searchFilter = signal('');

  isSysAdmin = computed(() => {
    const role = this.auth.userRole();
    return role === 'admin' || role === 'sysadmin';
  });

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

    return {
      backgroundColor: 'transparent',
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
        force: {
          repulsion: 90,
          edgeLength: [40, 130],
          gravity: 0.08,
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
          symbolSize: (NODE_SIZE[n.type] ?? 10) + Math.min(n.alert_count * 2, 14),
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

  load(refresh: boolean): void {
    this.loading.set(true);
    const url = `${environment.apiUrl}/topology/graph${refresh ? '?refresh=true' : ''}`;
    this.http.get<TopologyGraph>(url).subscribe({
      next: g => { this.graph.set(g); this.loading.set(false); },
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
        this.snack.open('KB extraction started', 'OK', { duration: 3000 });
        this.syncing.set(false);
      },
      error: () => {
        this.snack.open('Failed to start KB extraction', 'OK', { duration: 4000 });
        this.syncing.set(false);
      },
    });
  }
}
