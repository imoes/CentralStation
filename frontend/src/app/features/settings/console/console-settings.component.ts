import { Component, OnInit, OnDestroy, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { I18nService } from '../../../core/services/i18n.service';

type AgentType = 'hermes' | 'claude_cli' | 'codex_cli';

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

@Component({
  selector: 'cs-console-settings',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatCardModule, MatButtonModule, MatIconModule,
    MatInputModule, MatFormFieldModule,
    MatProgressSpinnerModule, MatSnackBarModule,
  ],
  template: `
    <div class="console-settings">
      <h2 class="section-title">{{ i18n.t('console.agent_section') }}</h2>
      <p class="section-hint">{{ i18n.t('console.agent_section_hint') }}</p>

      <div class="agent-cards">
        <!-- Hermes -->
        <div class="agent-card" [class.active]="currentAgent() === 'hermes'" (click)="selectHermes()">
          <div class="agent-icon"><mat-icon>memory</mat-icon></div>
          <div class="agent-info">
            <div class="agent-name">{{ i18n.t('console.agent_hermes') }}</div>
            <div class="agent-desc">{{ i18n.t('console.agent_hermes_desc') }}</div>
          </div>
          @if (currentAgent() === 'hermes') {
            <div class="agent-badge"><mat-icon>check_circle</mat-icon> {{ i18n.t('console.agent_active') }}</div>
          }
        </div>

        <!-- Claude -->
        <div class="agent-card" [class.active]="currentAgent() === 'claude_cli'">
          <div class="agent-icon">
            <img src="assets/icons/claude.svg" alt="Claude" width="28" height="28" onerror="this.style.display='none'">
            <mat-icon *ngIf="true">smart_toy</mat-icon>
          </div>
          <div class="agent-info">
            <div class="agent-name">{{ i18n.t('console.agent_claude') }}</div>
            <div class="agent-desc">{{ i18n.t('console.agent_claude_desc') }}</div>
          </div>
          @if (currentAgent() === 'claude_cli') {
            <div class="agent-badge connected"><mat-icon>check_circle</mat-icon> {{ i18n.t('console.connected') }}</div>
          }
          <div class="agent-oauth">
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
        </div>

        <!-- Codex -->
        <div class="agent-card" [class.active]="currentAgent() === 'codex_cli'">
          <div class="agent-icon"><mat-icon>code</mat-icon></div>
          <div class="agent-info">
            <div class="agent-name">{{ i18n.t('console.agent_codex') }}</div>
            <div class="agent-desc">{{ i18n.t('console.agent_codex_desc') }}</div>
          </div>
          @if (currentAgent() === 'codex_cli') {
            <div class="agent-badge connected"><mat-icon>check_circle</mat-icon> {{ i18n.t('console.connected') }}</div>
          }
          <div class="agent-oauth">
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
      border-radius: 8px;
      padding: 16px;
      cursor: pointer;
      transition: border-color 0.2s, box-shadow 0.2s;
    }
    .agent-card:hover { border-color: var(--mat-sys-primary); }
    .agent-card.active {
      border-color: var(--mat-sys-primary);
      box-shadow: 0 0 0 1px var(--mat-sys-primary);
    }
    .agent-icon { display: flex; align-items: center; margin-bottom: 8px; color: var(--mat-sys-primary); }
    .agent-name { font-weight: 600; font-size: 1rem; margin-bottom: 4px; }
    .agent-desc { font-size: 0.85rem; color: var(--mat-sys-on-surface-variant); }
    .agent-badge {
      display: inline-flex; align-items: center; gap: 4px;
      font-size: 0.8rem; color: var(--mat-sys-on-surface-variant);
      margin-top: 8px;
    }
    .agent-badge.connected { color: #4caf50; }
    .agent-oauth { margin-top: 12px; }
    .oauth-flow { display: flex; flex-direction: column; gap: 8px; }
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

  // Claude OAuth state
  claudeStep = signal<'idle' | 'loading' | 'authorize' | 'completing'>('idle');
  claudeSession = signal<ClaudeOAuthSession | null>(null);
  claudeCode = '';

  // Codex OAuth state
  codexStep = signal<'idle' | 'loading' | 'polling'>('idle');
  codexSession = signal<CodexOAuthSession | null>(null);
  private codexPollTimer: any = null;

  ngOnInit(): void {
    this.http.get<any>('/api/preferences').subscribe({
      next: (prefs) => {
        const agent = prefs?.computer_agent || 'hermes';
        this.currentAgent.set(agent as AgentType);
      },
      error: () => {},
    });
  }

  ngOnDestroy(): void {
    this.stopCodexPolling();
  }

  openUrl(url?: string): void {
    if (url) window.open(url, '_blank');
  }

  // ── Hermes ─────────────────────────────────────────────────────────

  selectHermes(): void {
    this.http.post('/api/computer/configure-agent', { agent: 'hermes' }).subscribe({
      next: () => {
        this.currentAgent.set('hermes');
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
        // status === 'pending' → keep polling
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
