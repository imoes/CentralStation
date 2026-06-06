import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  OnDestroy,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient, HttpParams } from '@angular/common/http';
import { ActivatedRoute, Router } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatTooltipModule } from '@angular/material/tooltip';
import { NgxEchartsDirective } from 'ngx-echarts';
import { ThemeService } from '../../core/services/theme.service';
import { environment } from '../../../environments/environment';

interface Vital {
  metric: string;
  label: string;
  value: number;
  unit: string;
  level: 'crit' | 'high' | 'ok';
  service: string;
  series: { time: string; value: number }[];
}

interface Message {
  id: string;
  external_id: string;
  severity: string;
  title: string;
  source: string;
  created_at: string;
  ai_insight: string;
}

interface HealthResponse {
  host: string;
  vitals: Vital[];
  messages: Message[];
  live: boolean;
}

interface Service {
  name: string;
  state: number;
  state_label: 'OK' | 'WARN' | 'CRIT' | 'UNKNOWN';
  summary: string;
}

interface ServicesResponse {
  host: string;
  services: Service[];
  counts: { crit: number; warn: number; unknown: number; ok: number; total: number };
}

interface GraphResponse {
  series: { time: string; value: number }[];
  title: string;
  unit: string;
  error: string;
}

const SVC_STATE_COLORS: Record<string, string> = {
  CRIT:    '#ff4433',
  WARN:    '#ffcc00',
  OK:      '#66cc66',
  UNKNOWN: '#99CCFF',
};

const SVC_STATES = ['all', 'errors', 'CRIT', 'WARN', 'UNKNOWN'];

const SEV_COLORS: Record<string, string> = {
  critical: '#ff4433',
  high:     '#ffcc00',
  medium:   '#FF9933',
  warning:  '#ffcc00',
  low:      '#99CCFF',
  info:     '#66cc66',
};

const SEVERITIES = ['all', 'critical', 'high', 'medium', 'low', 'info'];

