import { CommonModule } from '@angular/common';
import { Component, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { MatButtonModule } from '@angular/material/button';
import { MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSelectModule } from '@angular/material/select';
import { MatTooltipModule } from '@angular/material/tooltip';
import { environment } from '../../../environments/environment';
import { DashboardWidgetCreate } from './dashboard-widget.model';

interface FeedSearch {
  id: string;
  name: string;
  index_pattern: string;
  query_string: string;
}

@Component({
  selector: 'cs-add-widget-dialog',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatButtonModule,
    MatDialogModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
    MatProgressSpinnerModule,
    MatSelectModule,
    MatTooltipModule,
  ],
  template: `
    <h2 mat-dialog-title>Widget hinzufügen</h2>
    <mat-dialog-content class="dialog-body">
      <div class="type-grid">
        @for (type of widgetTypes; track type.value) {
          <button type="button" class="type-tile" [class.active]="widgetType === type.value" (click)="selectType(type.value)">
            <mat-icon>{{ type.icon }}</mat-icon>
            <span>{{ type.label }}</span>
          </button>
        }
      </div>

      <mat-form-field appearance="outline">
        <mat-label>Titel</mat-label>
        <input matInput [(ngModel)]="title">
      </mat-form-field>

      @if (widgetType !== 'timeseries' && widgetType !== 'grafana_panel' && widgetType !== 'ai_summary') {
        <mat-form-field appearance="outline">
          <mat-label>Gespeicherte Suche optional</mat-label>
          <mat-select [(ngModel)]="selectedSearchId" (ngModelChange)="applySearch()">
            <mat-option value="">Keine</mat-option>
            @for (s of searches(); track s.id) {
              <mat-option [value]="s.id">{{ s.name }}</mat-option>
            }
          </mat-select>
        </mat-form-field>

        <mat-form-field appearance="outline">
          <mat-label>Index-Pattern</mat-label>
          <input matInput [(ngModel)]="indexPattern">
        </mat-form-field>

        <mat-form-field appearance="outline">
          <mat-label>OpenSearch Query</mat-label>
          <textarea matInput rows="3" [(ngModel)]="queryString" placeholder="Leer = match_all"></textarea>
        </mat-form-field>
      }

      @if (widgetType === 'stat') {
        <mat-form-field appearance="outline">
          <mat-label>Severity-Schnellfilter</mat-label>
          <mat-select [(ngModel)]="severity" (ngModelChange)="applySeverity()">
            <mat-option value="">Query manuell</mat-option>
            <mat-option value="critical">critical</mat-option>
            <mat-option value="high">high</mat-option>
            <mat-option value="medium">medium</mat-option>
            <mat-option value="low">low</mat-option>
          </mat-select>
        </mat-form-field>
      }

      @if (widgetType === 'list') {
        <mat-form-field appearance="outline">
          <mat-label>Limit</mat-label>
          <input matInput type="number" min="1" max="50" [(ngModel)]="limit">
        </mat-form-field>
      }

      @if (widgetType === 'timeseries') {
        <!-- Datenquelle -->
        <div class="datasource-tabs">
          <button type="button" class="ds-tab" [class.active]="dataSource === 'checkmk'" (click)="dataSource = 'checkmk'">
            <mat-icon>monitor_heart</mat-icon> CheckMK
          </button>
          <button type="button" class="ds-tab" [class.active]="dataSource === 'prometheus'" (click)="dataSource = 'prometheus'">
            <mat-icon>show_chart</mat-icon> Prometheus / PromQL
          </button>
        </div>

        @if (dataSource === 'checkmk') {
          <mat-form-field appearance="outline">
            <mat-label>Hostname (exakt wie in CheckMK)</mat-label>
            <input matInput [(ngModel)]="cmkHost" placeholder="docker086.ippen.media">
            <mat-icon matSuffix>computer</mat-icon>
          </mat-form-field>
          <mat-form-field appearance="outline">
            <mat-label>Service-Name (exakt wie in CheckMK)</mat-label>
            <input matInput [(ngModel)]="cmkService" placeholder="CPU utilization">
            <mat-icon matSuffix>memory</mat-icon>
            <mat-hint>z.B. "CPU utilization", "Memory", "Filesystem /"</mat-hint>
          </mat-form-field>
          <div class="inline-fields">
            <mat-form-field appearance="outline">
              <mat-label>Graph-Index</mat-label>
              <input matInput type="number" min="0" max="10" [(ngModel)]="cmkGraphIndex">
              <mat-hint>0 = erster Graph des Service</mat-hint>
            </mat-form-field>
            <mat-form-field appearance="outline">
              <mat-label>Stunden</mat-label>
              <input matInput type="number" min="1" max="168" [(ngModel)]="hours">
            </mat-form-field>
            <mat-form-field appearance="outline">
              <mat-label>Einheit (optional)</mat-label>
              <input matInput [(ngModel)]="unit" placeholder="aus CheckMK">
            </mat-form-field>
          </div>
        }

        @if (dataSource === 'prometheus') {
          <!-- Lucene → PromQL converter -->
          <div class="converter-row">
            <mat-form-field appearance="outline" class="converter-field">
              <mat-label>Beschreibung / Lucene-Suchterme</mat-label>
              <textarea matInput rows="2" [(ngModel)]="convertPrompt"
                placeholder='z.B. "CPU-Auslastung docker086" oder "host:docker086 AND metric:cpu"'></textarea>
              <mat-hint>Natürliche Sprache → wird zu PromQL konvertiert</mat-hint>
            </mat-form-field>
            <button mat-flat-button color="accent" class="convert-btn"
                    [disabled]="!convertPrompt.trim() || convertingPromql()"
                    (click)="convertToPromql()"
                    matTooltip="Beschreibung in PromQL übersetzen (KI oder Regelwerk)">
              @if (convertingPromql()) {
                <mat-spinner diameter="16"></mat-spinner>
              } @else {
                <mat-icon>auto_fix_high</mat-icon>
              }
              → PromQL
            </button>
          </div>
          @if (promqlExplanation) {
            <div class="promql-hint">
              <mat-icon>info</mat-icon> {{ promqlExplanation }}
            </div>
          }
          <mat-form-field appearance="outline">
            <mat-label>PromQL</mat-label>
            <textarea matInput rows="4" [(ngModel)]="promql"
              placeholder='z.B. 100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'></textarea>
          </mat-form-field>
          <div class="inline-fields">
            <mat-form-field appearance="outline">
              <mat-label>Step</mat-label>
              <mat-select [(ngModel)]="step">
                <mat-option value="15s">15s</mat-option>
                <mat-option value="1m">1m</mat-option>
                <mat-option value="5m">5m</mat-option>
                <mat-option value="15m">15m</mat-option>
              </mat-select>
            </mat-form-field>
            <mat-form-field appearance="outline">
              <mat-label>Stunden</mat-label>
              <input matInput type="number" min="1" max="168" [(ngModel)]="hours">
            </mat-form-field>
            <mat-form-field appearance="outline">
              <mat-label>Einheit</mat-label>
              <input matInput [(ngModel)]="unit" placeholder="%">
            </mat-form-field>
          </div>
        }
      }

      @if (widgetType === 'grafana_panel') {
        <mat-form-field appearance="outline">
          <mat-label>Grafana Panel URL</mat-label>
          <textarea matInput rows="4" [(ngModel)]="panelUrl" placeholder="https://grafana/.../d-solo/..."></textarea>
        </mat-form-field>
      }
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button mat-dialog-close>Abbrechen</button>
      <button mat-flat-button color="primary" [disabled]="!canCreate()" (click)="create()">Erstellen</button>
    </mat-dialog-actions>
  `,
  styles: [`
    .dialog-body { min-width: 520px; display: flex; flex-direction: column; gap: 12px; padding-top: 6px; }
    .type-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 8px; }
    .type-tile {
      border: 1px solid var(--mat-sys-outline-variant);
      border-radius: 12px;
      background: var(--mat-sys-surface);
      color: var(--mat-sys-on-surface);
      padding: 12px 8px;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 6px;
      cursor: pointer;
    }
    .type-tile.active { border-color: var(--mat-sys-primary); background: color-mix(in srgb, var(--mat-sys-primary) 10%, transparent); }
    .type-tile mat-icon { color: var(--mat-sys-primary); }
    .type-tile span { font-size: 12px; font-weight: 600; }
    .inline-fields { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; }
    mat-form-field { width: 100%; }
    .converter-row { display: flex; gap: 10px; align-items: flex-start; }
    .converter-field { flex: 1; }
    .convert-btn { margin-top: 4px; flex-shrink: 0; height: 42px; }
    .convert-btn mat-spinner { display: inline-block; margin-right: 4px; }
    .promql-hint { display: flex; align-items: center; gap: 6px; font-size: 12px;
      color: var(--mat-sys-on-surface-variant); padding: 2px 0 6px; }
    .promql-hint mat-icon { font-size: 15px; height: 15px; width: 15px; }
    .datasource-tabs { display: flex; gap: 8px; }
    .ds-tab {
      flex: 1; padding: 10px; border-radius: 10px; cursor: pointer;
      display: flex; align-items: center; justify-content: center; gap: 8px;
      font-size: 13px; font-weight: 600;
      border: 1px solid var(--mat-sys-outline-variant);
      background: var(--mat-sys-surface); color: var(--mat-sys-on-surface);
    }
    .ds-tab.active { border-color: var(--mat-sys-primary); background: color-mix(in srgb, var(--mat-sys-primary) 10%, transparent); color: var(--mat-sys-primary); }
    .ds-tab mat-icon { font-size: 18px; height: 18px; width: 18px; }
  `],
})
export class AddWidgetDialogComponent implements OnInit {
  widgetTypes = [
    { value: 'stat', label: 'Stat', icon: 'counter_1' },
    { value: 'list', label: 'Liste', icon: 'view_list' },
    { value: 'donut', label: 'Donut', icon: 'donut_large' },
    { value: 'ai_summary', label: 'KI-Lage', icon: 'psychology' },
    { value: 'top_hosts', label: 'Top Hosts', icon: 'dns' },
    { value: 'timeseries', label: 'Zeitreihe', icon: 'show_chart' },
    { value: 'grafana_panel', label: 'Grafana', icon: 'dashboard' },
  ] as const;

