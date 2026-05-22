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
    canActivate: [authGuard, roleGuard('admin')],
    children: [
      { path: '', redirectTo: 'connectors', pathMatch: 'full' },
      {
        path: 'connectors',
        loadComponent: () => import('./features/settings/connectors/connectors.component').then(m => m.ConnectorsComponent),
      },
      {
        path: 'users',
        loadComponent: () => import('./features/settings/users/users.component').then(m => m.UsersComponent),
      },
      {
        path: 'ai',
        loadComponent: () => import('./features/settings/ai/ai-settings.component').then(m => m.AiSettingsComponent),
      },
      {
        path: 'audit',
        loadComponent: () => import('./features/settings/audit/audit-log.component').then(m => m.AuditLogComponent),
      },
    ],
  },
  { path: '**', redirectTo: 'dashboard' },
];