@Component({
  selector: 'cs-cockpit',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, FormsModule, MatIconModule, MatButtonModule, MatTooltipModule, NgxEchartsDirective],
  template: `
    <!-- Top LCARS cap bar -->
    <div class="cap-bar top">
      <div class="cap-tl"></div>
      <span class="cap-title">COCKPIT&nbsp;—&nbsp;{{ hostname() }}</span>
      <div class="cap-spacer"></div>
      @if (liveRefreshed()) {
        <span class="badge-live">LIVE ●</span>
      } @else if (loading()) {
        <span class="badge-loading">INIT…</span>
      }
      <button class="cap-close" (click)="close()" matTooltip="Fenster schließen">✕</button>
      <div class="cap-tr"></div>
    </div>

    <div class="cockpit-body">

      <!-- ── PERFORMANCE BLOCK ── -->
      <div class="block">
        <div class="block-head">
          <span>PERFORMANCE</span>
          @if (vitals().length === 0 && !loading()) {
            <span class="block-hint">Keine Metriken verfügbar</span>
          }
        </div>

        @if (baseVitals().length > 0 || filesystemVitals().length > 0) {
          <div class="gauges-row">
            @for (vital of baseVitals(); track vital.metric) {
              <div class="gauge-cell">
                <div class="gauge-label">{{ vital.label }}</div>
                <div echarts [options]="gaugeOptions(vital)" class="gauge-chart"></div>
                @if (vital.series.length > 1) {
                  <div echarts [options]="sparklineOptions(vital)" class="sparkline-chart"></div>
                }
                <div class="gauge-meta">
                  <span class="gauge-value" [attr.data-level]="vital.level">{{ vital.value }}{{ vital.unit }}</span>
                  <span class="gauge-level" [attr.data-level]="vital.level">{{ vital.level.toUpperCase() }}</span>
                </div>
              </div>
            }
            @for (vital of filesystemVitals(); track vital.metric) {
              <div class="gauge-cell">
                <div class="gauge-label">{{ vital.label }}</div>
                <div echarts [options]="gaugeOptions(vital)" class="gauge-chart"></div>
                <div class="gauge-meta">
                  <span class="gauge-value" [attr.data-level]="vital.level">{{ vital.value }}{{ vital.unit }}</span>
                  <span class="gauge-level" [attr.data-level]="vital.level">{{ vital.level.toUpperCase() }}</span>
                </div>
              </div>
            }
          </div>
        } @else if (loading()) {
          <div class="block-loading">METRIKEN WERDEN GELADEN…</div>
        }
      </div>

      <!-- ── SERVICES BLOCK ── -->
      <div class="block">
        <div class="block-head">
          <span>SERVICES</span>
          <span class="block-count">{{ serviceCounts().total }}</span>
          @if (serviceCounts().crit > 0) {
            <span class="count-pill crit">{{ serviceCounts().crit }} CRIT</span>
          }
          @if (serviceCounts().warn > 0) {
            <span class="count-pill warn">{{ serviceCounts().warn }} WARN</span>
          }
          <span class="count-pill ok">{{ serviceCounts().ok }} OK</span>
          <div class="block-filters">
            @for (st of svcStates; track st) {
              <button
                class="filter-chip"
                [class.active]="stateFilter() === st"
                [class.zero]="stateCount(st) === 0 && st !== 'all' && st !== 'errors'"
                (click)="setStateFilter(st)"
              >{{ st === 'all' ? 'ALLE' : st === 'errors' ? 'FEHLER' : st }}</button>
            }
          </div>
        </div>

        <div class="services-area">
          @if (servicesLoading()) {
            <div class="block-loading">SERVICES WERDEN GELADEN…</div>
          } @else if (filteredServices().length === 0) {
            <div class="alert-empty">
              @if (serviceCounts().total === 0) { Keine CheckMK-Services für diesen Host. }
              @else { Keine Services in dieser Auswahl. }
            </div>
          } @else {
            <div class="services-grid">
              @for (svc of filteredServices(); track svc.name) {
                <div class="svc-cell" [class.expanded]="expandedService() === svc.name">
                  <div class="svc-row" (click)="toggleService(svc)">
                    <span class="svc-dot" [style.background]="svcColor(svc.state_label)"></span>
                    <span class="svc-state" [style.color]="svcColor(svc.state_label)">{{ svc.state_label }}</span>
                    <div class="svc-info">
                      <span class="svc-name">{{ svc.name }}</span>
                      @if (svc.summary) {
                        <span class="svc-summary">{{ svc.summary }}</span>
                      }
                    </div>
                  </div>
                  @if (expandedService() === svc.name) {
                    <div class="svc-graph">
                      @if (graphLoading()) {
                        <div class="svc-graph-msg">GRAPH WIRD GELADEN…</div>
                      } @else if (serviceGraph() && serviceGraph()!.series.length > 0) {
                        <div echarts [options]="graphOptions()" class="svc-graph-chart"></div>
                      } @else {
                        <div class="svc-graph-msg">Keine Graph-Daten verfügbar.</div>
                      }
                    </div>
                  }
                </div>
              }
            </div>
          }
        </div>
      </div>

      <!-- ── ALERTS BLOCK ── -->
      <div class="block">
        <div class="block-head blue">
          <span>ALERTS &amp; MELDUNGEN</span>
          <span class="block-count">{{ filteredMessages().length }}</span>
          <div class="block-filters">
            @for (sev of severities; track sev) {
              <button
                class="filter-chip"
                [class.active]="severityFilter() === sev"
                (click)="setSeverity(sev)"
              >{{ sev.toUpperCase() }}</button>
            }
          </div>
        </div>

        @if (availableSources().length > 2) {
          <div class="source-filters">
            <span class="source-label">QUELLE</span>
            @for (src of availableSources(); track src) {
              <button
                class="filter-chip src"
                [class.active]="sourceFilter() === src"
                (click)="setSource(src)"
              >{{ src.toUpperCase() }}</button>
            }
          </div>
        }

        <div class="alert-list">
          @if (filteredMessages().length === 0) {
            <div class="alert-empty">
              @if (loading()) { LADE MELDUNGEN… }
              @else { Keine Meldungen für diesen Host. }
            </div>
          }
          @for (msg of filteredMessages(); track msg.id) {
            <div class="alert-row" [attr.data-severity]="msg.severity" (click)="openInFeed(msg)">
              <span class="sev-dot" [style.background]="sevColor(msg.severity)"></span>
              <span class="alert-severity">{{ msg.severity.toUpperCase() }}</span>
              <span class="alert-title">{{ msg.title }}</span>
              <span class="alert-source">{{ msg.source }}</span>
              <span class="alert-time">{{ relativeTime(msg.created_at) }}</span>
            </div>
          }
        </div>
      </div>

    </div>

    <!-- Bottom LCARS cap bar -->
    <div class="cap-bar bottom">
      <div class="cap-bl"></div>
      <span class="cap-bottom-label">CENTRALSTATION · SERVER COCKPIT</span>
      <div class="cap-spacer"></div>
      <span class="cap-host-id">{{ hostname() }}</span>
      <div class="cap-br"></div>
    </div>
  `,
  styles: [`
    :host {
      display: block;
      min-height: 100vh;
      background: #000;
      color: #ffe8a0;
      font-family: Roboto, 'Helvetica Neue', sans-serif;
      padding-bottom: 44px;
    }

    /* ── Cap Bars ── */
    .cap-bar {
      background: #FF9933;
      color: #000;
      font-weight: 700;
      font-size: 14px;
      letter-spacing: .1em;
      text-transform: uppercase;
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 0 0 0 4px;
      height: 32px;
    }
    .cap-bar.bottom {
      position: fixed;
      bottom: 0; left: 0; right: 0;
      background: #FF9933;
      z-index: 100;
      height: 28px;
      font-size: 11px;
    }
    .cap-tl { width: 32px; height: 32px; background: #000; border-radius: 0 0 0 0; flex-shrink: 0; }
    .cap-tr { width: 20px; height: 32px; background: #000; flex-shrink: 0; }
    .cap-bl { width: 20px; height: 28px; background: #000; border-radius: 0 0 0 18px; flex-shrink: 0; }
    .cap-br { width: 20px; height: 28px; background: #000; border-radius: 0 0 18px 0; flex-shrink: 0; }
    .cap-title { font-size: 16px; font-weight: 700; letter-spacing: .12em; white-space: nowrap; }
    .cap-spacer { flex: 1; }
    .cap-host-id { font-size: 11px; opacity: .7; }
    .cap-bottom-label { font-size: 11px; }
    .badge-live {
      background: #000; color: #66cc66;
      font-size: 11px; padding: 2px 10px; border-radius: 99px;
      font-weight: 700; letter-spacing: .06em;
      animation: pulse 2s infinite;
    }
    .badge-loading {
      background: #000; color: #ffcc66;
      font-size: 11px; padding: 2px 10px; border-radius: 99px;
    }
    .cap-close {
      background: transparent; border: none; color: #000;
      font-size: 16px; cursor: pointer; padding: 0 12px;
      font-weight: 700; line-height: 32px;
    }
    .cap-close:hover { background: rgba(0,0,0,.15); }

    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: .5; }
    }

    /* ── Body ── */
    .cockpit-body {
      padding: 12px 16px 12px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    /* ── Blocks ── */
    .block {
      background: #0a0804;
      border: 1px solid #3a2810;
      border-radius: 0 12px 12px 12px;
      overflow: hidden;
    }
    .block-head {
      background: #FF9933;
      color: #000;
      padding: 5px 14px;
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
      font-size: 13px;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .block-head.blue { background: #99CCFF; }
    .block-count {
      background: #000; color: #99CCFF;
      font-size: 11px; padding: 1px 8px; border-radius: 99px;
    }
    .block-hint { font-size: 11px; opacity: .65; }
    .block-loading {
      padding: 28px 16px;
      color: #e8a060;
      font-size: 12px;
      letter-spacing: .08em;
      text-align: center;
    }

    /* ── Gauges ── */
    .gauges-row {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
      gap: 8px;
      padding: 12px 16px;
    }
    .gauge-cell {
      display: flex;
      flex-direction: column;
      align-items: center;
      background: #0f0c08;
      border: 1px solid #2a1d0a;
      border-radius: 8px;
      padding: 8px 4px 6px;
    }
    .gauge-label {
      font-size: 10px;
      color: #FFCC99;
      text-transform: uppercase;
      letter-spacing: .1em;
      margin-bottom: 2px;
    }
    .gauge-chart { width: 100%; height: 130px; }
    .sparkline-chart { width: 100%; height: 46px; margin-top: -8px; }
    .gauge-meta {
      display: flex;
      gap: 8px;
      align-items: center;
      margin-top: 4px;
    }
    .gauge-value {
      font-size: 15px;
      font-weight: 700;
      color: #ffe8a0;
    }
    .gauge-value[data-level="crit"] { color: #ff4433; }
    .gauge-value[data-level="high"] { color: #ffcc00; }
    .gauge-value[data-level="ok"]   { color: #66cc66; }
    .gauge-level {
      font-size: 9px;
      padding: 1px 6px;
      border-radius: 3px;
      background: #1a1208;
      letter-spacing: .06em;
    }
    .gauge-level[data-level="crit"] { color: #ff4433; border: 1px solid #ff4433; }
    .gauge-level[data-level="high"] { color: #ffcc00; border: 1px solid #ffcc00; }
    .gauge-level[data-level="ok"]   { color: #66cc66; border: 1px solid #66cc66; }

    /* ── Services ── */
    .count-pill {
      font-size: 10px;
      font-weight: 700;
      padding: 1px 8px;
      border-radius: 99px;
      background: #000;
      letter-spacing: .04em;
    }
    .count-pill.crit { color: #ff4433; }
    .count-pill.warn { color: #ffcc00; }
    .count-pill.ok   { color: #66cc66; }

    .services-area { max-height: 52vh; overflow-y: auto; }
    .services-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 1px;
      padding: 6px;
    }
    .svc-cell {
      background: #0f0c08;
      border: 1px solid #1e1710;
    }
    .svc-cell.expanded { grid-column: 1 / -1; border-color: #3a2810; }
    .svc-row {
      display: flex;
      align-items: flex-start;
      gap: 8px;
      padding: 6px 10px;
      cursor: pointer;
      transition: background .1s;
    }
    .svc-row:hover { background: #1a1208; }
    .svc-dot {
      width: 8px; height: 8px;
      border-radius: 50%;
      flex-shrink: 0;
      margin-top: 3px;
    }
    .svc-state {
      font-size: 9px;
      font-weight: 700;
      letter-spacing: .04em;
      width: 48px;
      flex-shrink: 0;
      padding-top: 1px;
    }
    .svc-info {
      flex: 1;
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .svc-name {
      font-size: 12px;
      font-weight: 600;
      color: #ffe8a0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .svc-summary {
      font-size: 11px;
      color: #e8a060;
      font-family: inherit;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .svc-graph {
      border-top: 1px solid #2a1d0a;
      padding: 6px 10px 8px;
      background: #0a0804;
    }
    .svc-graph-chart { width: 100%; height: 130px; }
    .svc-graph-msg {
      padding: 16px;
      text-align: center;
      font-size: 11px;
      color: #e8a060;
      letter-spacing: .06em;
    }

    /* ── Alerts ── */
    .block-filters {
      display: flex;
      gap: 4px;
      flex-wrap: wrap;
      margin-left: auto;
    }
    .filter-chip {
      background: transparent;
      border: 1px solid #000;
      color: #000;
      border-radius: 3px;
      padding: 1px 8px;
      font-size: 10px;
      cursor: pointer;
      font-weight: 700;
      letter-spacing: .05em;
      font-family: inherit;
      opacity: .6;
      transition: opacity .15s;
    }
    .filter-chip.active, .filter-chip:hover { opacity: 1; background: rgba(0,0,0,.2); }
    .filter-chip.zero { opacity: .25; cursor: default; pointer-events: none; }

    /* Source filter row (below the block head) */
    .source-filters {
      display: flex;
      align-items: center;
      gap: 4px;
      flex-wrap: wrap;
      padding: 6px 14px;
      background: #0f0c08;
      border-bottom: 1px solid #2a1d0a;
    }
    .source-label {
      font-size: 9px;
      font-weight: 700;
      letter-spacing: .1em;
      color: #FFCC99;
      margin-right: 4px;
    }
    .filter-chip.src {
      border-color: #FFCC99;
      color: #FFCC99;
      opacity: .55;
    }
    .filter-chip.src.active, .filter-chip.src:hover {
      opacity: 1;
      background: #FFCC99;
      color: #000;
    }

    .alert-list {
      max-height: 42vh;
      overflow-y: auto;
    }
    .alert-empty {
      padding: 20px 16px;
      color: #5a3a18;
      font-size: 12px;
      letter-spacing: .06em;
      text-align: center;
    }
    .alert-row {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 7px 14px;
      border-bottom: 1px solid #1e1710;
      cursor: pointer;
      transition: background .1s;
    }
    .alert-row:hover { background: #1a1208; }
    .sev-dot {
      width: 7px; height: 7px;
      border-radius: 50%;
      flex-shrink: 0;
    }
    .alert-severity {
      font-size: 9px;
      font-weight: 700;
      letter-spacing: .06em;
      color: #e8a060;
      width: 60px;
      flex-shrink: 0;
    }
    .alert-title {
      flex: 1;
      font-size: 12px;
      color: #ffe8a0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .alert-source {
      font-size: 10px;
      color: #FFCC99;
      width: 70px;
      flex-shrink: 0;
      text-align: right;
    }
    .alert-time {
      font-size: 10px;
      color: #5a3a18;
      width: 56px;
      flex-shrink: 0;
      text-align: right;
    }
  `],
})
export class CockpitComponent implements OnInit, OnDestroy {
  private http = inject(HttpClient);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private themeSvc = inject(ThemeService);

