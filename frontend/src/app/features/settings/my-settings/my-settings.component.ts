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

interface JiraQuery {
  id: string;
  name: string;
  jql: string;
  position: number;
  enabled: boolean;
  show_in_widget: boolean;
}

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

        <mat-card class="settings-card query-card">
          <mat-card-header>
            <mat-card-title>Meine Jira-Queries</mat-card-title>
            <mat-card-subtitle>
              Default-Queries werden automatisch angelegt, wenn noch keine persönlichen Queries existieren.
            </mat-card-subtitle>
          </mat-card-header>
          <mat-card-content>
            <div class="ai-query-row">
              <mat-form-field appearance="outline">
                <mat-label>Neue Query per KI-Prompt</mat-label>
                <textarea matInput rows="2" [(ngModel)]="jqlPrompt"
                  placeholder="z.B. alle offenen P1 Tickets, die heute aktualisiert wurden"></textarea>
              </mat-form-field>
              <button mat-flat-button color="primary" (click)="createQueryWithAi()" [disabled]="generatingQuery() || !jqlPrompt.trim()">
                @if (generatingQuery()) { <mat-spinner diameter="18"></mat-spinner> }
                @else { <mat-icon>auto_awesome</mat-icon> }
                Query erstellen
              </button>
            </div>

            @if (loadingQueries()) {
              <div class="mini-spinner"><mat-spinner diameter="28"></mat-spinner></div>
            } @else {
              <div class="query-list">
                @for (query of jiraQueries(); track query.id) {
                  <div class="query-row">
                    <mat-form-field appearance="outline">
                      <mat-label>Name</mat-label>
                      <input matInput [(ngModel)]="query.name">
                    </mat-form-field>
                    <mat-form-field appearance="outline" class="jql-field">
                      <mat-label>JQL</mat-label>
                      <textarea matInput rows="2" [(ngModel)]="query.jql"></textarea>
                    </mat-form-field>
                    <div class="query-actions">
                      <button mat-stroked-button (click)="saveJiraQuery(query)">
                        <mat-icon>save</mat-icon>
                        Speichern
                      </button>
                      <button mat-icon-button color="warn" (click)="deleteJiraQuery(query.id)" aria-label="Query löschen">
                        <mat-icon>delete</mat-icon>
                      </button>
                    </div>
                  </div>
                }
              </div>
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
    .query-card { margin-top: 16px; }
    .ai-query-row { display: grid; grid-template-columns: 1fr auto; gap: 12px; align-items: center; }
    .ai-query-row mat-form-field { width: 100%; }
    .ai-query-row mat-spinner, .mini-spinner mat-spinner { display: inline-block; }
    .mini-spinner { display: flex; justify-content: center; padding: 20px; }
    .query-list { display: flex; flex-direction: column; gap: 12px; }
    .query-row {
      display: grid;
      grid-template-columns: 220px 1fr auto;
      gap: 10px;
      align-items: start;
      padding: 12px;
      border: 1px solid var(--mat-sys-outline-variant);
      border-radius: 12px;
    }
    .query-actions { display: flex; align-items: center; gap: 4px; padding-top: 4px; }
    .jql-field { width: 100%; }
    @media (max-width: 820px) {
      .ai-query-row, .query-row { grid-template-columns: 1fr; }
      .age-field { width: 100%; }
    }
  `],
})
export class MySettingsComponent implements OnInit {
  loading = signal(true);
  saving  = signal(false);
  loadingQueries = signal(true);
  generatingQuery = signal(false);
  jiraQueries = signal<JiraQuery[]>([]);
  jqlPrompt = '';

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
    forkJoin({
      prefs: this.http.get<any>(`${environment.apiUrl}/preferences`),
      vals: this.http.get<any>(`${environment.apiUrl}/feed/checkmk-filter-values`),
      queries: this.http.get<JiraQuery[]>(`${environment.apiUrl}/preferences/jira-queries`),
    }).subscribe({
      next: ({ prefs, vals, queries }) => {
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
      this.jiraQueries.set(queries);
      this.loading.set(false);
      this.loadingQueries.set(false);
    },
      error: () => {
        this.loading.set(false);
        this.loadingQueries.set(false);
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

  saveJiraQuery(query: JiraQuery) {
    this.http.patch(`${environment.apiUrl}/preferences/jira-queries/${query.id}`, {
      name: query.name,
      jql: query.jql,
      position: query.position,
      enabled: query.enabled,
      show_in_widget: query.show_in_widget,
    }).subscribe({
      next: () => this.snack.open('Query gespeichert', 'OK', { duration: 2500 }),
      error: () => this.snack.open('Query konnte nicht gespeichert werden', 'OK', { duration: 3000 }),
    });
  }

  deleteJiraQuery(queryId: string) {
    this.http.delete(`${environment.apiUrl}/preferences/jira-queries/${queryId}`).subscribe({
      next: () => {
        this.jiraQueries.update(queries => queries.filter(q => q.id !== queryId));
        this.snack.open('Query gelöscht', 'OK', { duration: 2500 });
      },
      error: () => this.snack.open('Query konnte nicht gelöscht werden', 'OK', { duration: 3000 }),
    });
  }

  createQueryWithAi() {
    const prompt = this.jqlPrompt.trim();
    if (!prompt) return;
    this.generatingQuery.set(true);
    this.http.post<{ name?: string; jql: string }>(`${environment.apiUrl}/preferences/jira-queries/generate`, {
      description: prompt,
    }).subscribe({
      next: generated => {
        this.http.post<JiraQuery>(`${environment.apiUrl}/preferences/jira-queries`, {
          name: generated.name || prompt.slice(0, 80),
          jql: generated.jql,
          position: this.jiraQueries().length,
          show_in_widget: true,
        }).subscribe({
          next: query => {
            this.jiraQueries.update(queries => [...queries, query]);
            this.jqlPrompt = '';
            this.generatingQuery.set(false);
            this.snack.open('KI-Query erstellt', 'OK', { duration: 2500 });
          },
          error: () => {
            this.generatingQuery.set(false);
            this.snack.open('Query konnte nicht gespeichert werden', 'OK', { duration: 3000 });
          },
        });
      },
      error: err => {
        this.generatingQuery.set(false);
        this.snack.open(err?.error?.detail ?? 'KI konnte keine Query erzeugen', 'OK', { duration: 3500 });
      },
    });
  }
}
