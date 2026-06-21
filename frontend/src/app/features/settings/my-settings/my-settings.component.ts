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
import { AuthService } from '../../../core/auth/auth.service';
import { MatDividerModule } from '@angular/material/divider';

@Component({
  selector: 'cs-my-settings',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatCardModule, MatFormFieldModule, MatInputModule, MatSelectModule,
    MatButtonModule, MatChipsModule, MatSnackBarModule,
    MatProgressSpinnerModule, MatIconModule, MatSlideToggleModule, MatDividerModule,
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

      <!-- ── Persönliche Konnektoren ── -->
      <mat-card class="settings-card">
        <mat-card-header>
          <mat-card-title>
            <mat-icon style="vertical-align:middle;margin-right:8px;">cable</mat-icon>
            Persönliche Konnektoren
          </mat-card-title>
          <mat-card-subtitle>Überschreiben die Admin-Einstellungen für deine Sitzung</mat-card-subtitle>
        </mat-card-header>
        <mat-card-content>

          <!-- LLM / KI-Konnektor -->
          <div class="connector-section">
            <div class="connector-header">
              <span class="connector-title">KI-Konnektor (LLM)</span>
              <span class="connector-hint">Überschreibt den Admin-KI-Konnektor für Hermes Konsole und KI-Bericht</span>
              @if (llmConnector.active) {
                <span class="connector-badge active">Aktiv</span>
              }
            </div>
            <div class="connector-fields">
              <mat-form-field appearance="outline" class="conn-field-wide">
                <mat-label>Endpoint URL</mat-label>
                <input matInput [(ngModel)]="llmConnector.base_url" placeholder="https://api.openai.com/v1">
              </mat-form-field>
              <mat-form-field appearance="outline" class="conn-field">
                <mat-label>Modell</mat-label>
                <input matInput [(ngModel)]="llmConnector.model" placeholder="gpt-4o">
              </mat-form-field>
              <mat-form-field appearance="outline" class="conn-field-wide">
                <mat-label>API Key</mat-label>
                <input matInput [type]="llmConnector.showKey ? 'text' : 'password'" [(ngModel)]="llmConnector.api_key">
                <button matSuffix mat-icon-button (click)="llmConnector.showKey = !llmConnector.showKey" type="button" tabindex="-1">
                  <mat-icon>{{ llmConnector.showKey ? 'visibility_off' : 'visibility' }}</mat-icon>
                </button>
              </mat-form-field>
              <mat-form-field appearance="outline" class="conn-field">
                <mat-label>API-Modus</mat-label>
                <mat-select [(ngModel)]="llmConnector.api_mode">
                  <mat-option value="chat_completions">OpenAI Chat Completions</mat-option>
                  <mat-option value="anthropic_messages">Anthropic Messages</mat-option>
                  <mat-option value="responses">OpenAI Responses</mat-option>
                </mat-select>
              </mat-form-field>
            </div>
            <div class="connector-actions">
              <button mat-flat-button color="primary" [disabled]="llmConnector.saving" (click)="saveLlmConnector()">
                <mat-icon>save</mat-icon> Speichern
              </button>
              @if (llmConnector.active) {
                <button mat-stroked-button color="warn" [disabled]="llmConnector.saving" (click)="deleteLlmConnector()">
                  <mat-icon>delete</mat-icon> Entfernen
                </button>
              }
            </div>
          </div>

          <mat-divider class="connector-divider"></mat-divider>

          <!-- MCP Server -->
          <div class="connector-section">
            <div class="connector-header">
              <span class="connector-title">MCP Server</span>
              <span class="connector-hint">Zusätzlicher MCP-Server für die Hermes Konsole</span>
              @if (mcpConnector.active) {
                <span class="connector-badge active">Aktiv</span>
              }
            </div>
            <div class="connector-fields">
              <mat-form-field appearance="outline" class="conn-field">
                <mat-label>Name</mat-label>
                <input matInput [(ngModel)]="mcpConnector.name" placeholder="Mein MCP Server">
              </mat-form-field>
              <mat-form-field appearance="outline" class="conn-field-wide">
                <mat-label>Server URL</mat-label>
                <input matInput [(ngModel)]="mcpConnector.base_url" placeholder="https://my-mcp-server.example.com/mcp">
              </mat-form-field>
              <mat-form-field appearance="outline" class="conn-field-wide">
                <mat-label>Token (optional)</mat-label>
                <input matInput [type]="mcpConnector.showKey ? 'text' : 'password'" [(ngModel)]="mcpConnector.token">
                <button matSuffix mat-icon-button (click)="mcpConnector.showKey = !mcpConnector.showKey" type="button" tabindex="-1">
                  <mat-icon>{{ mcpConnector.showKey ? 'visibility_off' : 'visibility' }}</mat-icon>
                </button>
              </mat-form-field>
            </div>
            <div class="connector-actions">
              <button mat-flat-button color="primary" [disabled]="mcpConnector.saving" (click)="saveMcpConnector()">
                <mat-icon>save</mat-icon> Speichern
              </button>
              @if (mcpConnector.active) {
                <button mat-stroked-button color="warn" [disabled]="mcpConnector.saving" (click)="deleteMcpConnector()">
                  <mat-icon>delete</mat-icon> Entfernen
                </button>
              }
            </div>
          </div>

          <mat-divider class="connector-divider"></mat-divider>

          <!-- AWX-NG -->
          <div class="connector-section">
            <div class="connector-header">
              <span class="connector-title">AWX-NG (Ansible Manager)</span>
              <span class="connector-hint">Schaltet den Maschinenraum-Navpunkt frei und integriert AWX-NG MCP in Hermes</span>
              @if (awxConnector.active) {
                <span class="connector-badge active">Aktiv — Maschinenraum sichtbar</span>
              }
            </div>
            <div class="connector-fields">
              <mat-form-field appearance="outline" class="conn-field-wide">
                <mat-label>AWX-NG URL</mat-label>
                <input matInput [(ngModel)]="awxConnector.base_url" placeholder="http://awx-ng.ippen.media">
              </mat-form-field>
              <mat-form-field appearance="outline" class="conn-field">
                <mat-label>Benutzername</mat-label>
                <input matInput [(ngModel)]="awxConnector.username" placeholder="admin">
              </mat-form-field>
              <mat-form-field appearance="outline" class="conn-field">
                <mat-label>Passwort</mat-label>
                <input matInput [type]="awxConnector.showKey ? 'text' : 'password'" [(ngModel)]="awxConnector.password">
                <button matSuffix mat-icon-button (click)="awxConnector.showKey = !awxConnector.showKey" type="button" tabindex="-1">
                  <mat-icon>{{ awxConnector.showKey ? 'visibility_off' : 'visibility' }}</mat-icon>
                </button>
              </mat-form-field>
            </div>
            <div class="connector-actions">
              <button mat-flat-button color="primary" [disabled]="awxConnector.saving" (click)="saveAwxConnector()">
                <mat-icon>save</mat-icon> Speichern
              </button>
              @if (awxConnector.active) {
                <button mat-stroked-button color="warn" [disabled]="awxConnector.saving" (click)="deleteAwxConnector()">
                  <mat-icon>delete</mat-icon> Entfernen
                </button>
              }
            </div>
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
    .connector-section { padding: 8px 0; }
    .connector-header { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
    .connector-title { font-weight: 600; font-size: 14px; }
    .connector-hint { font-size: 12px; color: var(--mat-sys-on-surface-variant); }
    .connector-badge { font-size: 11px; padding: 2px 8px; border-radius: 12px; background: var(--mat-sys-primary-container); color: var(--mat-sys-on-primary-container); font-weight: 600; }
    .connector-fields { display: flex; flex-wrap: wrap; gap: 8px; }
    .conn-field { width: 200px; }
    .conn-field-wide { width: 340px; }
    .connector-actions { display: flex; gap: 8px; margin-top: 4px; }
    .connector-divider { margin: 16px 0; }
    @media (max-width: 600px) { .conn-field, .conn-field-wide { width: 100%; } }
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

  llmConnector = { active: false, saving: false, showKey: false, base_url: '', model: '', api_key: '', api_mode: 'chat_completions' };
  mcpConnector = { active: false, saving: false, showKey: false, name: '', base_url: '', token: '' };
  awxConnector = { active: false, saving: false, showKey: false, base_url: '', username: '', password: '' };

  constructor(
    private http: HttpClient,
    private snack: MatSnackBar,
    public theme: ThemeService,
    public i18n: I18nService,
    private auth: AuthService,
  ) {}

  ngOnInit() {
    // Load personal connectors
    this.http.get<any[]>(`${environment.apiUrl}/connectors/my`).subscribe({
      next: conns => {
        for (const c of conns) {
          if (c.type === 'llm') {
            this.llmConnector.active = true;
            this.llmConnector.base_url = c.base_url || '';
          }
          if (c.type === 'mcp_server') {
            this.mcpConnector.active = true;
            this.mcpConnector.name = c.name || '';
            this.mcpConnector.base_url = c.base_url || '';
          }
          if (c.type === 'awx_ng') {
            this.awxConnector.active = true;
            this.awxConnector.base_url = c.base_url || '';
          }
        }
        // Load credential placeholders for active connectors (user endpoint, masked)
        for (const c of conns) {
          if (c.type === 'llm') {
            this.http.get<any>(`${environment.apiUrl}/connectors/my/llm/credentials`).subscribe({
              next: d => {
                const creds = d?.credentials || {};
                this.llmConnector.model = creds.model || '';
                this.llmConnector.api_key = creds.api_key || '';
                this.llmConnector.api_mode = creds.api_mode || 'chat_completions';
              },
              error: () => {},
            });
          }
          if (c.type === 'mcp_server') {
            this.http.get<any>(`${environment.apiUrl}/connectors/my/mcp_server/credentials`).subscribe({
              next: d => { this.mcpConnector.token = d?.credentials?.token || ''; },
              error: () => {},
            });
          }
          if (c.type === 'awx_ng') {
            this.http.get<any>(`${environment.apiUrl}/connectors/my/awx_ng/credentials`).subscribe({
              next: d => {
                const creds = d?.credentials || {};
                this.awxConnector.username = creds.username || '';
                this.awxConnector.password = creds.password || '';
              },
              error: () => {},
            });
          }
        }
      },
      error: () => {},
    });

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

  saveLlmConnector() {
    if (!this.llmConnector.base_url || !this.llmConnector.model) {
      this.snack.open('URL und Modell sind erforderlich', 'OK', { duration: 3000 }); return;
    }
    this.llmConnector.saving = true;
    this.http.put(`${environment.apiUrl}/connectors/my/llm`, {
      type: 'llm', name: 'Mein KI-Konnektor', enabled: true,
      base_url: this.llmConnector.base_url,
      credentials: { model: this.llmConnector.model, api_key: this.llmConnector.api_key, api_mode: this.llmConnector.api_mode },
    }).subscribe({
      next: () => { this.llmConnector.saving = false; this.llmConnector.active = true; this.snack.open('KI-Konnektor gespeichert', 'OK', { duration: 3000 }); },
      error: () => { this.llmConnector.saving = false; this.snack.open('Fehler beim Speichern', 'OK', { duration: 3000 }); },
    });
  }

  deleteLlmConnector() {
    this.llmConnector.saving = true;
    this.http.delete(`${environment.apiUrl}/connectors/my/llm`).subscribe({
      next: () => { this.llmConnector.saving = false; this.llmConnector.active = false; this.llmConnector.base_url = ''; this.llmConnector.model = ''; this.llmConnector.api_key = ''; this.snack.open('KI-Konnektor entfernt', 'OK', { duration: 3000 }); },
      error: () => { this.llmConnector.saving = false; this.snack.open('Fehler beim Löschen', 'OK', { duration: 3000 }); },
    });
  }

  saveMcpConnector() {
    if (!this.mcpConnector.base_url || !this.mcpConnector.name) {
      this.snack.open('Name und URL sind erforderlich', 'OK', { duration: 3000 }); return;
    }
    this.mcpConnector.saving = true;
    this.http.put(`${environment.apiUrl}/connectors/my/mcp_server`, {
      type: 'mcp_server', name: this.mcpConnector.name, enabled: true,
      base_url: this.mcpConnector.base_url,
      credentials: { token: this.mcpConnector.token },
    }).subscribe({
      next: () => { this.mcpConnector.saving = false; this.mcpConnector.active = true; this.snack.open('MCP-Server gespeichert', 'OK', { duration: 3000 }); },
      error: () => { this.mcpConnector.saving = false; this.snack.open('Fehler beim Speichern', 'OK', { duration: 3000 }); },
    });
  }

  deleteMcpConnector() {
    this.mcpConnector.saving = true;
    this.http.delete(`${environment.apiUrl}/connectors/my/mcp_server`).subscribe({
      next: () => { this.mcpConnector.saving = false; this.mcpConnector.active = false; this.mcpConnector.base_url = ''; this.mcpConnector.token = ''; this.snack.open('MCP-Server entfernt', 'OK', { duration: 3000 }); },
      error: () => { this.mcpConnector.saving = false; this.snack.open('Fehler beim Löschen', 'OK', { duration: 3000 }); },
    });
  }

  saveAwxConnector() {
    if (!this.awxConnector.base_url || !this.awxConnector.username) {
      this.snack.open('URL und Benutzername sind erforderlich', 'OK', { duration: 3000 }); return;
    }
    this.awxConnector.saving = true;
    this.http.put(`${environment.apiUrl}/connectors/my/awx_ng`, {
      type: 'awx_ng', name: 'AWX-NG', enabled: true,
      base_url: this.awxConnector.base_url,
      credentials: { username: this.awxConnector.username, password: this.awxConnector.password },
    }).subscribe({
      next: () => {
        this.awxConnector.saving = false;
        this.awxConnector.active = true;
        this.snack.open('AWX-NG Konnektor gespeichert — Maschinenraum wird sichtbar', 'OK', { duration: 4000 });
        // Reload user profile so has_awx_ng gets updated in nav
        this.auth.fetchMe();
      },
      error: () => { this.awxConnector.saving = false; this.snack.open('Fehler beim Speichern', 'OK', { duration: 3000 }); },
    });
  }

  deleteAwxConnector() {
    this.awxConnector.saving = true;
    this.http.delete(`${environment.apiUrl}/connectors/my/awx_ng`).subscribe({
      next: () => {
        this.awxConnector.saving = false;
        this.awxConnector.active = false;
        this.awxConnector.base_url = '';
        this.awxConnector.username = '';
        this.awxConnector.password = '';
        this.snack.open('AWX-NG Konnektor entfernt', 'OK', { duration: 3000 });
        this.auth.fetchMe();
      },
      error: () => { this.awxConnector.saving = false; this.snack.open('Fehler beim Löschen', 'OK', { duration: 3000 }); },
    });
  }
}
