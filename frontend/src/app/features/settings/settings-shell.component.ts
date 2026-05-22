import { Component } from '@angular/core';
import { RouterOutlet, RouterLink, RouterLinkActive } from '@angular/router';
import { MatTabsModule } from '@angular/material/tabs';
import { MatIconModule } from '@angular/material/icon';

@Component({
  selector: 'cs-settings-shell',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive, MatTabsModule, MatIconModule],
  template: `
    <div class="settings-container">
      <nav mat-tab-nav-bar [tabPanel]="tabPanel" class="settings-tabs">
        <a mat-tab-link routerLink="connectors" routerLinkActive #connectors="routerLinkActive" [active]="connectors.isActive">
          <mat-icon>cable</mat-icon>&nbsp;Connectors
        </a>
        <a mat-tab-link routerLink="users" routerLinkActive #users="routerLinkActive" [active]="users.isActive">
          <mat-icon>group</mat-icon>&nbsp;Benutzer
        </a>
        <a mat-tab-link routerLink="ai" routerLinkActive #ai="routerLinkActive" [active]="ai.isActive">
          <mat-icon>psychology</mat-icon>&nbsp;KI-Konfiguration
        </a>
        <a mat-tab-link routerLink="audit" routerLinkActive #audit="routerLinkActive" [active]="audit.isActive">
          <mat-icon>history</mat-icon>&nbsp;Audit-Log
        </a>
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
export class SettingsShellComponent {}
