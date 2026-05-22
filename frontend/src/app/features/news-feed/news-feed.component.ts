import { Component, OnInit, OnDestroy, AfterViewInit, ElementRef, ViewChild, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatChipsModule } from '@angular/material/chips';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatDividerModule } from '@angular/material/divider';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatBadgeModule } from '@angular/material/badge';
import { MatSliderModule } from '@angular/material/slider';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatSelectModule } from '@angular/material/select';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { environment } from '../../../../environments/environment';

interface FeedItem {
  id: string;
  type: 'alert' | 'email' | 'teams_message';
  source: 'checkmk' | 'graylog' | 'wazuh' | 'o365' | 'teams';
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info';
  title: string;
  body: string | null;
  metadata: Record<string, any> | null;
  created_at: string;
  status: 'new' | 'acknowledged';
  location_name: string | null;
  location_city: string | null;
  external_url: string | null;
}

interface FeedPrefs {
  checkmk_min_age_minutes: number;
  sources_enabled: string[];
  teams_channels: string[];
}

const SOURCE_META: Record<string, { label: string; icon: string; color: string }> = {
  checkmk:  { label: 'CheckMK',       icon: 'monitor_heart',    color: '#1565c0' },
  graylog:  { label: 'Graylog',       icon: 'article',          color: '#6a1b9a' },
  wazuh:    { label: 'Wazuh',         icon: 'security',         color: '#b71c1c' },
  o365:     { label: 'E-Mail',        icon: 'mail',             color: '#e65100' },
  teams:    { label: 'Teams',         icon: 'groups',           color: '#0f4c96' },
};

const SEVERITY_COLOR: Record<string, string> = {
  critical: '#b71c1c',
  high:     '#e65100',
  medium:   '#f57c00',
  low:      '#388e3c',
  info:     '#0288d1',
};

