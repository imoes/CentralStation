import { Component, OnInit, OnDestroy, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatDividerModule } from '@angular/material/divider';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatTooltipModule } from '@angular/material/tooltip';
import { I18nService } from '../../../core/services/i18n.service';

type AgentType = 'hermes' | 'claude_cli' | 'codex_cli';

type ApiMode = 'chat_completions' | 'anthropic_messages' | 'codex_responses' | 'bedrock_converse';

const API_MODE_LABELS: Record<ApiMode, string> = {
  chat_completions:  'Chat Completions (OpenAI-kompatibel)',
  anthropic_messages: 'Anthropic Messages API',
  codex_responses:   'OpenAI Responses API (Codex / GPT-5)',
  bedrock_converse:  'AWS Bedrock Converse',
};

interface ClaudeOAuthSession {
  session_id: string;
  authorize_url: string;
  expires_in_minutes: number;
}

interface CodexOAuthSession {
  session_id: string;
  user_code: string;
  verification_uri: string;
  expires_in_minutes: number;
  poll_interval_seconds: number;
}

interface HermesLLM {
  configured: boolean;
  api_mode?: ApiMode;
  model?: string;
  base_url?: string;
  timeout_seconds?: number;
  thinking_mode?: boolean;
  has_api_key?: boolean;
}

