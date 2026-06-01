import { Component, OnInit, OnDestroy, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatChipsModule } from '@angular/material/chips';
import { MatSelectModule } from '@angular/material/select';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { Subject, takeUntil } from 'rxjs';
import { AlertService } from '../../core/services/alert.service';
import { WebsocketService, WsMessage } from '../../core/services/websocket.service';
import { Alert, Severity } from '../../core/models/alert.model';
import { environment } from '../../../environments/environment';

const SEVERITY_COLORS: Record<string, string> = {
  critical: '#d32f2f',
  high:     '#f57c00',
  medium:   '#1976d2',
  low:      '#388e3c',
  info:     '#607d8b',
};

@Component({
  selector: 'cs-alerts',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatCardModule, MatButtonModule, MatIconModule,
    MatChipsModule, MatSelectModule, MatFormFieldModule,
    MatProgressSpinnerModule, MatTooltipModule, MatSnackBarModule,
  ],
  template: `
    <div class="page-container">
      <div class="page-header">
        <div>
          <h2>Alerts</h2>
          <p class="page-subtitle">Persistenter Alert-Speicher — Incident-Tracking mit Status-Verwaltung</p>
        </div>
        <div class="header-actions">
          <mat-form-field appearance="outline" class="filter-field">
            <mat-label>Quelle</mat-label>
            <mat-select [(ngModel)]="filterSource" (selectionChange)="onSourceChange()">
              <mat-option value="">Alle</mat-option>
              <mat-option value="checkmk">CheckMK</mat-option>
              <mat-option value="graylog">Graylog</mat-option>
              <mat-option value="wazuh">Wazuh</mat-option>
            </mat-select>
          </mat-form-field>
          <mat-form-field appearance="outline" class="filter-field">
            <mat-label>Severity</mat-label>
            <mat-select [(ngModel)]="filterSeverity" (selectionChange)="load()">
              <mat-option value="">Alle</mat-option>
              <mat-option value="critical">Kritisch</mat-option>
              <mat-option value="high">Hoch</mat-option>
              <mat-option value="medium">Mittel</mat-option>
              <mat-option value="low">Niedrig</mat-option>
              <mat-option value="info">Info</mat-option>
            </mat-select>
          </mat-form-field>
          <mat-form-field appearance="outline" class="filter-field">
            <mat-label>Status</mat-label>
            <mat-select [(ngModel)]="filterStatus" (selectionChange)="load()">
              <mat-option value="">Alle</mat-option>
              <mat-option value="new">Neu</mat-option>
              <mat-option value="acknowledged">Bestätigt</mat-option>
            </mat-select>
          </mat-form-field>

          @if (filterSource === 'checkmk') {
            @if (availableCriticalities().length) {
              <mat-form-field appearance="outline" class="filter-field">
                <mat-label>Criticality</mat-label>
                <mat-select multiple [ngModel]="filterCriticality()" (ngModelChange)="filterCriticality.set($event)">
                  @for (v of availableCriticalities(); track v) {
                    <mat-option [value]="v">{{ v }}</mat-option>
                  }
                </mat-select>
              </mat-form-field>
            }
            @if (availableVes().length) {
              <mat-form-field appearance="outline" class="filter-field">
                <mat-label>Umgebung (VE)</mat-label>
                <mat-select multiple [ngModel]="filterVe()" (ngModelChange)="filterVe.set($event)">
                  @for (v of availableVes(); track v) {
                    <mat-option [value]="v">{{ v }}</mat-option>
                  }
                </mat-select>
              </mat-form-field>
            }
            @if (availableLocations().length) {
              <mat-form-field appearance="outline" class="filter-field">
                <mat-label>Location</mat-label>
                <mat-select multiple [ngModel]="filterLocation()" (ngModelChange)="filterLocation.set($event)">
                  @for (v of availableLocations(); track v) {
                    <mat-option [value]="v">{{ v }}</mat-option>
                  }
                </mat-select>
              </mat-form-field>
            }
            @if (availableSites().length) {
              <mat-form-field appearance="outline" class="filter-field">
                <mat-label>Site</mat-label>
                <mat-select multiple [ngModel]="filterSite()" (ngModelChange)="filterSite.set($event)">
                  @for (v of availableSites(); track v) {
                    <mat-option [value]="v">{{ v }}</mat-option>
                  }
                </mat-select>
              </mat-form-field>
            }
            @if (availableOs().length) {
              <mat-form-field appearance="outline" class="filter-field">
                <mat-label>Betriebssystem</mat-label>
                <mat-select multiple [ngModel]="filterOs()" (ngModelChange)="filterOs.set($event)">
                  @for (v of availableOs(); track v) {
                    <mat-option [value]="v">{{ v }}</mat-option>
                  }
                </mat-select>
              </mat-form-field>
            }
          }

          <button mat-stroked-button (click)="runAggregation()" [disabled]="aggregating()">
            @if (aggregating()) { <mat-spinner diameter="16"></mat-spinner> }
            @else { <mat-icon>sync</mat-icon> }
            Jetzt aktualisieren
          </button>
        </div>
      </div>

      @if (loading()) {
        <div class="spinner-center"><mat-spinner diameter="40"></mat-spinner></div>
      } @else {
        <div class="alert-list">
          @for (alert of filteredAlerts(); track alert.id) {
            <div class="alert-row" [class.alert-new]="alert.status === 'new'" [attr.data-severity]="alert.severity" [attr.data-source]="alert.source">
              <div class="severity-bar" [style.background-color]="severityColor(alert.severity)"></div>
              <!-- LCARS header bar: plain text only, no Material chips -->
              <div class="lcars-alert-header">
                <span class="lah-source">{{ alert.source | uppercase }}</span>
                <span class="lah-dot">·</span>
                <span class="lah-sev" [attr.data-sev]="alert.severity">{{ alert.severity | uppercase }}</span>
                @if (alertHost(alert); as host) {
                  <span class="lah-dot">·</span>
                  <span class="lah-host">{{ host }}</span>
                }
                @if (alert.location_name) {
                  <span class="lah-dot">·</span>
                  <span class="lah-loc">{{ alert.location_name }}</span>
                }
                <span class="lah-spacer"></span>
                <span class="lah-time">{{ alert.created_at | date:'dd.MM HH:mm' }}</span>
              </div>
              <div class="alert-content">
                <div class="alert-top">
                  <span class="alert-title">{{ alert.title }}</span>
                  <div class="alert-chips">
                    <mat-chip class="chip-source" [attr.data-chip-source]="alert.source">{{ alert.source }}</mat-chip>
                    <mat-chip class="chip-severity"
                      [style.background-color]="severityColor(alert.severity) + '33'"
                      [attr.data-chip-sev]="alert.severity">
                      {{ alert.severity }}
                    </mat-chip>
                    @if (alertHost(alert); as host) {
                      <mat-chip class="chip-host" matTooltip="Host / Server">
                        <mat-icon>dns</mat-icon>{{ host }}
                      </mat-chip>
                    }
                    @if (alert.location_name) {
                      <mat-chip class="chip-location">{{ alert.location_name }}</mat-chip>
                    }
                  </div>
                </div>
                @if (alert.body) {
                  <div class="alert-body">{{ alert.body | slice:0:200 }}{{ alert.body!.length > 200 ? '…' : '' }}</div>
                }
                @if (alert.ai_insight) {
                  <div class="ai-insight">
                    <mat-icon class="ai-insight-icon">psychology</mat-icon>
                    <span>{{ alert.ai_insight }}</span>
                  </div>
                }
                <div class="alert-meta">
                  <span class="alert-time">{{ alert.created_at | date:'dd.MM.yyyy HH:mm' }}</span>
                  <button mat-button class="ki-btn" (click)="requestEnrich(alert)" [disabled]="isEnriching(alert.id)">
                    @if (isEnriching(alert.id)) {
                      <mat-spinner diameter="14"></mat-spinner>
                    } @else {
                      <mat-icon>psychology</mat-icon>
                    }
                    {{ alert.ai_insight ? 'Neu analysieren' : 'KI Analyse' }}
                  </button>
                </div>
              </div>
              <div class="alert-actions">
                @if (alert.status === 'new') {
                  <button mat-icon-button matTooltip="Bestätigen" (click)="acknowledge(alert)">
                    <mat-icon>check_circle</mat-icon>
                  </button>
                } @else {
                  <mat-icon class="ack-icon" matTooltip="Bestätigt">task_alt</mat-icon>
                }
              </div>
            </div>
          }
          @if (filteredAlerts().length === 0) {
            <div class="empty-state">Keine Alerts gefunden.</div>
          }
        </div>
      }
    </div>
  `,
  styles: [`
    .page-container { padding: 24px; max-width: 1200px; }
    .page-header { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 16px; flex-wrap: wrap; gap: 8px; }
    .page-header h2 { margin: 0; }
    .page-subtitle { font-size: 13px; color: var(--mat-sys-on-surface-variant); margin: 2px 0 0; }
    .header-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .filter-field { width: 140px; }
    .alert-list { display: flex; flex-direction: column; gap: 4px; }
    .alert-row { display: flex; align-items: stretch; background: var(--mat-sys-surface); border-radius: 4px; overflow: hidden; box-shadow: 0 1px 2px rgba(0,0,0,.15); }
    .alert-row.alert-new { border-left: none; }
    .severity-bar { width: 4px; flex-shrink: 0; }
    .alert-content { flex: 1; padding: 10px 12px; min-width: 0; }
    .alert-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; }
    .alert-title { font-size: 14px; font-weight: 500; flex: 1; }
    .alert-chips { display: flex; gap: 4px; }
    mat-chip { font-size: 10px; min-height: 18px; }
    .chip-host { background: var(--mat-sys-surface-variant); font-family: monospace; }
    .chip-host mat-icon { font-size: 12px; width: 12px; height: 12px; margin-right: 3px; }
    .alert-body { font-size: 12px; color: var(--mat-sys-on-surface-variant); font-family: monospace; white-space: pre-wrap; word-break: break-all; }
    .ai-insight {
      display: flex; align-items: flex-start; gap: 6px;
      margin: 6px 0 4px; padding: 6px 8px; border-radius: 6px;
      background: color-mix(in srgb, var(--mat-sys-primary) 8%, transparent);
      font-size: 12px; line-height: 1.5; color: var(--mat-sys-on-surface);
    }
    .ai-insight-icon { font-size: 16px; height: 16px; width: 16px; color: var(--mat-sys-primary); flex-shrink: 0; margin-top: 1px; }
    .alert-meta { display: flex; align-items: center; gap: 8px; font-size: 11px; color: var(--mat-sys-on-surface-variant); margin-top: 4px; }
    .ki-btn { font-size: 11px; height: 26px; line-height: 26px; min-width: 0; padding: 0 8px; color: var(--mat-sys-primary); }
    .ki-btn mat-icon { font-size: 14px; height: 14px; width: 14px; margin-right: 3px; vertical-align: middle; }
    .alert-actions { display: flex; align-items: center; padding: 0 8px; }
    .ack-icon { color: var(--mat-sys-tertiary); font-size: 20px; width: 20px; height: 20px; }
    .empty-state { text-align: center; padding: 40px; color: var(--mat-sys-on-surface-variant); }
    .spinner-center { display: flex; justify-content: center; padding: 40px; }
    mat-spinner { display: inline-block; }

    /* ══ LCARS THEME ══════════════════════════════════════════════════════════ */
    :host-context(html.cs-theme-lcars) .page-container {
      font-family: 'Antonio','Eurostile','Roboto Condensed',sans-serif;
      padding: 12px 16px;
    }
    :host-context(html.cs-theme-lcars) .page-header h2 {
      font-size: 20px; font-weight: 800; letter-spacing: .22em; text-transform: uppercase;
      color: #ffcc66; background: #000; display: inline-block; padding: 3px 10px 3px 0;
      margin: 0;
    }
    :host-context(html.cs-theme-lcars) .page-subtitle { color: rgba(255,204,153,.5); font-size: 11px; letter-spacing: .06em; }
    :host-context(html.cs-theme-lcars) .alert-list { gap: 6px; }
    :host-context(html.cs-theme-lcars) .alert-row {
      background: #15120c;
      border: none;
      border-left: 8px solid #e87c3a;
      border-radius: 0 14px 14px 0;
      box-shadow: none;
      overflow: hidden;
    }
    /* severity → left border color (same as news feed) */
    :host-context(html.cs-theme-lcars) .alert-row[data-severity="critical"] { border-left-color: #ff5544; }
    :host-context(html.cs-theme-lcars) .alert-row[data-severity="high"]     { border-left-color: #ffcc00; }
    :host-context(html.cs-theme-lcars) .alert-row[data-severity="medium"]   { border-left-color: #ff9966; }
    :host-context(html.cs-theme-lcars) .alert-row[data-severity="warning"]  { border-left-color: #ffcc00; }
    :host-context(html.cs-theme-lcars) .alert-row[data-severity="low"]      { border-left-color: #7fb3d3; }
    :host-context(html.cs-theme-lcars) .alert-row[data-severity="info"]     { border-left-color: #66cc66; }
    /* hide the 4px severity-bar (replaced by border-left) */
    /* LCARS header: hidden by default */
    .lcars-alert-header { display: none; }

    :host-context(html.cs-theme-lcars) .severity-bar { display: none; }
    /* Show LCARS plain-text header, hide chip-based alert-top */
    :host-context(html.cs-theme-lcars) .lcars-alert-header {
      display: flex; align-items: center; gap: 6px;
      background: #e87c3a;   /* default: checkmk orange */
      padding: 7px 14px;
      border-radius: 0 13px 0 0;
      font-family: 'Antonio','Eurostile','Roboto Condensed',sans-serif;
      font-size: 11px; font-weight: 900; letter-spacing: .08em;
      color: #000; flex-shrink: 0;
    }
    :host-context(html.cs-theme-lcars) .alert-row[data-source="graylog"] .lcars-alert-header { background: #ffcc66; }
    :host-context(html.cs-theme-lcars) .alert-row[data-source="wazuh"]   .lcars-alert-header { background: #7fb3d3; }
    :host-context(html.cs-theme-lcars) .alert-row[data-source="o365"]    .lcars-alert-header { background: #c99aa4; }
    :host-context(html.cs-theme-lcars) .lah-source { font-size: 12px; }
    :host-context(html.cs-theme-lcars) .lah-dot    { opacity: .5; }
    :host-context(html.cs-theme-lcars) .lah-sev    { font-size: 10px; padding: 1px 6px; border-radius: 2px; background: rgba(0,0,0,.18); }
    :host-context(html.cs-theme-lcars) .lah-host   { font-family: 'Fira Code',monospace; font-size: 11px; opacity: .85; font-weight: 400; letter-spacing: 0; }
    :host-context(html.cs-theme-lcars) .lah-loc    { opacity: .65; font-size: 10px; }
    :host-context(html.cs-theme-lcars) .lah-spacer { flex: 1; }
    :host-context(html.cs-theme-lcars) .lah-time   { opacity: .55; font-size: 10px; font-weight: 400; }
    /* Body: dark bg, gold title, hidden chip row */
    :host-context(html.cs-theme-lcars) .alert-content { padding: 8px 14px 6px; }
    :host-context(html.cs-theme-lcars) .alert-top { margin-bottom: 4px; }
    :host-context(html.cs-theme-lcars) .alert-chips { display: none; }  /* shown in lcars-alert-header */
    :host-context(html.cs-theme-lcars) .alert-title { color: #ffe8a0 !important; font-size: 13px; font-weight: 600; }
    /* body content */
    :host-context(html.cs-theme-lcars) .alert-body { color: #e8a060; font-size: 11px; line-height: 1.5; }
    :host-context(html.cs-theme-lcars) .ai-insight {
      background: rgba(232,124,58,.1); border-left: 3px solid #e87c3a;
      color: #ffcc99; margin: 6px 0 4px; border-radius: 0;
    }
    :host-context(html.cs-theme-lcars) .ai-insight-icon { color: #e87c3a; }
    :host-context(html.cs-theme-lcars) .alert-meta { color: rgba(255,232,160,.45); font-size: 10px; padding: 0 14px 4px; }
    :host-context(html.cs-theme-lcars) .ai-insight { margin: 0 14px 4px; }
    :host-context(html.cs-theme-lcars) .ki-btn { color: #e87c3a !important; }
    :host-context(html.cs-theme-lcars) .alert-actions { background: #0a0804; border-left: 1px solid #2a1d0a; }
    :host-context(html.cs-theme-lcars) .ack-icon { color: #66cc66; }
    :host-context(html.cs-theme-lcars) .empty-state { color: #5a3a18; }
    :host-context(html.cs-theme-lcars) .spinner-center mat-spinner { --mdc-circular-progress-active-indicator-color: #e87c3a; }

    /* ══ HOLO THEME ══════════════════════════════════════════════════════════ */
    :host-context(html.cs-theme-holo) .page-header h2 { color: #9fe8ff; font-size: 18px; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
    :host-context(html.cs-theme-holo) .page-subtitle { color: rgba(143,184,207,.6); }
    :host-context(html.cs-theme-holo) .alert-row {
      background: rgba(10,28,46,.85); border: none;
      border-left: 6px solid #4fd6ff; border-radius: 0 12px 12px 0; box-shadow: none;
    }
    :host-context(html.cs-theme-holo) .alert-row[data-severity="critical"] { border-left-color: #ff5b6e; }
    :host-context(html.cs-theme-holo) .alert-row[data-severity="high"]     { border-left-color: #ffd84a; }
    :host-context(html.cs-theme-holo) .alert-row[data-severity="low"]      { border-left-color: #3dffa8; }
    :host-context(html.cs-theme-holo) .severity-bar { display: none; }
    :host-context(html.cs-theme-holo) .alert-content { padding: 8px 14px 6px; }
    :host-context(html.cs-theme-holo) .alert-top { background: transparent; }
    :host-context(html.cs-theme-holo) .alert-title { color: #cfeeff !important; }
    :host-context(html.cs-theme-holo) .alert-body { color: #8fb8cf; }
    :host-context(html.cs-theme-holo) .ai-insight { background: rgba(79,214,255,.08); border-left: 3px solid #4fd6ff; color: #bfefff; }
    :host-context(html.cs-theme-holo) .ai-insight-icon { color: #4fd6ff; }
    :host-context(html.cs-theme-holo) .alert-meta { color: rgba(143,184,207,.5); }
    :host-context(html.cs-theme-holo) .ki-btn { color: #4fd6ff !important; }
    :host-context(html.cs-theme-holo) .alert-actions { background: rgba(5,15,30,.5); }
    :host-context(html.cs-theme-holo) .ack-icon { color: #3dffa8; }
    :host-context(html.cs-theme-holo) .empty-state { color: rgba(79,214,255,.3); }
  `],
})
export class AlertsComponent implements OnInit, OnDestroy {
  alerts = signal<Alert[]>([]);
  loading = signal(true);
  aggregating = signal(false);
  filterSource = '';
  filterSeverity = '';
  filterStatus = '';