@Component({
  selector: 'cs-news-feed',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatCardModule, MatButtonModule, MatIconModule, MatChipsModule,
    MatProgressSpinnerModule, MatDividerModule, MatTooltipModule,
    MatSnackBarModule, MatBadgeModule, MatSliderModule,
    MatSlideToggleModule, MatSelectModule, MatFormFieldModule, MatInputModule,
  ],
  template: `
    <div class="feed-page">

      <!-- ── Top bar ────────────────────────────────────────────────────── -->
      <div class="feed-topbar">
        <h2>News Feed</h2>
        <div class="topbar-right">
          <mat-chip-set aria-label="Quellen">
            @for (src of allSources; track src.id) {
              <mat-chip
                [selected]="activeFilter().includes(src.id)"
                (click)="toggleFilter(src.id)"
                [style.--mdc-chip-selected-container-color]="src.color + '33'"
                [style.--mdc-chip-selected-label-text-color]="src.color"
                [style.border]="activeFilter().includes(src.id) ? '1px solid ' + src.color : '1px solid transparent'">
                <mat-icon style="font-size:16px;height:16px;width:16px;margin-right:4px">{{ src.icon }}</mat-icon>
                {{ src.label }}
              </mat-chip>
            }
          </mat-chip-set>
          <button mat-icon-button (click)="toggleFilters()" matTooltip="Filter" [class.active-icon]="hasActiveFilter()">
            <mat-icon>filter_list</mat-icon>
          </button>
          <button mat-icon-button (click)="showSettings.set(!showSettings())" matTooltip="Feed-Einstellungen">
            <mat-icon>tune</mat-icon>
          </button>
          <button mat-icon-button (click)="load(true)" matTooltip="Aktualisieren" [disabled]="loading()">
            <mat-icon>refresh</mat-icon>
          </button>
        </div>
      </div>

      <!-- ── Filter panel ───────────────────────────────────────────────────── -->
      @if (showFilters()) {
        <mat-card class="settings-card filter-card">
          <div class="filter-grid">
            <mat-form-field appearance="outline" class="filter-field">
              <mat-label>System / Hostname</mat-label>
              <input matInput [(ngModel)]="hostFilter" placeholder="z.B. srv-web01" (ngModelChange)="onFilterChange()">
              <mat-icon matSuffix>computer</mat-icon>
            </mat-form-field>
            <mat-form-field appearance="outline" class="filter-field">
              <mat-label>Alert-Schwere</mat-label>
              <mat-select [(ngModel)]="severityFilter" (ngModelChange)="onFilterChange()">
                <mat-option value="">Alle</mat-option>
                <mat-option value="critical">Critical</mat-option>
                <mat-option value="high">High</mat-option>
                <mat-option value="medium">Medium</mat-option>
                <mat-option value="low">Low</mat-option>
                <mat-option value="info">Info</mat-option>
              </mat-select>
            </mat-form-field>
            <mat-form-field appearance="outline" class="filter-field">
              <mat-label>Betriebssystem (CheckMK)</mat-label>
              <mat-select [(ngModel)]="osFilter" (ngModelChange)="onFilterChange()">
                <mat-option value="">Alle</mat-option>
                @for (v of filterValues.os; track v) {
                  <mat-option [value]="v">{{ v }}</mat-option>
                }
              </mat-select>
            </mat-form-field>
            <mat-form-field appearance="outline" class="filter-field">
              <mat-label>Standort / Location (CheckMK)</mat-label>
              <mat-select [(ngModel)]="locationFilter" (ngModelChange)="onFilterChange()">
                <mat-option value="">Alle</mat-option>
                @for (v of filterValues.location; track v) {
                  <mat-option [value]="v">{{ v }}</mat-option>
                }
              </mat-select>
            </mat-form-field>
            <mat-form-field appearance="outline" class="filter-field">
              <mat-label>Kritikalität (CheckMK)</mat-label>
              <mat-select [(ngModel)]="criticalityFilter" (ngModelChange)="onFilterChange()">
                <mat-option value="">Alle</mat-option>
                @for (v of filterValues.criticality; track v) {
                  <mat-option [value]="v">{{ v }}</mat-option>
                }
              </mat-select>
            </mat-form-field>
            <mat-form-field appearance="outline" class="filter-field">
              <mat-label>VE / Umgebung (CheckMK)</mat-label>
              <mat-select [(ngModel)]="veFilter" (ngModelChange)="onFilterChange()">
                <mat-option value="">Alle</mat-option>
                @for (v of filterValues.ve; track v) {
                  <mat-option [value]="v">{{ v }}</mat-option>
                }
              </mat-select>
            </mat-form-field>
          </div>
          @if (hasActiveFilter()) {
            <div style="padding: 0 4px 8px">
              <button mat-button color="warn" (click)="clearFilters()">
                <mat-icon>clear</mat-icon> Filter zurücksetzen
              </button>
            </div>
          }
        </mat-card>
      }

      <!-- ── Settings panel ──────────────────────────────────────────────── -->
      @if (showSettings()) {
        <mat-card class="settings-card">
          <div class="settings-grid">
            <div class="settings-field">
              <label>CheckMK Mindestalter (Minuten)</label>
              <div class="slider-row">
                <input type="range" min="1" max="60" [(ngModel)]="editPrefs.checkmk_min_age_minutes" class="age-slider">
                <span class="slider-value">{{ editPrefs.checkmk_min_age_minutes }} min</span>
              </div>
            </div>
            <div class="settings-field">
              <label>Aktivierte Quellen</label>
              <div class="source-toggles">
                @for (src of allSources; track src.id) {
                  <mat-slide-toggle
                    [(ngModel)]="sourceEnabled[src.id]"
                    [style.--mdc-switch-selected-track-color]="src.color"
                    [style.--mdc-switch-selected-handle-color]="src.color">
                    {{ src.label }}
                  </mat-slide-toggle>
                }
              </div>
            </div>
          </div>
          <div class="settings-actions">
            <button mat-stroked-button (click)="showSettings.set(false)">Abbrechen</button>
            <button mat-flat-button color="primary" (click)="savePrefs()">Speichern</button>
          </div>
        </mat-card>
      }

      <!-- ── Feed ────────────────────────────────────────────────────────── -->
      <div class="feed-column">

        @if (loading() && items().length === 0) {
          <div class="spinner-center"><mat-spinner diameter="48"></mat-spinner></div>
        }

        @for (item of visibleItems(); track item.id) {
          <mat-card class="feed-card" [class.card-acknowledged]="item.status === 'acknowledged'">

            <!-- Card header: avatar + meta -->
            <div class="card-top">
              <div class="source-avatar" [style.background]="sourceColor(item.source)">
                <mat-icon>{{ sourceIcon(item.source) }}</mat-icon>
              </div>
              <div class="card-meta">
                <div class="card-meta-row">
                  <span class="source-label" [style.color]="sourceColor(item.source)">
                    {{ sourceLabel(item.source) }}
                  </span>
                  <span class="severity-badge" [style.background]="severityColor(item.severity) + '22'" [style.color]="severityColor(item.severity)">
                    {{ item.severity }}
                  </span>
                  @if (item.location_name) {
                    <span class="location-tag">
                      <mat-icon style="font-size:12px;height:12px;width:12px">location_on</mat-icon>
                      {{ item.location_name }}{{ item.location_city ? ' · ' + item.location_city : '' }}
                    </span>
                  }
                </div>
                <span class="timestamp" [title]="item.created_at">{{ relTime(item.created_at) }}</span>
              </div>
              @if (item.status === 'acknowledged') {
                <span class="ack-stamp"><mat-icon>check_circle</mat-icon> Bestätigt</span>
              }
            </div>

            <!-- Title — clickable for O365/Teams to open original -->
            @if (item.external_url && (item.type === 'email' || item.type === 'teams_message')) {
              <a class="card-title card-title-link" [class.severity-critical]="item.severity === 'critical'"
                 (click)="openUrl(item.external_url)" role="button" matTooltip="Original öffnen">
                {{ item.title }}
                <mat-icon class="open-icon">open_in_new</mat-icon>
              </a>
            } @else {
              <div class="card-title" [class.severity-critical]="item.severity === 'critical'">
                {{ item.title }}
              </div>
            }

            <!-- Body -->
            @if (item.body) {
              <div class="card-body-text" [class.collapsed]="!expanded.has(item.id)"
                   [class.body-clickable]="item.external_url && (item.type === 'email' || item.type === 'teams_message')"
                   (click)="item.external_url && (item.type === 'email' || item.type === 'teams_message') ? openUrl(item.external_url!) : null">
                {{ item.body }}
              </div>
              @if (item.body.length > 200) {
                <button mat-button class="expand-btn" (click)="toggleExpand(item.id)">
                  {{ expanded.has(item.id) ? 'Weniger anzeigen' : 'Mehr anzeigen' }}
                </button>
              }
            }

            <!-- Sender (email / teams) -->
            @if ((item.type === 'email' || item.type === 'teams_message') && item.metadata?.['from']) {
              <div class="mail-from">
                <mat-icon style="font-size:14px;height:14px;width:14px">
                  {{ item.type === 'email' ? 'person' : 'chat' }}
                </mat-icon>
                {{ item.metadata!['from'] }}
              </div>
            }

            <mat-divider></mat-divider>

            <!-- Actions -->
            <div class="card-actions">
              @if (item.type === 'alert' && item.status === 'new') {
                <button mat-button class="action-btn" (click)="acknowledge(item)">
                  <mat-icon>check_circle_outline</mat-icon>
                  Bestätigen
                </button>
              }
              @if (item.external_url && item.type !== 'email' && item.type !== 'teams_message') {
                <button mat-button class="action-btn" (click)="openUrl(item.external_url!)">
                  <mat-icon>open_in_new</mat-icon> Details
                </button>
              }
              <button mat-button class="action-btn" (click)="createTicket(item)">
                <mat-icon>add_task</mat-icon>
                Ticket
              </button>
              <span class="spacer"></span>
              <span class="item-type-hint">{{ typeLabel(item.type) }}</span>
            </div>

          </mat-card>
        }

        <!-- Infinite scroll sentinel -->
        <div #scrollSentinel class="scroll-sentinel"></div>

        <!-- Loading more indicator -->
        @if (loadingMore()) {
          <div class="load-more-spinner"><mat-spinner diameter="32"></mat-spinner></div>
        }

        <!-- Empty state -->
        @if (!loading() && visibleItems().length === 0) {
          <div class="empty-state">
            <mat-icon>check_circle_outline</mat-icon>
            <p>Keine neuen Meldungen</p>
            <span>Alle Systeme sind ruhig.</span>
          </div>
        }

      </div>
    </div>
  `,
  styles: [`
    .feed-page { padding: 24px; max-width: 720px; margin: 0 auto; }

    /* Top bar */
    .feed-topbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; flex-wrap: wrap; gap: 12px; }
    .feed-topbar h2 { margin: 0; font-size: 22px; font-weight: 600; }
    .topbar-right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

    .active-icon { color: var(--mat-sys-primary) !important; }

    /* Filter + Settings panels */
    .settings-card { padding: 16px 20px; margin-bottom: 20px; }
    .filter-card { padding: 12px 16px 4px; }
    .filter-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
    .filter-field { width: 100%; }
    .settings-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 12px; }
    .settings-field label { font-size: 13px; font-weight: 500; color: var(--mat-sys-on-surface-variant); display: block; margin-bottom: 8px; }
    .slider-row { display: flex; align-items: center; gap: 12px; }
    .age-slider { flex: 1; }
    .slider-value { font-size: 14px; font-weight: 600; min-width: 40px; }
    .source-toggles { display: flex; flex-direction: column; gap: 8px; }
    .settings-actions { display: flex; justify-content: flex-end; gap: 8px; padding-top: 8px; }

    /* Feed cards */
    .feed-column { display: flex; flex-direction: column; gap: 12px; }
    .spinner-center { display: flex; justify-content: center; padding: 60px; }

    .feed-card {
      border-radius: 12px !important;
      overflow: hidden;
      transition: box-shadow 0.2s;
    }
    .feed-card:hover { box-shadow: 0 4px 20px rgba(0,0,0,.15) !important; }
    .card-acknowledged { opacity: 0.6; }

    /* Card top */
    .card-top { display: flex; align-items: flex-start; gap: 12px; padding: 16px 16px 12px; }
    .source-avatar {
      width: 42px; height: 42px; border-radius: 50%;
      display: flex; align-items: center; justify-content: center;
      flex-shrink: 0;
      color: #fff;
    }
    .source-avatar mat-icon { font-size: 20px; height: 20px; width: 20px; }
    .card-meta { flex: 1; min-width: 0; }
    .card-meta-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 2px; }
    .source-label { font-weight: 600; font-size: 13px; }
    .severity-badge {
      font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .5px;
      padding: 2px 7px; border-radius: 10px;
    }
    .location-tag {
      font-size: 11px; color: var(--mat-sys-on-surface-variant);
      display: flex; align-items: center; gap: 2px;
    }
    .timestamp { font-size: 12px; color: var(--mat-sys-on-surface-variant); }
    .ack-stamp {
      display: flex; align-items: center; gap: 4px;
      font-size: 12px; color: #388e3c; font-weight: 600;
    }
    .ack-stamp mat-icon { font-size: 14px; height: 14px; width: 14px; }

    /* Title */
    .card-title {
      padding: 0 16px 10px;
      font-size: 15px; font-weight: 600; line-height: 1.4;
    }
    .card-title-link {
      display: flex; align-items: center; gap: 6px;
      cursor: pointer; text-decoration: none; color: inherit;
      border-radius: 4px; transition: color 0.15s;
    }
    .card-title-link:hover { color: var(--mat-sys-primary); }
    .open-icon { font-size: 14px; height: 14px; width: 14px; opacity: 0.6; }
    .severity-critical { color: #b71c1c; }
    .body-clickable { cursor: pointer; }
    .body-clickable:hover { color: var(--mat-sys-on-surface); }

    /* Body */
    .card-body-text {
      padding: 0 16px 8px;
      font-size: 13px; line-height: 1.6;
      color: var(--mat-sys-on-surface-variant);
      white-space: pre-wrap;
      word-break: break-word;
    }
    .card-body-text.collapsed {
      max-height: 80px;
      overflow: hidden;
      -webkit-mask-image: linear-gradient(to bottom, black 50%, transparent 100%);
      mask-image: linear-gradient(to bottom, black 50%, transparent 100%);
    }
    .expand-btn { margin: 0 8px 4px; font-size: 12px; }
    .mail-from {
      padding: 0 16px 8px;
      font-size: 12px; color: var(--mat-sys-on-surface-variant);
      display: flex; align-items: center; gap: 4px;
    }

    /* Actions */
    .card-actions {
      display: flex; align-items: center;
      padding: 4px 8px;
      gap: 4px;
    }
    .action-btn { font-size: 13px; color: var(--mat-sys-on-surface-variant); }
    .action-btn mat-icon { font-size: 16px; height: 16px; width: 16px; margin-right: 4px; }
    .spacer { flex: 1; }
    .item-type-hint { font-size: 11px; color: var(--mat-sys-outline); padding-right: 8px; }

    /* Infinite scroll */
    .scroll-sentinel { height: 1px; }
    .load-more-spinner { display: flex; justify-content: center; padding: 16px; }
    .empty-state {
      display: flex; flex-direction: column; align-items: center;
      padding: 60px 20px; color: var(--mat-sys-on-surface-variant);
      gap: 8px;
    }
    .empty-state mat-icon { font-size: 48px; height: 48px; width: 48px; opacity: 0.4; }
    .empty-state p { font-size: 16px; font-weight: 500; margin: 0; }
    .empty-state span { font-size: 13px; }
  `],
})
export class NewsFeedComponent implements OnInit, AfterViewInit, OnDestroy {
  readonly allSources = Object.entries(SOURCE_META).map(([id, m]) => ({ id, ...m }));

