import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { forkJoin } from 'rxjs';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDividerModule } from '@angular/material/divider';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { environment } from '../../../../environments/environment';
import { I18nService } from '../../../core/services/i18n.service';

interface RetentionConfig {
  checkmk_days: number;
  graylog_days: number;
  wazuh_days: number;
  icinga2_days: number;
  o365_days: number;
  teams_days: number;
  coroot_days: number;
}

interface FeedSearch {
  id: string;
  name: string;
  index_pattern: string;
  query_string: string;
  enabled: boolean;
  is_system: boolean;
  is_exclusion: boolean;
  position: number;
}

const SOURCE_META = [
  { key: 'checkmk', label: 'CheckMK',  icon: 'monitor_heart', color: '#1565c0', desc: 'Monitoring-Alerts' },
  { key: 'graylog', label: 'Graylog',  icon: 'article',       color: '#6a1b9a', desc: 'Log-Einträge' },
  { key: 'wazuh',   label: 'Wazuh',    icon: 'security',      color: '#b71c1c', desc: 'Security-Alerts' },
  { key: 'icinga2', label: 'Icinga2',  icon: 'monitor_heart', color: '#06A000', desc: 'Monitoring-Alerts' },
  { key: 'o365',    label: 'E-Mail',   icon: 'mail',          color: '#e65100', desc: 'O365-Nachrichten' },
  { key: 'teams',   label: 'Teams',    icon: 'groups',        color: '#0f4c96', desc: 'Teams-Nachrichten' },
  { key: 'coroot',  label: 'Coroot',   icon: 'insights',      color: '#00897b', desc: 'APM & Incidents' },
];

