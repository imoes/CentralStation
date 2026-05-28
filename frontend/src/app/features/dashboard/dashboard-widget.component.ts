import { CommonModule } from '@angular/common';
import { Component, computed, inject, input, output } from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { NgxEchartsDirective } from 'ngx-echarts';
import {
  DashboardWidget,
  AiSummaryData,
  DonutData,
  FeedItem,
  GrafanaPanelData,
  ListData,
  SEVERITY_COLORS,
  StatData,
  TimeseriesData,
  TopHostsData,
  WidgetData,
} from './dashboard-widget.model';

@Component({
  selector: 'cs-dashboard-widget',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatCardModule,
    MatIconModule,
    MatProgressSpinnerModule,
    NgxEchartsDirective,
  ],
  template: `
    <mat-card class="widget-card" [class.edit-mode]="editMode()">
      <div class="widget-header">
        <div>
          <div class="widget-title">{{ widget().title }}</div>
          <div class="widget-subtitle">{{ widget().widget_type }}</div>
        </div>
        @if (editMode()) {
          <div class="edit-actions">
            <button mat-icon-button (click)="editWidget($event)" aria-label="Widget konfigurieren">
              <mat-icon>edit</mat-icon>
            </button>
            <button mat-icon-button (click)="removeWidget($event)" aria-label="Widget löschen">
              <mat-icon>close</mat-icon>
            </button>
          </div>
        }
      </div>

      <div class="widget-body">
        @if (!data() && widget().widget_type !== 'grafana_panel') {
          <div class="loading"><mat-spinner diameter="26"></mat-spinner></div>
        } @else {
          @switch (widget().widget_type) {
            @case ('stat') {
              <div class="stat-value">{{ statCount() ?? '...' }}</div>
            }
            @case ('list') {
              <div class="item-list">
                @for (item of listItems(); track item.id) {
                  <div class="list-item clickable" (click)="onItemClick($event, item.id)">
                    <span class="sev-dot" [style.background]="severityColor(item.severity)"></span>
                    <div class="list-copy">
                      <span class="list-title">{{ item.title }}</span>
                      <span class="list-meta">
                        @if (hostLabel(item)) {
                          <span class="list-host">{{ hostLabel(item) }}</span>
                          <span class="meta-sep"> · </span>
                        }
                        {{ item.source }} · {{ item.created_at | date:'dd.MM HH:mm' }}
                      </span>
                    </div>
                  </div>
                } @empty {
                  <div class="empty">Keine Treffer</div>
                }
              </div>
            }
            @case ('donut') {
              <div echarts [options]="donutOptions()" class="chart"></div>
            }
            @case ('ai_summary') {
              @if (aiSummary()) {
                <div class="ai-summary">
                  <p>{{ aiSummary() }}</p>
                  @for (finding of aiFindings(); track finding.title) {
                    <div class="finding clickable" (click)="onFindingClick($event, finding)">
                      <span class="sev-dot" [style.background]="severityColor(finding.severity ?? 'info')"></span>
                      <span>{{ finding.title }}</span>
                      <mat-icon class="finding-arrow">arrow_forward</mat-icon>
                    </div>
                  }
                </div>
              } @else {
                <div class="empty">Noch kein KI-Lagebericht vorhanden</div>
              }
            }
            @case ('top_hosts') {
              <div class="host-list">
                @for (host of topHosts(); track host.host) {
                  <div class="host-group">
                    <div class="host-row">
                      <mat-icon>dns</mat-icon>
                      <span class="host-name">{{ host.host }}</span>
                      <span class="host-count">{{ host.count }}</span>
                    </div>
                    @for (item of host.items; track item.id) {
                      <div class="host-item clickable" (click)="onItemClick($event, item.id)">
                        <span class="sev-dot" [style.background]="severityColor(item.severity)"></span>
                        <span class="host-item-title">{{ item.title }}</span>
                      </div>
                    }
                  </div>
                } @empty {
                  <div class="empty">Keine Problem-Hosts</div>
                }
              </div>
            }
            @case ('timeseries') {
              @if (timeseriesError()) {
                <div class="empty">{{ timeseriesError() }}</div>
              } @else {
                <div echarts [options]="timeseriesOptions()" class="chart"></div>
              }
            }
            @case ('grafana_panel') {
              @if (grafanaUrl()) {
                <iframe class="grafana-frame" [src]="grafanaUrl()!" loading="lazy"></iframe>
              } @else {
                <div class="empty">Keine Grafana-URL konfiguriert</div>
              }
            }
          }
        }
      </div>
    </mat-card>
  `,
  styles: [`
    .widget-card {
      height: 100%;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      border: 1px solid color-mix(in srgb, var(--mat-sys-outline-variant) 75%, transparent);
      box-shadow: 0 12px 28px rgba(15, 23, 42, .08);
      cursor: pointer;
    }
    .widget-card.edit-mode { outline: 2px dashed color-mix(in srgb, var(--mat-sys-primary) 55%, transparent); }
    .edit-actions { display: flex; gap: 0; }
    .widget-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 12px 14px 8px;
      flex-shrink: 0;
    }
    .widget-title { font-size: 14px; font-weight: 700; letter-spacing: .01em; }
    .widget-subtitle {
      color: var(--mat-sys-on-surface-variant);
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: .08em;
      margin-top: 2px;
    }
    .widget-body { flex: 1; min-height: 0; padding: 0 14px 14px; overflow: hidden; }
    .loading, .empty {
      height: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--mat-sys-on-surface-variant);
      font-size: 13px;
      text-align: center;
    }
    .stat-value {
      height: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: clamp(42px, 8vw, 74px);
      font-weight: 800;
      color: var(--mat-sys-primary);
      line-height: 1;
    }
    .item-list { display: flex; flex-direction: column; gap: 8px; min-height: 0; overflow: auto; height: 100%; }
    .list-item { display: flex; align-items: flex-start; gap: 8px; padding: 7px 0; border-bottom: 1px solid var(--mat-sys-outline-variant); }
    .list-item:last-child { border-bottom: 0; }
    .clickable { cursor: pointer; border-radius: 6px; transition: background 0.15s; }
    .clickable:hover { background: color-mix(in srgb, var(--mat-sys-primary) 8%, transparent); }
    .sev-dot { width: 9px; height: 9px; border-radius: 999px; margin-top: 5px; flex-shrink: 0; }
    .list-copy { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
    .list-title { font-size: 12px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .list-meta { font-size: 10px; color: var(--mat-sys-on-surface-variant); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .list-host { font-family: monospace; color: var(--mat-sys-on-surface); font-weight: 600; }
    .meta-sep { opacity: 0.5; }
    .chart { height: 100%; min-height: 140px; width: 100%; display: block; }
    .grafana-frame { width: 100%; height: 100%; border: 0; border-radius: 10px; background: #111827; }
    .ai-summary { height: 100%; overflow: auto; display: flex; flex-direction: column; gap: 7px; }
    .ai-summary p { margin: 0; font-size: 12px; line-height: 1.45; color: var(--mat-sys-on-surface-variant); }
    .finding { display: flex; align-items: center; gap: 7px; font-size: 12px; font-weight: 600; padding: 3px 4px; border-radius: 4px; cursor: pointer; }
    .finding:hover { background: color-mix(in srgb, var(--mat-sys-primary) 8%, transparent); }
    .finding-arrow { font-size: 14px; height: 14px; width: 14px; opacity: 0; margin-left: auto; color: var(--mat-sys-primary); }
    .finding:hover .finding-arrow { opacity: 1; }
    .host-list { display: flex; flex-direction: column; gap: 6px; overflow: auto; height: 100%; }
    .host-group { display: flex; flex-direction: column; gap: 2px; }
    .host-row {
      display: flex; align-items: center; gap: 8px;
      padding: 6px 9px; border-radius: 8px;
      background: var(--mat-sys-surface-variant);
      font-size: 12px;
    }
    .host-row mat-icon { font-size: 16px; height: 16px; width: 16px; color: var(--mat-sys-primary); }
    .host-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: monospace; font-weight: 700; }
    .host-count { background: #f57c00; color: #fff; border-radius: 999px; padding: 1px 7px; font-size: 11px; font-weight: 800; }
    .host-item {
      display: flex; align-items: center; gap: 6px;
      padding: 3px 9px 3px 28px; font-size: 11px;
      color: var(--mat-sys-on-surface-variant);
    }
    .host-item-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  `],
})
export class DashboardWidgetComponent {
  readonly widget = input.required<DashboardWidget>();
  readonly data   = input<WidgetData>();
  readonly editMode = input<boolean>(false);
  readonly remove      = output<void>();
  readonly edit        = output<void>();
  readonly itemClick   = output<string>();
  readonly findingClick = output<{ source: string; host: string | null; severity: string }>();

