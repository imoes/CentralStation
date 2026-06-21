import { Component, OnInit, OnDestroy, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../../environments/environment';
import { FormBuilder, FormGroup, ReactiveFormsModule, FormsModule } from '@angular/forms';
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
import { MatChipsModule } from '@angular/material/chips';
import { ConnectorService } from '../../../core/services/connector.service';
import { SettingItem } from '../../../core/models/connector.model';

interface TestResult { success: boolean; message: string; detail: string | null; }
interface CodexStatus {
  authenticated: boolean;
  message: string;
  expires_at?: string;
  authenticated_at?: string;
  base_url?: string;
}
interface OAuthSession {
  session_id: string;
  user_code: string;
  verification_uri: string;
  expires_in_minutes: number;
  poll_interval_seconds: number;
}

const SETTING_GROUPS: { title: string; keys: string[]; testGroup?: string; showOnlyFor?: string[] }[] = [
  {
    title: 'Vision Model',
    keys: ['llm.vision_base_url', 'llm.vision_model', 'llm.vision_api_key'],
    testGroup: 'vision',
  },
  {
    title: 'SearXNG Web Search',
    keys: ['searxng.base_url', 'searxng.enabled', 'searxng.results_count'],
    testGroup: 'searxng',
  },
  {
    title: 'Computer Console (Hermes)',
    keys: ['computer.show_reasoning'],
  },
  {
    title: 'Jira / Tickets',
    keys: ['jira.ticket_project'],
  },
  {
    title: 'Agent Settings',
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
      'agent.topology_refresh_interval_minutes',
      'agent.jira_severity_threshold',
      'agent.checkmk_locations',
    ],
  },
];

const BOOLEAN_KEYS = new Set([
  'searxng.enabled', 'agent.auto_jira', 'agent.auto_enrich', 'agent.rag_enabled',
  'llm.thinking_mode', 'workflow.web_search', 'agent.score_learning_enabled', 'agent.scoring_enabled',
  'computer.show_reasoning',
]);
// Boolean settings that default to ON when no DB row exists yet.
const DEFAULT_ON_KEYS = new Set(['computer.show_reasoning']);
const SELECT_KEYS: Record<string, string[]> = {
  'llm.api_mode': ['chat_completions', 'responses'],
  'llm.provider': ['custom', 'openai-codex', 'claude-oauth'],
  'agent.jira_severity_threshold': ['critical', 'high', 'medium'],
};
const OAUTH_PROVIDERS = new Set(['openai-codex', 'claude-oauth']);
const SECRET_MASK = '••••••••';

