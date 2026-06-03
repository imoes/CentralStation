import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../../environments/environment';
import { FormBuilder, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatSelectModule } from '@angular/material/select';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDividerModule } from '@angular/material/divider';
import { MatTooltipModule } from '@angular/material/tooltip';
import { ConnectorService } from '../../../core/services/connector.service';
import { SettingItem } from '../../../core/models/connector.model';

interface TestResult { success: boolean; message: string; detail: string | null; }

const SETTING_GROUPS: { title: string; keys: string[]; testGroup?: string; codexSection?: boolean }[] = [
  {
    title: 'LLM Konfiguration',
    keys: ['llm.base_url', 'llm.model', 'llm.api_mode', 'llm.api_key', 'llm.timeout_seconds', 'llm.thinking_mode'],
    testGroup: 'llm',
  },
  {
    title: 'OpenAI Codex Fallback (Hermes OAuth)',
    keys: ['llm.codex_fallback_enabled', 'llm.codex_hermes_provider', 'llm.codex_base_url', 'llm.codex_model', 'llm.codex_timeout_seconds'],
    testGroup: 'codex',
    codexSection: true,
  },
  {
    title: 'Vision Modell',
    keys: ['llm.vision_base_url', 'llm.vision_model', 'llm.vision_api_key'],
    testGroup: 'vision',
  },
  {
    title: 'SearXNG Web-Suche',
    keys: ['searxng.base_url', 'searxng.enabled', 'searxng.results_count'],
    testGroup: 'searxng',
  },
  {
    title: 'Agent Einstellungen',
    keys: [
      'agent.interval_minutes',
      'agent.aggregation_interval_minutes',
      'agent.auto_jira',
      'agent.auto_enrich',
      'agent.rag_enabled',
      'workflow.web_search',
      'agent.scoring_enabled',
      'agent.enrich_score_threshold',
      'agent.max_alerts_for_llm',
      'agent.flap_window_minutes',
      'agent.flap_threshold',
      'agent.score_learning_enabled',
      'agent.score_delta_decay_days',
      'agent.worklist_interval_minutes',
      'agent.worklist_size',
      'agent.generative_interval_minutes',
      'agent.jira_severity_threshold',
      'agent.checkmk_locations',
    ],
  },
];

const BOOLEAN_KEYS = new Set(['searxng.enabled', 'agent.auto_jira', 'agent.auto_enrich', 'agent.rag_enabled', 'llm.thinking_mode', 'workflow.web_search', 'agent.score_learning_enabled', 'agent.scoring_enabled', 'llm.codex_fallback_enabled']);
const SELECT_KEYS: Record<string, string[]> = {
  'llm.api_mode': ['chat_completions', 'responses'],
  'agent.jira_severity_threshold': ['critical', 'high', 'medium'],
};
const SECRET_MASK = '••••••••';

