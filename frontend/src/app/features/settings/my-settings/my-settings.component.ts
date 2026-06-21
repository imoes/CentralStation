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
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { forkJoin } from 'rxjs';
import { environment } from '../../../../environments/environment';
import { ThemeService } from '../../../core/services/theme.service';
import { AppLanguage, I18nService } from '../../../core/services/i18n.service';

@Component({
  selector: 'cs-my-settings',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatCardModule, MatFormFieldModule, MatInputModule, MatSelectModule,
    MatButtonModule, MatChipsModule, MatSnackBarModule,
    MatProgressSpinnerModule, MatIconModule, MatSlideToggleModule,
  ],
  template: `
    <div class="page-container">
      <div class="page-header">
        <h2>{{ i18n.t('settings.my.title') }}</h2>
        <button mat-raised-button color="primary" [disabled]="saving()" (click)="save()">
          @if (saving()) { <mat-spinner diameter="18"></mat-spinner> }
          @else { <ng-container><mat-icon>save</mat-icon> {{ i18n.t('settings.my.save') }}</ng-container> }
        </button>
      </div>

      <mat-card class="settings-card">
        <mat-card-header>
          <mat-card-title>{{ i18n.t('settings.my.appearance.title') }}</mat-card-title>
          <mat-card-subtitle>{{ i18n.t('settings.my.appearance.subtitle') }}</mat-card-subtitle>
        </mat-card-header>
        <mat-card-content>
          <mat-form-field appearance="outline" class="full-width">
            <mat-label>{{ i18n.t('settings.my.language') }}</mat-label>
            <mat-select [ngModel]="i18n.language()" (ngModelChange)="setLanguage($event)">
              <mat-option value="en">{{ i18n.t('language.en') }}</mat-option>
              <mat-option value="de">{{ i18n.t('language.de') }}</mat-option>
            </mat-select>
          </mat-form-field>
          <div class="theme-grid">
            @for (t of themes; track t.id) {
              <button class="theme-card" [class.active]="theme.theme() === t.id" (click)="theme.setTheme(t.id)">
                <span class="theme-swatch" [style.background]="t.swatch"></span>
                <span class="theme-name">{{ i18n.t(t.labelKey) }}</span>
                <span class="theme-desc">{{ i18n.t(t.descKey) }}</span>
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
            <mat-card-title>{{ i18n.t('settings.my.filter.title') }}</mat-card-title>
            <mat-card-subtitle>
              {{ i18n.t('settings.my.filter.subtitle') }}
            </mat-card-subtitle>
          </mat-card-header>
          <mat-card-content>

            <mat-form-field appearance="outline" class="age-field">
              <mat-label>{{ i18n.t('settings.my.minAge') }}</mat-label>
              <input matInput type="number" min="0" max="1440" [(ngModel)]="minAgeMins">
              <mat-hint>{{ i18n.t('settings.my.minAgeHint') }}</mat-hint>
            </mat-form-field>

            <mat-form-field appearance="outline" class="full-width">
              <mat-label>{{ i18n.t('settings.my.location') }}</mat-label>
              <mat-select multiple [(ngModel)]="selLocations">
                @for (v of filterValues.location; track v) {
                  <mat-option [value]="v">{{ v }}</mat-option>
                }
              </mat-select>
              <mat-hint>{{ i18n.t('settings.my.locationHint') }}</mat-hint>
            </mat-form-field>

            <mat-form-field appearance="outline" class="full-width">
              <mat-label>{{ i18n.t('settings.my.ve') }}</mat-label>
              <mat-select multiple [(ngModel)]="selVe">
                @for (v of filterValues.ve; track v) {
                  <mat-option [value]="v">{{ v }}</mat-option>
                }
              </mat-select>
            </mat-form-field>

            <mat-form-field appearance="outline" class="full-width">
              <mat-label>{{ i18n.t('settings.my.criticality') }}</mat-label>
              <mat-select multiple [(ngModel)]="selCriticality">
                @for (v of filterValues.criticality; track v) {
                  <mat-option [value]="v">{{ v }}</mat-option>
                }
              </mat-select>
            </mat-form-field>

            <mat-form-field appearance="outline" class="full-width">
              <mat-label>{{ i18n.t('settings.my.os') }}</mat-label>
              <mat-select multiple [(ngModel)]="selOs">
                @for (v of filterValues.os; track v) {
                  <mat-option [value]="v">{{ v }}</mat-option>
                }
              </mat-select>
            </mat-form-field>

            <mat-form-field appearance="outline" class="full-width">
              <mat-label>{{ i18n.t('settings.my.hostgroup') }}</mat-label>
              <mat-select multiple [(ngModel)]="selHostgroups">
                @for (v of filterValues.hostgroups; track v) {
                  <mat-option [value]="v">{{ v }}</mat-option>
                }
              </mat-select>
              <mat-hint>{{ i18n.t('settings.my.hostgroupHint') }}</mat-hint>
            </mat-form-field>

            @if (selLocations.length || selVe.length || selCriticality.length || selOs.length || selHostgroups.length) {
              <div class="active-filters">
                <span class="filter-label">{{ i18n.t('settings.my.activeFilters') }}</span>
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
                {{ i18n.t('settings.my.noFilters') }}
              </p>
            }

          </mat-card-content>
        </mat-card>

        <mat-card class="settings-card">
          <mat-card-header>
            <mat-card-title>{{ i18n.t('settings.my.password.title') }}</mat-card-title>
          </mat-card-header>
          <mat-card-content>
            <mat-form-field appearance="outline" class="pw-field">
              <mat-label>{{ i18n.t('settings.my.password.current') }}</mat-label>
              <input matInput [type]="pwShow ? 'text' : 'password'" [(ngModel)]="pwCurrent" autocomplete="current-password">
              <button matSuffix mat-icon-button (click)="pwShow = !pwShow" type="button" tabindex="-1">
                <mat-icon>{{ pwShow ? 'visibility_off' : 'visibility' }}</mat-icon>
              </button>
            </mat-form-field>
            <mat-form-field appearance="outline" class="pw-field">
              <mat-label>{{ i18n.t('settings.my.password.new') }}</mat-label>
              <input matInput [type]="pwShow ? 'text' : 'password'" [(ngModel)]="pwNew" autocomplete="new-password">
            </mat-form-field>
            <mat-form-field appearance="outline" class="pw-field">
              <mat-label>{{ i18n.t('settings.my.password.confirm') }}</mat-label>
              <input matInput [type]="pwShow ? 'text' : 'password'" [(ngModel)]="pwConfirm" autocomplete="new-password">
              @if (pwNew && pwConfirm && pwNew !== pwConfirm) {
                <mat-error>{{ i18n.t('settings.my.password.mismatch') }}</mat-error>
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
                {{ i18n.t('settings.my.password.submit') }}
              </button>
              <span class="pw-hint">{{ i18n.t('settings.my.password.minHint') }}</span>
            </div>
          </mat-card-content>
        </mat-card>
      }

      <!-- ── SSH-Zugang ── -->
      <mat-card class="settings-card">
        <mat-card-header>
          <mat-card-title>
            <mat-icon style="vertical-align:middle;margin-right:8px;">terminal</mat-icon>
            SSH-Zugang (Hermes &amp; Werkbank)
          </mat-card-title>
          <mat-card-subtitle>Benutzername und Key für SSH in Hermes-Konsole und Werkbank-Terminal</mat-card-subtitle>
        </mat-card-header>
        <mat-card-content>
          <mat-form-field appearance="outline" class="ssh-field">
            <mat-label>SSH-Benutzername</mat-label>
            <input matInput [(ngModel)]="sshUsername" placeholder="z.B. marvin">
          </mat-form-field>
          <mat-form-field appearance="outline" class="full-width">
            <mat-label>SSH Private Key (PEM)</mat-label>
            <textarea matInput rows="6" [(ngModel)]="sshKey"
                      placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea>
            <mat-hint>Inhalt von ~/.ssh/id_rsa oder id_ed25519 (leer lassen um vorhandenen zu behalten)</mat-hint>
          </mat-form-field>
          <mat-form-field appearance="outline" class="ssh-field">
            <mat-label>Passwort (Alternativ zum Key)</mat-label>
            <input matInput [type]="sshPwShow ? 'text' : 'password'" [(ngModel)]="sshPassword">
            <button matSuffix mat-icon-button (click)="sshPwShow = !sshPwShow" type="button" tabindex="-1">
              <mat-icon>{{ sshPwShow ? 'visibility_off' : 'visibility' }}</mat-icon>
            </button>
          </mat-form-field>
          <div class="ssh-actions">
            <button mat-flat-button color="primary" [disabled]="sshSaving() || !sshUsername" (click)="saveSshSettings()">
              @if (sshSaving()) { <mat-spinner diameter="18"></mat-spinner> }
              @else { <mat-icon>save</mat-icon> }
              Speichern
            </button>
            @if (sshActive) {
              <button mat-stroked-button color="warn" [disabled]="sshSaving()" (click)="deleteSshSettings()">
                <mat-icon>delete</mat-icon> Entfernen
              </button>
            }
          </div>
        </mat-card-content>
      </mat-card>

      <!-- ── E-Mail-Berichte ── -->
      <mat-card class="settings-card">
        <mat-card-header>
          <mat-card-title>
            <mat-icon style="vertical-align:middle;margin-right:8px;">email</mat-icon>
            E-Mail-Berichte
          </mat-card-title>
        </mat-card-header>
        <mat-card-content>
          <p style="margin:0 0 12px;font-size:13px;color:var(--mat-sys-on-surface-variant);">
            KI-Insights aus deinen CheckMK-Filter-Einstellungen, gruppiert nach Host und Service.
          </p>

          <div class="digest-row">
            <mat-slide-toggle [(ngModel)]="digestDaily">Täglicher Bericht</mat-slide-toggle>
            @if (digestDaily) {
              <mat-form-field class="digest-hour-field" appearance="outline">
                <mat-label>Stunde (UTC)</mat-label>
                <input matInput type="number" min="0" max="23" [(ngModel)]="digestDailyHour">
              </mat-form-field>
              <span class="digest-hint">:00 Uhr UTC</span>
            }
          </div>

          <div class="digest-row">
            <mat-slide-toggle [(ngModel)]="digestWeekly">Wöchentlicher Bericht</mat-slide-toggle>
            @if (digestWeekly) {
              <mat-form-field class="digest-select-field" appearance="outline">
                <mat-label>Wochentag</mat-label>
                <mat-select [(ngModel)]="digestWeeklyDay">
                  @for (d of weekdays; track d.value) {
                    <mat-option [value]="d.value">{{ d.label }}</mat-option>
                  }
                </mat-select>
              </mat-form-field>
              <mat-form-field class="digest-hour-field" appearance="outline">
                <mat-label>Stunde (UTC)</mat-label>
                <input matInput type="number" min="0" max="23" [(ngModel)]="digestWeeklyHour">
              </mat-form-field>
              <span class="digest-hint">:00 Uhr UTC</span>
            }
          </div>

          <div style="margin-top:12px;">
            <button mat-flat-button color="primary" [disabled]="digestSaving()" (click)="saveDigestSettings()">
              @if (digestSaving()) { <mat-spinner diameter="18"></mat-spinner> }
              @else { <mat-icon>save</mat-icon> }
              Speichern
            </button>
          </div>
        </mat-card-content>
      </mat-card>

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
    .digest-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 4px 0; }
    .digest-hour-field { width: 100px; }
    .digest-select-field { width: 140px; }
    .digest-hint { font-size: 12px; color: var(--mat-sys-on-surface-variant); }
    .ssh-field { width: 100%; max-width: 380px; }
    .ssh-actions { display: flex; align-items: center; gap: 12px; padding-top: 4px; }
  `],
})
export class MySettingsComponent implements OnInit {
  loading = signal(true);
  saving  = signal(false);

