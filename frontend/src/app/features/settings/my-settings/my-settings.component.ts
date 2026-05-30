import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatButtonModule } from '@angular/material/button';
import { MatChipsModule } from '@angular/material/chips';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatIconModule } from '@angular/material/icon';
import { forkJoin } from 'rxjs';
import { environment } from '../../../../environments/environment';
import { ThemeService } from '../../../core/services/theme.service';

@Component({
  selector: 'cs-my-settings',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatCardModule, MatFormFieldModule, MatInputModule, MatSelectModule,
    MatButtonModule, MatChipsModule, MatSnackBarModule,
    MatProgressSpinnerModule, MatIconModule,
  ],
  template: `
    <div class="page-container">
      <div class="page-header">
        <h2>Meine Einstellungen</h2>
        <button mat-raised-button color="primary" [disabled]="saving()" (click)="save()">
          @if (saving()) { <mat-spinner diameter="18"></mat-spinner> }
          @else { <ng-container><mat-icon>save</mat-icon> Speichern</ng-container> }
        </button>
      </div>

      <mat-card class="settings-card">
        <mat-card-header>
          <mat-card-title>Darstellung</mat-card-title>
          <mat-card-subtitle>Design der gesamten Anwendung. Wird pro Benutzer gespeichert.</mat-card-subtitle>
        </mat-card-header>
        <mat-card-content>
          <div class="theme-grid">
            @for (t of themes; track t.id) {
              <button class="theme-card" [class.active]="theme.theme() === t.id" (click)="theme.setTheme(t.id)">
                <span class="theme-swatch" [style.background]="t.swatch"></span>
                <span class="theme-name">{{ t.label }}</span>
                <span class="theme-desc">{{ t.desc }}</span>
              </button>
            }
          </div>
        </mat-card-content>
      </mat-card>

      @if (loading()) {
        <div class="spinner-center"><mat-spinner diameter="40"></mat-spinner></div>
      } @else {
        <mat-card class="settings-card">
          <mat-card-header>
            <mat-card-title>KI-Agent — CheckMK Filter</mat-card-title>
            <mat-card-subtitle>
              Bestimmt welche CheckMK-Alerts der KI-Agent und der News Feed anzeigen.
              Nichts ausgewählt = alle Werte werden berücksichtigt.
            </mat-card-subtitle>
          </mat-card-header>
          <mat-card-content>

            <mat-form-field appearance="outline" class="age-field">
              <mat-label>Mindestalter CheckMK-Meldungen (Minuten)</mat-label>
              <input matInput type="number" min="0" max="1440" [(ngModel)]="minAgeMins">
              <mat-hint>Meldungen die jünger als dieser Wert sind werden ausgeblendet (Standard: 5)</mat-hint>
            </mat-form-field>

            <mat-form-field appearance="outline" class="full-width">
              <mat-label>Standort (Location / Ordner)</mat-label>
              <mat-select multiple [(ngModel)]="selLocations">
                @for (v of filterValues.location; track v) {
                  <mat-option [value]="v">{{ v }}</mat-option>
                }
              </mat-select>
              <mat-hint>Basiert auf dem CheckMK-Ordner-Pfad des Hosts</mat-hint>
            </mat-form-field>

            <mat-form-field appearance="outline" class="full-width">
              <mat-label>VE / Unternehmen</mat-label>
              <mat-select multiple [(ngModel)]="selVe">
                @for (v of filterValues.ve; track v) {
                  <mat-option [value]="v">{{ v }}</mat-option>
                }
              </mat-select>
            </mat-form-field>

            <mat-form-field appearance="outline" class="full-width">
              <mat-label>Kritikalität</mat-label>
              <mat-select multiple [(ngModel)]="selCriticality">
                @for (v of filterValues.criticality; track v) {
                  <mat-option [value]="v">{{ v }}</mat-option>
                }
              </mat-select>
            </mat-form-field>

            <mat-form-field appearance="outline" class="full-width">
              <mat-label>Betriebssystem</mat-label>
              <mat-select multiple [(ngModel)]="selOs">
                @for (v of filterValues.os; track v) {
                  <mat-option [value]="v">{{ v }}</mat-option>
                }
              </mat-select>
            </mat-form-field>

            <mat-form-field appearance="outline" class="full-width">
              <mat-label>Hostgruppe</mat-label>
              <mat-select multiple [(ngModel)]="selHostgroups">
                @for (v of filterValues.hostgroups; track v) {
                  <mat-option [value]="v">{{ v }}</mat-option>
                }
              </mat-select>
              <mat-hint>CheckMK Hostgruppen-Filter für KI-Agent und Alerts</mat-hint>
            </mat-form-field>

            @if (selLocations.length || selVe.length || selCriticality.length || selOs.length || selHostgroups.length) {
              <div class="active-filters">
                <span class="filter-label">Aktive Filter:</span>
                @for (v of selLocations; track v) {
                  <mat-chip>
                    <mat-icon matChipAvatar style="font-size:14px">location_on</mat-icon>
                    {{ v }}
                  </mat-chip>
                }
                @for (v of selVe; track v) {
                  <mat-chip>
                    <mat-icon matChipAvatar style="font-size:14px">business</mat-icon>
                    {{ v }}
                  </mat-chip>
                }
                @for (v of selCriticality; track v) {
                  <mat-chip>
                    <mat-icon matChipAvatar style="font-size:14px">warning</mat-icon>
                    {{ v }}
                  </mat-chip>
                }
                @for (v of selOs; track v) {
                  <mat-chip>
                    <mat-icon matChipAvatar style="font-size:14px">computer</mat-icon>
                    {{ v }}
                  </mat-chip>
                }
                @for (v of selHostgroups; track v) {
                  <mat-chip>
                    <mat-icon matChipAvatar style="font-size:14px">group_work</mat-icon>
                    {{ v }}
                  </mat-chip>
                }
              </div>
            } @else {
              <p class="no-filter-hint">
                <mat-icon style="vertical-align:middle;font-size:16px">info</mat-icon>
                Kein Filter aktiv — der KI-Agent analysiert alle CheckMK-Standorte.
              </p>
            }

          </mat-card-content>
        </mat-card>

        <mat-card class="settings-card">
          <mat-card-header>
            <mat-card-title>Passwort ändern</mat-card-title>
          </mat-card-header>
          <mat-card-content>
            <mat-form-field appearance="outline" class="pw-field">
              <mat-label>Aktuelles Passwort</mat-label>
              <input matInput [type]="pwShow ? 'text' : 'password'" [(ngModel)]="pwCurrent" autocomplete="current-password">
              <button matSuffix mat-icon-button (click)="pwShow = !pwShow" type="button" tabindex="-1">
                <mat-icon>{{ pwShow ? 'visibility_off' : 'visibility' }}</mat-icon>
              </button>
            </mat-form-field>
            <mat-form-field appearance="outline" class="pw-field">
              <mat-label>Neues Passwort</mat-label>
              <input matInput [type]="pwShow ? 'text' : 'password'" [(ngModel)]="pwNew" autocomplete="new-password">
            </mat-form-field>
            <mat-form-field appearance="outline" class="pw-field">
              <mat-label>Neues Passwort bestätigen</mat-label>
              <input matInput [type]="pwShow ? 'text' : 'password'" [(ngModel)]="pwConfirm" autocomplete="new-password">
              @if (pwNew && pwConfirm && pwNew !== pwConfirm) {
                <mat-error>Passwörter stimmen nicht überein</mat-error>
              }
            </mat-form-field>
            @if (pwError) {
              <p class="pw-error"><mat-icon>error</mat-icon>{{ pwError }}</p>
            }
            <div class="pw-actions">
              <button mat-flat-button color="primary"
                      [disabled]="pwSaving() || !pwCurrent || !pwNew || pwNew !== pwConfirm || pwNew.length < 8"
                      (click)="changePassword()">
                @if (pwSaving()) { <mat-spinner diameter="18"></mat-spinner> }
                @else { <mat-icon>lock_reset</mat-icon> }
                Passwort ändern
              </button>
              <span class="pw-hint">Mindestens 8 Zeichen</span>
            </div>
          </mat-card-content>
        </mat-card>
      }
    </div>
  `,
  styles: [`
    .page-container { padding: 24px; max-width: 700px; }
    .page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
    .page-header h2 { margin: 0; }
    .theme-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
    .theme-card { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; padding: 12px; cursor: pointer;
      border: 2px solid var(--mat-sys-outline-variant); border-radius: 12px; background: var(--mat-sys-surface-container); text-align: left; font-family: inherit; }
    .theme-card.active { border-color: var(--mat-sys-primary); box-shadow: 0 0 0 2px var(--mat-sys-primary); }
    .theme-swatch { width: 100%; height: 42px; border-radius: 8px; }
    .theme-name { font-weight: 700; font-size: 14px; color: var(--mat-sys-on-surface); }
    .theme-desc { font-size: 11px; color: var(--mat-sys-on-surface-variant); }
    .settings-card mat-card-content { padding-top: 16px; display: flex; flex-direction: column; gap: 8px; }
    .full-width { width: 100%; }
    .age-field { width: 320px; }
    .spinner-center { display: flex; justify-content: center; padding: 40px; }
    mat-spinner { display: inline-block; }
    .active-filters { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; padding: 8px 0 4px; }
    .filter-label { font-size: 12px; color: var(--mat-sys-on-surface-variant); margin-right: 4px; }
    .no-filter-hint { font-size: 13px; color: var(--mat-sys-on-surface-variant); margin: 4px 0 0; display: flex; align-items: center; gap: 4px; }
    @media (max-width: 820px) { .age-field { width: 100%; } }
    .pw-field { width: 100%; max-width: 380px; }
    .pw-actions { display: flex; align-items: center; gap: 16px; padding-top: 4px; }
    .pw-hint { font-size: 12px; color: var(--mat-sys-on-surface-variant); }
    .pw-error { display: flex; align-items: center; gap: 6px; color: var(--mat-sys-error); font-size: 13px; margin: 0 0 8px; }
    .pw-error mat-icon { font-size: 16px; height: 16px; width: 16px; }
  `],
})
export class MySettingsComponent implements OnInit {
  loading = signal(true);
  saving  = signal(false);