  searches = signal<FeedSearch[]>([]);
  convertingPromql = signal(false);
  widgetType: DashboardWidgetCreate['widget_type'] = 'list';
  title = 'Neueste Alerts';
  selectedSearchId = '';
  indexPattern = 'cs-feed-*';
  queryString = '';
  severity = '';
  limit = 8;
  convertPrompt = '';
  promqlExplanation = '';
  promql = '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)';
  step = '1m';
  hours = 4;
  unit = '%';
  panelUrl = '';
  dataSource: 'checkmk' | 'prometheus' = 'checkmk';
  cmkHost = '';
  cmkService = 'CPU utilization';
  cmkGraphIndex = 0;

  constructor(
    private http: HttpClient,
    private ref: MatDialogRef<AddWidgetDialogComponent, DashboardWidgetCreate>,
  ) {}

  ngOnInit() {
    this.http.get<FeedSearch[]>(`${environment.apiUrl}/feed-searches/`).subscribe({
      next: searches => this.searches.set(searches),
    });
  }

  selectType(type: DashboardWidgetCreate['widget_type']) {
    this.widgetType = type;
    const titleByType: Record<DashboardWidgetCreate['widget_type'], string> = {
      stat: 'Alert Count',
      list: 'Neueste Alerts',
      donut: 'Severity-Verteilung',
      ai_summary: 'KI-Lagebericht',
      top_hosts: 'Top Problem-Hosts',
      timeseries: 'CPU-Auslastung',
      grafana_panel: 'Grafana Panel',
    };
    this.title = titleByType[type];
  }