  @ViewChild('scrollSentinel') private sentinelRef!: ElementRef<HTMLElement>;
  private observer?: IntersectionObserver;

  items = signal<FeedItem[]>([]);
  loading = signal(false);
  loadingMore = signal(false);
  showSettings = signal(false);
  showFilters = signal(false);
  activeFilter = signal<string[]>([]);
  expanded = new Set<string>();

  hostFilter = '';
  severityFilter = '';
  osFilter = '';
  locationFilter = '';
  criticalityFilter = '';
  veFilter = '';

  filterValues: { os: string[]; location: string[]; criticality: string[]; ve: string[] } = {
    os: [], location: [], criticality: [], ve: [],
  };

  editPrefs: FeedPrefs = { checkmk_min_age_minutes: 5, sources_enabled: ['checkmk','graylog','wazuh'], teams_channels: [] };
  sourceEnabled: Record<string, boolean> = {};
  private offset = 0;
  private readonly pageSize = 50;
  private hasMore = false;
  private refreshTimer?: ReturnType<typeof setInterval>;

  visibleItems = computed(() => {
    const f = this.activeFilter();
    if (f.length === 0) return this.items();
    return this.items().filter(i => f.includes(i.source));
  });

  constructor(private http: HttpClient, private snackBar: MatSnackBar) {}

