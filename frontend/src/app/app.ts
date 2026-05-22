import { Component, computed, OnInit } from '@angular/core';
import { Router, RouterOutlet, RouterLink, RouterLinkActive } from '@angular/router';
import { CommonModule } from '@angular/common';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatListModule } from '@angular/material/list';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatBadgeModule } from '@angular/material/badge';
import { AuthService } from './core/auth/auth.service';
import { WebsocketService } from './core/services/websocket.service';

interface NavItem {
  path: string;
  label: string;
  icon: string;
  roles: string[];
}

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [
    CommonModule, RouterOutlet, RouterLink, RouterLinkActive,
    MatSidenavModule, MatToolbarModule, MatListModule,
    MatIconModule, MatButtonModule, MatBadgeModule,
  ],
  template: `
    @if (auth.isLoggedIn()) {
      <mat-sidenav-container class="app-container">
        <mat-sidenav mode="side" opened class="sidenav">
          <div class="sidenav-header">
            <mat-icon>hub</mat-icon>
            <span>CentralStation</span>
          </div>
          <mat-nav-list>
            @for (item of visibleNavItems(); track item.path) {
              <a mat-list-item [routerLink]="item.path" routerLinkActive="active">
                <mat-icon matListItemIcon>{{ item.icon }}</mat-icon>
                <span matListItemTitle>{{ item.label }}</span>
              </a>
            }
          </mat-nav-list>
          <div class="sidenav-footer">
            <span class="role-chip">{{ auth.userRole() }}</span>
            <button mat-icon-button (click)="auth.logout()" title="Abmelden">
              <mat-icon>logout</mat-icon>
            </button>
          </div>
        </mat-sidenav>
        <mat-sidenav-content>
          <router-outlet></router-outlet>
        </mat-sidenav-content>
      </mat-sidenav-container>
    } @else {
      <router-outlet></router-outlet>
    }
  `,
  styleUrl: './app.scss',
})
export class App implements OnInit {
  private readonly navItems: NavItem[] = [
    { path: '/dashboard',    label: 'Dashboard',        icon: 'dashboard',    roles: ['admin','sysadmin','network_technician','viewer'] },
    { path: '/alerts',       label: 'Alerts',           icon: 'notifications',roles: ['admin','sysadmin'] },
    { path: '/kanban',       label: 'Kanban',           icon: 'view_kanban',  roles: ['admin','sysadmin','network_technician'] },
    { path: '/network',      label: 'Netzwerk',         icon: 'router',       roles: ['admin','network_technician'] },
    { path: '/ai-insights',  label: 'KI-Insights',      icon: 'psychology',   roles: ['admin','sysadmin'] },
    { path: '/settings',     label: 'Einstellungen',    icon: 'settings',     roles: ['admin'] },
  ];

  visibleNavItems = computed(() => {
    const role = this.auth.userRole();
    return this.navItems.filter(i => role && i.roles.includes(role));
  });

  constructor(public auth: AuthService, private ws: WebsocketService) {}

  ngOnInit() {
    if (this.auth.isLoggedIn()) {
      this.auth.fetchMe();
      this.ws.connect();
    }
  }
}
