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
            <div class="alert-row" [class.alert-new]="alert.status === 'new'">
              <div class="severity-bar" [style.background-color]="severityColor(alert.severity)"></div>
              <div class="alert-content">
                <div class="alert-top">
                  <span class="alert-title">{{ alert.title }}</span>
                  <div class="alert-chips">
                    <mat-chip class="chip-source">{{ alert.source }}</mat-chip>
                    <mat-chip class="chip-severity" [style.background-color]="severityColor(alert.severity) + '33'">
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
                <div class="alert-meta">
                  <span class="alert-time">{{ alert.created_at | date:'dd.MM.yyyy HH:mm' }}</span>
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
    .alert-meta { font-size: 11px; color: var(--mat-sys-on-surface-variant); margin-top: 4px; }
    .alert-actions { display: flex; align-items: center; padding: 0 8px; }
    .ack-icon { color: var(--mat-sys-tertiary); font-size: 20px; width: 20px; height: 20px; }
    .empty-state { text-align: center; padding: 40px; color: var(--mat-sys-on-surface-variant); }
    .spinner-center { display: flex; justify-content: center; padding: 40px; }
    mat-spinner { display: inline-block; }
  `],
})
export class AlertsComponent implements OnInit, OnDestroy {
  alerts = signal<Alert[]>([]);
  loading = signal(true);
  aggregating = signal(false);
  filterSource = '';
  filterSeverity = '';
  filterStatus = 'new';

  // CheckMK-specific metadata filters (client-side, multi-select, pre-populated from user prefs)
  filterCriticality = signal<string[]>([]);
  filterVe          = signal<string[]>([]);
  filterLocation    = signal<string[]>([]);
  filterSite        = signal<string[]>([]);
  filterOs          = signal<string[]>([]);

  filteredAlerts = computed(() => {
    let list = this.alerts();
    const crit = this.filterCriticality();
    const ve   = this.filterVe();
    const loc  = this.filterLocation();
    const site = this.filterSite();
    const os   = this.filterOs();
    const meta = (a: Alert, k: string) => String(a.metadata_?.[k] ?? '');
    if (crit.length) list = list.filter(a => crit.includes(meta(a, 'criticality')));
    if (ve.length)   list = list.filter(a => ve.includes(meta(a, 've')));
    if (loc.length)  list = list.filter(a => loc.includes(meta(a, 'location')));
    if (site.length) list = list.filter(a => site.includes(meta(a, 'site')));
    if (os.length)   list = list.filter(a => os.includes(meta(a, 'os')));
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