  hostname = signal('');
  vitals = signal<Vital[]>([]);
  allMessages = signal<Message[]>([]);
  loading = signal(true);
  liveRefreshed = signal(false);
  severityFilter = signal('all');
  sourceFilter = signal('all');

  // Services
  services = signal<Service[]>([]);
  serviceCounts = signal<ServicesResponse['counts']>({ crit: 0, warn: 0, unknown: 0, ok: 0, total: 0 });
  servicesLoading = signal(true);
  stateFilter = signal('errors');
  expandedService = signal<string | null>(null);
  serviceGraph = signal<GraphResponse | null>(null);
  graphLoading = signal(false);

  readonly severities = SEVERITIES;
  readonly svcStates = SVC_STATES;

  /** CPU + RAM vitals only — filesystems come from the services list (all mount paths). */
  readonly baseVitals = computed(() =>
    this.vitals().filter(v => v.metric !== 'fs_used_percent')
  );

  /** All Filesystem services from CheckMK converted to gauge-compatible objects. */
  readonly filesystemVitals = computed((): Vital[] => {
    const pctRe = /(\d+(?:\.\d+)?)\s*%/;
    return this.services()
      .filter(s => /^filesystem\s/i.test(s.name))
      .map(s => {
        const mount = s.name.replace(/^filesystem\s+/i, '') || '/';
        const match = s.summary?.match(pctRe);
        const value = match ? parseFloat(match[1]) : 0;
        const lvl: 'crit' | 'high' | 'ok' =
          s.state_label === 'CRIT' ? 'crit' :
          s.state_label === 'WARN' ? 'high' :
          value >= 90 ? 'crit' : value >= 75 ? 'high' : 'ok';
        return { metric: `fs:${mount}`, label: mount, value, unit: '%', level: lvl, service: s.name, series: [] };
      });
  });