@Component({
  selector: 'cs-console-settings',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatCardModule, MatButtonModule, MatIconModule,
    MatInputModule, MatFormFieldModule,
    MatSelectModule, MatSlideToggleModule, MatDividerModule,
    MatProgressSpinnerModule, MatSnackBarModule, MatTooltipModule,
  ],
  template: `
    <div class="console-settings">
      <h2 class="section-title">{{ i18n.t('console.agent_section') }}</h2>
      <p class="section-hint">{{ i18n.t('console.agent_section_hint') }}</p>

      <div class="agent-cards">

        <!-- ── Hermes ──────────────────────────────────────────────── -->
        <div class="agent-card" [class.active]="currentAgent() === 'hermes'" (click)="selectHermes()">
          <div class="agent-row">
            <div class="agent-icon"><mat-icon>memory</mat-icon></div>
            <div class="agent-info">
              <div class="agent-name">{{ i18n.t('console.agent_hermes') }}</div>
              <div class="agent-desc">{{ i18n.t('console.agent_hermes_desc') }}</div>
            </div>
            @if (currentAgent() === 'hermes') {
              <div class="agent-badge"><mat-icon>check_circle</mat-icon> {{ i18n.t('console.agent_active') }}</div>
            }
          </div>

          <!-- Hermes LLM config — nur wenn Hermes aktiv -->
          @if (currentAgent() === 'hermes') {
            <div class="hermes-llm" (click)="$event.stopPropagation()">
              <mat-divider style="margin: 12px 0"></mat-divider>
              <div class="llm-header">
                <span class="llm-title">LLM-Konfiguration</span>
                <mat-slide-toggle
                  [checked]="!hermesLLM().configured"
                  (change)="toggleGlobalLLM($event.checked)"
                  matTooltip="Nutze die globale Admin-LLM-Konfiguration">
                  Globale Konfiguration
                </mat-slide-toggle>
              </div>

              @if (hermesLLM().configured) {
                <div class="llm-form">
                  <mat-form-field appearance="outline" class="llm-field">
                    <mat-label>API-Modus</mat-label>
                    <mat-select [(ngModel)]="llmForm.api_mode">
                      @for (mode of apiModes; track mode) {
                        <mat-option [value]="mode">{{ apiModeLabel(mode) }}</mat-option>
                      }
                    </mat-select>
                    <mat-hint>Protokoll für die LLM-Kommunikation</mat-hint>
                  </mat-form-field>

                  <mat-form-field appearance="outline" class="llm-field">
                    <mat-label>Modell</mat-label>
                    <input matInput [(ngModel)]="llmForm.model"
                           [placeholder]="modelPlaceholder(llmForm.api_mode)">
                    <mat-hint>z.B. {{ modelPlaceholder(llmForm.api_mode) }}</mat-hint>
                  </mat-form-field>

                  @if (needsBaseUrl(llmForm.api_mode)) {
                    <mat-form-field appearance="outline" class="llm-field">
                      <mat-label>Base URL</mat-label>
                      <input matInput [(ngModel)]="llmForm.base_url"
                             placeholder="https://api.example.com/v1">
                      <mat-hint>API-Endpunkt (ohne Trailing-Slash)</mat-hint>
                    </mat-form-field>
                  }

                  <mat-form-field appearance="outline" class="llm-field">
                    <mat-label>API Key</mat-label>
                    <input matInput [(ngModel)]="llmForm.api_key" type="password"
                           [placeholder]="hermesLLM().has_api_key ? '••••••••  (leer lassen zum Beibehalten)' : 'sk-...'">
                    <mat-hint>Leer lassen um vorhandenen Key beizubehalten</mat-hint>
                  </mat-form-field>

                  <div class="llm-row">
                    <mat-form-field appearance="outline" class="llm-field-sm">
                      <mat-label>Timeout (s)</mat-label>
                      <input matInput [(ngModel)]="llmForm.timeout_seconds" type="number" min="10" max="600">
                    </mat-form-field>

                    <div class="toggle-row">
                      <span>Reasoning / Thinking Mode</span>
                      <mat-slide-toggle [(ngModel)]="llmForm.thinking_mode"></mat-slide-toggle>
                    </div>
                  </div>

                  <div class="llm-actions">
                    <button mat-flat-button color="primary" [disabled]="savingLLM()" (click)="saveLLM()">
                      @if (savingLLM()) { <mat-spinner diameter="18"></mat-spinner> }
                      @else { <mat-icon>save</mat-icon> }
                      Speichern
                    </button>
                  </div>
                </div>
              } @else {
                <p class="global-hint">
                  <mat-icon style="font-size:16px;height:16px;width:16px;vertical-align:middle">info</mat-icon>
                  Verwendet die globale LLM-Konfiguration aus den Admin-Einstellungen.
                </p>
              }
            </div>
          }
        </div>

        <!-- ── Claude ──────────────────────────────────────────────── -->
        <div class="agent-card" [class.active]="currentAgent() === 'claude_cli'">
          <div class="agent-row">
            <div class="agent-icon"><mat-icon>smart_toy</mat-icon></div>
            <div class="agent-info">
              <div class="agent-name">{{ i18n.t('console.agent_claude') }}</div>
              <div class="agent-desc">{{ i18n.t('console.agent_claude_desc') }}</div>
            </div>
            @if (currentAgent() === 'claude_cli') {
              <div class="agent-badge connected"><mat-icon>check_circle</mat-icon> {{ i18n.t('console.connected') }}</div>
            }
          </div>
          <div class="agent-oauth" (click)="$event.stopPropagation()">
            @if (claudeStep() === 'idle') {
              <button mat-stroked-button color="primary" (click)="startClaudeOAuth()">
                <mat-icon>login</mat-icon> {{ i18n.t('console.connect_claude') }}
              </button>
            }
            @if (claudeStep() === 'loading') {
              <mat-spinner diameter="24"></mat-spinner>
            }
            @if (claudeStep() === 'authorize') {
              <div class="oauth-flow">
                <p>{{ i18n.t('console.oauth_step1') }}</p>
                <a [href]="claudeSession()?.authorize_url" target="_blank" class="oauth-url">
                  {{ claudeSession()?.authorize_url | slice:0:60 }}…
                </a>
                <button mat-stroked-button color="accent" (click)="openUrl(claudeSession()?.authorize_url)">
                  <mat-icon>open_in_new</mat-icon> {{ i18n.t('console.oauth_authorize') }}
                </button>
                <p>{{ i18n.t('console.oauth_step2') }}</p>
                <mat-form-field appearance="outline" class="code-field">
                  <input matInput [(ngModel)]="claudeCode" [placeholder]="i18n.t('console.oauth_code_placeholder')" />
                </mat-form-field>
                <button mat-flat-button color="primary" [disabled]="!claudeCode" (click)="completeClaudeOAuth()">
                  {{ i18n.t('console.oauth_confirm') }}
                </button>
                <button mat-button (click)="resetClaudeOAuth()">{{ i18n.t('setup.skip') }}</button>
              </div>
            }
            @if (claudeStep() === 'completing') {
              <mat-spinner diameter="24"></mat-spinner>
            }
          </div>

          <!-- Claude Modell-Auswahl — nur wenn verbunden und kein OAuth-Flow aktiv -->
          @if (claudeStep() === 'idle' && claudeConnected()) {
            <div class="cli-model-select" (click)="$event.stopPropagation()">
              <mat-divider style="margin: 12px 0"></mat-divider>
              <mat-form-field appearance="outline" class="llm-field">
                <mat-label>Modell</mat-label>
                <mat-select [(ngModel)]="claudeModel">
                  @for (m of claudeModels(); track m) {
                    <mat-option [value]="m">{{ m }}</mat-option>
                  }
                </mat-select>
                <mat-hint>{{ claudeModelsSource() === 'api' ? 'Live von Anthropic API' : 'Statische Auswahl' }}</mat-hint>
              </mat-form-field>
              <div class="llm-actions">
                <button mat-flat-button color="primary" (click)="saveCLIModel('claude', claudeModel)">
                  <mat-icon>save</mat-icon> Speichern
                </button>
              </div>
            </div>
          }
        </div>

        <!-- ── Codex ───────────────────────────────────────────────── -->
        <div class="agent-card" [class.active]="currentAgent() === 'codex_cli'">
          <div class="agent-row">
            <div class="agent-icon"><mat-icon>code</mat-icon></div>
            <div class="agent-info">
              <div class="agent-name">{{ i18n.t('console.agent_codex') }}</div>
              <div class="agent-desc">{{ i18n.t('console.agent_codex_desc') }}</div>
            </div>
            @if (currentAgent() === 'codex_cli') {
              <div class="agent-badge connected"><mat-icon>check_circle</mat-icon> {{ i18n.t('console.connected') }}</div>
            }
          </div>
          <div class="agent-oauth" (click)="$event.stopPropagation()">
            @if (codexStep() === 'idle') {
              <button mat-stroked-button color="primary" (click)="startCodexOAuth()">
                <mat-icon>login</mat-icon> {{ i18n.t('console.connect_codex') }}
              </button>
            }
            @if (codexStep() === 'loading') {
              <mat-spinner diameter="24"></mat-spinner>
            }
            @if (codexStep() === 'polling') {
              <div class="oauth-flow">
                <p>{{ i18n.t('console.codex_code_label') }}</p>
                <div class="device-code">{{ codexSession()?.user_code }}</div>
                <button mat-stroked-button color="accent" (click)="openUrl(codexSession()?.verification_uri)">
                  <mat-icon>open_in_new</mat-icon> {{ codexSession()?.verification_uri }}
                </button>
                <div class="polling-indicator">
                  <mat-spinner diameter="20"></mat-spinner>
                  <span>{{ i18n.t('console.codex_waiting') }}</span>
                </div>
                <button mat-button (click)="resetCodexOAuth()">{{ i18n.t('setup.skip') }}</button>
              </div>
            }
          </div>

          <!-- Codex Modell-Auswahl — nur wenn verbunden und kein OAuth-Flow aktiv -->
          @if (codexStep() === 'idle' && codexConnected()) {
            <div class="cli-model-select" (click)="$event.stopPropagation()">
              <mat-divider style="margin: 12px 0"></mat-divider>
              <mat-form-field appearance="outline" class="llm-field">
                <mat-label>Modell</mat-label>
                <mat-select [(ngModel)]="codexModel">
                  @for (m of codexModels(); track m) {
                    <mat-option [value]="m">{{ m }}</mat-option>
                  }
                </mat-select>
                <mat-hint>{{ codexModelsSource() === 'api' ? 'Live von OpenAI API' : 'Statische Auswahl' }}</mat-hint>
              </mat-form-field>
              <div class="llm-actions">
                <button mat-flat-button color="primary" (click)="saveCLIModel('codex', codexModel)">
                  <mat-icon>save</mat-icon> Speichern
                </button>
              </div>
            </div>
          }
        </div>

      </div>
    </div>
  `,
  styles: [`
    .console-settings { padding: 24px; max-width: 800px; }
    .section-title { font-size: 1.25rem; font-weight: 500; margin: 0 0 4px; }
    .section-hint { color: var(--mat-sys-on-surface-variant); margin: 0 0 24px; font-size: 0.9rem; }
    .agent-cards { display: flex; flex-direction: column; gap: 16px; }
    .agent-card {
      border: 1px solid var(--mat-sys-outline-variant);
      border-radius: 8px; padding: 16px; cursor: pointer;
      transition: border-color 0.2s, box-shadow 0.2s;
    }
    .agent-card:hover { border-color: var(--mat-sys-primary); }
    .agent-card.active { border-color: var(--mat-sys-primary); box-shadow: 0 0 0 1px var(--mat-sys-primary); }
    .agent-row { display: flex; align-items: flex-start; gap: 12px; }
    .agent-icon { color: var(--mat-sys-primary); flex-shrink: 0; }
    .agent-info { flex: 1; }
    .agent-name { font-weight: 600; font-size: 1rem; margin-bottom: 4px; }
    .agent-desc { font-size: 0.85rem; color: var(--mat-sys-on-surface-variant); }
    .agent-badge {
      display: inline-flex; align-items: center; gap: 4px;
      font-size: 0.8rem; color: var(--mat-sys-on-surface-variant); white-space: nowrap;
    }
    .agent-badge.connected { color: #4caf50; }
    .agent-oauth { margin-top: 12px; }

    /* Hermes LLM section */
    .hermes-llm { margin-top: 4px; }
    .llm-header {
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 12px;
    }
    .llm-title { font-weight: 500; font-size: 0.9rem; }
    .llm-form { display: flex; flex-direction: column; gap: 4px; }
    .llm-field { width: 100%; }
    .llm-field-sm { width: 140px; flex-shrink: 0; }
    .llm-row { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
    .toggle-row { display: flex; align-items: center; gap: 10px; font-size: 0.875rem; }
    .llm-actions { display: flex; gap: 8px; margin-top: 4px; }
    .global-hint {
      font-size: 0.85rem; color: var(--mat-sys-on-surface-variant);
      display: flex; align-items: center; gap: 6px; margin: 4px 0 0;
    }

    .cli-model-select { margin-top: 4px; }
    .oauth-flow { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
    .oauth-url { font-size: 0.8rem; word-break: break-all; color: var(--mat-sys-primary); }
    .code-field { width: 100%; }
    .device-code {
      font-size: 1.8rem; font-weight: 700; letter-spacing: 0.2rem;
      padding: 12px 16px; background: var(--mat-sys-surface-variant);
      border-radius: 4px; text-align: center; margin: 8px 0;
    }
    .polling-indicator { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
  `],
})
export class ConsoleSettingsComponent implements OnInit, OnDestroy {
  readonly i18n = inject(I18nService);
  private readonly http = inject(HttpClient);
  private readonly snack = inject(MatSnackBar);

