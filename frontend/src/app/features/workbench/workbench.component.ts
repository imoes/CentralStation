import { Component, OnInit, signal, computed, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { environment } from '../../../environments/environment';
import { ComputerService } from '../../core/services/computer.service';
import { I18nService } from '../../core/services/i18n.service';

interface WorkSession {
  id: string;
  title: string;
  jira_key: string | null;
  status: string;
  computer_session_id: string | null;
  gitlab_project_id: string | null;
  gitlab_branch: string | null;
  gitlab_mr_iid: number | null;
  gitlab_mr_url: string | null;
}

@Component({
  selector: 'cs-workbench',
  standalone: true,
  imports: [
    CommonModule, RouterLink,
    MatIconModule, MatTooltipModule, MatSnackBarModule,
  ],
  template: `
    <div class="wb-root">
      <!-- ── LCARS Header / context strip ───────────────────────── -->
      <div class="wb-header">
        <div class="cap-tl"></div>
        <div class="header-bar">
          <span class="header-title">{{ i18n.t('workbench.title') }}</span>
          @if (session(); as s) {
            <span class="ctx-title" [title]="s.title">{{ s.title }}</span>
            @if (s.jira_key) {
              <span class="ctx-chip">{{ s.jira_key }}</span>
            }
            @if (s.gitlab_branch) {
              <span class="ctx-chip git"><mat-icon class="ic">fork_right</mat-icon>{{ s.gitlab_branch }}</span>
            }
            @if (s.gitlab_mr_url) {
              <a class="ctx-chip mr" [href]="s.gitlab_mr_url" target="_blank" rel="noopener">
                <mat-icon class="ic">merge</mat-icon>MR !{{ s.gitlab_mr_iid }}
              </a>
            }
          } @else {
            <span class="ctx-title">{{ i18n.t('workbench.own_workspace') }}</span>
          }
          <span class="spacer"></span>
          <button type="button" class="hbtn" (click)="openHermes()" [matTooltip]="i18n.t('workbench.open_hermes_tooltip')">
            <svg width="18" height="18" viewBox="0 0 100 100" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <rect x="29" y="29" width="42" height="42" rx="7"/>
              <text x="50" y="56" text-anchor="middle" font-family="Arial,sans-serif" font-size="15" font-weight="700" letter-spacing="1" fill="currentColor" stroke="none">AI</text>
              <line x1="39" y1="29" x2="39" y2="19"/><line x1="50" y1="29" x2="50" y2="12"/><line x1="61" y1="29" x2="61" y2="19"/>
              <polyline points="34,29 34,20 20,20"/><polyline points="66,29 66,20 80,20"/>
              <line x1="39" y1="71" x2="39" y2="81"/><line x1="50" y1="71" x2="50" y2="88"/><line x1="61" y1="71" x2="61" y2="81"/>
              <polyline points="34,71 34,80 20,80"/><polyline points="66,71 66,80 80,80"/>
              <line x1="29" y1="39" x2="19" y2="39"/><line x1="29" y1="50" x2="12" y2="50"/><line x1="29" y1="61" x2="19" y2="61"/>
              <polyline points="29,34 20,34 20,20"/><polyline points="29,66 20,66 20,80"/>
              <line x1="71" y1="39" x2="81" y2="39"/><line x1="71" y1="50" x2="88" y2="50"/><line x1="71" y1="61" x2="81" y2="61"/>
              <polyline points="71,34 80,34 80,20"/><polyline points="71,66 80,66 80,80"/>
              <rect x="37" y="17" width="4" height="4" rx="1" fill="currentColor" stroke="none"/><rect x="48" y="10" width="4" height="4" rx="1" fill="currentColor" stroke="none"/><rect x="59" y="17" width="4" height="4" rx="1" fill="currentColor" stroke="none"/>
              <rect x="37" y="79" width="4" height="4" rx="1" fill="currentColor" stroke="none"/><rect x="48" y="86" width="4" height="4" rx="1" fill="currentColor" stroke="none"/><rect x="59" y="79" width="4" height="4" rx="1" fill="currentColor" stroke="none"/>
              <rect x="17" y="37" width="4" height="4" rx="1" fill="currentColor" stroke="none"/><rect x="10" y="48" width="4" height="4" rx="1" fill="currentColor" stroke="none"/><rect x="17" y="59" width="4" height="4" rx="1" fill="currentColor" stroke="none"/>
              <rect x="79" y="37" width="4" height="4" rx="1" fill="currentColor" stroke="none"/><rect x="86" y="48" width="4" height="4" rx="1" fill="currentColor" stroke="none"/><rect x="79" y="59" width="4" height="4" rx="1" fill="currentColor" stroke="none"/>
            </svg> {{ i18n.t('workbench.hermes') }}
          </button>
          <button type="button" class="hbtn" (click)="openTab()" [matTooltip]="i18n.t('workbench.open_new_tab_tooltip')">
            <mat-icon>open_in_new</mat-icon>
          </button>
          <button type="button" class="hbtn" (click)="reload()" [matTooltip]="i18n.t('workbench.reload_ide_tooltip')">
            <mat-icon>refresh</mat-icon>
          </button>
        </div>
        <div class="cap-tr"></div>
      </div>

      <!-- ── IDE iframe ─────────────────────────────────────────── -->
      <div class="wb-body">
        @if (loading()) {
          <div class="status">{{ i18n.t('workbench.preparing_ide') }}</div>
        } @else if (error()) {
          <div class="status err">{{ error() }}</div>
        }
        @if (ideUrl()) {
          <iframe class="ide-frame" [src]="ideUrl()" title="Web-IDE"
                  allow="clipboard-read; clipboard-write"></iframe>
        }
      </div>

      <div class="wb-footer">
        <div class="cap-bl"></div>
        <span class="foot">{{ i18n.t('workbench.footer') }}</span>
        <div class="cap-br"></div>
      </div>
    </div>

  `,
  styles: [`
    :host { display: flex; flex-direction: column; height: 100vh; background: #111; color: #ffcc99; font-family: 'Antonio','Roboto',sans-serif; }
    .wb-root { display: flex; flex-direction: column; height: 100%; }
    .wb-header { display: flex; align-items: stretch; height: 48px; flex-shrink: 0; }
    .cap-tl { width: 18px; background: #ff9933; border-radius: 18px 0 0 0; }
    .cap-tr { width: 18px; background: #ff9933; border-radius: 0 18px 0 0; }
    .cap-bl { width: 18px; background: #ff9933; border-radius: 0 0 0 18px; }
    .cap-br { width: 18px; background: #ff9933; border-radius: 0 0 18px 0; }
    .header-bar { flex: 1; background: #ff9933; display: flex; align-items: center; gap: 12px; padding: 0 14px; }
    .header-title { font-size: 1.25rem; font-weight: 700; letter-spacing: 0.12em; color: #111; }
    .ctx-title { font-size: 0.9rem; font-weight: 600; color: #111; max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .ctx-chip { display: inline-flex; align-items: center; gap: 3px; background: #111; color: #ffcc99; font-size: 0.72rem; font-weight: 700; padding: 2px 8px; border-radius: 10px; text-decoration: none; }
    .ctx-chip.git { color: #aaccff; }
    .ctx-chip.mr { color: #66cc66; }
    .ctx-chip .ic { font-size: 13px; width: 13px; height: 13px; }
    .spacer { flex: 1; }
    /* Plain LCARS pills — dark pill + light text, readable on the orange header */
    .hbtn { display: inline-flex; align-items: center; gap: 5px; background: #111; color: #ffcc99; border: none; border-radius: 12px; padding: 5px 12px; font-family: inherit; font-size: 0.8rem; font-weight: 700; letter-spacing: 0.04em; cursor: pointer; }
    .hbtn:hover { background: #2a2a2a; color: #ff9933; }
    .hbtn mat-icon { font-size: 18px; width: 18px; height: 18px; color: inherit; }
    .wb-body { flex: 1; position: relative; background: #000; }
    .ide-frame { width: 100%; height: 100%; border: 0; display: block; }
    .status { padding: 24px; font-size: 0.95rem; color: #ffcc99; }
    .status.err { color: #ff6666; }
    .wb-footer { display: flex; align-items: stretch; height: 24px; flex-shrink: 0; }
    .foot { background: #ff9933; color: #111; font-size: 0.65rem; font-weight: 700; letter-spacing: 0.12em; padding: 0 12px; display: flex; align-items: center; margin-right: 4px; }
  `],
})
export class WorkbenchComponent implements OnInit {
  private http = inject(HttpClient);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private san = inject(DomSanitizer);
  private snack = inject(MatSnackBar);
  private computer = inject(ComputerService);
  readonly i18n = inject(I18nService);

  loading = signal(true);
  error = signal<string | null>(null);
  ideUrl = signal<SafeResourceUrl | null>(null);
  rawIdeUrl = signal<string | null>(null);
  session = signal<WorkSession | null>(null);

  ngOnInit(): void {
    // NOTE: do NOT unregister code-server's service worker here. It is required —
    // it serves extension webview CSS/JS (Claude Code panel etc.). The blank-on-
    // reload issue it used to cause is now fixed via the Service-Worker-Allowed
    // header in nginx (coder/code-server#2106 + #2038).

    // Hermes handoff: router state carries a pre-computed IDE URL (file already written).
    const stateUrl = (history.state as { ideUrl?: string })?.ideUrl;
    if (stateUrl) {
      this.setIde(stateUrl);
      this.loading.set(false);
      return;
    }

    const id = this.route.snapshot.paramMap.get('id');
    if (id) {
      this.http.get<WorkSession>(`${environment.apiUrl}/workflow/${id}`).subscribe({
        next: s => { this.session.set(s); this.provision(id); },
        error: () => { this.error.set(this.i18n.t('errors.worksession_not_found')); this.loading.set(false); },
      });
    } else {
      this.ensureOwn();
    }
  }

  /** Standalone IDE (no WorkSession): just ensure the container + open the workspace root. */
  private ensureOwn(): void {
    this.http.post<{ ide_base: string }>(`${environment.apiUrl}/ide/session/ensure`, {}, { withCredentials: true })
      .subscribe({
        next: r => { this.setIde(`${r.ide_base}?folder=/home/yolo/workspaces`); this.loading.set(false); },
        error: e => { this.error.set(this.msg(e)); this.loading.set(false); },
      });
  }

  /** WorkSession context: ensure container, clone repo + checkout branch, open. */
  private provision(id: string): void {
    this.http.post<{ ide_url: string }>(`${environment.apiUrl}/ide/workspace/${id}/provision`, {}, { withCredentials: true })
      .subscribe({
        next: r => { this.setIde(r.ide_url); this.loading.set(false); },
        error: e => { this.error.set(this.msg(e)); this.loading.set(false); },
      });
  }

  private setIde(url: string): void {
    this.rawIdeUrl.set(url);
    this.ideUrl.set(this.san.bypassSecurityTrustResourceUrl(url));
  }

  openTab(): void {
    const u = this.rawIdeUrl();
    if (!u) return;
    // Ensure the container is running before opening the new tab — otherwise
    // the new window hits nginx before Docker DNS has the container registered.
    this.http.post(`${environment.apiUrl}/ide/session/ensure`, {}).subscribe({
      next: () => window.open(u, '_blank', 'noopener'),
      error: () => window.open(u, '_blank', 'noopener'), // open anyway on error
    });
  }

  reload(): void {
    const cur = this.ideUrl();
    if (!cur) return;
    // Force iframe reload by clearing + re-setting on next tick.
    this.ideUrl.set(null);
    setTimeout(() => this.ideUrl.set(cur), 50);
  }

  /** Seamless Hermes: resume the linked session, or open a context-seeded one. */
  openHermes(): void {
    const s = this.session();
    if (s?.computer_session_id) {
      this.computer.resumeSession(s.computer_session_id);
      return;
    }
    const seed = s
      ? `Arbeite an WorkSession "${s.title}"${s.jira_key ? ' (' + s.jira_key + ')' : ''}` +
        `${s.gitlab_branch ? ', Branch ' + s.gitlab_branch : ''}.`
      : 'Werkbank-Arbeitsplatz geöffnet.';
    this.computer.openWithContext(seed, s?.title ?? 'Werkbank');
    this.snack.open('Hermes geöffnet', '', { duration: 1500 });
  }

  private msg(e: any): string {
    return 'IDE-Fehler: ' + (e?.error?.detail ?? e?.message ?? 'unbekannt');
  }
}
