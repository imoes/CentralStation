import { Component, computed, signal, OnInit, inject } from '@angular/core';
import { RouterOutlet, RouterLink, RouterLinkActive } from '@angular/router';
import { MatTabsModule } from '@angular/material/tabs';
import { MatIconModule } from '@angular/material/icon';
import { CommonModule } from '@angular/common';
import { AuthService } from '../../core/auth/auth.service';
import { I18nService } from '../../core/services/i18n.service';
import { HttpClient } from '@angular/common/http';

@Component({
  selector: 'cs-settings-shell',
  standalone: true,
  imports: [CommonModule, RouterOutlet, RouterLink, RouterLinkActive, MatTabsModule, MatIconModule],
  template: `
    <div class="settings-container">
      <nav mat-tab-nav-bar [tabPanel]="tabPanel" class="settings-tabs">
        <a mat-tab-link routerLink="connectors" routerLinkActive #connectors="routerLinkActive" [active]="connectors.isActive">
          <mat-icon>cable</mat-icon>&nbsp;{{ isAdmin() ? i18n.t('settings.tabs.connectors') : i18n.t('settings.tabs.myConnectors') }}
        </a>
        <a mat-tab-link routerLink="my" routerLinkActive #my="routerLinkActive" [active]="my.isActive">
          <mat-icon>manage_accounts</mat-icon>&nbsp;{{ i18n.t('settings.tabs.mySettings') }}
        </a>
        <a mat-tab-link routerLink="skills" routerLinkActive #skills="routerLinkActive" [active]="skills.isActive">
          <mat-icon>psychology</mat-icon>&nbsp;Skills
        </a>
        @if (consoleEnabled()) {
          <a mat-tab-link routerLink="console" routerLinkActive #consoleTab="routerLinkActive" [active]="consoleTab.isActive">
            <mat-icon>terminal</mat-icon>&nbsp;Konsole
          </a>
        }
        @if (isAdmin()) {
          <a mat-tab-link routerLink="users" routerLinkActive #users="routerLinkActive" [active]="users.isActive">
            <mat-icon>group</mat-icon>&nbsp;{{ i18n.t('settings.tabs.users') }}
          </a>
          <a mat-tab-link routerLink="ai" routerLinkActive #ai="routerLinkActive" [active]="ai.isActive">
            <mat-icon>tune</mat-icon>&nbsp;{{ i18n.t('settings.tabs.ai') }}
          </a>
          <a mat-tab-link routerLink="audit" routerLinkActive #audit="routerLinkActive" [active]="audit.isActive">
            <mat-icon>history</mat-icon>&nbsp;{{ i18n.t('settings.tabs.audit') }}
          </a>
          <a mat-tab-link routerLink="feed" routerLinkActive #feedTab="routerLinkActive" [active]="feedTab.isActive">
            <mat-icon>feed</mat-icon>&nbsp;{{ i18n.t('settings.tabs.feed') }}
          </a>
        }
      </nav>
      <mat-tab-nav-panel #tabPanel>
        <router-outlet></router-outlet>
      </mat-tab-nav-panel>
    </div>
  `,
  styles: [`
    .settings-container { display: flex; flex-direction: column; height: 100%; }
    .settings-tabs { border-bottom: 1px solid var(--mat-sys-outline-variant); padding: 0 16px; }
    mat-tab-nav-panel { flex: 1; overflow: auto; }
  `],
})
export class SettingsShellComponent implements OnInit {
  isAdmin = computed(() => this.auth.userRole() === 'admin');
  consoleEnabled = signal(false);
  readonly i18n: I18nService;
  private readonly http = inject(HttpClient);

  constructor(private auth: AuthService, i18n: I18nService) {
    this.i18n = i18n;
  }

  ngOnInit(): void {
    this.http.get<any>('/api/preferences').subscribe({
      next: (prefs) => this.consoleEnabled.set(!!prefs?.computer_console_enabled),
      error: () => {},
    });
  }
}