  currentAgent = signal<AgentType>('hermes');
  hermesLLM = signal<HermesLLM>({ configured: false });
  savingLLM = signal(false);

  readonly apiModes: ApiMode[] = ['chat_completions', 'anthropic_messages', 'codex_responses', 'bedrock_converse'];

  // Editable LLM form state
  llmForm = {
    api_mode: 'chat_completions' as ApiMode,
    model: '',
    base_url: '',
    api_key: '',
    timeout_seconds: 120,
    thinking_mode: false,
  };

  // Claude OAuth state
  claudeStep = signal<'idle' | 'loading' | 'authorize' | 'completing'>('idle');
  claudeSession = signal<ClaudeOAuthSession | null>(null);
  claudeCode = '';

  // Claude model selection
  claudeModels = signal<string[]>([]);
  claudeModelsSource = signal<'api' | 'static'>('static');
  claudeConnected = signal(false);
  claudeModel = '';

  // Codex OAuth state
  codexStep = signal<'idle' | 'loading' | 'polling'>('idle');
  codexSession = signal<CodexOAuthSession | null>(null);
  private codexPollTimer: any = null;

  // Codex model selection
  codexModels = signal<string[]>([]);
  codexModelsSource = signal<'api' | 'static'>('static');
  codexConnected = signal(false);
  codexModel = '';