  ngOnInit() {
    this.loadPrefs();
    this.load(true);
    this.refreshTimer = setInterval(() => this.load(true), 30_000);
  }

  ngAfterViewInit() {
    this.observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && this.hasMore && !this.loadingMore() && !this.loading()) {
          this.loadMore();
        }
      },
      { threshold: 0.1 }
    );
    this.observer.observe(this.sentinelRef.nativeElement);
  }

  ngOnDestroy() {
    if (this.refreshTimer) clearInterval(this.refreshTimer);
    this.observer?.disconnect();
  }

  loadPrefs() {
    this.http.get<any>(`${environment.apiUrl}/preferences`).subscribe({
      next: (p) => {
        this.editPrefs = {
          checkmk_min_age_minutes: p.feed_checkmk_min_age_minutes ?? 5,
          sources_enabled: p.feed_sources_enabled ?? ['checkmk','graylog','wazuh'],
          teams_channels: p.feed_teams_channels ?? [],
        };
        this.allSources.forEach(s => {
          this.sourceEnabled[s.id] = this.editPrefs.sources_enabled.includes(s.id);
        });
      },
    });
  }

  hasActiveFilter(): boolean {
    return !!(this.hostFilter || this.severityFilter || this.osFilter ||
              this.locationFilter || this.criticalityFilter || this.veFilter);
  }

  toggleFilters() {
    const next = !this.showFilters();
    this.showFilters.set(next);
    if (next && this.filterValues.os.length === 0) {
      this.loadFilterValues();
    }
  }

  loadFilterValues() {
    this.http.get<any>(`${environment.apiUrl}/feed/checkmk-filter-values`).subscribe({
      next: (v) => { this.filterValues = v; },
    });
  }

  onFilterChange() {
    this.load(true);
  }

  clearFilters() {
    this.hostFilter = '';
    this.severityFilter = '';
    this.osFilter = '';
    this.locationFilter = '';
    this.criticalityFilter = '';
    this.veFilter = '';
    this.load(true);
  }

  load(reset = false) {
    if (reset) {
      this.offset = 0;
      this.items.set([]);
      this.loading.set(true);
    } else {
      this.loadingMore.set(true);
    }
    const params: Record<string, any> = { limit: this.pageSize, offset: this.offset };
    if (this.severityFilter)    params['severity']    = this.severityFilter;
    if (this.hostFilter)        params['host']        = this.hostFilter;
    if (this.osFilter)          params['os']          = this.osFilter;
    if (this.locationFilter)    params['location']    = this.locationFilter;
    if (this.criticalityFilter) params['criticality'] = this.criticalityFilter;
    if (this.veFilter)          params['ve']          = this.veFilter;
    this.http.get<FeedItem[]>(`${environment.apiUrl}/feed/`, { params }).subscribe({
      next: (data) => {
        this.items.update(prev => reset ? data : [...prev, ...data]);
        this.hasMore = data.length === this.pageSize;
        this.loading.set(false);
        this.loadingMore.set(false);
      },
      error: () => { this.loading.set(false); this.loadingMore.set(false); },
    });
  }

  loadMore() {
    this.offset += this.pageSize;
    this.load(false);
  }

  savePrefs() {
    const enabled = this.allSources.filter(s => this.sourceEnabled[s.id]).map(s => s.id);
    this.editPrefs.sources_enabled = enabled;
    this.http.patch(`${environment.apiUrl}/preferences`, {
      feed_checkmk_min_age_minutes: this.editPrefs.checkmk_min_age_minutes,
      feed_sources_enabled: enabled,
      feed_teams_channels: this.editPrefs.teams_channels,
    }).subscribe({
      next: () => {
        this.snackBar.open('Einstellungen gespeichert', '', { duration: 2000 });
        this.showSettings.set(false);
        this.load(true);
      },
      error: () => this.snackBar.open('Fehler beim Speichern', '', { duration: 3000 }),
    });
  }

  toggleFilter(src: string) {
    this.activeFilter.update(prev =>
      prev.includes(src) ? prev.filter(s => s !== src) : [...prev, src]
    );
  }

  toggleExpand(id: string) {
    if (this.expanded.has(id)) {
      this.expanded.delete(id);
    } else {
      this.expanded.add(id);
    }
  }

  acknowledge(item: FeedItem) {
    this.http.post(`${environment.apiUrl}/feed/${item.id}/acknowledge`, {}).subscribe({
      next: () => {
        this.items.update(prev => prev.map(i =>
          i.id === item.id ? { ...i, status: 'acknowledged' as const } : i
        ));
        this.snackBar.open('Bestätigt', '', { duration: 2000 });
      },
      error: (err) => this.snackBar.open(err?.error?.detail ?? 'Fehler', '', { duration: 3000 }),
    });
  }

  createTicket(item: FeedItem) {
    this.snackBar.open('Ticket-Erstellung wird demnächst verfügbar sein', '', { duration: 2500 });
  }

  openUrl(url: string) {
    window.open(url, '_blank', 'noopener');
  }

  relTime(iso: string): string {
    if (!iso) return '';
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60_000);
    if (mins < 1)  return 'gerade eben';
    if (mins < 60) return `vor ${mins} Min.`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `vor ${hrs} Std.`;
    const days = Math.floor(hrs / 24);
    return `vor ${days} Tag${days !== 1 ? 'en' : ''}`;
  }

  sourceIcon(src: string)  { return SOURCE_META[src]?.icon  ?? 'info'; }
  sourceLabel(src: string) { return SOURCE_META[src]?.label ?? src; }
  sourceColor(src: string) { return SOURCE_META[src]?.color ?? '#757575'; }
  severityColor(sev: string) { return SEVERITY_COLOR[sev] ?? '#757575'; }
  typeLabel(type: string): string {
    const m: Record<string, string> = {
      alert: 'Monitoring Alert',
      email: 'E-Mail',
      teams_message: 'Teams Nachricht',
    };
    return m[type] ?? type;
  }
}