  // CheckMK-specific metadata filters (client-side, multi-select, pre-populated from user prefs)
  filterCriticality = signal<string[]>([]);
  filterVe          = signal<string[]>([]);
  filterLocation    = signal<string[]>([]);
  filterSite        = signal<string[]>([]);
  filterOs          = signal<string[]>([]);
  filterHostgroup   = signal<string[]>([]);

  filteredAlerts = computed(() => {
    let list = this.alerts();
    const crit      = this.filterCriticality();
    const ve        = this.filterVe();
    const loc       = this.filterLocation();
    const site      = this.filterSite();
    const os        = this.filterOs();
    const hostgroup = this.filterHostgroup();
    // Metadata filters only apply to checkmk alerts; non-checkmk alerts always pass through
    const isCheckmk = (a: Alert) => a.source === 'checkmk';
    const metaOk = (a: Alert, k: string, vals: string[]) => {
      if (!vals.length || !isCheckmk(a)) return true;
      const v = a.metadata_?.[k];
      return !v || vals.includes(String(v));
    };
    const hostgroupOk = (a: Alert) => {
      if (!hostgroup.length || !isCheckmk(a)) return true;
      const hgs: string[] = (a.metadata_?.['hostgroups'] as unknown as string[]) || [];
      return !hgs.length || hgs.some(h => hostgroup.includes(h));
    };
    list = list.filter(a =>
      metaOk(a, 'criticality', crit) &&
      metaOk(a, 've', ve) &&
      metaOk(a, 'location', loc) &&
      metaOk(a, 'site', site) &&
      metaOk(a, 'os', os) &&
      hostgroupOk(a)
    );
    return list;
  });