  ngOnInit(): void {
    this.http.get<any>('/api/preferences').subscribe({
      next: (prefs) => {
        const agent = prefs?.computer_agent || 'hermes';
        this.currentAgent.set(agent as AgentType);
        if (agent === 'hermes') this.loadHermesLLM();
      },
      error: () => {},
    });
    this.loadCLIModels('claude');
    this.loadCLIModels('codex');
  }

  ngOnDestroy(): void {
    this.stopCodexPolling();
  }

  openUrl(url?: string): void {
    if (url) window.open(url, '_blank');
  }

  apiModeLabel(mode: string): string {
    return API_MODE_LABELS[mode as ApiMode] ?? mode;
  }

  needsBaseUrl(mode: string): boolean {
    return mode !== 'anthropic_messages' && mode !== 'codex_responses';
  }

  modelPlaceholder(mode: string): string {
    switch (mode) {
      case 'anthropic_messages': return 'claude-opus-4-8';
      case 'codex_responses':    return 'gpt-5.5';
      case 'bedrock_converse':   return 'anthropic.claude-3-5-sonnet-20241022-v2:0';
      default:                   return 'llama3.2 / mistral-large / etc.';
    }
  }

  // ── Hermes LLM ─────────────────────────────────────────────────────

  private loadHermesLLM(): void {
    this.http.get<HermesLLM>('/api/computer/hermes-llm').subscribe({
      next: (cfg) => {
        this.hermesLLM.set(cfg);
        if (cfg.configured) {
          this.llmForm.api_mode = (cfg.api_mode ?? 'chat_completions') as ApiMode;
          this.llmForm.model = cfg.model ?? '';
          this.llmForm.base_url = cfg.base_url ?? '';
          this.llmForm.api_key = '';  // always blank on load (masked)
          this.llmForm.timeout_seconds = cfg.timeout_seconds ?? 120;
          this.llmForm.thinking_mode = cfg.thinking_mode ?? false;
        }
      },
      error: () => {},
    });
  }

