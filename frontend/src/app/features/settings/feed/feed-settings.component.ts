import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDividerModule } from '@angular/material/divider';
import { MatTooltipModule } from '@angular/material/tooltip';
import { environment } from '../../../../environments/environment';

interface RetentionConfig {
  checkmk_days: number;
  graylog_days: number;
  wazuh_days: number;
  o365_days: number;
  teams_days: number;
}

const SOURCE_META = [
  { key: 'checkmk', label: 'CheckMK',  icon: 'monitor_heart', color: '#1565c0', desc: 'Monitoring-Alerts' },
  { key: 'graylog', label: 'Graylog',  icon: 'article',       color: '#6a1b9a', desc: 'Log-Einträge' },
  { key: 'wazuh',   label: 'Wazuh',    icon: 'security',      color: '#b71c1c', desc: 'Security-Alerts' },
  { key: 'o365',    label: 'E-Mail',   icon: 'mail',          color: '#e65100', desc: 'O365-Nachrichten' },
  { key: 'teams',   label: 'Teams',    icon: 'groups',        color: '#0f4c96', desc: 'Teams-Nachrichten' },
];

@Component({
  selector: 'cs-feed-settings',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatCardModule, MatButtonModule, MatIconModule,
    MatFormFieldModule, MatInputModule, MatProgressSpinnerModule,
    MatSnackBarModule, MatDividerModule, MatTooltipModule,
  ],
  template: `
    <div class="page-container">
      <div class="page-header">
        <h2>Feed-Einstellungen</h2>
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
              Speichern
            </button>
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
  `],
})
export class FeedSettingsComponent implements OnInit {
  readonly sources = SOURCE_META;

  loading = signal(true);
  saving = signal(false);
  retention: Record<string, number> = {
    checkmk_days: 90, graylog_days: 90, wazuh_days: 90,
    o365_days: 90, teams_days: 90,
  };

  constructor(private http: HttpClient, private snackBar: MatSnackBar) {}

  ngOnInit() { this.load(); }

  load() {
    this.loading.set(true);
    this.http.get<any>(`${environment.apiUrl}/settings/`).subscribe({
      next: (s) => {
        for (const src of this.sources) {
          const key = `feed.retention.${src.key}_days`;
          if (s[key]) this.retention[`${src.key}_days`] = parseInt(s[key], 10);
        }
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  save() {
    this.saving.set(true);
    const updates: Record<string, string> = {};
    for (const src of this.sources) {
      updates[`feed.retention.${src.key}_days`] = String(this.retention[`${src.key}_days`] || 90);
    }
    this.http.patch(`${environment.apiUrl}/settings/`, updates).subscribe({
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
}