@Component({
  selector: 'cs-ai-settings',
  standalone: true,
  imports: [
    CommonModule, ReactiveFormsModule,
    MatCardModule, MatFormFieldModule, MatInputModule,
    MatButtonModule, MatIconModule, MatSlideToggleModule,
    MatSelectModule, MatProgressSpinnerModule, MatSnackBarModule,
    MatDividerModule, MatTooltipModule,
  ],
  template: `
    <div class="page-container">
      <div class="page-header">
        <h2>Globale Einstellungen</h2>
        <button mat-raised-button color="primary" [disabled]="saving()" (click)="saveAll()">
          @if (saving()) {
            <mat-spinner diameter="18"></mat-spinner>
          } @else {
            <ng-container><mat-icon>save</mat-icon> Speichern</ng-container>
          }
        </button>
      </div>

      @if (loading()) {
        <div class="spinner-center"><mat-spinner diameter="40"></mat-spinner></div>
      } @else if (form) {
        <form [formGroup]="form">
          @for (group of groups; track group.title) {
            <mat-card class="settings-card">
              <mat-card-header>
                <mat-card-title>{{ group.title }}</mat-card-title>
                @if (group.testGroup) {
                  <div class="card-header-actions">
                    <button mat-stroked-button
                            [disabled]="testingGroup() === group.testGroup"
                            (click)="testGroup(group.testGroup!)"
                            matTooltip="Verbindung mit gespeicherten Werten testen">
                      @if (testingGroup() === group.testGroup) {
                        <mat-spinner diameter="16"></mat-spinner>
                      } @else {
                        <ng-container><mat-icon>wifi_tethering</mat-icon></ng-container>
                      }
                      Verbindung testen
                    </button>
                  </div>
                }
              </mat-card-header>
              <!-- Codex OAuth status banner -->
              @if (group.codexSection && codexStatus()) {
                <div class="codex-status-banner" [class.authenticated]="codexStatus()!.authenticated">
                  <mat-icon>{{ codexStatus()!.authenticated ? 'verified_user' : 'no_accounts' }}</mat-icon>
                  <div class="codex-status-text">
                    <strong>Hermes OAuth: {{ codexStatus()!.authenticated ? 'Eingeloggt' : 'Nicht eingeloggt' }}</strong>
                    <span>{{ codexStatus()!.message }}</span>
                    @if (!codexStatus()!.authenticated) {
                      <code>hermes auth {{ codexStatus()!.provider }}</code>
                    }
                    @if (codexStatus()!.authenticated && codexStatus()!.expires_at) {
                      <span class="expires">Gültig bis: {{ codexStatus()!.expires_at }}</span>
                    }
                  </div>
                  <button mat-icon-button (click)="loadCodexStatus()" matTooltip="Status neu laden">
                    <mat-icon>refresh</mat-icon>
                  </button>
                </div>
              }
              <mat-card-content>
                @for (key of group.keys; track key) {
                  @if (isBooleanKey(key)) {
                    <div class="toggle-row">
                      <span class="key-label">{{ keyLabel(key) }}</span>
                      <mat-slide-toggle [formControlName]="key"></mat-slide-toggle>
                    </div>
                  } @else if (isSelectKey(key)) {
                    <mat-form-field appearance="outline" class="setting-field">
                      <mat-label>{{ keyLabel(key) }}</mat-label>
                      <mat-select [formControlName]="key">
                        @for (opt of selectOptions(key); track opt) {
                          <mat-option [value]="opt">{{ opt }}</mat-option>
                        }
                      </mat-select>
                    </mat-form-field>
                  } @else {
                    <mat-form-field appearance="outline" class="setting-field">
                      <mat-label>{{ keyLabel(key) }}</mat-label>
                      <input matInput [formControlName]="key"
                             [type]="isSecret(key) ? 'password' : 'text'"
                             [placeholder]="isSecret(key) ? 'Leer lassen = unverändert' : ''">
                    </mat-form-field>
                  }
                }

                @if (group.testGroup && testResults()[group.testGroup]) {
                  @let res = testResults()[group.testGroup]!;
                  <div class="test-result" [class.success]="res.success" [class.error]="!res.success">
                    <mat-icon>{{ res.success ? 'check_circle' : 'error' }}</mat-icon>
                    <div class="test-result-text">
                      <span class="test-message">{{ res.message }}</span>
                      @if (res.detail) {
                        <span class="test-detail">{{ res.detail }}</span>
                      }
                    </div>
                  </div>
                }
              </mat-card-content>
            </mat-card>
          }
        </form>
      }
    </div>
  `,
  styles: [`
    .page-container { padding: 24px; max-width: 800px; }
    .page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
    .page-header h2 { margin: 0; }
    .settings-card { margin-bottom: 16px; }
    .settings-card mat-card-content { padding-top: 16px; }
    .setting-field { width: 100%; margin-bottom: 4px; }
    .toggle-row { display: flex; align-items: center; justify-content: space-between; padding: 8px 0; }
    .key-label { font-size: 14px; }
    .spinner-center { display: flex; justify-content: center; padding: 40px; }
    mat-spinner { display: inline-block; }

    mat-card-header { display: flex; align-items: center; justify-content: space-between; }
    .card-header-actions { margin-left: auto; }
    .card-header-actions button { font-size: 13px; }
    .card-header-actions mat-icon { font-size: 16px; height: 16px; width: 16px; vertical-align: middle; margin-right: 4px; }

    .test-result {
      display: flex; align-items: flex-start; gap: 8px;
      padding: 10px 12px; border-radius: 6px; margin-top: 8px;
      font-size: 13px;
    }
    .test-result.success { background: color-mix(in srgb, #4caf50 12%, transparent); color: #2e7d32; }
    .test-result.error   { background: color-mix(in srgb, #f44336 12%, transparent); color: #c62828; }
    .test-result mat-icon { font-size: 18px; height: 18px; width: 18px; flex-shrink: 0; margin-top: 1px; }
    .test-result-text { display: flex; flex-direction: column; gap: 2px; }
    .test-message { font-weight: 500; }
    .test-detail { font-size: 11px; opacity: 0.85; font-family: monospace; word-break: break-all; }
    .codex-status-banner {
      display: flex; align-items: flex-start; gap: 10px;
      margin: 0 16px 8px; padding: 10px 14px;
      border-radius: 8px; border-left: 4px solid #f57c00;
      background: color-mix(in srgb, #f57c00 8%, var(--mat-sys-surface-container));
    }
    .codex-status-banner.authenticated { border-left-color: #388e3c; background: color-mix(in srgb, #388e3c 8%, var(--mat-sys-surface-container)); }
    .codex-status-banner mat-icon { font-size: 22px; height: 22px; width: 22px; flex-shrink: 0; margin-top: 2px; }
    .codex-status-banner:not(.authenticated) mat-icon { color: #f57c00; }
    .codex-status-banner.authenticated mat-icon { color: #388e3c; }
    .codex-status-text { display: flex; flex-direction: column; gap: 3px; flex: 1; font-size: 13px; }
    .codex-status-text strong { font-weight: 700; }
    .codex-status-text code { font-family: monospace; font-size: 12px; background: var(--mat-sys-surface-variant); padding: 2px 6px; border-radius: 4px; }
    .codex-status-text .expires { font-size: 11px; opacity: .7; }
  `],
})
export class AiSettingsComponent implements OnInit {
  groups = SETTING_GROUPS;
  loading = signal(true);
  saving = signal(false);
  testingGroup = signal<string | null>(null);
  testResults = signal<Record<string, TestResult>>({});
  codexStatus = signal<any>(null);
  form: FormGroup | null = null;
  private settingsMap = new Map<string, SettingItem>();

