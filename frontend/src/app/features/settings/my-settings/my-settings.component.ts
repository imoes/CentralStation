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
import { environment } from '../../../../environments/environment';

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
          @else { <mat-icon>save</mat-icon> Speichern }
        </button>
      </div>

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

            @if (selLocations.length || selVe.length || selCriticality.length || selOs.length) {
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
              </div>
            } @else {
              <p class="no-filter-hint">
                <mat-icon style="vertical-align:middle;font-size:16px">info</mat-icon>
                Kein Filter aktiv — der KI-Agent analysiert alle CheckMK-Standorte.
              </p>
            }

          </mat-card-content>
        </mat-card>
      }
    </div>
  `,
  styles: [`
    .page-container { padding: 24px; max-width: 700px; }
    .page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
    .page-header h2 { margin: 0; }
    .settings-card mat-card-content { padding-top: 16px; display: flex; flex-direction: column; gap: 8px; }
    .full-width { width: 100%; }
    .age-field { width: 320px; }
    .spinner-center { display: flex; justify-content: center; padding: 40px; }
    mat-spinner { display: inline-block; }
    .active-filters { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; padding: 8px 0 4px; }
    .filter-label { font-size: 12px; color: var(--mat-sys-on-surface-variant); margin-right: 4px; }
    .no-filter-hint { font-size: 13px; color: var(--mat-sys-on-surface-variant); margin: 4px 0 0; display: flex; align-items: center; gap: 4px; }
  `],
})
export class MySettingsComponent implements OnInit {
  loading = signal(true);
  saving  = signal(false);

  minAgeMins:     number   = 5;
  selLocations:   string[] = [];
  selVe:          string[] = [];
  selCriticality: string[] = [];
  selOs:          string[] = [];

  filterValues: { location: string[]; ve: string[]; criticality: string[]; os: string[] } = {
    location: [], ve: [], criticality: [], os: [],
  };

  constructor(private http: HttpClient, private snack: MatSnackBar) {}

  ngOnInit() {
    // Load user prefs and available filter values in parallel
    Promise.all([
      this.http.get<any>(`${environment.apiUrl}/preferences`).toPromise(),
      this.http.get<any>(`${environment.apiUrl}/feed/checkmk-filter-values`).toPromise(),
    ]).then(([prefs, vals]) => {
      this.minAgeMins     = prefs?.feed_checkmk_min_age_minutes ?? 5;
      this.selLocations   = prefs?.checkmk_locations   || [];
      this.selVe          = prefs?.checkmk_ve          || [];
      this.selCriticality = prefs?.checkmk_criticality || [];
      this.selOs          = prefs?.checkmk_os          || [];

      // Merge saved selections with available values so saved items always appear
      const merge = (saved: string[], avail: string[]) =>
        [...new Set([...saved, ...avail])].sort();

      this.filterValues = {
        location:    merge(this.selLocations,   vals?.location    || []),
        ve:          merge(this.selVe,          vals?.ve          || []),
        criticality: merge(this.selCriticality, vals?.criticality || []),
        os:          merge(this.selOs,          vals?.os          || []),
      };
      this.loading.set(false);
    }).catch(() => this.loading.set(false));
  }

  save() {
    this.saving.set(true);
    this.http.patch(`${environment.apiUrl}/preferences`, {
      feed_checkmk_min_age_minutes: this.minAgeMins > 0 ? this.minAgeMins : 0,
      checkmk_locations:   this.selLocations.length   ? this.selLocations   : null,
      checkmk_ve:          this.selVe.length          ? this.selVe          : null,
      checkmk_criticality: this.selCriticality.length ? this.selCriticality : null,
      checkmk_os:          this.selOs.length          ? this.selOs          : null,
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