  private sanitizer = inject(DomSanitizer);

  // ── derived state (computed = stable reference until deps change) ──────────

  readonly statCount = computed(() => {
    const d = this.data() as StatData | undefined;
    return typeof d?.count === 'number' ? d.count : null;
  });

  readonly listItems = computed(() => {
    const d = this.data() as ListData | undefined;
    return Array.isArray(d?.items) ? d.items : [] as FeedItem[];
  });

  private readonly donutBuckets = computed(() => {
    const d = this.data() as DonutData | undefined;
    return Array.isArray(d?.buckets) ? d.buckets : [] as Array<{ key: string; count: number }>;
  });

  readonly donutOptions = computed(() => {
    const buckets = this.donutBuckets();
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: {
        bottom: 4,
        textStyle: { color: '#94a3b8', fontSize: 11 },
        itemWidth: 12,
        itemHeight: 12,
      },
      series: [{
        type: 'pie',
        radius: ['42%', '68%'],
        center: ['50%', '44%'],
        label: { show: false },
        emphasis: { label: { show: true, fontSize: 13, fontWeight: 'bold' } },
        data: buckets.map(b => ({
          name: b.key,
          value: b.count,
          itemStyle: { color: SEVERITY_COLORS[b.key] ?? '#64748b' },
        })),
      }],
    };
  });

  readonly aiSummary = computed(() => {
    const d = this.data() as AiSummaryData | undefined;
    return d?.summary ?? '';
  });

  readonly aiFindings = computed(() => {
    const d = this.data() as AiSummaryData | undefined;
    return Array.isArray(d?.findings) ? d.findings : [] as Array<{ title: string; severity?: string }>;
  });

  readonly topHosts = computed(() => {
    const d = this.data() as TopHostsData | undefined;
    return Array.isArray(d?.hosts)
      ? d.hosts
      : [] as Array<{ host: string; count: number; items: FeedItem[]; external_url?: string | null }>;
  });

  readonly timeseriesOptions = computed(() => {
    const d = this.data() as TimeseriesData | undefined;
    const unit = d?.unit ?? '';

    // Multi-host: series_list → one line per host
    if (d?.series_list && d.series_list.length > 0) {
      // Use timestamps from the first non-empty series as x-axis
      const first = d.series_list.find(s => s.series.length > 0);
      const xLabels = (first?.series ?? []).map(p =>
        new Date(p.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      );
      const palette = ['#60a5fa', '#34d399', '#f97316', '#a78bfa', '#fb7185', '#facc15'];
      return {
        tooltip: { trigger: 'axis' },
        legend: { bottom: 0, textStyle: { color: '#94a3b8', fontSize: 10 } },
        grid: { left: 50, right: 14, top: 16, bottom: 40 },
        xAxis: {
          type: 'category',
          data: xLabels,
          axisLabel: { color: '#94a3b8', fontSize: 10 },
        },
        yAxis: {
          type: 'value',
          axisLabel: { formatter: `{value}${unit}`, color: '#94a3b8', fontSize: 10 },
          splitLine: { lineStyle: { color: '#334155' } },
        },
        series: d.series_list.map((s, i) => ({
          name: s.label,
          type: 'line',
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 2, color: palette[i % palette.length] },
          itemStyle: { color: palette[i % palette.length] },
          data: s.series.map(p => p.value),
        })),
      };
    }

    // Single host
    const series = Array.isArray(d?.series) ? d.series : [];
    return {
      tooltip: { trigger: 'axis' },
      grid: { left: 50, right: 14, top: 16, bottom: 28 },
      xAxis: {
        type: 'category',
        data: series.map(p =>
          new Date(p.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        ),
        axisLabel: { color: '#94a3b8', fontSize: 10 },
      },
      yAxis: {
        type: 'value',
        axisLabel: { formatter: `{value}${unit}`, color: '#94a3b8', fontSize: 10 },
        splitLine: { lineStyle: { color: '#334155' } },
      },
      series: [{
        type: 'line',
        smooth: true,
        showSymbol: false,
        areaStyle: { opacity: 0.18 },
        lineStyle: { width: 2 },
        data: series.map(p => p.value),
      }],
    };
  });

  readonly timeseriesError = computed(() => {
    const d = this.data() as TimeseriesData | undefined;
    return d?.error ?? '';
  });

  readonly grafanaUrl = computed((): SafeResourceUrl | null => {
    const cfgUrl  = this.widget().config['panel_url'];
    const dataUrl = (this.data() as GrafanaPanelData | undefined)?.panel_url;
    const url = typeof dataUrl === 'string' && dataUrl
      ? dataUrl
      : typeof cfgUrl === 'string'
      ? cfgUrl
      : '';
    return url ? this.sanitizer.bypassSecurityTrustResourceUrl(url) : null;
  });

  severityColor(severity: string): string {
    return SEVERITY_COLORS[severity] ?? '#64748b';
  }

  hostLabel(item: FeedItem): string {
    const meta = (item.metadata ?? {}) as Record<string, unknown>;
    return (meta['container_name'] as string) || (meta['host'] as string) || '';
  }

  removeWidget(event: MouseEvent) {
    event.stopPropagation();
    this.remove.emit();
  }

  editWidget(event: MouseEvent) {
    event.stopPropagation();
    this.edit.emit();
  }

  onItemClick(event: MouseEvent, itemId: string) {
    event.stopPropagation();
    this.itemClick.emit(itemId);
  }

  onFindingClick(event: MouseEvent, finding: { source?: string; severity?: string; host?: string | null }) {
    event.stopPropagation();
    this.findingClick.emit({
      source: finding.source ?? '',
      host: finding.host ?? null,
      severity: finding.severity ?? '',
    });
  }
}