@Component({
  selector: 'cs-feed-settings',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatCardModule, MatButtonModule, MatIconModule,
    MatFormFieldModule, MatInputModule, MatProgressSpinnerModule,
    MatSnackBarModule, MatDividerModule, MatTooltipModule, MatSlideToggleModule,
  ],
  template: `
    <div class="page-container">
      <div class="page-header">
        <h2>{{ i18n.t('settings.tabs.feed') }}</h2>
      </div>

      @if (loading()) {
        <div class="spinner-center"><mat-spinner diameter="40"></mat-spinner></div>
      } @else {
        <!-- Retention -->
        <mat-card>
          <div class="section-header">
            <mat-icon>delete_sweep</mat-icon>
            <div>
              <h3>Aufbewahrungsfristen (Retention)</h3>
              <p class="hint">Feed-Nachrichten werden täglich um 03:00 Uhr bereinigt. Standard: 90 Tage.</p>
            </div>
          </div>
          <mat-divider></mat-divider>

          <div class="retention-grid">
            @for (src of sources; track src.key) {
              <div class="retention-row">
                <div class="source-icon" [style.background]="src.color">
                  <mat-icon>{{ src.icon }}</mat-icon>
                </div>
                <div class="source-info">
                  <span class="source-label">{{ src.label }}</span>
                  <span class="source-desc">{{ src.desc }}</span>
                </div>
                <mat-form-field appearance="outline" class="days-field">
                  <mat-label>Tage</mat-label>
                  <input matInput type="number" min="1" max="730"
                         [(ngModel)]="retention[src.key + '_days']">
                  <span matSuffix>Tage</span>
                </mat-form-field>
              </div>
            }
          </div>

          <div class="card-actions">
            <button mat-flat-button color="primary" (click)="save()" [disabled]="saving()">
              @if (saving()) { <mat-spinner diameter="18"></mat-spinner> }
              @else { <mat-icon>save</mat-icon> }
              {{ i18n.t('common.save') }}
            </button>
          </div>
        </mat-card>

        <!-- System searches -->
        <mat-card>
          <div class="section-header">
            <mat-icon>manage_search</mat-icon>
            <div>
              <h3>System-Suchen</h3>
              <p class="hint">
                Diese gespeicherten OpenSearch-Queries stehen im Feed und in Dashboard-Widgets zur Verfügung.
                Query-Syntax: Lucene gegen <code>cs-feed-*</code> Indices.<br>
                Verfügbare Felder: <code>title</code>, <code>body</code>, <code>source</code>, <code>severity</code>,
                <code>status</code>, <code>metadata.container_name</code>, <code>metadata.host</code>.
                Hinweis: Der Nachrichtentext steht meist in <code>title</code> (nicht <code>body</code>).
              </p>
            </div>
          </div>
          <mat-divider></mat-divider>

          <div class="search-list">
            @for (search of systemSearches(); track search.id) {
              <div class="search-row" [class.exclusion-row]="search.is_exclusion">
                <div class="search-main">
                  <mat-form-field appearance="outline">
                    <mat-label>Name</mat-label>
                    <input matInput [(ngModel)]="search.name">
                  </mat-form-field>
                  <mat-form-field appearance="outline">
                    <mat-label>Index</mat-label>
                    <input matInput [(ngModel)]="search.index_pattern">
                  </mat-form-field>
                  <mat-form-field appearance="outline" class="query-field">
                    <mat-label>Lucene Query</mat-label>
                    <textarea matInput rows="2" [(ngModel)]="search.query_string"></textarea>
                  </mat-form-field>
                </div>
                <div class="search-actions">
                  <mat-slide-toggle [(ngModel)]="search.enabled">{{ i18n.t('common.enabled') }}</mat-slide-toggle>
                  <mat-slide-toggle [(ngModel)]="search.is_exclusion" color="warn">
                    <span class="exclusion-label">Ausblenden</span>
                  </mat-slide-toggle>
                  <button mat-stroked-button (click)="previewSearch(search)" [disabled]="previewing() === search.id">
                    @if (previewing() === search.id) { <mat-spinner diameter="16"></mat-spinner> }
                    @else { <mat-icon>visibility</mat-icon> }
                    Vorschau
                  </button>
                  <button mat-flat-button color="primary" (click)="saveSearch(search)">{{ i18n.t('common.save') }}</button>
                  <button mat-icon-button color="warn" [matTooltip]="i18n.t('common.delete')" (click)="deleteSearch(search)">
                    <mat-icon>delete</mat-icon>
                  </button>
                </div>
                @if (search.is_exclusion) {
                  <div class="exclusion-hint">
                    <mat-icon>block</mat-icon>
                    Passende Meldungen werden automatisch aus dem Feed ausgeblendet.
                  </div>
                }
                @if (previewFor(search.id).length > 0) {
                  <div class="preview-box">
                    @for (item of previewFor(search.id); track item.id) {
                      <div class="preview-item">{{ item.source }} · {{ item.severity }} · {{ item.title }}</div>
                    }
                  </div>
                }
              </div>
            }
          </div>

          <div class="new-search">
            <h4>Neue System-Suche</h4>
            <div class="new-search-grid">
              <mat-form-field appearance="outline">
                <mat-label>Name</mat-label>
                <input matInput [(ngModel)]="newSearch.name">
              </mat-form-field>
              <mat-form-field appearance="outline">
                <mat-label>Index-Pattern</mat-label>
                <input matInput [(ngModel)]="newSearch.index_pattern">
              </mat-form-field>
              <mat-form-field appearance="outline" class="query-field">
                <mat-label>Lucene Query</mat-label>
                <textarea matInput rows="2" [(ngModel)]="newSearch.query_string"></textarea>
              </mat-form-field>
            </div>
            <div class="new-search-options">
              <mat-slide-toggle [(ngModel)]="newSearch.is_exclusion" color="warn">
                Ausblenden (passende Meldungen aus Feed verstecken)
              </mat-slide-toggle>
            </div>
            <div class="card-actions">
              <button mat-flat-button color="primary" (click)="createSystemSearch()" [disabled]="!newSearch.name.trim()">
                <mat-icon>add</mat-icon>
                {{ i18n.t('feed.save_search') }}
              </button>
            </div>
          </div>
        </mat-card>

        <!-- OpenSearch info -->
        <mat-card class="info-card">
          <div class="section-header">
            <mat-icon>storage</mat-icon>
            <div>
              <h3>Speicher: OpenSearch</h3>
              <p class="hint">
                Feed-Nachrichten werden in OpenSearch gespeichert (getrennte Indices pro Quelle:
                <code>cs-feed-checkmk</code>, <code>cs-feed-o365</code>, etc.).<br>
                Monitoring-Alerts werden beim Import indexiert. E-Mails und Teams-Nachrichten
                werden beim ersten Abrufen gespeichert.<br>
                Zugriffskontrolle: E-Mails und Teams-Nachrichten sind immer an den jeweiligen
                Benutzer gebunden und für andere nicht sichtbar.
              </p>
            </div>
          </div>
        </mat-card>
      }
    </div>
  `,
  styles: [`
    .page-container { padding: 24px; max-width: 700px; }
    .page-header { margin-bottom: 20px; }
    .page-header h2 { margin: 0; }
    .spinner-center { display: flex; justify-content: center; padding: 60px; }

    mat-card { margin-bottom: 20px; }
    .section-header { display: flex; align-items: flex-start; gap: 16px; padding: 16px 16px 12px; }
    .section-header mat-icon { font-size: 24px; height: 24px; width: 24px; margin-top: 4px; color: var(--mat-sys-primary); }
    .section-header h3 { margin: 0 0 4px; font-size: 16px; }
    .hint { margin: 0; font-size: 12px; color: var(--mat-sys-on-surface-variant); line-height: 1.5; }
    code { background: var(--mat-sys-surface-variant); padding: 1px 4px; border-radius: 4px; font-size: 11px; }

    .retention-grid { display: flex; flex-direction: column; }
    .retention-row {
      display: flex; align-items: center; gap: 14px;
      padding: 12px 16px;
      border-bottom: 1px solid var(--mat-sys-outline-variant);
    }
    .retention-row:last-child { border-bottom: none; }
    .source-icon {
      width: 36px; height: 36px; border-radius: 50%;
      display: flex; align-items: center; justify-content: center;
      color: #fff; flex-shrink: 0;
    }
    .source-icon mat-icon { font-size: 18px; height: 18px; width: 18px; }
    .source-info { flex: 1; display: flex; flex-direction: column; }
    .source-label { font-weight: 600; font-size: 14px; }
    .source-desc { font-size: 12px; color: var(--mat-sys-on-surface-variant); }
    .days-field { width: 120px; }

    .card-actions { display: flex; justify-content: flex-end; padding: 8px 16px 12px; }
    .info-card .section-header { padding: 16px; }

    .search-list { display: flex; flex-direction: column; }
    .search-row { padding: 14px 16px; border-bottom: 1px solid var(--mat-sys-outline-variant); }
    .search-row:last-child { border-bottom: none; }
    .exclusion-row { background: color-mix(in srgb, #b71c1c 6%, transparent); border-left: 3px solid #b71c1c; }
    .search-main { display: grid; grid-template-columns: 1fr 180px; gap: 10px; }
    .query-field { grid-column: 1 / -1; }
    .search-actions { display: flex; align-items: center; justify-content: flex-end; gap: 10px; flex-wrap: wrap; }
    .search-actions mat-spinner { display: inline-block; margin-right: 4px; }
    .exclusion-label { font-size: 12px; }
    .exclusion-hint {
      display: flex; align-items: center; gap: 6px;
      margin-top: 8px; padding: 6px 10px;
      background: color-mix(in srgb, #b71c1c 12%, transparent);
      border-radius: 6px; font-size: 12px; color: #ef9a9a;
    }
    .exclusion-hint mat-icon { font-size: 16px; height: 16px; width: 16px; color: #ef9a9a; }
    .preview-box {
      margin-top: 10px;
      padding: 10px 12px;
      border-radius: 10px;
      background: var(--mat-sys-surface-variant);
      font-size: 12px;
      color: var(--mat-sys-on-surface-variant);
    }
    .preview-item { padding: 3px 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .new-search { padding: 14px 16px 16px; background: color-mix(in srgb, var(--mat-sys-primary) 5%, transparent); }
    .new-search h4 { margin: 0 0 12px; }
    .new-search-grid { display: grid; grid-template-columns: 1fr 180px; gap: 10px; }
    .new-search-options { padding: 4px 0 12px; }
    @media (max-width: 760px) {
      .search-main, .new-search-grid { grid-template-columns: 1fr; }
      .search-actions { justify-content: flex-start; }
    }
  `],
})
export class FeedSettingsComponent implements OnInit {
  readonly i18n = inject(I18nService);
  readonly sources = SOURCE_META;