  applySearch() {
    const search = this.searches().find(s => s.id === this.selectedSearchId);
    if (!search) return;
    this.indexPattern = search.index_pattern;
    this.queryString = search.query_string;
    if (!this.title) this.title = search.name;
  }

  applySeverity() {
    if (this.severity) this.queryString = `severity:${this.severity}`;
  }

  convertToPromql() {
    const msg = this.convertPrompt.trim();
    if (!msg) return;
    this.convertingPromql.set(true);
    this.http.post<{ promql: string; explanation: string }>(
      `${environment.apiUrl}/ai/promql-assistant`,
      { message: msg },
    ).subscribe({
      next: res => {
        if (res.promql) this.promql = res.promql;
        this.promqlExplanation = res.explanation || '';
        this.convertingPromql.set(false);
      },
      error: () => this.convertingPromql.set(false),
    });
  }

  canCreate(): boolean {
    if (!this.title.trim()) return false;
    if (this.widgetType === 'timeseries') {
      if (this.dataSource === 'checkmk') return !!this.cmkHost.trim() && !!this.cmkService.trim();
      return !!this.promql.trim();
    }
    if (this.widgetType === 'grafana_panel') return !!this.panelUrl.trim();
    return true;
  }

  create() {
    const base = { widget_type: this.widgetType, title: this.title.trim(), gs_w: 4, gs_h: 3 };
    if (this.widgetType === 'stat') {
      this.ref.close({ ...base, gs_w: 2, gs_h: 2, config: { index_pattern: this.indexPattern, query_string: this.queryString } });
    } else if (this.widgetType === 'list') {
      this.ref.close({ ...base, config: { index_pattern: this.indexPattern, query_string: this.queryString, limit: Number(this.limit) || 8 } });
    } else if (this.widgetType === 'donut') {
      this.ref.close({ ...base, gs_w: 5, gs_h: 4, config: { index_pattern: this.indexPattern, query_string: this.queryString } });
    } else if (this.widgetType === 'ai_summary') {
      this.ref.close({ ...base, gs_w: 4, gs_h: 2, config: { agent_type: 'sysadmin' } });
    } else if (this.widgetType === 'top_hosts') {
      this.ref.close({ ...base, gs_w: 4, gs_h: 3, config: { index_pattern: this.indexPattern, query_string: this.queryString || 'NOT status:resolved', limit: Number(this.limit) || 8 } });
    } else if (this.widgetType === 'timeseries') {
      if (this.dataSource === 'checkmk') {
        this.ref.close({ ...base, gs_w: 5, gs_h: 4, config: {
          data_source: 'checkmk',
          host: this.cmkHost.trim(),
          service: this.cmkService.trim(),
          graph_index: Number(this.cmkGraphIndex) || 0,
          hours: Number(this.hours) || 4,
          unit: this.unit,
        }});
      } else {
        this.ref.close({ ...base, gs_w: 5, gs_h: 4, config: {
          data_source: 'prometheus',
          promql: this.promql,
          step: this.step,
          hours: Number(this.hours) || 4,
          unit: this.unit,
        }});
      }
    } else {
      this.ref.close({ ...base, gs_w: 6, gs_h: 4, config: { panel_url: this.panelUrl, refresh_seconds: 30 } });
    }
  }
}