  readonly filteredServices = computed(() => {
    const st = this.stateFilter();
    const svcs = this.services();
    if (st === 'all')    return svcs;
    if (st === 'errors') return svcs.filter(s => s.state_label !== 'OK');
    if (st === 'WARN')   return svcs.filter(s => s.state_label === 'CRIT' || s.state_label === 'WARN');
    if (st === 'CRIT')   return svcs.filter(s => s.state_label === 'CRIT');
    return svcs.filter(s => s.state_label === st);
  });

  /** Distinct sources present in the loaded messages, for the source filter chips. */
  readonly availableSources = computed(() => {
    const set = new Set<string>();
    for (const m of this.allMessages()) {
      if (m.source) set.add(m.source);
    }
    return ['all', ...Array.from(set).sort()];
  });

  readonly filteredMessages = computed(() => {
    const sev = this.severityFilter();
    const src = this.sourceFilter();
    return this.allMessages().filter(m =>
      (sev === 'all' || m.severity === sev) &&
      (src === 'all' || m.source === src),
    );
  });

  ngOnInit() {
    // Standalone full-screen window: hide the app navigation (same pattern as the bridge).
    document.body.classList.add('cockpit-active');

    const host = this.route.snapshot.paramMap.get('hostname') ?? '';
    this.hostname.set(host);

    // Load cached first
    this.http.get<HealthResponse>(`${environment.apiUrl}/hosts/${encodeURIComponent(host)}/health`)
      .subscribe({
        next: data => {
          this.vitals.set(data.vitals);
          this.allMessages.set(data.messages);
          this.loading.set(false);
          // Then load live
          this.http.get<HealthResponse>(`${environment.apiUrl}/hosts/${encodeURIComponent(host)}/health?live=true`)
            .subscribe({
              next: liveData => {
                this.vitals.set(liveData.vitals);
                this.allMessages.set(liveData.messages);
                this.liveRefreshed.set(true);
              },
              error: () => { /* keep cached data */ },
            });
        },
        error: () => this.loading.set(false),
      });

    // Load the full CheckMK services list in parallel
    this.http.get<ServicesResponse>(`${environment.apiUrl}/hosts/${encodeURIComponent(host)}/services`)
      .subscribe({
        next: data => {
          this.services.set(data.services);
          this.serviceCounts.set(data.counts);
          this.servicesLoading.set(false);
        },
        error: () => this.servicesLoading.set(false),
      });
  }

