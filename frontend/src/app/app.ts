import { Component, computed, effect, inject, OnDestroy, OnInit, signal, ViewChild } from '@angular/core';
import { Router, RouterOutlet, RouterLink, RouterLinkActive, NavigationEnd } from '@angular/router';
import { filter } from 'rxjs/operators';
import { Subscription } from 'rxjs';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { AuthService } from './core/auth/auth.service';
import { WebsocketService } from './core/services/websocket.service';
import { ThemeService } from './core/services/theme.service';
import { environment } from '../environments/environment';
import { ComputerComponent } from './features/computer/computer.component';

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
    MatIconModule, MatButtonModule,
    ComputerComponent,
  ],
  template: `
    @if (auth.isLoggedIn()) {
      <div class="cs-shell"
           [class.t-classic]="theme()==='classic'"
           [class.t-lcars]="theme()==='lcars'"
           [class.t-holo]="theme()==='holo'">

        <!-- Bridge & Problemboard sind position:fixed-Fullscreen mit eigener Nav -->
        @if (!fullscreenRoute()) {
          <nav class="cs-nav-sidebar" aria-label="Hauptnavigation">
            <div class="cs-nav-head">
              <mat-icon class="cs-brand-icon">hub</mat-icon>
              <span class="cs-brand">CentralStation</span>
            </div>
            @for (item of visibleNavItems(); track item.path) {
              <a class="cs-nav-item" [routerLink]="item.path" routerLinkActive="cs-nav-item-active"
                 [title]="item.label">
                <mat-icon class="cs-nav-icon">{{ item.icon }}</mat-icon>
                <span class="cs-nav-label">{{ item.label }}</span>
                @if (item.path === '/feed' && unreadFeedCount() > 0) {
                  <span class="cs-badge">{{ unreadFeedCount() > 99 ? '99+' : unreadFeedCount() }}</span>
                }
                @if (item.path === '/my-tickets' && unreadTicketCount() > 0) {
                  <span class="cs-badge">{{ unreadTicketCount() > 99 ? '99+' : unreadTicketCount() }}</span>
                }
              </a>
            }
            <div class="cs-nav-footer">
              <span class="cs-role-chip">{{ auth.userRole() }}</span>
              @if (computerEnabled()) {
                <button mat-icon-button (click)="computer?.toggle()" title="Computer Console (Ctrl+K)">
                  <mat-icon>smart_toy</mat-icon>
                </button>
              }
              <button mat-icon-button (click)="auth.logout()" title="Abmelden">
                <mat-icon>logout</mat-icon>
              </button>
            </div>
          </nav>
        }

        <div class="cs-main" [class.cs-main--full]="fullscreenRoute()">
          <router-outlet></router-outlet>
        </div>

        @if (computerEnabled()) {
          <app-computer #computer></app-computer>
        }
      </div>
    } @else {
      <router-outlet></router-outlet>
    }
  `,
  styleUrl: './app.scss',
})
export class App implements OnInit, OnDestroy {
  @ViewChild('computer') computer?: ComputerComponent;

  computerEnabled = computed(() => this.auth.user()?.computer_console_enabled ?? false);

  private readonly navItems: NavItem[] = [
    { path: '/dashboard',    label: 'Dashboard',        icon: 'dashboard',    roles: ['admin','sysadmin','network_technician','viewer'] },
    { path: '/bridge',       label: 'Bridge',           icon: 'rocket_launch',roles: ['admin','sysadmin','network_technician','viewer'] },
    { path: '/feed',         label: 'News Feed',        icon: 'feed',         roles: ['admin','sysadmin','network_technician'] },
    { path: '/problems',    label: 'Problems',         icon: 'report_problem', roles: ['admin','sysadmin','network_technician'] },
    { path: '/alerts',       label: 'Alerts',           icon: 'notifications',roles: ['admin'] },
    { path: '/my-tickets',   label: 'My Tickets',       icon: 'assignment',   roles: ['admin','sysadmin'] },
    { path: '/kanban',       label: 'Kanban',           icon: 'view_kanban',  roles: ['admin','sysadmin','network_technician'] },
    { path: '/ai-insights',  label: 'AI Insights',      icon: 'psychology',   roles: ['admin','sysadmin'] },
    { path: '/settings',     label: 'Settings',         icon: 'settings',     roles: ['admin','sysadmin','network_technician','viewer'] },
    { path: '/help',         label: 'Help',             icon: 'help',         roles: ['admin','sysadmin','network_technician','viewer'] },
  ];

  unreadFeedCount = signal<number>(0);
  unreadTicketCount = signal<number>(0);
  // Bridge & Problemboard are position:fixed overlays with their own nav — hide app hamburger there.
  private static readonly FULLSCREEN_ROUTES = ['/bridge', '/problems'];
  fullscreenRoute = signal<boolean>(App.isFullscreen(location.pathname));
  private routerSub?: Subscription;
  private badgeInterval: ReturnType<typeof setInterval> | null = null;
  private http = inject(HttpClient);
  private themeService = inject(ThemeService);
  theme = this.themeService.theme;
  private router = inject(Router);
  private _cockpitMsgHandler = (e: MessageEvent) => {
    if (e.origin !== window.location.origin) return;
    if (e.data?.type !== 'cockpit:focus-alert') return;
    const { id, host } = e.data as { id: string; host: string };
    this.router.navigate(['/feed'], { queryParams: { host, highlight: id } });
  };

  visibleNavItems = computed(() => {
    const role = this.auth.userRole();
    return this.navItems.filter(i => role && i.roles.includes(role));
  });

  constructor(public auth: AuthService, private ws: WebsocketService) {
    // Apply the locally-stored theme immediately (before login / first paint)
    this.themeService.initFromStorage();
    effect(() => {
      if (this.auth.isLoggedIn()) {
        this.auth.fetchMe();
        this.themeService.loadFromPreference();
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

  private static isFullscreen(url: string): boolean {
    const path = (url.split('?')[0] || '').replace(/\/+$/, '');
    return App.FULLSCREEN_ROUTES.some(r => path === r || path.startsWith(r + '/'));
  }

  ngOnInit() {
    window.addEventListener('message', this._cockpitMsgHandler);
    this.routerSub = this.router.events
      .pipe(filter((e): e is NavigationEnd => e instanceof NavigationEnd))
      .subscribe(e => {
        this.fullscreenRoute.set(App.isFullscreen(e.urlAfterRedirects));
      });
  }

  ngOnDestroy() {
    if (this.badgeInterval) clearInterval(this.badgeInterval);
    this.routerSub?.unsubscribe();
    window.removeEventListener('message', this._cockpitMsgHandler);
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
}
