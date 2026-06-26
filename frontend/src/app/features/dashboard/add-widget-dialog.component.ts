import { CommonModule } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSelectModule } from '@angular/material/select';
import { MatTooltipModule } from '@angular/material/tooltip';
import { environment } from '../../../environments/environment';
import { DashboardWidget, DashboardWidgetCreate } from './dashboard-widget.model';
import { I18nService } from '../../core/services/i18n.service';

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
    <h2 mat-dialog-title>{{ isEdit ? i18n.t('dashboard.widget.configure') : i18n.t('dashboard.widget.add') }}</h2>
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
        <mat-label>{{ i18n.t('common.title') }}</mat-label>
        <input matInput [(ngModel)]="title">
      </mat-form-field>

      @if (widgetType !== 'timeseries' && widgetType !== 'grafana_panel' && widgetType !== 'ai_summary' && widgetType !== 'top_hosts') {
        <mat-form-field appearance="outline">
          <mat-label>{{ i18n.t('dashboard.widget.saved_search') }}</mat-label>
          <mat-select [(ngModel)]="selectedSearchId" (ngModelChange)="applySearch()">
            <mat-option value="">Keine</mat-option>
            @for (s of searches(); track s.id) {
              <mat-option [value]="s.id">{{ s.name }}</mat-option>
            }
          </mat-select>
        </mat-form-field>

        <mat-form-field appearance="outline">
          <mat-label>{{ i18n.t('dashboard.widget.index_pattern') }}</mat-label>
          <input matInput [(ngModel)]="indexPattern">
        </mat-form-field>

        <!-- KI Query-Assistent -->
        <div class="converter-row">
          <mat-form-field appearance="outline" class="converter-field">
            <mat-label>{{ i18n.t('dashboard.widget.query_description') }}</mat-label>
            <textarea matInput rows="2" [(ngModel)]="queryPrompt"
              placeholder='z.B. "Alle Wazuh-Alerts von docker086" oder "kritische CheckMK-Fehler"'></textarea>
            <mat-hint>{{ i18n.t('dashboard.widget.natural_language_hint') }}</mat-hint>
          </mat-form-field>
          <button mat-flat-button color="accent" class="convert-btn"
                  [disabled]="!queryPrompt.trim() || convertingQuery()"
                  (click)="convertToQuery()"
                  [matTooltip]="i18n.t('dashboard.widget.convert_tooltip')">
            @if (convertingQuery()) {
              <mat-spinner diameter="16"></mat-spinner>
            } @else {
              <mat-icon>auto_fix_high</mat-icon>
            }
            {{ i18n.t('dashboard.widget.convert_to_query') }}
          </button>
        </div>
        @if (queryExplanation) {
          <div class="promql-hint">
            <mat-icon>info</mat-icon> {{ queryExplanation }}
          </div>
        }

        <mat-form-field appearance="outline">
          <mat-label>{{ i18n.t('dashboard.widget.opensearch_query') }}</mat-label>
          <textarea matInput rows="3" [(ngModel)]="queryString" [placeholder]="i18n.t('dashboard.widget.query_empty_hint')"></textarea>
        </mat-form-field>
      }

      @if (widgetType === 'bar') {
        <mat-form-field appearance="outline">
          <mat-label>{{ i18n.t('dashboard.widget.aggregate_by') }}</mat-label>
          <mat-select [(ngModel)]="aggField">
            <mat-option value="severity">Severity</mat-option>
            <mat-option value="source">Quelle (source)</mat-option>
            <mat-option value="host">Hostname</mat-option>
            <mat-option value="container">Container-Name</mat-option>
            <mat-option value="hostgroup">CheckMK Hostgruppe</mat-option>
          </mat-select>
          <mat-hint>Jeder Balken = ein Wert dieses Feldes, Klick → Feed-Filter</mat-hint>
        </mat-form-field>
      }

      @if (widgetType === 'stat') {
        <mat-form-field appearance="outline">
          <mat-label>{{ i18n.t('dashboard.widget.severity_filter') }}</mat-label>
          <mat-select [(ngModel)]="severity" (ngModelChange)="applySeverity()">
            <mat-option value="">{{ i18n.t('dashboard.widget.manual_query') }}</mat-option>
            <mat-option value="critical">critical</mat-option>
            <mat-option value="high">high</mat-option>
            <mat-option value="medium">medium</mat-option>
            <mat-option value="low">low</mat-option>
          </mat-select>
        </mat-form-field>
      }

      @if (widgetType === 'list') {
        <mat-form-field appearance="outline">
          <mat-label>{{ i18n.t('dashboard.widget.limit') }}</mat-label>
          <input matInput type="number" min="1" max="50" [(ngModel)]="limit">
        </mat-form-field>
      }

      @if (widgetType === 'gauge') {
        <mat-form-field appearance="outline">
          <mat-label>{{ i18n.t('dashboard.widget.total_query') }}</mat-label>
          <input matInput [(ngModel)]="totalQueryString" placeholder="* (alle)">
        </mat-form-field>
        <div style="display:flex;gap:8px">
          <mat-form-field appearance="outline" style="flex:1">
            <mat-label>{{ i18n.t('dashboard.widget.warn_percent') }}</mat-label>
            <input matInput type="number" min="0" max="100" [(ngModel)]="gaugeWarn">
          </mat-form-field>
          <mat-form-field appearance="outline" style="flex:1">
            <mat-label>{{ i18n.t('dashboard.widget.critical_percent') }}</mat-label>
            <input matInput type="number" min="0" max="100" [(ngModel)]="gaugeCritical">
          </mat-form-field>
        </div>
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
          <div class="host-list-label">
            Hosts <span class="hint-text">(mehrere Hosts werden als überlagerte Linien dargestellt)</span>
          </div>
          @for (host of cmkHosts; track $index; let i = $index) {
            <div class="host-row">
              <mat-form-field appearance="outline" class="host-field">
                <mat-label>Host {{ i + 1 }}</mat-label>
                <input matInput [(ngModel)]="cmkHosts[i]" placeholder="docker086.ippen.media">
                <mat-icon matSuffix>computer</mat-icon>
              </mat-form-field>
              @if (cmkHosts.length > 1) {
                <button mat-icon-button color="warn" type="button" (click)="removeHost(i)" matTooltip="Host entfernen">
                  <mat-icon>remove_circle_outline</mat-icon>
                </button>
              }
            </div>
          }
          <button mat-stroked-button type="button" (click)="addHost()" class="add-host-btn">
            <mat-icon>add</mat-icon> Host hinzufügen
          </button>
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
      <button mat-flat-button color="primary" [disabled]="!canCreate()" (click)="create()">{{ isEdit ? 'Speichern' : 'Erstellen' }}</button>
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
    .host-list-label { font-size: 13px; font-weight: 600; color: var(--mat-sys-on-surface); margin-bottom: -4px; }
    .hint-text { font-size: 11px; font-weight: 400; color: var(--mat-sys-on-surface-variant); margin-left: 6px; }
    .host-row { display: flex; align-items: center; gap: 8px; }
    .host-field { flex: 1; }
    .add-host-btn { align-self: flex-start; }
  `],
})
export class AddWidgetDialogComponent implements OnInit {
  private dialogData = inject<{ existingWidget?: DashboardWidget } | null>(MAT_DIALOG_DATA, { optional: true });

  get isEdit(): boolean { return !!this.dialogData?.existingWidget; }

  widgetTypes = [
    { value: 'stat', label: 'Stat', icon: 'counter_1' },
    { value: 'list', label: 'Liste', icon: 'view_list' },
    { value: 'donut', label: 'Donut', icon: 'donut_large' },
    { value: 'bar', label: 'Balken', icon: 'bar_chart' },
    { value: 'gauge', label: 'Gauge', icon: 'speed' },
    { value: 'ai_summary', label: 'KI-Lage', icon: 'psychology' },
    { value: 'top_hosts', label: 'Top Hosts', icon: 'dns' },
    { value: 'timeseries', label: 'Zeitreihe', icon: 'show_chart' },
    { value: 'grafana_panel', label: 'Grafana', icon: 'dashboard' },
  ] as const;

  searches = signal<FeedSearch[]>([]);
  convertingPromql = signal(false);
  convertingQuery = signal(false);
  widgetType: DashboardWidgetCreate['widget_type'] = 'list';
  title = 'Neueste Alerts';
  selectedSearchId = '';
  indexPattern = 'cs-feed-*';
  queryString = '';
  severity = '';
  limit = 8;
  convertPrompt = '';
  promqlExplanation = '';
  queryPrompt = '';
  queryExplanation = '';
  aggField = 'severity';
  promql = '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)';
  step = '1m';
  hours = 4;
  unit = '%';
  totalQueryString = '*';
  gaugeWarn = 70;
  gaugeCritical = 90;
  panelUrl = '';
  dataSource: 'checkmk' | 'prometheus' = 'checkmk';
  cmkHosts: string[] = [''];
  cmkService = 'CPU utilization';
  cmkGraphIndex = 0;

  addHost() { this.cmkHosts = [...this.cmkHosts, '']; }
  removeHost(i: number) { this.cmkHosts = this.cmkHosts.filter((_, idx) => idx !== i); }

  constructor(
    private http: HttpClient,
    private ref: MatDialogRef<AddWidgetDialogComponent, DashboardWidgetCreate>,
  ) {}

  ngOnInit() {
    this.http.get<FeedSearch[]>(`${environment.apiUrl}/feed-searches/`).subscribe({
      next: searches => {
        this.searches.set(searches);
        this._populateFromExisting();
      },
    });
  }

  private _populateFromExisting() {
    const w = this.dialogData?.existingWidget;
    if (!w) return;
    this.widgetType = w.widget_type as DashboardWidgetCreate['widget_type'];
    this.title = w.title;
    const cfg = w.config as Record<string, unknown>;

    // Common fields
    this.indexPattern = (cfg['index_pattern'] as string) || 'cs-feed-*';
    this.queryString  = (cfg['query_string']  as string) || '';
    this.limit        = Number(cfg['limit']) || 8;
    this.panelUrl     = (cfg['panel_url']     as string) || '';
    this.aggField     = (cfg['agg_field']     as string) || 'severity';

    // Timeseries
    const ds = cfg['data_source'] as string | undefined;
    if (w.widget_type === 'timeseries') {
      this.dataSource = ds === 'prometheus' ? 'prometheus' : 'checkmk';
      if (this.dataSource === 'checkmk') {
        const hosts = cfg['hosts'] as string[] | undefined;
        const singleHost = cfg['host'] as string | undefined;
        this.cmkHosts     = (hosts && hosts.length) ? [...hosts] : (singleHost ? [singleHost] : ['']);
        this.cmkService   = (cfg['service']     as string) || 'CPU utilization';
        this.cmkGraphIndex = Number(cfg['graph_index']) || 0;
        this.hours        = Number(cfg['hours'])       || 4;
        this.unit         = (cfg['unit'] as string)    || '%';
      } else {
        this.promql = (cfg['promql'] as string) || this.promql;
        this.step   = (cfg['step']   as string) || '1m';
        this.hours  = Number(cfg['hours']) || 4;
        this.unit   = (cfg['unit']   as string) || '%';
      }
    }

    // Search-ID pre-select for list/stat/donut/top_hosts
    const sid = cfg['search_id'] as string | undefined;
    if (sid) this.selectedSearchId = sid;
  }

  selectType(type: DashboardWidgetCreate['widget_type']) {
    this.widgetType = type;
    const titleByType: Record<string, string> = {
      stat: 'Alert Count',
      list: 'Neueste Alerts',
      donut: 'Severity-Verteilung',
      bar: 'Alerts nach Severity',
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

  convertToQuery() {
    const msg = this.queryPrompt.trim();
    if (!msg) return;
    this.convertingQuery.set(true);
    const existing = this.queryString.trim();
    const context = existing
      ? `Bestehende Query (bitte erweitern, nicht ersetzen): ${existing}`
      : undefined;
    this.http.post<{ reply: string; index_pattern: string; query_string: string }>(
      `${environment.apiUrl}/ai/search-assistant`,
      { message: msg, context },
    ).subscribe({
      next: res => {
        if (res.query_string !== undefined) this.queryString = res.query_string;
        if (res.index_pattern && !existing) this.indexPattern = res.index_pattern;
        this.queryExplanation = res.reply || '';
        this.convertingQuery.set(false);
      },
      error: () => this.convertingQuery.set(false),
    });
  }

  canCreate(): boolean {
    if (!this.title.trim()) return false;
    if (this.widgetType === 'timeseries') {
      if (this.dataSource === 'checkmk') return this.cmkHosts.some(h => h.trim()) && !!this.cmkService.trim();
      return !!this.promql.trim();
    }
    if (this.widgetType === 'grafana_panel') return !!this.panelUrl.trim();
    return true;
  }

  create() {
    const base = { widget_type: this.widgetType, title: this.title.trim(), gs_w: 4, gs_h: 3 };
    if (this.widgetType === 'gauge') {
      this.ref.close({ ...base, gs_w: 3, gs_h: 3, config: {
        index_pattern: this.indexPattern,
        query_string: this.queryString,
        total_query_string: this.totalQueryString || '*',
        unit: this.unit,
        warn: Number(this.gaugeWarn),
        critical: Number(this.gaugeCritical),
      }});
      return;
    }
    if (this.widgetType === 'stat') {
      this.ref.close({ ...base, gs_w: 2, gs_h: 2, config: { index_pattern: this.indexPattern, query_string: this.queryString } });
    } else if (this.widgetType === 'list') {
      this.ref.close({ ...base, config: { index_pattern: this.indexPattern, query_string: this.queryString, limit: Number(this.limit) || 8 } });
    } else if (this.widgetType === 'donut') {
      this.ref.close({ ...base, gs_w: 5, gs_h: 4, config: { index_pattern: this.indexPattern, query_string: this.queryString } });
    } else if (this.widgetType === 'bar') {
      this.ref.close({ ...base, gs_w: 5, gs_h: 4, config: { index_pattern: this.indexPattern, query_string: this.queryString, agg_field: this.aggField, limit: 10 } });
    } else if (this.widgetType === 'ai_summary') {
      this.ref.close({ ...base, gs_w: 4, gs_h: 2, config: { agent_type: 'sysadmin' } });
    } else if (this.widgetType === 'top_hosts') {
      this.ref.close({ ...base, gs_w: 4, gs_h: 3, config: { index_pattern: this.indexPattern, query_string: this.queryString || 'NOT status:resolved', limit: Number(this.limit) || 8 } });
    } else if (this.widgetType === 'timeseries') {
      if (this.dataSource === 'checkmk') {
        const hosts = this.cmkHosts.map(h => h.trim()).filter(Boolean);
        this.ref.close({ ...base, gs_w: 5, gs_h: 4, config: {
          data_source: 'checkmk',
          hosts: hosts,
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