  readonly themes: { id: 'classic'|'holo'|'lcars'; label: string; desc: string; swatch: string }[] = [
    { id: 'classic', label: 'Klassisch', desc: 'Hell, aufgeräumt, blauer Schleier', swatch: 'linear-gradient(135deg,#eef4fb,#1565c0)' },
    { id: 'holo',    label: 'Holo-HUD',  desc: 'Dunkelblau, Cyan-Glow',            swatch: 'linear-gradient(135deg,#050d1a,#4fd6ff)' },
    { id: 'lcars',   label: 'LCARS',     desc: 'Schwarz/Orange, Star Trek',        swatch: 'linear-gradient(135deg,#000,#ff9966)' },
  ];

  pwCurrent = '';
  pwNew     = '';
  pwConfirm = '';
  pwShow    = false;
  pwSaving  = signal(false);
  pwError   = '';

  minAgeMins:     number   = 5;
  selLocations:   string[] = [];
  selVe:          string[] = [];
  selCriticality: string[] = [];
  selOs:          string[] = [];
  selHostgroups:  string[] = [];

  filterValues: { location: string[]; ve: string[]; criticality: string[]; os: string[]; hostgroups: string[] } = {
    location: [], ve: [], criticality: [], os: [], hostgroups: [],
  };

  constructor(private http: HttpClient, private snack: MatSnackBar, public theme: ThemeService) {}