  constructor(
    private fb: FormBuilder,
    private svc: ConnectorService,
    private snack: MatSnackBar,
    private http: HttpClient,
  ) {}

  ngOnInit() {
    this.svc.getSettings().subscribe({
      next: res => {
        this.settingsMap.clear();
        res.settings.forEach(s => this.settingsMap.set(s.key, s));
        this.buildForm();
        this.loading.set(false);
      },
    });
    this.loadCodexStatus();
  }

  loadCodexStatus() {
    this.http.get<any>(`${environment.apiUrl}/settings/codex-status`).subscribe({
      next: s => this.codexStatus.set(s),
      error: () => {},
    });
  }

  buildForm() {
    const controls: Record<string, unknown> = {};
    for (const group of SETTING_GROUPS) {
      for (const key of group.keys) {
        const item = this.settingsMap.get(key);
        let val: string | boolean = item?.value ?? '';
        if (item?.is_secret && item.value === SECRET_MASK) val = '';
        if (BOOLEAN_KEYS.has(key)) val = val === 'true' || (typeof val !== 'string' && !!val);
        controls[key] = [val];
      }
    }
    this.form = this.fb.group(controls);
  }

  saveAll() {
    if (!this.form) return;
    this.saving.set(true);
    const v = this.form.value;
    const allKeys = SETTING_GROUPS.flatMap(g => g.keys);

    const updates = allKeys.map(key => {
      let val = v[key];
      if (BOOLEAN_KEYS.has(key)) val = val ? 'true' : 'false';
      const item = this.settingsMap.get(key);
      // Skip empty secret fields (keep existing)
      if (item?.is_secret && !val) return Promise.resolve();
      return this.svc.updateSetting(key, val === '' ? null : String(val)).toPromise();
    });

    Promise.all(updates).then(() => {
      this.saving.set(false);
      this.snack.open('Einstellungen gespeichert', 'OK', { duration: 3000 });
    }).catch(() => {
      this.saving.set(false);
      this.snack.open('Fehler beim Speichern', 'OK', { duration: 4000 });
    });
  }

  testGroup(group: string) {
    this.testingGroup.set(group);
    this.testResults.update(r => { const n = { ...r }; delete n[group]; return n; });
    // codex uses a dedicated endpoint
    const obs = group === 'codex'
      ? this.http.post<any>(`${environment.apiUrl}/settings/test/codex`, {})
      : this.svc.testSettingGroup(group);
    obs.subscribe({
      next: result => {
        this.testResults.update(r => ({ ...r, [group]: result }));
        this.testingGroup.set(null);
        if (group === 'codex') this.loadCodexStatus();
      },
      error: err => {
        this.testResults.update(r => ({
          ...r,
          [group]: { success: false, message: err?.error?.detail ?? 'Unbekannter Fehler', detail: null },
        }));
        this.testingGroup.set(null);
      },
    });
  }