  availableCriticalities = computed(() =>
    [...new Set(this.alerts().map(a => a.metadata_?.['criticality'] as string).filter(Boolean))].sort()
  );
  availableVes = computed(() =>
    [...new Set(this.alerts().map(a => a.metadata_?.['ve'] as string).filter(Boolean))].sort()
  );
  availableLocations = computed(() =>
    [...new Set(this.alerts().map(a => a.metadata_?.['location'] as string).filter(Boolean))].sort()
  );
  availableSites = computed(() =>
    [...new Set(this.alerts().map(a => a.metadata_?.['site'] as string).filter(Boolean))].sort()
  );
  availableOs = computed(() =>
    [...new Set(this.alerts().map(a => a.metadata_?.['os'] as string).filter(Boolean))].sort()
  );

  private readonly STORAGE_KEY = 'cs_alerts_filters';
  private destroy$ = new Subject<void>();

  constructor(
    private svc: AlertService,
    private ws: WebsocketService,
    private snack: MatSnackBar,
    private http: HttpClient,
  ) {}

  ngOnInit() {
    this.restoreFilters();
    // Pre-populate CheckMK meta-filters from user preferences (My Settings)
    this.http.get<any>(`${environment.apiUrl}/preferences`).subscribe({
      next: prefs => {
        if (prefs?.checkmk_criticality?.length) this.filterCriticality.set(prefs.checkmk_criticality);
        if (prefs?.checkmk_locations?.length)   this.filterLocation.set(prefs.checkmk_locations);
        if (prefs?.checkmk_ve?.length)          this.filterVe.set(prefs.checkmk_ve);
        if (prefs?.checkmk_os?.length)          this.filterOs.set(prefs.checkmk_os);
        if (prefs?.checkmk_hostgroups?.length)  this.filterHostgroup.set(prefs.checkmk_hostgroups);
      },
    });
    this.load();
    this.ws.messages().pipe(takeUntil(this.destroy$)).subscribe((msg: WsMessage) => {
      if (msg.type === 'new_alert' || msg.type === 'alert_acknowledged') {
        this.load();
      }
    });
  }