  loading = signal(true);
  saving = signal(false);
  retention: Record<string, number> = {
    checkmk_days: 90, graylog_days: 90, wazuh_days: 90,
    icinga2_days: 90, o365_days: 90, teams_days: 90, coroot_days: 90,
  };
  searches = signal<FeedSearch[]>([]);
  previewing = signal<string | null>(null);
  previewItems = signal<Record<string, Array<{ id: string; source: string; severity: string; title: string }>>>({});
  newSearch = {
    name: '',
    index_pattern: 'cs-feed-*',
    query_string: '',
    enabled: true,
    is_exclusion: false,
  };

  constructor(private http: HttpClient, private snackBar: MatSnackBar) {}

  ngOnInit() { this.load(); }

  load() {
    this.loading.set(true);
    forkJoin({
      settings: this.http.get<{ settings: { key: string; value: string }[] }>(`${environment.apiUrl}/settings/`),
      searches: this.http.get<FeedSearch[]>(`${environment.apiUrl}/feed-searches/`),
    }).subscribe({
      next: ({ settings, searches }) => {
        const map: Record<string, string> = {};
        for (const item of settings.settings ?? []) map[item.key] = item.value;
        for (const src of this.sources) {
          const key = `feed.retention.${src.key}_days`;
          if (map[key]) this.retention[`${src.key}_days`] = parseInt(map[key], 10);
        }
        this.searches.set(searches);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  save() {
    this.saving.set(true);
    const calls = this.sources.map(src => {
      const key = `feed.retention.${src.key}_days`;
      const value = String(this.retention[`${src.key}_days`] || 90);
      return this.http.patch(`${environment.apiUrl}/settings/${key}`, { value });
    });
    forkJoin(calls).subscribe({
      next: () => {
        this.snackBar.open('Aufbewahrungsfristen gespeichert', '', { duration: 2500 });
        this.saving.set(false);
      },
      error: (e) => {
        this.snackBar.open(e?.error?.detail ?? 'Fehler', '', { duration: 3000 });
        this.saving.set(false);
      },
    });
  }

  systemSearches(): FeedSearch[] {
    return this.searches().filter(s => s.is_system);
  }

  saveSearch(search: FeedSearch) {
    this.http.patch(`${environment.apiUrl}/feed-searches/${search.id}`, {
      name: search.name,
      index_pattern: search.index_pattern,
      query_string: search.query_string,
      enabled: search.enabled,
      is_exclusion: search.is_exclusion,
      position: search.position,
    }).subscribe({
      next: () => this.snackBar.open('Suche gespeichert', '', { duration: 2000 }),
      error: (e) => this.snackBar.open(e?.error?.detail ?? 'Fehler beim Speichern', '', { duration: 3000 }),
    });
  }

  createSystemSearch() {
    this.http.post<FeedSearch>(`${environment.apiUrl}/feed-searches/system`, this.newSearch).subscribe({
      next: search => {
        this.searches.update(searches => [...searches, search]);
        this.newSearch = { name: '', index_pattern: 'cs-feed-*', query_string: '', enabled: true, is_exclusion: false };
        this.snackBar.open('System-Suche angelegt', '', { duration: 2000 });
      },
      error: (e) => this.snackBar.open(e?.error?.detail ?? 'Suche konnte nicht angelegt werden', '', { duration: 3000 }),
    });
  }

  deleteSearch(search: FeedSearch) {
    if (!confirm(`System-Filter „${search.name}" wirklich löschen?`)) return;
    this.http.delete(`${environment.apiUrl}/feed-searches/${search.id}`).subscribe({
      next: () => {
        this.searches.update(searches => searches.filter(s => s.id !== search.id));
        this.snackBar.open('System-Filter gelöscht', '', { duration: 2000 });
      },
      error: (e) => this.snackBar.open(e?.error?.detail ?? 'Filter konnte nicht gelöscht werden', 'OK', { duration: 3500 }),
    });
  }

  previewSearch(search: FeedSearch) {
    this.previewing.set(search.id);
    this.http.get<{ items: Array<{ id: string; source: string; severity: string; title: string }> }>(
      `${environment.apiUrl}/feed-searches/${search.id}/preview`,
      { params: { size: '5' } },
    ).subscribe({
      next: result => {
        this.previewItems.update(prev => ({ ...prev, [search.id]: result.items ?? [] }));
        this.previewing.set(null);
      },
      error: (e) => {
        this.previewing.set(null);
        this.snackBar.open(e?.error?.detail ?? 'Vorschau fehlgeschlagen', '', { duration: 3000 });
      },
    });
  }

  previewFor(searchId: string) {
    return this.previewItems()[searchId] ?? [];
  }
}