  isBooleanKey(key: string): boolean { return BOOLEAN_KEYS.has(key); }
  isSelectKey(key: string): boolean { return key in SELECT_KEYS; }
  isSecret(key: string): boolean { return !!this.settingsMap.get(key)?.is_secret; }
  selectOptions(key: string): string[] { return SELECT_KEYS[key] ?? []; }

  keyLabel(key: string): string {
    const labels: Record<string, string> = {
      'llm.base_url':                   'LLM Basis-URL',
      'llm.model':                       'LLM Modell',
      'llm.api_mode':                    'LLM API Modus',
      'llm.api_key':                     'API Key',
      'llm.timeout_seconds':             'Timeout (Sekunden)',
      'llm.vision_base_url':             'Vision LLM URL',
      'llm.vision_model':                'Vision Modell',
      'llm.vision_api_key':              'Vision API Key',
      'searxng.base_url':                'SearXNG URL',
      'searxng.enabled':                 'SearXNG aktiviert',
      'searxng.results_count':           'Anzahl Suchergebnisse',
      'llm.thinking_mode':                    'Thinking Mode (Extended Reasoning)',
      'llm.codex_fallback_enabled':           'OpenAI Codex Fallback aktivieren',
      'llm.codex_hermes_provider':            'Hermes Provider Name (z.B. openai-codex)',
      'llm.codex_base_url':                   'Codex API URL (leer = https://api.openai.com/v1)',
      'llm.codex_model':                      'Codex Modell (z.B. gpt-4o)',
      'llm.codex_timeout_seconds':            'Codex Timeout (Sekunden)',
      'agent.interval_minutes':               'KI-Agent Intervall (Minuten)',
      'agent.aggregation_interval_minutes':   'Alert-Abruf Intervall (Minuten)',
      'agent.auto_jira':                      'Automatisch Jira-Tickets erstellen',
      'agent.auto_enrich':                    'KI-Anreicherung automatisch (aus = On Demand)',
      'agent.rag_enabled':                    'Wissensdatenbank-Suche (RAG) im KI-Agenten',
      'workflow.web_search':                  'Websuche bei KI-Analyse (News Feed / Alerts)',
      'agent.scoring_enabled':                'CPU-Scoring aktiv (aus = alle Alerts ans LLM, für Beta-Vergleich)',
      'agent.enrich_score_threshold':         'Score-Schwellwert für automatische KI-Anreicherung (0–200, default 80)',
      'agent.max_alerts_for_llm':             'Max. Alerts pro KI-Agent-Lauf ans LLM (default 30)',
      'agent.flap_window_minutes':            'Flapping-Erkennungsfenster in Minuten (default 30)',
      'agent.flap_threshold':                 'Wiederholungen bis Flapping erkannt (default 3)',
      'agent.score_learning_enabled':         'Adaptives Scoring — lernt aus Jira-Tickets und Nutzerreaktionen',
      'agent.score_delta_decay_days':         'Score-Delta Verfallszeit in Tagen (default 7)',
      'agent.worklist_interval_minutes':      'Brücke: Prioritätenliste neu berechnen alle N Minuten (default 15)',
      'agent.worklist_size':                  'Brücke: Anzahl Einträge in der Prioritätenliste (default 15)',
      'agent.generative_interval_minutes':    'Generativ-Dashboard: KI komponiert das Lagebild neu alle N Minuten (default 15)',
      'agent.jira_severity_threshold':        'Mindest-Severity für Jira',
      'agent.checkmk_locations':              'CheckMK Standort-Filter (Komma-getrennt, z.B. München,Kassel)',
      'agent.checkmk_ve':                     'CheckMK VE-Filter (Komma-getrennt, z.B. VE1,VE2)',
      'agent.checkmk_criticality':            'CheckMK Criticality-Filter (Komma-getrennt, z.B. critical,prod)',
      'agent.checkmk_os':                     'CheckMK OS-Filter (Komma-getrennt, z.B. Linux,Windows)',
    };
    return labels[key] ?? key;
  }
}
