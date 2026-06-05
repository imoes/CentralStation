import { Routes } from '@angular/router';
import { authGuard, roleGuard } from './core/auth/auth.guard';

export const routes: Routes = [
  { path: '', redirectTo: 'dashboard', pathMatch: 'full' },
  {
    path: 'login',
    loadComponent: () => import('./features/auth/login/login.component').then(m => m.LoginComponent),
  },
  {
    path: 'dashboard',
    canActivate: [authGuard],
    loadComponent: () => import('./features/dashboard/dashboard.component').then(m => m.DashboardComponent),
  },
  {
    path: 'bridge',
    canActivate: [authGuard],
    loadComponent: () => import('./features/bridge/bridge.component').then(m => m.BridgeComponent),
  },
  {
    path: 'alerts',
    canActivate: [authGuard, roleGuard('admin', 'sysadmin')],
    loadComponent: () => import('./features/alerts/alerts.component').then(m => m.AlertsComponent),
  },
  {
    path: 'kanban',
    canActivate: [authGuard, roleGuard('admin', 'sysadmin', 'network_technician')],
    loadComponent: () => import('./features/kanban/kanban.component').then(m => m.KanbanComponent),
  },
  {
    path: 'network',
    canActivate: [authGuard, roleGuard('admin', 'network_technician')],
    loadComponent: () => import('./features/network/network.component').then(m => m.NetworkComponent),
  },
  {
    path: 'ai-insights',
    canActivate: [authGuard, roleGuard('admin', 'sysadmin')],
    loadComponent: () => import('./features/ai-insights/ai-insights.component').then(m => m.AiInsightsComponent),
  },
  {
    path: 'settings',
    canActivate: [authGuard],
    loadComponent: () => import('./features/settings/settings-shell.component').then(m => m.SettingsShellComponent),
    children: [
      { path: '', redirectTo: 'connectors', pathMatch: 'full' },
      {
        path: 'connectors',
        canActivate: [authGuard],
        loadComponent: () => import('./features/settings/connectors/connectors.component').then(m => m.ConnectorsComponent),
      },
      {
        path: 'my',
        canActivate: [authGuard],
        loadComponent: () => import('./features/settings/my-settings/my-settings.component').then(m => m.MySettingsComponent),
      },
      {
        path: 'users',
        canActivate: [authGuard, roleGuard('admin')],
        loadComponent: () => import('./features/settings/users/users.component').then(m => m.UsersComponent),
      },
      {
        path: 'ai',
        canActivate: [authGuard, roleGuard('admin')],
        loadComponent: () => import('./features/settings/ai/ai-settings.component').then(m => m.AiSettingsComponent),
      },
      {
        path: 'audit',
        canActivate: [authGuard, roleGuard('admin')],
        loadComponent: () => import('./features/settings/audit/audit-log.component').then(m => m.AuditLogComponent),
      },
      {
        path: 'feed',
        canActivate: [authGuard, roleGuard('admin')],
        loadComponent: () => import('./features/settings/feed/feed-settings.component').then(m => m.FeedSettingsComponent),
      },
    ],
  },
  {
    path: 'setup',
    canActivate: [authGuard],
    loadComponent: () => import('./features/setup-wizard/setup-wizard.component').then(m => m.SetupWizardComponent),
  },
  {
    path: 'my-tickets',
    canActivate: [authGuard, roleGuard('admin', 'sysadmin')],
    loadComponent: () => import('./features/my-tickets/my-tickets.component').then(m => m.MyTicketsComponent),
  },
  {
    path: 'feed',
    canActivate: [authGuard, roleGuard('admin', 'sysadmin', 'network_technician')],
    loadComponent: () => import('./features/news-feed/news-feed.component').then(m => m.NewsFeedComponent),
  },
  {
    path: 'help',
    canActivate: [authGuard],
    loadComponent: () => import('./features/help/help.component').then(m => m.HelpComponent),
  },
  {
    path: 'cockpit/:hostname',
    canActivate: [authGuard],
    loadComponent: () => import('./features/cockpit/cockpit.component').then(m => m.CockpitComponent),
  },
  { path: '**', redirectTo: 'dashboard' },
];