  ngOnDestroy() {
    document.body.classList.remove('cockpit-active');
  }

  setSeverity(sev: string) {
    this.severityFilter.set(sev);
  }

  setSource(src: string) {
    this.sourceFilter.set(src);
  }

  setStateFilter(st: string) {
    this.stateFilter.set(st);
  }

  stateCount(st: string): number {
    const c = this.serviceCounts();
    switch (st) {
      case 'CRIT':    return c.crit;
      case 'WARN':    return c.crit + c.warn;
      case 'UNKNOWN': return c.unknown;
      case 'errors':  return c.crit + c.warn + c.unknown;
      default:       return c.total;
    }
  }

  svcColor(stateLabel: string): string {
    return SVC_STATE_COLORS[stateLabel] ?? '#888';
  }

  /** Toggle the inline graph for a service, loading its time series on demand. */
  toggleService(svc: Service) {
    if (this.expandedService() === svc.name) {
      this.expandedService.set(null);
      this.serviceGraph.set(null);
      return;
    }
    this.expandedService.set(svc.name);
    this.serviceGraph.set(null);
    this.graphLoading.set(true);
    const host = this.hostname();
    const metric = this.inferMetric(svc.name);
    const url = `${environment.apiUrl}/hosts/${encodeURIComponent(host)}/graph`
      + `?service=${encodeURIComponent(svc.name)}`
      + (metric ? `&metric=${encodeURIComponent(metric)}` : '');
    this.http.get<GraphResponse>(url).subscribe({
      next: data => {
        // Ignore if the user already collapsed/switched while loading
        if (this.expandedService() === svc.name) {
          this.serviceGraph.set(data);
          this.graphLoading.set(false);
        }
      },
      error: () => {
        if (this.expandedService() === svc.name) {
          this.serviceGraph.set({ series: [], title: svc.name, unit: '', error: 'load failed' });
          this.graphLoading.set(false);
        }
      },
    });
  }

