import { Component, OnInit, OnDestroy, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient, HttpParams } from '@angular/common/http';
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
import { WebsocketService, WsMessage } from '../../core/services/websocket.service';
import { environment } from '../../../environments/environment';
import { I18nService } from '../../core/services/i18n.service';

const SEVERITY_COLORS: Record<string, string> = {
  critical: '#d32f2f', high: '#f57c00', medium: '#1976d2', low: '#388e3c', info: '#607d8b',
};
const VENDOR_ICONS: Record<string, string> = {
  Juniper: '🟠', Cisco: '🔵', VMware: '🟣', Unknown: '⚪',
};

@Component({
  selector: 'cs-network',
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
        <h2>Netzwerk Switch-Events</h2>
        <div class="header-actions">
          <mat-form-field appearance="outline" class="filter-field">
            <mat-label>Switch-Typ</mat-label>
            <mat-select [(ngModel)]="filterType" (selectionChange)="load()">
              <mat-option value="">Alle</mat-option>
              <mat-option value="nsa">NSA (Access)</mat-option>
              <mat-option value="nss">NSS (Server)</mat-option>
              <mat-option value="nsc">NSC (Core)</mat-option>
            </mat-select>
          </mat-form-field>
          <mat-form-field appearance="outline" class="filter-field">
            <mat-label>Severity</mat-label>
            <mat-select [(ngModel)]="filterSeverity" (selectionChange)="load()">
              <mat-option value="">Alle</mat-option>
              <mat-option value="high">Hoch</mat-option>
              <mat-option value="medium">Mittel</mat-option>
              <mat-option value="low">Niedrig</mat-option>
              <mat-option value="info">Info</mat-option>
            </mat-select>
          </mat-form-field>
          <mat-form-field appearance="outline" class="filter-field">
            <mat-label>Status</mat-label>
            <mat-select [(ngModel)]="filterStatus" (selectionChange)="load()">
              <mat-option value="">{{ i18n.t('common.all') }}</mat-option>
              <mat-option value="new">{{ i18n.t('status.new') }}</mat-option>
              <mat-option value="acknowledged">{{ i18n.t('status.acknowledged') }}</mat-option>
            </mat-select>
          </mat-form-field>
          <button mat-stroked-button [disabled]="triggering()" (click)="triggerAgent()">
            @if (triggering()) { <mat-spinner diameter="16"></mat-spinner> }
            @else { <mat-icon>network_check</mat-icon> }
            Network Agent
          </button>
        </div>
      </div>

      @if (loading()) {
        <div class="spinner-center"><mat-spinner diameter="40"></mat-spinner></div>
      } @else {
        <!-- Stats row -->
        <div class="stats-row">
          @for (stat of stats(); track stat.location) {
            <mat-card class="stat-card">
              <div class="stat-location">{{ stat.location }}</div>
              <div class="stat-count">{{ stat.count }}</div>
              <div class="stat-label">Events</div>
            </mat-card>
          }
        </div>

        <!-- Event list -->
        <div class="event-list">
          @for (event of events(); track event.id) {
            <div class="event-row" [class.event-new]="event.status === 'new'">
              <div class="sev-bar" [style.background-color]="sevColor(event.severity)"></div>
              <div class="event-content">
                <div class="event-top">
                  <span class="event-vendor">{{ vendorIcon(event.vendor) }}</span>
                  <span class="event-switch">{{ event.switch_name }}</span>
                  <mat-chip class="type-chip">{{ event.switch_type?.toUpperCase() }}</mat-chip>
                  @if (event.location_name) {
                    <mat-chip class="loc-chip">{{ event.location_name }}</mat-chip>
                  }
                  @if (event.location_city) {
                    <span class="city">{{ event.location_city }}</span>
                  }
                  <span class="event-time">{{ event.created_at | date:'dd.MM HH:mm' }}</span>
                </div>
                <div class="event-message">{{ event.message | slice:0:250 }}</div>
              </div>
              <div class="event-actions">
                @if (event.status === 'new') {
                  <button mat-icon-button [matTooltip]="i18n.t('common.acknowledge')" (click)="acknowledge(event)">
                    <mat-icon>check_circle</mat-icon>
                  </button>
                } @else {
                  <mat-icon class="ack-icon">task_alt</mat-icon>
                }
              </div>
            </div>
          }
          @if (events().length === 0) {
            <div class="empty-state">Keine Switch-Events gefunden.</div>
          }
        </div>
      }
    </div>
  `,
  styles: [`
    .page-container { padding: 24px; max-width: 1200px; }
    .page-header { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 16px; flex-wrap: wrap; gap: 8px; }
    .page-header h2 { margin: 0; }
    .header-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .filter-field { width: 140px; }
    .stats-row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
    .stat-card { padding: 12px 16px; min-width: 100px; text-align: center; }
    .stat-location { font-size: 12px; font-weight: 600; }
    .stat-count { font-size: 24px; font-weight: 700; }
    .stat-label { font-size: 11px; color: var(--mat-sys-on-surface-variant); }
    .event-list { display: flex; flex-direction: column; gap: 4px; }
    .event-row { display: flex; align-items: stretch; background: var(--mat-sys-surface); border-radius: 4px; overflow: hidden; box-shadow: 0 1px 2px rgba(0,0,0,.15); }
    .sev-bar { width: 4px; flex-shrink: 0; }
    .event-content { flex: 1; padding: 8px 12px; min-width: 0; }
    .event-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; }
    .event-vendor { font-size: 14px; }
    .event-switch { font-family: monospace; font-weight: 700; font-size: 13px; }
    mat-chip { font-size: 10px; min-height: 18px; }
    .type-chip { background: #e3f2fd; }
    .loc-chip { background: #f3e5f5; }
    .city { font-size: 12px; color: var(--mat-sys-on-surface-variant); }
    .event-time { margin-left: auto; font-size: 11px; color: var(--mat-sys-on-surface-variant); }
    .event-message { font-size: 11px; font-family: monospace; color: var(--mat-sys-on-surface-variant); word-break: break-all; }
    .event-actions { display: flex; align-items: center; padding: 0 8px; }
    .ack-icon { color: var(--mat-sys-tertiary); font-size: 20px; width: 20px; height: 20px; }
    .empty-state { text-align: center; padding: 40px; color: var(--mat-sys-on-surface-variant); }
    .spinner-center { display: flex; justify-content: center; padding: 40px; }
    mat-spinner { display: inline-block; }
  `],
})
export class NetworkComponent implements OnInit, OnDestroy {
  readonly i18n = inject(I18nService);
  events = signal<any[]>([]);
  stats = signal<{ location: string; count: number }[]>([]);
  loading = signal(true);
  triggering = signal(false);
  filterType = '';
  filterSeverity = '';
  filterStatus = 'new';

  private destroy$ = new Subject<void>();

  constructor(
    private http: HttpClient,
    private ws: WebsocketService,
    private snack: MatSnackBar,
  ) {}

  ngOnInit() {
    this.load();
    this.ws.messages().pipe(takeUntil(this.destroy$)).subscribe((msg: WsMessage) => {
      if (msg.type === 'network_event') this.load();
    });
  }

  ngOnDestroy() { this.destroy$.next(); this.destroy$.complete(); }

  load() {
    this.loading.set(true);
    let p = new HttpParams();
    if (this.filterType)     p = p.set('switch_type', this.filterType);
    if (this.filterSeverity) p = p.set('severity', this.filterSeverity);
    if (this.filterStatus)   p = p.set('status', this.filterStatus);
    p = p.set('limit', '200');

    this.http.get<any[]>(`${environment.apiUrl}/network/switch-events`, { params: p }).subscribe({
      next: evts => {
        this.events.set(evts);
        this.buildStats(evts);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  buildStats(evts: any[]) {
    const counts = new Map<string, number>();
    for (const e of evts) {
      const loc = e.location_name || 'Unbekannt';
      counts.set(loc, (counts.get(loc) ?? 0) + 1);
    }
    this.stats.set(
      [...counts.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 8)
        .map(([location, count]) => ({ location, count }))
    );
  }

  acknowledge(event: any) {
    this.http.post(`${environment.apiUrl}/network/switch-events/${event.id}/acknowledge`, {}).subscribe({
      next: () => this.load(),
      error: () => this.snack.open('Fehler', 'OK', { duration: 3000 }),
    });
  }

  triggerAgent() {
    this.triggering.set(true);
    this.http.post(`${environment.apiUrl}/ai/trigger/network`, {}).subscribe({
      next: () => {
        this.triggering.set(false);
        this.snack.open('Netzwerk-Agent gestartet', 'OK', { duration: 4000 });
      },
      error: () => {
        this.triggering.set(false);
        this.snack.open('Fehler beim Starten des Agenten', 'OK', { duration: 3000 });
      },
    });
  }

  sevColor(sev: string): string {
    return SEVERITY_COLORS[sev] ?? '#607d8b';
  }

  vendorIcon(vendor: string): string {
    return VENDOR_ICONS[vendor] ?? '⚪';
  }
}