  readonly themes: { id: 'classic'|'holo'|'lcars'; labelKey: string; descKey: string; swatch: string }[] = [
    { id: 'classic', labelKey: 'theme.classic.label', descKey: 'theme.classic.desc', swatch: 'linear-gradient(135deg,#eef4fb,#1565c0)' },
    { id: 'holo',    labelKey: 'theme.holo.label',    descKey: 'theme.holo.desc',    swatch: 'linear-gradient(135deg,#050d1a,#4fd6ff)' },
    { id: 'lcars',   labelKey: 'theme.lcars.label',   descKey: 'theme.lcars.desc',   swatch: 'linear-gradient(135deg,#000 40%,#e87c3a)' },
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

  digestDaily      = false;
  digestDailyHour  = 7;
  digestWeekly     = false;
  digestWeeklyDay  = 0;
  digestWeeklyHour = 7;
  digestSaving     = signal(false);

  sshUsername = '';
  sshKey      = '';
  sshPassword = '';
  sshPwShow   = false;
  sshActive   = false;
  sshSaving   = signal(false);

  readonly weekdays = [
    { value: 0, label: 'Montag' },
    { value: 1, label: 'Dienstag' },
    { value: 2, label: 'Mittwoch' },
    { value: 3, label: 'Donnerstag' },
    { value: 4, label: 'Freitag' },
    { value: 5, label: 'Samstag' },
    { value: 6, label: 'Sonntag' },
  ];

  filterValues: { location: string[]; ve: string[]; criticality: string[]; os: string[]; hostgroups: string[] } = {
    location: [], ve: [], criticality: [], os: [], hostgroups: [],
  };

  constructor(
    private http: HttpClient,
    private snack: MatSnackBar,
    public theme: ThemeService,
    public i18n: I18nService,
  ) {}

  ngOnInit() {
    this.loadSshSettings();
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

        const ns = prefs?.notification_settings || {};
        this.digestDaily      = !!ns.digest_daily;
        this.digestDailyHour  = ns.digest_daily_hour  ?? 7;
        this.digestWeekly     = !!ns.digest_weekly;
        this.digestWeeklyDay  = ns.digest_weekly_day  ?? 0;
        this.digestWeeklyHour = ns.digest_weekly_hour ?? 7;

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
        this.snack.open(this.i18n.t('settings.my.password.changed'), 'OK', { duration: 3000 });
      },
      error: (err) => {
        this.pwSaving.set(false);
        this.pwError = err?.error?.detail === 'Current password wrong'
          ? this.i18n.t('settings.my.password.currentWrong')
          : (err?.error?.detail ?? this.i18n.t('settings.my.password.error'));
      },
    });
  }

  saveDigestSettings() {
    this.digestSaving.set(true);
    this.http.patch(`${environment.apiUrl}/preferences`, {
      notification_settings: {
        digest_daily:       this.digestDaily,
        digest_daily_hour:  this.digestDailyHour,
        digest_weekly:      this.digestWeekly,
        digest_weekly_day:  this.digestWeeklyDay,
        digest_weekly_hour: this.digestWeeklyHour,
      },
    }).subscribe({
      next: () => {
        this.digestSaving.set(false);
        this.snack.open('Gespeichert', 'OK', { duration: 3000 });
      },
      error: () => {
        this.digestSaving.set(false);
        this.snack.open('Fehler beim Speichern', 'OK', { duration: 3000 });
      },
    });
  }

  loadSshSettings() {
    this.http.get<any[]>(`${environment.apiUrl}/connectors/my`).subscribe({
      next: list => {
        const ssh = list.find((c: any) => c.type === 'ssh');
        if (!ssh) return;
        this.sshActive = true;
        this.http.get<{ credentials: Record<string, string> }>(
          `${environment.apiUrl}/connectors/my/ssh/credentials`
        ).subscribe({
          next: ({ credentials: creds }) => {
            this.sshUsername = creds['username'] ?? '';
            // Key/password never pre-filled for security
          },
        });
      },
    });
  }

  saveSshSettings() {
    if (!this.sshUsername) return;
    this.sshSaving.set(true);
    const credentials: Record<string, string> = { username: this.sshUsername };
    if (this.sshKey.trim()) credentials['private_key'] = this.sshKey.trim();
    if (this.sshPassword) credentials['password'] = this.sshPassword;
    this.http.put(`${environment.apiUrl}/connectors/my/ssh`, {
      name: 'SSH-Zugang',
      type: 'ssh',
      base_url: null,
      credentials,
      enabled: true,
    }).subscribe({
      next: () => {
        this.sshSaving.set(false);
        this.sshActive = true;
        this.sshPassword = '';
        this.sshKey = '';
        this.snack.open('SSH-Einstellungen gespeichert', 'OK', { duration: 3000 });
      },
      error: () => {
        this.sshSaving.set(false);
        this.snack.open('Fehler beim Speichern der SSH-Einstellungen', 'OK', { duration: 3000 });
      },
    });
  }

  deleteSshSettings() {
    if (!confirm('SSH-Einstellungen wirklich entfernen?')) return;
    this.sshSaving.set(true);
    this.http.delete(`${environment.apiUrl}/connectors/my/ssh`).subscribe({
      next: () => {
        this.sshSaving.set(false);
        this.sshActive = false;
        this.sshUsername = '';
        this.sshKey = '';
        this.sshPassword = '';
        this.snack.open('SSH-Einstellungen entfernt', 'OK', { duration: 3000 });
      },
      error: () => {
        this.sshSaving.set(false);
        this.snack.open('Fehler beim Entfernen', 'OK', { duration: 3000 });
      },
    });
  }

  setLanguage(lang: AppLanguage) {
    this.i18n.setLanguage(lang);
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
        this.snack.open(this.i18n.t('settings.my.saved'), 'OK', { duration: 3000 });
      },
      error: () => {
        this.saving.set(false);
        this.snack.open(this.i18n.t('settings.my.saveError'), 'OK', { duration: 3000 });
      },
    });
  }

}