  private inferMetric(serviceName: string): string {
    const n = serviceName.toLowerCase();
    if (n.startsWith('filesystem') || n.startsWith('fs ') || n.includes('disk')) return 'fs_used_percent';
    if (n.includes('memory') || n === 'mem') return 'mem_used_percent';
    if (n.includes('cpu') || n.includes('load')) return 'load1';
    return '';
  }

  sevColor(severity: string): string {
    return SEV_COLORS[severity] ?? '#888';
  }

  openInFeed(msg: Message) {
    window.opener?.open(`/feed?host=${encodeURIComponent(this.hostname())}`, '_self');
    window.close();
  }

  close() {
    window.close();
  }

  relativeTime(iso: string): string {
    if (!iso) return '';
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'gerade';
    if (mins < 60) return `${mins}m`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h`;
    return `${Math.floor(hrs / 24)}d`;
  }

  gaugeOptions(vital: Vital) {
    const color = vital.level === 'crit' ? '#ff4433' : vital.level === 'high' ? '#ffcc00' : '#66cc66';
    const max = vital.metric === 'load1' ? 16 : 100;
    const value = vital.metric === 'load1' ? vital.value : vital.value;
    return {
      backgroundColor: 'transparent',
      series: [{
        type: 'gauge',
        radius: '88%',
        startAngle: 210,
        endAngle: -30,
        min: 0,
        max,
        splitNumber: 5,
        axisLine: {
          lineStyle: {
            width: 10,
            color: [
              [value / max, color],
              [1, '#1e1710'],
            ],
          },
        },
        pointer: {
          length: '55%',
          width: 4,
          itemStyle: { color },
        },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
        detail: {
          formatter: `{value}${vital.unit}`,
          color,
          fontSize: 18,
          fontWeight: 'bold',
          fontFamily: 'Antonio, Eurostile, monospace',
          offsetCenter: [0, '60%'],
        },
        title: { show: false },
        data: [{ value: vital.metric === 'load1' ? vital.value : vital.value, name: vital.label }],
      }],
    };
  }

  sparklineOptions(vital: Vital) {
    const color = vital.level === 'crit' ? '#ff4433' : vital.level === 'high' ? '#ffcc00' : '#66cc66';
    return {
      backgroundColor: 'transparent',
      grid: { left: 0, right: 0, top: 2, bottom: 0 },
      xAxis: {
        type: 'category' as const,
        show: false,
        data: vital.series.map(p => p.time),
        boundaryGap: false,
      },
      yAxis: { type: 'value' as const, show: false },
      series: [{
        type: 'line' as const,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1.5, color },
        areaStyle: { color, opacity: 0.12 },
        data: vital.series.map(p => p.value),
      }],
    };
  }

  /** ECharts options for the on-demand service graph (24h time series). */
  graphOptions() {
    const g = this.serviceGraph();
    const series = g?.series ?? [];
    const color = '#FFCC99';
    return {
      backgroundColor: 'transparent',
      grid: { left: 44, right: 12, top: 10, bottom: 22 },
      tooltip: {
        trigger: 'axis' as const,
        backgroundColor: '#1a1208',
        borderColor: '#3a2810',
        textStyle: { color: '#ffe8a0', fontSize: 11 },
      },
      xAxis: {
        type: 'category' as const,
        data: series.map(p => p.time),
        boundaryGap: false,
        axisLine: { lineStyle: { color: '#3a2810' } },
        axisLabel: {
          color: '#e8a060',
          fontSize: 9,
          formatter: (v: string) => {
            const d = new Date(v);
            return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
          },
        },
      },
      yAxis: {
        type: 'value' as const,
        axisLine: { show: false },
        axisLabel: { color: '#e8a060', fontSize: 9 },
        splitLine: { lineStyle: { color: '#1e1710' } },
      },
      series: [{
        type: 'line' as const,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1.5, color },
        areaStyle: { color, opacity: 0.1 },
        data: series.map(p => p.value),
      }],
    };
  }
}
