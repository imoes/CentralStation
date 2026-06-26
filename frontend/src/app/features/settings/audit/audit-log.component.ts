import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient, HttpParams } from '@angular/common/http';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';
import { environment } from '../../../../environments/environment';
import { I18nService } from '../../../core/services/i18n.service';

@Component({
  selector: 'cs-audit-log',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatCardModule, MatButtonModule, MatIconModule,
    MatFormFieldModule, MatInputModule, MatSelectModule,
    MatProgressSpinnerModule, MatChipsModule,
  ],
  template: `
    <div class="page-container">
      <div class="page-header">
        <h2>Audit Log</h2>
        <div class="header-actions">
          <mat-form-field appearance="outline" class="filter-field">
            <mat-label>{{ i18n.t('settings.audit.action_label') }}</mat-label>
            <input matInput [(ngModel)]="filterAction" (ngModelChange)="load()">
          </mat-form-field>
          <mat-form-field appearance="outline" class="filter-field">
            <mat-label>{{ i18n.t('settings.audit.resource_label') }}</mat-label>
            <mat-select [(ngModel)]="filterResource" (selectionChange)="load()">
              <mat-option value="">{{ i18n.t('common.all') }}</mat-option>
              <mat-option value="user">{{ i18n.t('common.user') }}</mat-option>
              <mat-option value="connector">Connector</mat-option>
              <mat-option value="setting">Setting</mat-option>
              <mat-option value="alert">Alert</mat-option>
              <mat-option value="network_event">Network</mat-option>
            </mat-select>
          </mat-form-field>
        </div>
      </div>

      @if (loading()) {
        <div class="spinner-center"><mat-spinner diameter="40"></mat-spinner></div>
      } @else {
        <mat-card>
          <div class="log-list">
            @for (entry of logs(); track entry.id) {
              <div class="log-entry">
                <span class="log-time">{{ entry.timestamp | date:'dd.MM.yyyy HH:mm:ss' }}</span>
                <mat-chip class="action-chip" [class.action-delete]="entry.action.includes('delet')" [class.action-create]="entry.action.includes('creat')">
                  {{ entry.action }}
                </mat-chip>
                @if (entry.resource_type) {
                  <mat-chip class="resource-chip">{{ entry.resource_type }}</mat-chip>
                }
                @if (entry.resource_id) {
                  <span class="resource-id">{{ entry.resource_id }}</span>
                }
                @if (entry.user_id) {
                  <span class="user-id">user:{{ entry.user_id | slice:0:8 }}…</span>
                }
              </div>
            }
            @if (logs().length === 0) {
              <div class="empty-state">Keine Einträge gefunden.</div>
            }
          </div>
        </mat-card>
      }
    </div>
  `,
  styles: [`
    .page-container { padding: 24px; max-width: 1000px; }
    .page-header { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 16px; flex-wrap: wrap; gap: 8px; }
    .page-header h2 { margin: 0; }
    .header-actions { display: flex; gap: 8px; flex-wrap: wrap; }
    .filter-field { width: 180px; }
    .log-list { display: flex; flex-direction: column; gap: 2px; }
    .log-entry { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-bottom: 1px solid var(--mat-sys-outline-variant); font-size: 12px; flex-wrap: wrap; }
    .log-time { color: var(--mat-sys-on-surface-variant); min-width: 140px; font-family: monospace; }
    mat-chip { font-size: 10px; min-height: 18px; }
    .action-delete { background: #ffebee; color: #c62828; }
    .action-create { background: #e8f5e9; color: #2e7d32; }
    .resource-chip { background: #e3f2fd; }
    .resource-id { font-family: monospace; color: var(--mat-sys-primary); }
    .user-id { font-family: monospace; font-size: 11px; color: var(--mat-sys-on-surface-variant); }
    .empty-state { text-align: center; padding: 32px; color: var(--mat-sys-on-surface-variant); }
    .spinner-center { display: flex; justify-content: center; padding: 40px; }
  `],
})
export class AuditLogComponent implements OnInit {
  readonly i18n = inject(I18nService);
  logs = signal<any[]>([]);
  loading = signal(true);
  filterAction = '';
  filterResource = '';

  constructor(private http: HttpClient) {}

  ngOnInit() { this.load(); }

  load() {
    this.loading.set(true);
    let p = new HttpParams().set('limit', '200');
    if (this.filterAction) p = p.set('action', this.filterAction);
    if (this.filterResource) p = p.set('resource_type', this.filterResource);

    this.http.get<any[]>(`${environment.apiUrl}/audit/`, { params: p }).subscribe({
      next: data => { this.logs.set(data); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }
}