  private saveFilters() {
    localStorage.setItem(this.STORAGE_KEY, JSON.stringify({
      source: this.filterSource,
      severity: this.filterSeverity,
      status: this.filterStatus,
    }));
  }

  private restoreFilters() {
    try {
      const raw = localStorage.getItem(this.STORAGE_KEY);
      if (raw) {
        const saved = JSON.parse(raw);
        if (saved.source   !== undefined) this.filterSource   = saved.source;
        if (saved.severity !== undefined) this.filterSeverity = saved.severity;
        if (saved.status   !== undefined) this.filterStatus   = saved.status;
      }
    } catch { /* ignore parse errors */ }
  }

  ngOnDestroy() { this.destroy$.next(); this.destroy$.complete(); }

  onSourceChange() {
    if (this.filterSource !== 'checkmk') {
      this.filterCriticality.set([]);
      this.filterVe.set([]);
      this.filterLocation.set([]);
      this.filterSite.set([]);
      this.filterOs.set([]);
    }
    this.load();
  }

  load() {
    this.saveFilters();
    this.loading.set(true);
    this.svc.list({
      source: this.filterSource || undefined,
      severity: this.filterSeverity || undefined,
      status: this.filterStatus || undefined,
      limit: 200,
    }).subscribe({
      next: alerts => { this.alerts.set(alerts); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }

  acknowledge(alert: Alert) {
    this.svc.acknowledge(alert.id).subscribe({
      next: () => this.load(),
      error: () => this.snack.open('Fehler beim Bestätigen', 'OK', { duration: 3000 }),
    });
  }

  enrichingIds = signal<Set<string>>(new Set());

  isEnriching(id: string) { return this.enrichingIds().has(id); }

  requestEnrich(alert: Alert) {
    this.enrichingIds.update(s => new Set([...s, alert.id]));
    this.http.post<{ ai_insight: string }>(`${environment.apiUrl}/feed/${alert.id}/enrich`, {}).subscribe({
      next: res => {
        this.alerts.update(list => list.map(a => a.id === alert.id ? { ...a, ai_insight: res.ai_insight } : a));
        this.enrichingIds.update(s => { const n = new Set(s); n.delete(alert.id); return n; });
      },
      error: () => {
        this.enrichingIds.update(s => { const n = new Set(s); n.delete(alert.id); return n; });
        this.snack.open('KI-Analyse fehlgeschlagen', 'OK', { duration: 3000 });
      },
    });
  }

  runAggregation() {
    this.aggregating.set(true);
    this.svc.aggregate().subscribe({
      next: res => {
        this.aggregating.set(false);
        this.snack.open(`${res.new_alerts} neue Alerts`, 'OK', { duration: 3000 });
        this.load();
      },
      error: () => {
        this.aggregating.set(false);
        this.snack.open('Aggregation fehlgeschlagen', 'OK', { duration: 3000 });
      },
    });
  }

  severityColor(sev: string): string {
    return SEVERITY_COLORS[sev] ?? '#607d8b';
  }

  alertHost(alert: Alert): string {
    const m = alert.metadata_;
    if (!m) return '';
    return (m['host'] as string) || (m['container_name'] as string) || (m['agent'] as string) || '';
  }
}
