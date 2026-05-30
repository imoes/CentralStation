import { Component, computed, effect, inject, OnDestroy, OnInit, signal } from '@angular/core';
import { Router, RouterOutlet, RouterLink, RouterLinkActive } from '@angular/router';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatListModule } from '@angular/material/list';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatBadgeModule } from '@angular/material/badge';
import { AuthService } from './core/auth/auth.service';
import { WebsocketService } from './core/services/websocket.service';
import { environment } from '../environments/environment';

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
              @if (item.path === '/feed') {
                <a mat-list-item [routerLink]="item.path" routerLinkActive="active"
                   (click)="onFeedClick()">
                  <mat-icon matListItemIcon>{{ item.icon }}</mat-icon>
                  <span matListItemTitle>
                    {{ item.label }}
                    @if (unreadFeedCount() > 0) {
                      <span class="feed-badge">{{ unreadFeedCount() > 99 ? '99+' : unreadFeedCount() }}</span>
                    }
                  </span>
                </a>
              } @else if (item.path === '/my-tickets') {
                <a mat-list-item [routerLink]="item.path" routerLinkActive="active">
                  <mat-icon matListItemIcon>{{ item.icon }}</mat-icon>
                  <span matListItemTitle>
                    {{ item.label }}
                    @if (unreadTicketCount() > 0) {
                      <span class="feed-badge">{{ unreadTicketCount() > 99 ? '99+' : unreadTicketCount() }}</span>
                    }
                  </span>
                </a>
              } @else {
                <a mat-list-item [routerLink]="item.path" routerLinkActive="active">
                  <mat-icon matListItemIcon>{{ item.icon }}</mat-icon>
                  <span matListItemTitle>{{ item.label }}</span>
                </a>
              }
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
export class App implements OnInit, OnDestroy {
  private readonly navItems: NavItem[] = [
    { path: '/dashboard',    label: 'Dashboard',        icon: 'dashboard',    roles: ['admin','sysadmin','network_technician','viewer'] },
    { path: '/bridge',       label: 'Brücke',           icon: 'rocket_launch',roles: ['admin','sysadmin','network_technician','viewer'] },
    { path: '/feed',         label: 'News Feed',        icon: 'feed',         roles: ['admin','sysadmin','network_technician'] },
    { path: '/alerts',       label: 'Alerts',           icon: 'notifications',roles: ['admin','sysadmin'] },
    { path: '/my-tickets',   label: 'Meine Tickets',    icon: 'assignment',   roles: ['admin','sysadmin'] },
    { path: '/kanban',       label: 'Kanban',           icon: 'view_kanban',  roles: ['admin','sysadmin','network_technician'] },
    { path: '/network',      label: 'Netzwerk',         icon: 'router',       roles: ['admin','network_technician'] },
    { path: '/ai-insights',  label: 'KI-Insights',      icon: 'psychology',   roles: ['admin','sysadmin'] },
    { path: '/settings',     label: 'Einstellungen',    icon: 'settings',     roles: ['admin','sysadmin','network_technician','viewer'] },
    { path: '/help',         label: 'Hilfe',            icon: 'help',         roles: ['admin','sysadmin','network_technician','viewer'] },
  ];

  unreadFeedCount = signal<number>(0);
  unreadTicketCount = signal<number>(0);
  private badgeInterval: ReturnType<typeof setInterval> | null = null;
  private http = inject(HttpClient);

  visibleNavItems = computed(() => {
    const role = this.auth.userRole();
    return this.navItems.filter(i => role && i.roles.includes(role));
  });

  constructor(public auth: AuthService, private ws: WebsocketService) {
    effect(() => {
      if (this.auth.isLoggedIn()) {
        this.auth.fetchMe();
        this.ws.connect();
        if (!this.badgeInterval) this.startBadgePolling();
      } else {
        if (this.badgeInterval) {
          clearInterval(this.badgeInterval);
          this.badgeInterval = null;
        }
        this.unreadFeedCount.set(0);
        this.unreadTicketCount.set(0);
      }
    });
  }

  ngOnInit() {}

  ngOnDestroy() {
    if (this.badgeInterval) clearInterval(this.badgeInterval);
  }

  startBadgePolling() {
    this.fetchUnreadCount();
    this.badgeInterval = setInterval(() => this.fetchUnreadCount(), 60_000);
  }

  fetchUnreadCount() {
    const since = localStorage.getItem('feed_last_seen') ?? new Date(0).toISOString();
    this.http.get<{ count: number }>(`${environment.apiUrl}/feed/unread-count`, { params: { since } })
      .subscribe({ next: r => this.unreadFeedCount.set(r.count), error: () => {} });
    this.fetchTicketUnread();
  }

  /** Live nav badge: loads seen map from server preferences, counts updated tickets. */
  private fetchTicketUnread() {
    this.http.get<{ ticket_seen_map: Record<string, string> }>(
      `${environment.apiUrl}/preferences`,
    ).subscribe({
      next: prefs => {
        const seenMap = prefs.ticket_seen_map ?? {};
        this.http.get<Array<{ issues: Array<{ key: string; fields: { updated: string } }> }>>(
          `${environment.apiUrl}/jira-view/my-tickets`,
        ).subscribe({
          next: groups => {
            const now = new Date().toISOString();
            let count = 0;
            let changed = false;
            const updatedMap = { ...seenMap };
            for (const group of groups) {
              for (const issue of (group.issues ?? []) as Array<{ key: string; fields: { updated: string; status?: { statusCategory?: { key: string } } } }>) {
                const isDone = issue.fields.status?.statusCategory?.key === 'done';
                if (isDone) {
                  if (issue.key in updatedMap) { delete updatedMap[issue.key]; changed = true; }
                  continue;
                }
                const seen = updatedMap[issue.key];
                if (!seen) { updatedMap[issue.key] = now; changed = true; continue; }
                if (new Date(issue.fields.updated) > new Date(seen)) count++;
              }
            }
            if (changed) {
              this.http.patch(`${environment.apiUrl}/preferences`, { ticket_seen_map: updatedMap }).subscribe();
            }
            this.unreadTicketCount.set(count);
          },
          error: () => {},
        });
      },
      error: () => {},
    });
  }

  clearFeedBadge() {
    this.unreadFeedCount.set(0);
  }

  onFeedClick() {
    // Badge is cleared by the news-feed component after items load (3s delay)
    // This just provides immediate visual feedback
  }
}
