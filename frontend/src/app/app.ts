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
import { ThemeService } from './core/services/theme.service';
import { I18nService } from './core/services/i18n.service';
import { environment } from '../environments/environment';

interface NavItem {
  path: string;
  labelKey: string;
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
        <mat-sidenav mode="side" opened class="sidenav" [class.collapsed]="navCollapsed()">
          <div class="sidenav-header">
            <button mat-icon-button class="nav-toggle" (click)="toggleNav()" [title]="i18n.t('app.nav.toggle')">
              <mat-icon>menu</mat-icon>
            </button>
            <mat-icon class="brand-icon">hub</mat-icon>
            <span class="brand-label">CentralStation</span>
          </div>
          <mat-nav-list>
            @for (item of visibleNavItems(); track item.path) {
              @if (item.path === '/feed') {
                <a mat-list-item [routerLink]="item.path" routerLinkActive="active"
                   (click)="onFeedClick()" [title]="i18n.t(item.labelKey)">
                  <mat-icon matListItemIcon>{{ item.icon }}</mat-icon>
                  <span matListItemTitle class="nav-label">
                    {{ i18n.t(item.labelKey) }}
                    @if (unreadFeedCount() > 0) {
                      <span class="feed-badge">{{ unreadFeedCount() > 99 ? '99+' : unreadFeedCount() }}</span>
                    }
                  </span>
                </a>
              } @else if (item.path === '/my-tickets') {
                <a mat-list-item [routerLink]="item.path" routerLinkActive="active" [title]="i18n.t(item.labelKey)">
                  <mat-icon matListItemIcon>{{ item.icon }}</mat-icon>
                  <span matListItemTitle class="nav-label">
                    {{ i18n.t(item.labelKey) }}
                    @if (unreadTicketCount() > 0) {
                      <span class="feed-badge">{{ unreadTicketCount() > 99 ? '99+' : unreadTicketCount() }}</span>
                    }
                  </span>
                </a>
              } @else {
                <a mat-list-item [routerLink]="item.path" routerLinkActive="active" [title]="i18n.t(item.labelKey)">
                  <mat-icon matListItemIcon>{{ item.icon }}</mat-icon>
                  <span matListItemTitle class="nav-label">{{ i18n.t(item.labelKey) }}</span>
                </a>
              }
            }
          </mat-nav-list>
          <div class="sidenav-footer">
            <span class="role-chip">{{ auth.userRole() }}</span>
            <button mat-icon-button (click)="auth.logout()" [title]="i18n.t('app.nav.logout')">
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
    { path: '/dashboard',    labelKey: 'app.nav.dashboard',  icon: 'dashboard',    roles: ['admin','sysadmin','network_technician','viewer'] },
    { path: '/bridge',       labelKey: 'app.nav.bridge',     icon: 'rocket_launch',roles: ['admin','sysadmin','network_technician','viewer'] },
    { path: '/feed',         labelKey: 'app.nav.feed',       icon: 'feed',         roles: ['admin','sysadmin','network_technician'] },
    { path: '/alerts',       labelKey: 'app.nav.alerts',     icon: 'notifications',roles: ['admin','sysadmin'] },
    { path: '/my-tickets',   labelKey: 'app.nav.myTickets',  icon: 'assignment',   roles: ['admin','sysadmin'] },
    { path: '/kanban',       labelKey: 'app.nav.kanban',     icon: 'view_kanban',  roles: ['admin','sysadmin','network_technician'] },
    { path: '/ai-insights',  labelKey: 'app.nav.aiInsights', icon: 'psychology',   roles: ['admin','sysadmin'] },
    { path: '/settings',     labelKey: 'app.nav.settings',   icon: 'settings',     roles: ['admin','sysadmin','network_technician','viewer'] },
    { path: '/help',         labelKey: 'app.nav.help',       icon: 'help',         roles: ['admin','sysadmin','network_technician','viewer'] },
  ];

  unreadFeedCount = signal<number>(0);
  unreadTicketCount = signal<number>(0);
  navCollapsed = signal<boolean>(localStorage.getItem('cs_nav_collapsed') === '1');
  private badgeInterval: ReturnType<typeof setInterval> | null = null;
  private http = inject(HttpClient);
  private themeService = inject(ThemeService);
  readonly i18n = inject(I18nService);

  visibleNavItems = computed(() => {
    const role = this.auth.userRole();
    return this.navItems.filter(i => role && i.roles.includes(role));
  });

  constructor(public auth: AuthService, private ws: WebsocketService) {
    // Apply the locally-stored theme immediately (before login / first paint)
    this.themeService.initFromStorage();
    this.i18n.initFromStorage();
    effect(() => {
      if (this.auth.isLoggedIn()) {
        this.auth.fetchMe();
        this.themeService.loadFromPreference();
        this.i18n.loadFromPreference();
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

  toggleNav() {
    this.navCollapsed.update(v => {
      const next = !v;
      localStorage.setItem('cs_nav_collapsed', next ? '1' : '0');
      return next;
    });
  }
}