  ngOnInit() {
    forkJoin({
      prefs: this.http.get<any>(`${environment.apiUrl}/preferences`),
      vals: this.http.get<any>(`${environment.apiUrl}/feed/checkmk-filter-values`),
    }).subscribe({
      next: ({ prefs, vals }) => {
        this.minAgeMins     = prefs?.feed_checkmk_min_age_minutes ?? 5;
        this.selLocations   = prefs?.checkmk_locations   || [];
        this.selVe          = prefs?.checkmk_ve          || [];
        this.selCriticality = prefs?.checkmk_criticality || [];
        this.selOs          = prefs?.checkmk_os          || [];
        this.selHostgroups  = prefs?.checkmk_hostgroups  || [];

        const merge = (saved: string[], avail: string[]) =>
          [...new Set([...saved, ...avail])].sort();

        this.filterValues = {
          location:    merge(this.selLocations,   vals?.location    || []),
          ve:          merge(this.selVe,          vals?.ve          || []),
          criticality: merge(this.selCriticality, vals?.criticality || []),
          os:          merge(this.selOs,          vals?.os          || []),
          hostgroups:  merge(this.selHostgroups,  vals?.hostgroups  || []),
        };
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  changePassword() {
    if (this.pwNew !== this.pwConfirm || this.pwNew.length < 8) return;
    this.pwSaving.set(true);
    this.pwError = '';
    this.http.post(`${environment.apiUrl}/auth/change-password`, {
      current_password: this.pwCurrent,
      new_password: this.pwNew,
    }).subscribe({
      next: () => {
        this.pwSaving.set(false);
        this.pwCurrent = '';
        this.pwNew     = '';
        this.pwConfirm = '';
        this.snack.open('Passwort erfolgreich geändert', 'OK', { duration: 3000 });
      },
      error: (err) => {
        this.pwSaving.set(false);
        this.pwError = err?.error?.detail === 'Current password wrong'
          ? 'Aktuelles Passwort falsch'
          : (err?.error?.detail ?? 'Fehler beim Ändern des Passworts');
      },
    });
  }

  save() {
    this.saving.set(true);
    this.http.patch(`${environment.apiUrl}/preferences`, {
      feed_checkmk_min_age_minutes: this.minAgeMins > 0 ? this.minAgeMins : 0,
      checkmk_locations:   this.selLocations.length   ? this.selLocations   : null,
      checkmk_ve:          this.selVe.length          ? this.selVe          : null,
      checkmk_criticality: this.selCriticality.length ? this.selCriticality : null,
      checkmk_os:          this.selOs.length          ? this.selOs          : null,
      checkmk_hostgroups:  this.selHostgroups.length  ? this.selHostgroups  : null,
    }).subscribe({
      next: () => {
        this.saving.set(false);
        this.snack.open('Einstellungen gespeichert', 'OK', { duration: 3000 });
      },
      error: () => {
        this.saving.set(false);
        this.snack.open('Fehler beim Speichern', 'OK', { duration: 3000 });
      },
    });
  }
}