  toggleGlobalLLM(useGlobal: boolean): void {
    if (useGlobal) {
      // Delete personal config → fall back to global
      this.http.put('/api/computer/hermes-llm', { use_global: true }).subscribe({
        next: () => {
          this.hermesLLM.set({ configured: false });
          this.snack.open('Nutzt jetzt globale LLM-Konfiguration', '', { duration: 3000 });
        },
        error: (e) => this.snack.open(`Fehler: ${e.error?.detail || e.message}`, '', { duration: 4000 }),
      });
    } else {
      // Switch to personal config mode — show the form with sensible defaults
      this.hermesLLM.set({ configured: true, has_api_key: false });
    }
  }

  saveLLM(): void {
    this.savingLLM.set(true);
    this.http.put('/api/computer/hermes-llm', {
      api_mode: this.llmForm.api_mode,
      model: this.llmForm.model,
      base_url: this.llmForm.base_url,
      api_key: this.llmForm.api_key || null,
      timeout_seconds: this.llmForm.timeout_seconds,
      thinking_mode: this.llmForm.thinking_mode,
      use_global: false,
    }).subscribe({
      next: () => {
        this.savingLLM.set(false);
        this.hermesLLM.update(s => ({ ...s, configured: true, has_api_key: !!(s.has_api_key || this.llmForm.api_key) }));
        this.llmForm.api_key = '';
        this.snack.open('Hermes LLM-Konfiguration gespeichert', '', { duration: 3000 });
      },
      error: (e) => {
        this.savingLLM.set(false);
        this.snack.open(`Fehler: ${e.error?.detail || e.message}`, '', { duration: 4000 });
      },
    });
  }

  // ── CLI Model Selection ─────────────────────────────────────────────

  loadCLIModels(provider: 'claude' | 'codex'): void {
    this.http.get<any>(`/api/computer/models/${provider}`).subscribe({
      next: (res) => {
        const models: string[] = res.models ?? [];
        const current: string = res.current_model ?? '';
        const connected = res.source === 'api' || !!current;
        if (provider === 'claude') {
          this.claudeModels.set(models);
          this.claudeModelsSource.set(res.source ?? 'static');
          this.claudeConnected.set(connected);
          if (current) this.claudeModel = current;
          else if (models.length) this.claudeModel = models[0];
        } else {
          this.codexModels.set(models);
          this.codexModelsSource.set(res.source ?? 'static');
          this.codexConnected.set(connected);
          if (current) this.codexModel = current;
          else if (models.length) this.codexModel = models[0];
        }
      },
      error: () => {},
    });
  }

  saveCLIModel(provider: 'claude' | 'codex', model: string): void {
    this.http.patch('/api/computer/cli-model', { provider, model }).subscribe({
      next: () => this.snack.open(`${provider === 'claude' ? 'Claude' : 'Codex'} Modell gespeichert: ${model}`, '', { duration: 2500 }),
      error: (e) => this.snack.open(`Fehler: ${e.error?.detail || e.message}`, '', { duration: 4000 }),
    });
  }

  // ── Hermes ─────────────────────────────────────────────────────────

  selectHermes(): void {
    this.http.post('/api/computer/configure-agent', { agent: 'hermes' }).subscribe({
      next: () => {
        this.currentAgent.set('hermes');
        this.loadHermesLLM();
        this.snack.open('Hermes als Console-Agent gesetzt', '', { duration: 3000 });
      },
      error: (e) => this.snack.open(`Fehler: ${e.error?.detail || e.message}`, '', { duration: 4000 }),
    });
  }

