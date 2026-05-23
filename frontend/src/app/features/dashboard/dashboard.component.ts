import { Component, OnInit, OnDestroy, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { RouterModule } from '@angular/router';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { NgxEchartsDirective, provideEchartsCore } from 'ngx-echarts';
import * as echarts from 'echarts/core';
import { BarChart } from 'echarts/charts';
import { TitleComponent, TooltipComponent, GridComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { Subject, takeUntil, interval } from 'rxjs';
import { AlertService } from '../../core/services/alert.service';
import { WebsocketService, WsMessage } from '../../core/services/websocket.service';
import { AlertSummary } from '../../core/models/alert.model';
import { environment } from '../../../environments/environment';

echarts.use([BarChart, TitleComponent, TooltipComponent, GridComponent, CanvasRenderer]);

const SEV_COLOR: Record<string, string> = {
  critical: '#d32f2f',
  high:     '#f57c00',
  medium:   '#1976d2',
  low:      '#388e3c',
  info:     '#607d8b',
};

interface AiAnalysis {
  id: string;
  run_at: string;
  severity_summary: string;
  findings: Array<{ title: string; severity: string; description?: string }>;
  recommendations: Array<{ title: string; priority?: string; description?: string }>;
  findings_count: number;
  recommendations_count: number;
}

interface FeedItem {
  id: string;
  source: string;
  severity: string;
  title: string;
  host?: string;
  created_at: string;
  ai_insight?: string;
  metadata_?: Record<string, unknown>;
}

@Component({
  selector: 'cs-dashboard',
  standalone: true,
  imports: [
    CommonModule, RouterModule,
    MatCardModule, MatButtonModule, MatIconModule, MatProgressSpinnerModule, MatTooltipModule,
    NgxEchartsDirective,
  ],
  providers: [provideEchartsCore({ echarts })],
  template: `
    <div class="dashboard">

      <!-- ── Zeile 1: Health-Ampel ──────────────────────────────────────── -->
      <div class="ampel-row">

        <mat-card class="ampel-card" [class.ampel-alert]="getSummaryCount('critical') > 0">
          <div class="ampel-icon-wrap"><mat-icon class="ampel-icon crit-icon">crisis_alert</mat-icon></div>
          <div class="ampel-count crit-color">{{ getSummaryCount('critical') }}</div>
          <div class="ampel-label">KRITISCH</div>
        </mat-card>

        <mat-card class="ampel-card">
          <div class="ampel-icon-wrap"><mat-icon class="ampel-icon high-icon">warning</mat-icon></div>
          <div class="ampel-count high-color">{{ getSummaryCount('high') }}</div>
          <div class="ampel-label">HOCH</div>
        </mat-card>

        <mat-card class="ampel-card">
          <div class="ampel-icon-wrap"><mat-icon class="ampel-icon med-icon">info</mat-icon></div>
          <div class="ampel-count med-color">{{ getSummaryCount('medium') }}</div>
          <div class="ampel-label">MITTEL</div>
        </mat-card>

        <mat-card class="ampel-card">
          <div class="ampel-icon-wrap"><mat-icon class="ampel-icon ok-icon">check_circle</mat-icon></div>
          <div class="ampel-count ok-color">{{ totalAlerts() }}</div>
          <div class="ampel-label">GESAMT</div>
        </mat-card>

        <!-- KI-Lagebericht summary card -->
        <mat-card class="ampel-card ki-lage-mini">
          <div class="ki-lage-top">
            <mat-icon class="ki-brain-icon">psychology</mat-icon>
            <span class="ki-lage-title">KI-Lagebericht</span>
            @if (latestAnalysis()) {
              <span class="ki-age">{{ analysisAge() }}</span>
            }
          </div>
          @if (loadingAnalysis()) {
            <mat-spinner diameter="20"></mat-spinner>
          } @else if (latestAnalysis()) {
            <div class="ki-summary-text">{{ latestAnalysis()!.severity_summary }}</div>
          } @else {
            <div class="ki-no-data">Noch keine Analyse vorhanden</div>
          }
          <div class="ki-lage-actions">
            <button mat-button color="primary" [routerLink]="['/ai-insights']" class="ki-detail-btn">
              Vollständige Analyse
              <mat-icon iconPositionEnd>arrow_forward</mat-icon>
            </button>
            <button mat-icon-button (click)="triggerAgent()" [disabled]="triggeringAgent()"
                    matTooltip="KI-Agent jetzt auslösen">
              @if (triggeringAgent()) { <mat-spinner diameter="18"></mat-spinner> }
              @else { <mat-icon>play_circle</mat-icon> }
            </button>
          </div>
        </mat-card>

      </div>

      <!-- ── Zeile 2: Alert-Feed + KI-Detail + Top-Hosts ─────────────────── -->
      <div class="main-row">

        <!-- Alert Feed -->
        <mat-card class="feed-card">
          <mat-card-header>
            <mat-card-title>
              <mat-icon style="vertical-align:middle;margin-right:6px">rss_feed</mat-icon>
              Aktive Alerts
            </mat-card-title>
            <mat-card-subtitle>Letzte 15 Meldungen — alle Quellen</mat-card-subtitle>
          </mat-card-header>
          <mat-card-content>
            @if (loadingFeed()) {
              <div class="spinner-center"><mat-spinner diameter="30"></mat-spinner></div>
            } @else if (feedItems().length === 0) {
              <div class="empty-state">
                <mat-icon>check_circle_outline</mat-icon>
                <span>Keine aktiven Alerts — alles grün!</span>
              </div>
            } @else {
              <div class="feed-list">
                @for (item of feedItems(); track item.id) {
                  <div class="feed-item">
                    <span class="sev-dot" [style.background-color]="sevColor(item.severity)"></span>
                    <div class="feed-item-body">
                      <div class="feed-item-top">
                        <span class="source-chip">{{ item.source }}</span>
                        @if (itemHost(item)) {
                          <span class="host-chip">{{ itemHost(item) }}</span>
                        }
                        <span class="feed-time">{{ item.created_at | date:'dd.MM HH:mm' }}</span>
                      </div>
                      <div class="feed-title">{{ item.title }}</div>
                      @if (item.ai_insight) {
                        <div class="feed-insight">
                          <mat-icon class="insight-icon">psychology</mat-icon>
                          <span>{{ item.ai_insight }}</span>
                        </div>
                      }
                    </div>
                  </div>
                }
              </div>
            }
          </mat-card-content>
        </mat-card>

        <!-- Right column -->
        <div class="right-col">

          <!-- KI-Detail Card -->
          @if (latestAnalysis()) {
            <mat-card class="ki-detail-card">
              <mat-card-header>
                <mat-card-title>
                  <mat-icon style="vertical-align:middle;margin-right:6px">psychology</mat-icon>
                  KI-Analyse — Findings
                </mat-card-title>
              </mat-card-header>
              <mat-card-content>
                @for (f of latestAnalysis()!.findings.slice(0,5); track f.title) {
                  <div class="finding-item">
                    <span class="sev-dot" [style.background-color]="sevColor(f.severity)"></span>
                    <div>
                      <div class="finding-title">{{ f.title }}</div>
                      @if (f.description) {
                        <div class="finding-desc">{{ f.description }}</div>
                      }
                    </div>
                  </div>
                }
                @if (latestAnalysis()!.recommendations.length > 0) {
                  <div class="rec-header">
                    <mat-icon style="font-size:14px;vertical-align:middle">lightbulb</mat-icon>
                    Empfehlungen
                  </div>
                  @for (r of latestAnalysis()!.recommendations.slice(0,3); track r.title) {
                    <div class="rec-item">{{ r.title }}</div>
                  }
                }
              </mat-card-content>
            </mat-card>
          }

          <!-- Top Problem Hosts Chart -->
          <mat-card class="hosts-card">
            <mat-card-header>
              <mat-card-title>
                <mat-icon style="vertical-align:middle;margin-right:6px">dns</mat-icon>
                Top Problem-Hosts
              </mat-card-title>
            </mat-card-header>
            <mat-card-content>
              @if (topHostsData().length > 0) {
                <div echarts [options]="hostsChartOptions()" class="hosts-chart"></div>
              } @else {
                <div class="empty-state small">
                  <mat-icon>check_circle_outline</mat-icon>
                  <span>Keine Problem-Hosts</span>
                </div>
              }
            </mat-card-content>
          </mat-card>

        </div>
      </div>

    </div>
  `,
  styles: [`
    .dashboard { padding: 16px; display: flex; flex-direction: column; gap: 16px; }

    /* ── Ampel row ── */
    .ampel-row { display: flex; gap: 12px; flex-wrap: wrap; align-items: stretch; }
    .ampel-card {
      flex: 1; min-width: 110px; padding: 12px 16px; text-align: center;
      display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px;
      transition: box-shadow .2s;
    }
    .ampel-card.ampel-alert { box-shadow: 0 0 0 2px #d32f2f40; }
    .ampel-icon-wrap { line-height: 1; }
    .ampel-icon { font-size: 22px; width: 22px; height: 22px; }
    .ampel-count { font-size: 28px; font-weight: 700; line-height: 1.1; }
    .ampel-label { font-size: 10px; text-transform: uppercase; letter-spacing: .5px; color: var(--mat-sys-on-surface-variant); }
    .crit-icon, .crit-color { color: #d32f2f; }
    .high-icon, .high-color { color: #f57c00; }
    .med-icon,  .med-color  { color: #1976d2; }
    .ok-icon,   .ok-color   { color: #388e3c; }

    /* KI-Lage mini card */
    .ki-lage-mini {
      flex: 3; min-width: 260px; padding: 12px 16px;
      display: flex; flex-direction: column; gap: 6px;
    }
    .ki-lage-top { display: flex; align-items: center; gap: 6px; }
    .ki-brain-icon { color: var(--mat-sys-primary); font-size: 18px; width: 18px; height: 18px; }
    .ki-lage-title { font-weight: 600; font-size: 13px; }
    .ki-age { font-size: 11px; color: var(--mat-sys-on-surface-variant); margin-left: auto; }
    .ki-summary-text { font-size: 12px; color: var(--mat-sys-on-surface-variant); line-height: 1.4; }
    .ki-no-data { font-size: 12px; color: var(--mat-sys-on-surface-variant); font-style: italic; }
    .ki-lage-actions { display: flex; align-items: center; margin-top: 2px; }
    .ki-detail-btn { font-size: 11px; padding: 0 4px; height: 28px; }

    /* ── Main row ── */
    .main-row { display: flex; gap: 16px; align-items: flex-start; }
    .feed-card { flex: 3; min-width: 0; }
    .right-col { flex: 2; min-width: 280px; display: flex; flex-direction: column; gap: 16px; }

    /* Feed list */
    .feed-list { display: flex; flex-direction: column; gap: 0; }
    .feed-item { display: flex; gap: 10px; align-items: flex-start; padding: 8px 0; border-bottom: 1px solid var(--mat-sys-outline-variant); }
    .feed-item:last-child { border-bottom: none; }
    .sev-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; margin-top: 5px; }
    .feed-item-body { flex: 1; min-width: 0; }
    .feed-item-top { display: flex; align-items: center; gap: 6px; margin-bottom: 2px; flex-wrap: wrap; }
    .source-chip { font-size: 10px; background: var(--mat-sys-surface-variant); padding: 1px 6px; border-radius: 8px; flex-shrink: 0; }
    .host-chip { font-size: 10px; background: #1976d210; color: #1976d2; padding: 1px 6px; border-radius: 8px; font-family: monospace; flex-shrink: 0; max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .feed-time { font-size: 10px; color: var(--mat-sys-on-surface-variant); margin-left: auto; white-space: nowrap; }
    .feed-title { font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .feed-insight { display: flex; align-items: flex-start; gap: 4px; margin-top: 3px; font-size: 11px; color: var(--mat-sys-on-surface-variant); background: var(--mat-sys-primary-container, #e8f0fe); border-radius: 4px; padding: 4px 6px; line-height: 1.4; }
    .insight-icon { font-size: 13px; width: 13px; height: 13px; flex-shrink: 0; margin-top: 1px; color: var(--mat-sys-primary); }

    /* KI detail card */
    .ki-detail-card mat-card-content { display: flex; flex-direction: column; gap: 6px; padding-top: 8px; }
    .finding-item { display: flex; gap: 8px; align-items: flex-start; font-size: 12px; }
    .finding-title { font-weight: 500; }
    .finding-desc { color: var(--mat-sys-on-surface-variant); font-size: 11px; }
    .rec-header { font-size: 11px; font-weight: 600; color: var(--mat-sys-on-surface-variant); text-transform: uppercase; letter-spacing: .5px; margin-top: 6px; display: flex; align-items: center; gap: 3px; }
    .rec-item { font-size: 12px; padding: 3px 0 3px 16px; border-left: 2px solid var(--mat-sys-primary); }

    /* Hosts chart */
    .hosts-chart { height: 180px; }

    /* Misc */
    .spinner-center { display: flex; justify-content: center; padding: 24px; }
    mat-spinner { display: inline-block; }
    .empty-state { display: flex; align-items: center; gap: 8px; padding: 24px; color: var(--mat-sys-on-surface-variant); justify-content: center; }
    .empty-state.small { padding: 16px; font-size: 12px; }
    .empty-state mat-icon { opacity: .5; }
  `],
})
export class DashboardComponent implements OnInit, OnDestroy {
  summary         = signal<AlertSummary>({});
  feedItems       = signal<FeedItem[]>([]);
  latestAnalysis  = signal<AiAnalysis | null>(null);
  loadingFeed     = signal(true);
  loadingAnalysis = signal(true);
  triggeringAgent = signal(false);

  private destroy$ = new Subject<void>();

  topHostsData = computed<Array<{host: string; count: number}>>(() => {
    const counts = new Map<string, number>();
    for (const item of this.feedItems()) {
      const h = this.itemHost(item);
      if (h) counts.set(h, (counts.get(h) ?? 0) + 1);
    }
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8)
      .map(([host, count]) => ({ host, count }));
  });

  hostsChartOptions = computed<any>(() => {
    const data = this.topHostsData();
    if (!data.length) return {};
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: '2%', right: '4%', bottom: '3%', containLabel: true },
      xAxis: { type: 'value', axisLabel: { fontSize: 10 } },
      yAxis: {
        type: 'category',
        data: data.map(d => d.host).reverse(),
        axisLabel: { fontSize: 10, width: 120, overflow: 'truncate' },
      },
      series: [{
        type: 'bar',
        data: data.map(d => d.count).reverse(),
        itemStyle: { color: '#f57c00' },
        label: { show: true, position: 'right', fontSize: 10 },
      }],
    };
  });

  totalAlerts = computed<number>(() => {
    const s = this.summary() as Record<string, number>;
    return Object.values(s).reduce((a, b) => a + b, 0);
  });

  analysisAge = computed<string>(() => {
    const a = this.latestAnalysis();
    if (!a) return '';
    const diff = Date.now() - new Date(a.run_at).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 60) return `vor ${mins} Min.`;
    return `vor ${Math.floor(mins / 60)} Std.`;
  });

  constructor(
    private alertSvc: AlertService,
    private ws: WebsocketService,
    private http: HttpClient,
  ) {}

  ngOnInit() {
    this.loadAll();
    interval(60_000).pipe(takeUntil(this.destroy$)).subscribe(() => this.loadAll());
    this.ws.messages().pipe(takeUntil(this.destroy$)).subscribe((msg: WsMessage) => {
      if (msg.type === 'new_alert' || msg.type === 'ai_analysis') this.loadAll();
    });
  }

  ngOnDestroy() { this.destroy$.next(); this.destroy$.complete(); }

  loadAll() {
    this.loadSummary();
    this.loadFeed();
    this.loadAnalysis();
  }

  loadSummary() {
    this.alertSvc.summary().subscribe({ next: s => this.summary.set(s) });
  }

  loadFeed() {
    this.loadingFeed.set(true);
    this.http.get<FeedItem[]>(`${environment.apiUrl}/feed/`, { params: { limit: '15' } }).subscribe({
      next:  items => { this.feedItems.set(items); this.loadingFeed.set(false); },
      error: ()    => this.loadingFeed.set(false),
    });
  }

  loadAnalysis() {
    this.loadingAnalysis.set(true);
    this.http.get<AiAnalysis[]>(`${environment.apiUrl}/ai/analyses`, {
      params: { agent_type: 'sysadmin', limit: '1' },
    }).subscribe({
      next:  list => { this.latestAnalysis.set(list[0] ?? null); this.loadingAnalysis.set(false); },
      error: ()   => this.loadingAnalysis.set(false),
    });
  }

  triggerAgent() {
    this.triggeringAgent.set(true);
    this.http.post(`${environment.apiUrl}/ai/trigger/sysadmin`, {}).subscribe({
      next:  () => { this.triggeringAgent.set(false); setTimeout(() => this.loadAnalysis(), 3000); },
      error: () => this.triggeringAgent.set(false),
    });
  }

  getSummaryCount(key: string): number {
    return (this.summary() as Record<string, number>)[key] ?? 0;
  }

  sevColor(sev: string): string {
    return SEV_COLOR[sev] ?? '#607d8b';
  }

  itemHost(item: FeedItem): string {
    if (item.host) return item.host;
    const m = item.metadata_ as Record<string, unknown> | undefined;
    if (!m) return '';
    return (m['host'] as string) || (m['container_name'] as string) || '';
  }
}