@Component({
  selector: 'cs-ai-settings',
  standalone: true,
  imports: [
    CommonModule, ReactiveFormsModule, FormsModule,
    MatCardModule, MatFormFieldModule, MatInputModule,
    MatButtonModule, MatIconModule, MatSlideToggleModule,
    MatSelectModule, MatProgressSpinnerModule, MatSnackBarModule,
    MatDividerModule, MatTooltipModule, MatChipsModule,
  ],
  template: `
    <div class="page-container">
      <div class="page-header">
        <h2>Global Settings</h2>
        <button mat-raised-button color="primary" [disabled]="saving()" (click)="saveAll()">
          @if (saving()) {
            <mat-spinner diameter="18"></mat-spinner>
          } @else {
            <ng-container><mat-icon>save</mat-icon> Save</ng-container>
          }
        </button>
      </div>

      <!-- OpenAI Codex OAuth card — nur bei openai-codex sichtbar -->
      @if (currentProvider() === 'openai-codex') {
      <mat-card class="settings-card oauth-card">
        <mat-card-header>
          <mat-card-title>OpenAI Codex — Login</mat-card-title>
          <div class="card-header-actions">
            <button mat-icon-button (click)="loadCodexStatus()" matTooltip="Refresh status">
              <mat-icon>refresh</mat-icon>
            </button>
          </div>
        </mat-card-header>
        <mat-card-content>
          @if (codexStatus()) {
            <div class="codex-status-banner" [class.authenticated]="codexStatus()!.authenticated">
              <mat-icon>{{ codexStatus()!.authenticated ? 'verified_user' : 'no_accounts' }}</mat-icon>
              <div class="codex-status-text">
                <strong>{{ codexStatus()!.authenticated ? 'Logged in' : 'Not logged in' }}</strong>
                <span>{{ codexStatus()!.message }}</span>
                @if (codexStatus()!.authenticated && codexStatus()!.authenticated_at) {
                  <span class="expires">Logged in: {{ codexStatus()!.authenticated_at | date:'yyyy-MM-dd HH:mm' }}</span>
                }
                @if (codexStatus()!.authenticated && codexStatus()!.expires_at) {
                  <span class="expires">Valid until: {{ codexStatus()!.expires_at | date:'yyyy-MM-dd HH:mm' }}</span>
                }
              </div>
            </div>
          }

          @if (!oauthSession()) {
            <div class="oauth-actions">
              @if (codexStatus()?.authenticated) {
                <button mat-stroked-button color="warn" (click)="logoutCodex()">
                  <mat-icon>logout</mat-icon> Sign out
                </button>
              }
              <button mat-raised-button color="primary"
                      [disabled]="startingOAuth()"
                      (click)="startOAuth()">
                @if (startingOAuth()) {
                  <mat-spinner diameter="18"></mat-spinner>
                } @else {
                  <mat-icon>login</mat-icon>
                }
                {{ codexStatus()?.authenticated ? 'Sign in again' : 'Sign in with OpenAI' }}
              </button>
            </div>
          } @else {
            <div class="oauth-flow">
              @if (oauthPollStatus() === 'authorized') {
                <div class="oauth-success">
                  <mat-icon>check_circle</mat-icon>
                  <span>Successfully signed in!</span>
                </div>
              } @else if (oauthPollStatus() === 'timeout') {
                <div class="oauth-error">
                  <mat-icon>timer_off</mat-icon>
                  <span>Timed out — please try again.</span>
                </div>
                <button mat-stroked-button (click)="cancelOAuth()">Close</button>
              } @else if (oauthPollStatus() === 'error') {
                <div class="oauth-error">
                  <mat-icon>error</mat-icon>
                  <span>Error during sign-in.</span>
                </div>
                <button mat-stroked-button (click)="cancelOAuth()">Close</button>
              } @else {
                <div class="oauth-code-block">
                  <p class="oauth-instructions">
                    Open the link below in your browser and enter the code:
                  </p>
                  <div class="oauth-code">{{ oauthSession()!.user_code }}</div>
                  <a [href]="oauthSession()!.verification_uri" target="_blank" rel="noopener">
                    <button mat-stroked-button>
                      <mat-icon>open_in_new</mat-icon>
                      {{ oauthSession()!.verification_uri }}
                    </button>
                  </a>
                  <div class="oauth-waiting">
                    <mat-spinner diameter="20"></mat-spinner>
                    <span>Waiting for confirmation…</span>
                  </div>
                  <button mat-button (click)="cancelOAuth()">Cancel</button>
                </div>
              }
            </div>
          }
        </mat-card-content>
      </mat-card>
      } <!-- end @if openai-codex -->

      <!-- Claude OAuth card (shown when claude-oauth provider is selected) -->
      @if (currentProvider() === 'claude-oauth') {
        <mat-card class="settings-card oauth-card">
          <mat-card-header>
            <mat-card-title>Claude — OAuth Login</mat-card-title>
            <div class="card-header-actions">
              <button mat-icon-button (click)="loadClaudeStatus()" matTooltip="Status aktualisieren">
                <mat-icon>refresh</mat-icon>
              </button>
            </div>
          </mat-card-header>
          <mat-card-content>
            @if (claudeStatus()) {
              <div class="codex-status-banner" [class.authenticated]="claudeStatus()!.authenticated">
                <mat-icon>{{ claudeStatus()!.authenticated ? 'verified_user' : 'no_accounts' }}</mat-icon>
                <div class="codex-status-text">
                  <strong>{{ claudeStatus()!.authenticated ? 'Eingeloggt' : 'Nicht eingeloggt' }}</strong>
                  <span>{{ claudeStatus()!.message }}</span>
                  @if (claudeStatus()!.authenticated && claudeStatus()!.expires_at) {
                    <span class="expires">Gültig bis: {{ claudeStatus()!.expires_at | date:'yyyy-MM-dd HH:mm' }}</span>
                  }
                </div>
              </div>
            }

            @if (!claudeOAuthUrl()) {
              <div class="oauth-actions">
                @if (claudeStatus()?.authenticated) {
                  <button mat-stroked-button color="warn" (click)="logoutClaude()">
                    <mat-icon>logout</mat-icon> Abmelden
                  </button>
                }
                <button mat-raised-button color="primary"
                        [disabled]="startingClaudeOAuth()"
                        (click)="startClaudeOAuth()">
                  @if (startingClaudeOAuth()) {
                    <mat-spinner diameter="18"></mat-spinner>
                  } @else {
                    <mat-icon>login</mat-icon>
                  }
                  {{ claudeStatus()?.authenticated ? 'Erneut anmelden' : 'Mit Claude anmelden' }}
                </button>
              </div>
            } @else {
              <div class="oauth-flow">
                <p class="oauth-instructions">
                  1. Öffne den Link, melde dich bei Claude an und autorisiere den Zugriff.<br>
                  2. Kopiere den angezeigten Code und füge ihn unten ein.
                </p>
                <a [href]="claudeOAuthUrl()!" target="_blank" rel="noopener">
                  <button mat-stroked-button>
                    <mat-icon>open_in_new</mat-icon>
                    Bei Claude anmelden
                  </button>
                </a>
                <mat-form-field appearance="outline" class="setting-field" style="margin-top:8px">
                  <mat-label>Autorisierungs-Code</mat-label>
                  <input matInput [(ngModel)]="claudeCode" [ngModelOptions]="{standalone: true}"
                         placeholder="Code aus dem Browser einfügen">
                </mat-form-field>
                <div class="oauth-actions">
                  <button mat-raised-button color="primary"
                          [disabled]="completingClaudeOAuth() || !claudeCode()"
                          (click)="completeClaudeOAuth()">
                    @if (completingClaudeOAuth()) {
                      <mat-spinner diameter="18"></mat-spinner>
                    } @else {
                      <mat-icon>check</mat-icon>
                    }
                    Code bestätigen
                  </button>
                  <button mat-button (click)="cancelClaudeOAuth()">Abbrechen</button>
                </div>
              </div>
            }
          </mat-card-content>
        </mat-card>
      }

      @if (loading()) {
        <div class="spinner-center"><mat-spinner diameter="40"></mat-spinner></div>
      } @else if (form) {
        <form [formGroup]="form">
          @for (group of groups; track group.title) {
            @if (isGroupVisible(group)) {
            <mat-card class="settings-card">
              <mat-card-header>
                <mat-card-title>{{ group.title }}</mat-card-title>
                @if (group.testGroup) {
                  <div class="card-header-actions">
                    <button mat-stroked-button
                            [disabled]="testingGroup() === group.testGroup"
                            (click)="testGroup(group.testGroup!)"
                            matTooltip="Test connection with saved values">
                      @if (testingGroup() === group.testGroup) {
                        <mat-spinner diameter="16"></mat-spinner>
                      } @else {
                        <ng-container><mat-icon>wifi_tethering</mat-icon></ng-container>
                      }
                      Test connection
                    </button>
                  </div>
                }
              </mat-card-header>
              <mat-card-content>
                @for (key of group.keys; track key) {
                  @if (isHiddenForProvider(key)) {
                    <!-- hidden when OAuth provider selected -->
                  } @else if (key === 'jira.ticket_project') {
                    <mat-form-field appearance="outline" class="setting-field">
                      <mat-label>{{ keyLabel(key) }}</mat-label>
                      <mat-select [formControlName]="key" (selectionChange)="onJiraProjectChange($event.value)">
                        @for (p of jiraProjects(); track p.key) {
                          <mat-option [value]="p.key">{{ p.key }} — {{ p.name }} ({{ p.connector === 'jira_sd' ? 'ServiceDesk' : 'Jira' }})</mat-option>
                        }
                      </mat-select>
                      <mat-hint>{{ jiraProjects().length ? 'Target project for created tickets' : 'No projects loaded — check Jira/ServiceDesk connector' }}</mat-hint>
                    </mat-form-field>
                  } @else if (isBooleanKey(key)) {
                    <div class="toggle-row">
                      <span class="key-label">{{ keyLabel(key) }}</span>
                      <mat-slide-toggle [formControlName]="key"></mat-slide-toggle>
                    </div>
                  } @else if (isSelectKey(key)) {
                    <mat-form-field appearance="outline" class="setting-field">
                      <mat-label>{{ keyLabel(key) }}</mat-label>
                      <mat-select [formControlName]="key">
                        @for (opt of selectOptions(key); track opt) {
                          <mat-option [value]="opt">{{ selectLabel(key, opt) }}</mat-option>
                        }
                      </mat-select>
                    </mat-form-field>
                  } @else {
                    <mat-form-field appearance="outline" class="setting-field">
                      <mat-label>{{ keyLabel(key) }}</mat-label>
                      <input matInput [formControlName]="key"
                             [type]="isSecret(key) ? 'password' : 'text'"
                             [placeholder]="isSecret(key) ? 'Leave empty to keep unchanged' : ''">
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
            } <!-- end @if isGroupVisible -->
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

    /* OAuth Card */
    .oauth-card { border-left: 4px solid var(--mat-sys-primary); }
    .codex-status-banner {
      display: flex; align-items: flex-start; gap: 10px;
      margin-bottom: 16px; padding: 10px 14px;
      border-radius: 8px; border-left: 4px solid #f57c00;
      background: color-mix(in srgb, #f57c00 8%, var(--mat-sys-surface-container));
    }
    .codex-status-banner.authenticated { border-left-color: #388e3c; background: color-mix(in srgb, #388e3c 8%, var(--mat-sys-surface-container)); }
    .codex-status-banner mat-icon { font-size: 22px; height: 22px; width: 22px; flex-shrink: 0; margin-top: 2px; }
    .codex-status-banner:not(.authenticated) mat-icon { color: #f57c00; }
    .codex-status-banner.authenticated mat-icon { color: #388e3c; }
    .codex-status-text { display: flex; flex-direction: column; gap: 3px; flex: 1; font-size: 13px; }
    .codex-status-text strong { font-weight: 700; }
    .codex-status-text .expires { font-size: 11px; opacity: .7; }

    .oauth-actions { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
    .oauth-actions button mat-icon { margin-right: 6px; }

    .oauth-flow { display: flex; flex-direction: column; gap: 12px; }
    .oauth-code-block { display: flex; flex-direction: column; gap: 12px; align-items: flex-start; }
    .oauth-instructions { margin: 0; font-size: 14px; }
    .oauth-code {
      font-family: monospace; font-size: 28px; font-weight: 700; letter-spacing: 4px;
      padding: 12px 24px; border-radius: 8px;
      background: var(--mat-sys-surface-variant);
      color: var(--mat-sys-on-surface);
      border: 2px solid var(--mat-sys-primary);
      user-select: all;
    }
    .oauth-waiting { display: flex; align-items: center; gap: 10px; font-size: 14px; opacity: .8; }
    .oauth-success { display: flex; align-items: center; gap: 8px; color: #388e3c; font-weight: 600; font-size: 15px; }
    .oauth-success mat-icon { color: #388e3c; font-size: 24px; height: 24px; width: 24px; }
    .oauth-error { display: flex; align-items: center; gap: 8px; color: #c62828; font-weight: 500; font-size: 14px; }
    .oauth-error mat-icon { color: #c62828; font-size: 22px; height: 22px; width: 22px; }
  `],
})
export class AiSettingsComponent implements OnInit, OnDestroy {
  groups = SETTING_GROUPS;
  loading = signal(true);
  saving = signal(false);
  testingGroup = signal<string | null>(null);
  testResults = signal<Record<string, TestResult>>({});
  codexStatus = signal<CodexStatus | null>(null);
  claudeStatus = signal<CodexStatus | null>(null);
  claudeOAuthUrl = signal<string | null>(null);
  claudeSessionId = signal<string | null>(null);
  claudeCode = signal<string>('');
  startingClaudeOAuth = signal(false);
  completingClaudeOAuth = signal(false);
  jiraProjects = signal<{ key: string; name: string; connector: string }[]>([]);
  oauthSession = signal<OAuthSession | null>(null);
  oauthPollStatus = signal<'pending' | 'authorized' | 'timeout' | 'error' | null>(null);
  startingOAuth = signal(false);
  currentProvider = signal<string>('custom');
  isOAuthProvider = computed(() => OAUTH_PROVIDERS.has(this.currentProvider()));
  form: FormGroup | null = null;
  private settingsMap = new Map<string, SettingItem>();
  private pollTimer: ReturnType<typeof setInterval> | null = null;

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
    this.loadClaudeStatus();
    this.loadJiraProjects();
  }

  private providerSub: ReturnType<typeof setTimeout> | null = null;

  ngOnDestroy() {
    this.stopPolling();
  }

  loadJiraProjects() {
    this.http.get<{ projects: { key: string; name: string; connector: string }[] }>(
      `${environment.apiUrl}/settings/jira-projects`,
    ).subscribe({
      next: r => this.jiraProjects.set(r.projects || []),
      error: () => {},
    });
  }

  /** When a project is picked, also store which connector hosts it (IMIT → jira_sd). */
  onJiraProjectChange(key: string) {
    const p = this.jiraProjects().find(x => x.key === key);
    if (p) {
      this.svc.updateSetting('jira.ticket_connector', p.connector).subscribe({ next: () => {}, error: () => {} });
    }
  }

  loadCodexStatus() {
    this.http.get<CodexStatus>(`${environment.apiUrl}/oauth/openai-codex/status`).subscribe({
      next: s => this.codexStatus.set(s),
      error: () => {},
    });
  }

  loadClaudeStatus() {
    this.http.get<CodexStatus>(`${environment.apiUrl}/oauth/claude-oauth/status`).subscribe({
      next: s => this.claudeStatus.set(s),
      error: () => {},
    });
  }

  startClaudeOAuth() {
    this.startingClaudeOAuth.set(true);
    this.http.post<{ session_id: string; authorize_url: string }>(
      `${environment.apiUrl}/oauth/claude-oauth/start`, {}
    ).subscribe({
      next: r => {
        this.claudeSessionId.set(r.session_id);
        this.claudeOAuthUrl.set(r.authorize_url);
        this.claudeCode.set('');
        this.startingClaudeOAuth.set(false);
      },
      error: err => {
        this.startingClaudeOAuth.set(false);
        this.snack.open(`Fehler: ${err?.error?.detail ?? 'Start fehlgeschlagen'}`, 'OK', { duration: 5000 });
      },
    });
  }

  completeClaudeOAuth() {
    const sid = this.claudeSessionId();
    const code = this.claudeCode().trim();
    if (!sid || !code) return;
    this.completingClaudeOAuth.set(true);
    this.http.post<{ status: string }>(
      `${environment.apiUrl}/oauth/claude-oauth/complete`, { session_id: sid, code }
    ).subscribe({
      next: () => {
        this.completingClaudeOAuth.set(false);
        this.cancelClaudeOAuth();
        this.loadClaudeStatus();
        this.snack.open('Erfolgreich mit Claude angemeldet!', 'OK', { duration: 4000 });
      },
      error: err => {
        this.completingClaudeOAuth.set(false);
        this.snack.open(`Fehler: ${err?.error?.detail ?? 'Code ungültig'}`, 'OK', { duration: 6000 });
      },
    });
  }

  cancelClaudeOAuth() {
    this.claudeOAuthUrl.set(null);
    this.claudeSessionId.set(null);
    this.claudeCode.set('');
  }

  logoutClaude() {
    this.http.delete(`${environment.apiUrl}/oauth/claude-oauth/logout`).subscribe({
      next: () => {
        this.loadClaudeStatus();
        this.snack.open('Abgemeldet', 'OK', { duration: 3000 });
      },
      error: () => this.snack.open('Fehler beim Abmelden', 'OK', { duration: 3000 }),
    });
  }

  startOAuth() {
    this.startingOAuth.set(true);
    this.http.post<OAuthSession>(`${environment.apiUrl}/oauth/openai-codex/start`, {}).subscribe({
      next: session => {
        this.oauthSession.set(session);
        this.oauthPollStatus.set('pending');
        this.startingOAuth.set(false);
        const intervalMs = (session.poll_interval_seconds ?? 5) * 1000;
        this.pollTimer = setInterval(() => this.pollOAuth(), intervalMs);
      },
      error: err => {
        this.startingOAuth.set(false);
        this.snack.open(
          `Error: ${err?.error?.detail ?? 'Connection to OpenAI failed'}`,
          'OK', { duration: 5000 }
        );
      },
    });
  }

  private pollOAuth() {
    const session = this.oauthSession();
    if (!session) return;
    this.http.post<{ status: string }>(
      `${environment.apiUrl}/oauth/openai-codex/poll/${session.session_id}`, {}
    ).subscribe({
      next: res => {
        if (res.status === 'authorized') {
          this.stopPolling();
          this.oauthPollStatus.set('authorized');
          this.loadCodexStatus();
          setTimeout(() => this.cancelOAuth(), 2000);
          this.snack.open('Successfully signed in with OpenAI Codex!', 'OK', { duration: 4000 });
        } else if (res.status === 'timeout') {
          this.stopPolling();
          this.oauthPollStatus.set('timeout');
        } else if (res.status === 'error') {
          this.stopPolling();
          this.oauthPollStatus.set('error');
        }
        // 'pending' → continue polling
      },
      error: () => {
        this.stopPolling();
        this.oauthPollStatus.set('error');
      },
    });
  }

  cancelOAuth() {
    this.stopPolling();
    this.oauthSession.set(null);
    this.oauthPollStatus.set(null);
  }

  logoutCodex() {
    this.http.delete(`${environment.apiUrl}/oauth/openai-codex/logout`).subscribe({
      next: () => {
        this.loadCodexStatus();
        this.snack.open('Signed out', 'OK', { duration: 3000 });
      },
      error: () => this.snack.open('Error signing out', 'OK', { duration: 3000 }),
    });
  }

  private stopPolling() {
    if (this.pollTimer !== null) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  buildForm() {
    const controls: Record<string, unknown> = {};
    for (const group of SETTING_GROUPS) {
      for (const key of group.keys) {
        const item = this.settingsMap.get(key);
        let val: string | boolean = item?.value ?? '';
        if (item?.is_secret && item.value === SECRET_MASK) val = '';
        if (BOOLEAN_KEYS.has(key)) {
          // Default-on booleans: when the setting row doesn't exist yet, fall back
          // to true instead of an unchecked toggle.
          if (item === undefined && DEFAULT_ON_KEYS.has(key)) val = true;
          else val = val === 'true' || (typeof val !== 'string' && !!val);
        }
        controls[key] = [val];
      }
    }
    this.form = this.fb.group(controls);

    // NOTE: control name 'llm.provider' contains a dot. form.get('llm.provider')
    // would treat the dot as a PATH separator (llm → provider) and return null.
    // Pass an array so the literal name (incl. dot) is used.
    const providerCtrl = this.form.get(['llm.provider']);
    if (providerCtrl) {
      this.currentProvider.set(providerCtrl.value || 'custom');
      providerCtrl.valueChanges.subscribe(v => this.currentProvider.set(v || 'custom'));
    }
  }

  isHiddenForProvider(key: string): boolean {
    if (!this.isOAuthProvider()) return false;
    // For OAuth providers: hide all manual LLM connection fields — they're auto-configured.
    return ['llm.api_key', 'llm.api_mode', 'llm.base_url', 'llm.model', 'llm.timeout_seconds'].includes(key);
  }

  isGroupVisible(group: { showOnlyFor?: string[] }): boolean {
    if (!group.showOnlyFor) return true;
    return group.showOnlyFor.includes(this.currentProvider());
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
      if (item?.is_secret && !val) return Promise.resolve();
      return this.svc.updateSetting(key, val === '' ? null : String(val)).toPromise();
    });

    Promise.all(updates).then(() => {
      this.saving.set(false);
      this.snack.open('Settings saved', 'OK', { duration: 3000 });
    }).catch(() => {
      this.saving.set(false);
      this.snack.open('Error saving settings', 'OK', { duration: 4000 });
    });
  }

  testGroup(group: string) {
    this.testingGroup.set(group);
    this.testResults.update(r => { const n = { ...r }; delete n[group]; return n; });
    this.svc.testSettingGroup(group).subscribe({
      next: result => {
        this.testResults.update(r => ({ ...r, [group]: result }));
        this.testingGroup.set(null);
      },
      error: err => {
        this.testResults.update(r => ({
          ...r,
          [group]: { success: false, message: err?.error?.detail ?? 'Unknown error', detail: null },
        }));
        this.testingGroup.set(null);
      },
    });
  }

  isBooleanKey(key: string): boolean { return BOOLEAN_KEYS.has(key); }
  isSelectKey(key: string): boolean { return key in SELECT_KEYS; }
  isSecret(key: string): boolean { return !!this.settingsMap.get(key)?.is_secret; }
  selectOptions(key: string): string[] { return SELECT_KEYS[key] ?? []; }

  selectLabel(key: string, opt: string): string {
    if (key === 'llm.provider') {
      if (opt === 'custom') return 'Custom / Self-hosted endpoint';
      if (opt === 'openai-codex') return 'OpenAI Codex (OAuth)';
      if (opt === 'claude-oauth') return 'Claude (OAuth — claude setup-token)';
    }
    return opt;
  }

  keyLabel(key: string): string {
    const labels: Record<string, string> = {
      'llm.provider':                       'LLM Provider',
      'llm.base_url':                        'LLM Base URL (custom endpoint only)',
      'llm.model':                           'LLM Model',
      'llm.api_mode':                        'LLM API Mode',
      'llm.api_key':                         'API Key',
      'llm.timeout_seconds':                 'Timeout (seconds)',
      'llm.codex_model':                     'OpenAI Codex Model (e.g. gpt-4o)',
      'llm.codex_timeout_seconds':           'OpenAI Codex Timeout (seconds)',
      'llm.claude_model':                    'Claude Model (e.g. claude-opus-4-8)',
      'llm.vision_base_url':                 'Vision LLM URL',
      'llm.vision_model':                    'Vision Model',
      'llm.vision_api_key':                  'Vision API Key',
      'searxng.base_url':                    'SearXNG URL',
      'searxng.enabled':                     'SearXNG enabled',
      'searxng.results_count':               'Number of search results',
      'llm.thinking_mode':                   'Thinking Mode (Extended Reasoning)',
      'agent.interval_minutes':              'AI Agent interval (minutes)',
      'agent.aggregation_interval_minutes':  'Alert fetch interval (minutes)',
      'agent.auto_jira':                     'Create Jira tickets automatically',
      'agent.auto_enrich':                   'AI enrichment automatic (off = on demand)',
      'agent.rag_enabled':                   'Knowledge base search (RAG) in AI agent',
      'workflow.web_search':                 'Web search during AI analysis',
      'agent.scoring_enabled':               'Alert scoring enabled',
      'agent.enrich_score_threshold':        'Score threshold for AI enrichment',
      'agent.max_alerts_for_llm':            'Max alerts per AI agent run sent to LLM',
      'agent.flap_window_minutes':           'Flap detection window (minutes)',
      'agent.flap_threshold':                'Repetitions until flapping detected',
      'agent.score_learning_enabled':        'Adaptive scoring enabled',
      'agent.score_delta_decay_days':        'Score delta decay time (days)',
      'agent.worklist_interval_minutes':     'Priority list refresh interval (minutes)',
      'agent.worklist_size':                 'Number of entries in the priority list',
      'agent.generative_interval_minutes':   'Generative dashboard interval (minutes)',
      'agent.topology_refresh_interval_minutes': 'Topology map refresh interval (minutes)',
      'agent.jira_severity_threshold':       'Minimum severity for Jira tickets',
      'agent.checkmk_locations':             'CheckMK location filter (comma-separated)',
      'jira.ticket_project':                 'Ticket project (target for created tickets)',
      'computer.show_reasoning':             'Reasoning in Hermes-Sitzung anzeigen',
    };
    return labels[key] ?? key;
  }
}
