import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
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

        @if (vitals().length > 0) {
          <div class="gauges-row">
            @for (vital of vitals(); track vital.metric) {
              <div class="gauge-cell">
                <div class="gauge-label">{{ vital.label }}</div>
                <div echarts [options]="gaugeOptions(vital)" class="gauge-chart"></div>
                @if (vital.series.length > 1) {
                  <div echarts [options]="sparklineOptions(vital)" class="sparkline-chart"></div>
                }
                <div class="gauge-meta">
                  <span class="gauge-value" [attr.data-level]="vital.level">
                    {{ vital.value }}{{ vital.unit }}
                  </span>
                  <span class="gauge-level" [attr.data-level]="vital.level">
                    {{ vital.level.toUpperCase() }}
                  </span>
                </div>
              </div>
            }
          </div>
        } @else if (loading()) {
          <div class="block-loading">METRIKEN WERDEN GELADEN…</div>
        }
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
      font-family: 'Antonio', 'Eurostile', 'Share Tech Mono', monospace;
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
      grid-template-columns: repeat(3, 1fr);
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
export class CockpitComponent implements OnInit {
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

  readonly severities = SEVERITIES;

  readonly filteredMessages = computed(() => {
    const sev = this.severityFilter();
    const msgs = this.allMessages();
    if (sev === 'all') return msgs;
    return msgs.filter(m => m.severity === sev);
  });

  ngOnInit() {
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
  }

  setSeverity(sev: string) {
    this.severityFilter.set(sev);
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
}