  // ── Claude OAuth ────────────────────────────────────────────────────

  startClaudeOAuth(): void {
    this.claudeStep.set('loading');
    this.http.post<ClaudeOAuthSession>('/api/oauth/claude-oauth/user/start', {}).subscribe({
      next: (session) => {
        this.claudeSession.set(session);
        this.claudeStep.set('authorize');
        this.claudeCode = '';
      },
      error: (e) => {
        this.claudeStep.set('idle');
        this.snack.open(`Claude OAuth Fehler: ${e.error?.detail || e.message}`, '', { duration: 4000 });
      },
    });
  }

  completeClaudeOAuth(): void {
    const session = this.claudeSession();
    if (!session || !this.claudeCode) return;
    this.claudeStep.set('completing');
    this.http.post<any>('/api/oauth/claude-oauth/user/complete', {
      session_id: session.session_id,
      code: this.claudeCode,
    }).subscribe({
      next: (tokens) => {
        this.http.post('/api/computer/configure-agent', {
          agent: 'claude_cli',
          access_token: tokens.access_token,
          refresh_token: tokens.refresh_token,
          expires_at: tokens.expires_at,
        }).subscribe({
          next: () => {
            this.currentAgent.set('claude_cli');
            this.claudeStep.set('idle');
            this.claudeSession.set(null);
            this.snack.open('Claude als Console-Agent konfiguriert', '', { duration: 3000 });
            this.loadCLIModels('claude');
          },
          error: (e) => {
            this.claudeStep.set('authorize');
            this.snack.open(`Fehler beim Konfigurieren: ${e.error?.detail || e.message}`, '', { duration: 4000 });
          },
        });
      },
      error: (e) => {
        this.claudeStep.set('authorize');
        this.snack.open(`Claude Token-Austausch fehlgeschlagen: ${e.error?.detail || e.message}`, '', { duration: 4000 });
      },
    });
  }

  resetClaudeOAuth(): void {
    this.claudeStep.set('idle');
    this.claudeSession.set(null);
    this.claudeCode = '';
  }

  // ── Codex Device-Code OAuth ─────────────────────────────────────────

  startCodexOAuth(): void {
    this.codexStep.set('loading');
    this.http.post<CodexOAuthSession>('/api/oauth/openai-codex/user/start', {}).subscribe({
      next: (session) => {
        this.codexSession.set(session);
        this.codexStep.set('polling');
        this.startCodexPolling(session.session_id, session.poll_interval_seconds || 5);
      },
      error: (e) => {
        this.codexStep.set('idle');
        this.snack.open(`Codex OAuth Fehler: ${e.error?.detail || e.message}`, '', { duration: 4000 });
      },
    });
  }

  private startCodexPolling(sessionId: string, intervalSeconds: number): void {
    this.stopCodexPolling();
    this.codexPollTimer = setInterval(() => this.pollCodex(sessionId), intervalSeconds * 1000);
  }

  private pollCodex(sessionId: string): void {
    this.http.post<any>(`/api/oauth/openai-codex/user/poll/${sessionId}`, {}).subscribe({
      next: (res) => {
        if (res.status === 'authorized') {
          this.stopCodexPolling();
          this.http.post('/api/computer/configure-agent', {
            agent: 'codex_cli',
            access_token: res.access_token,
            refresh_token: res.refresh_token,
          }).subscribe({
            next: () => {
              this.currentAgent.set('codex_cli');
              this.codexStep.set('idle');
              this.codexSession.set(null);
              this.snack.open('Codex als Console-Agent konfiguriert', '', { duration: 3000 });
              this.loadCLIModels('codex');
            },
            error: (e) => {
              this.codexStep.set('idle');
              this.snack.open(`Fehler beim Konfigurieren: ${e.error?.detail || e.message}`, '', { duration: 4000 });
            },
          });
        } else if (res.status === 'timeout' || res.status === 'error') {
          this.stopCodexPolling();
          this.codexStep.set('idle');
          this.snack.open('Codex OAuth Timeout oder Fehler', '', { duration: 4000 });
        }
      },
      error: () => {
        this.stopCodexPolling();
        this.codexStep.set('idle');
      },
    });
  }

  private stopCodexPolling(): void {
    if (this.codexPollTimer) {
      clearInterval(this.codexPollTimer);
      this.codexPollTimer = null;
    }
  }

  resetCodexOAuth(): void {
    this.stopCodexPolling();
    this.codexStep.set('idle');
    this.codexSession.set(null);
  }
}
