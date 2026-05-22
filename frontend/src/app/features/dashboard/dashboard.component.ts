import { Component, OnInit, OnDestroy, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { NgxEchartsDirective, provideEchartsCore } from 'ngx-echarts';
import * as echarts from 'echarts/core';
import { PieChart, BarChart, LineChart } from 'echarts/charts';
import {
  TitleComponent, TooltipComponent, LegendComponent,
  GridComponent, DatasetComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { Subject, takeUntil, interval } from 'rxjs';
import { AlertService } from '../../core/services/alert.service';
import { WebsocketService, WsMessage } from '../../core/services/websocket.service';
import { AlertSummary } from '../../core/models/alert.model';
import { environment } from '../../../environments/environment';

echarts.use([
  PieChart, BarChart, LineChart,
  TitleComponent, TooltipComponent, LegendComponent, GridComponent, DatasetComponent,
  CanvasRenderer,
]);

const SEVERITY_PALETTE = {
  critical: '#d32f2f',
  high:     '#f57c00',
  medium:   '#1976d2',
  low:      '#388e3c',
  info:     '#607d8b',
};

@Component({
  selector: 'cs-dashboard',
  standalone: true,
  imports: [
    CommonModule,
    MatCardModule, MatButtonModule, MatIconModule, MatProgressSpinnerModule,
    NgxEchartsDirective,
  ],
  providers: [
    provideEchartsCore({ echarts }),
  ],
  template: `
    <div class="dashboard">
      <!-- Alert Summary Cards -->
      <div class="summary-row">
        @for (sev of severities; track sev.key) {
          <mat-card class="summary-card" [style.border-top-color]="sev.color">
            <div class="summary-count">{{ getSummaryCount(sev.key) }}</div>
            <div class="summary-label">{{ sev.label }}</div>
          </mat-card>
        }
      </div>

      <!-- Alert Distribution Pie -->
      <div class="charts-row">
        <mat-card class="chart-card">
          <mat-card-header><mat-card-title>Alert-Verteilung</mat-card-title></mat-card-header>
          <mat-card-content>
            <div echarts [options]="pieOptions()" class="chart-container"></div>
          </mat-card-content>
        </mat-card>

        <!-- Prometheus CPU Chart -->
        <mat-card class="chart-card">
          <mat-card-header>
            <mat-card-title>CPU-Auslastung (Prometheus)</mat-card-title>
          </mat-card-header>
          <mat-card-content>
            @if (prometheusLoading()) {
              <div class="spinner-center"><mat-spinner diameter="30"></mat-spinner></div>
            } @else {
              <div echarts [options]="cpuOptions()" class="chart-container"></div>
            }
          </mat-card-content>
        </mat-card>
      </div>

      <!-- Recent Alerts Timeline -->
      <mat-card class="timeline-card">
        <mat-card-header>
          <mat-card-title>Letzte Alerts</mat-card-title>
          <mat-card-subtitle>Neue Alerts (letzten 24h)</mat-card-subtitle>
        </mat-card-header>
        <mat-card-content>
          @if (recentAlerts().length > 0) {
            <div class="timeline">
              @for (alert of recentAlerts(); track alert.id) {
                <div class="timeline-item">
                  <span class="tl-dot" [style.background-color]="sevColor(alert.severity)"></span>
                  <span class="tl-time">{{ alert.created_at | date:'HH:mm' }}</span>
                  <span class="tl-source chip-source">{{ alert.source }}</span>
                  <span class="tl-title">{{ alert.title }}</span>
                </div>
              }
            </div>
          } @else {
            <div class="empty-state">Keine neuen Alerts.</div>
          }
        </mat-card-content>
      </mat-card>
    </div>
  `,
  styles: [`
    .dashboard { padding: 16px; display: flex; flex-direction: column; gap: 16px; }
    .summary-row { display: flex; gap: 12px; flex-wrap: wrap; }
    .summary-card { flex: 1; min-width: 120px; padding: 16px; border-top: 4px solid; cursor: default; text-align: center; }
    .summary-count { font-size: 32px; font-weight: 700; line-height: 1; }
    .summary-label { font-size: 12px; text-transform: uppercase; color: var(--mat-sys-on-surface-variant); margin-top: 4px; }
    .charts-row { display: flex; gap: 16px; flex-wrap: wrap; }
    .chart-card { flex: 1; min-width: 300px; }
    .chart-container { height: 220px; }
    .timeline-card { }
    .timeline { display: flex; flex-direction: column; gap: 6px; padding-top: 8px; }
    .timeline-item { display: flex; align-items: center; gap: 8px; font-size: 12px; }
    .tl-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
    .tl-time { color: var(--mat-sys-on-surface-variant); min-width: 40px; }
    .tl-source { font-size: 10px; background: var(--mat-sys-surface-variant); padding: 2px 6px; border-radius: 10px; }
    .tl-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .spinner-center { display: flex; justify-content: center; padding: 20px; }
    .empty-state { text-align: center; padding: 24px; color: var(--mat-sys-on-surface-variant); }
  `],
})
export class DashboardComponent implements OnInit, OnDestroy {
  severities = [
    { key: 'critical', label: 'Kritisch', color: '#d32f2f' },
    { key: 'high',     label: 'Hoch',     color: '#f57c00' },
    { key: 'medium',   label: 'Mittel',   color: '#1976d2' },
    { key: 'low',      label: 'Niedrig',  color: '#388e3c' },
    { key: 'info',     label: 'Info',     color: '#607d8b' },
  ];

  summary = signal<AlertSummary>({});
  recentAlerts = signal<any[]>([]);
  prometheusLoading = signal(true);
  pieOptions = signal<any>({});
  cpuOptions = signal<any>({});

  private destroy$ = new Subject<void>();

  constructor(
    private alertSvc: AlertService,
    private ws: WebsocketService,
    private http: HttpClient,
  ) {}

  ngOnInit() {
    this.loadSummary();
    this.loadRecentAlerts();
    this.loadPrometheusChart();

    // Refresh every 60s
    interval(60_000).pipe(takeUntil(this.destroy$)).subscribe(() => {
      this.loadSummary();
      this.loadRecentAlerts();
    });

    this.ws.messages().pipe(takeUntil(this.destroy$)).subscribe((msg: WsMessage) => {
      if (msg.type === 'new_alert') {
        this.loadSummary();
        this.loadRecentAlerts();
      }
    });
  }

  ngOnDestroy() { this.destroy$.next(); this.destroy$.complete(); }

  loadSummary() {
    this.alertSvc.summary().subscribe({
      next: s => {
        this.summary.set(s);
        this.updatePieChart(s);
      },
    });
  }

  loadRecentAlerts() {
    this.alertSvc.list({ status: 'new', limit: 10 }).subscribe({
      next: alerts => this.recentAlerts.set(alerts.slice(0, 10)),
    });
  }

  updatePieChart(s: AlertSummary) {
    const data = this.severities
      .filter(sev => (s as any)[sev.key] > 0)
      .map(sev => ({ name: sev.label, value: (s as any)[sev.key], itemStyle: { color: sev.color } }));

    this.pieOptions.set({
      tooltip: { trigger: 'item' },
      legend: { bottom: 0, textStyle: { fontSize: 11 } },
      series: [{
        type: 'pie',
        radius: ['40%', '70%'],
        data,
        label: { show: false },
      }],
    });
  }

  loadPrometheusChart() {
    // Query the backend for a Prometheus connector, then fetch metrics
    this.http.get<any[]>(`${environment.apiUrl}/connectors/`).subscribe({
      next: connectors => {
        const prom = connectors.find((c: any) => c.type === 'prometheus' && c.enabled);
        if (!prom) {
          this.prometheusLoading.set(false);
          this.cpuOptions.set(this.emptyChart('Kein Prometheus-Connector konfiguriert'));
          return;
        }
        // Fetch through backend proxy (connector test endpoint returns health)
        // For real charts, use Prometheus directly
        const promUrl = prom.base_url;
        const end = Math.floor(Date.now() / 1000);
        const start = end - 3600;
        this.http.get(`${promUrl}/api/v1/query_range`, {
          params: {
            query: '100 - (avg(irate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
            start: String(start),
            end: String(end),
            step: '60',
          },
        }).subscribe({
          next: (data: any) => {
            this.prometheusLoading.set(false);
            const series = data?.data?.result?.[0]?.values ?? [];
            this.cpuOptions.set({
              tooltip: { trigger: 'axis' },
              xAxis: { type: 'category', data: series.map((v: any[]) => new Date(v[0] * 1000).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })), axisLabel: { fontSize: 10 } },
              yAxis: { type: 'value', min: 0, max: 100, axisLabel: { formatter: '{value}%' } },
              series: [{ type: 'line', data: series.map((v: any[]) => parseFloat(v[1]).toFixed(1)), smooth: true, areaStyle: { opacity: 0.3 }, lineStyle: { color: '#1976d2' }, itemStyle: { color: '#1976d2' } }],
              grid: { left: '10%', right: '4%', bottom: '15%' },
            });
          },
          error: () => {
            this.prometheusLoading.set(false);
            this.cpuOptions.set(this.emptyChart('Prometheus nicht erreichbar'));
          },
        });
      },
      error: () => {
        this.prometheusLoading.set(false);
        this.cpuOptions.set(this.emptyChart(''));
      },
    });
  }

  emptyChart(msg: string): object {
    return {
      title: { text: msg, left: 'center', top: 'center', textStyle: { color: '#999', fontSize: 12 } },
    };
  }

  getSummaryCount(key: string): number {
    return (this.summary() as Record<string, number>)[key] ?? 0;
  }

  sevColor(sev: string): string {
    return (SEVERITY_PALETTE as Record<string, string>)[sev] ?? '#607d8b';
  }
}
