import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatDialogModule, MatDialog } from '@angular/material/dialog';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatTooltipModule } from '@angular/material/tooltip';
import { ProjectsService, ProjectResponse } from '../../core/services/projects.service';
import { I18nService } from '../../core/services/i18n.service';

@Component({
  selector: 'cs-projects',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatButtonModule, MatIconModule, MatInputModule,
    MatProgressSpinnerModule, MatDialogModule, MatSnackBarModule, MatTooltipModule,
  ],
  template: `
    <div class="projects-container">
      <div class="projects-header lcars-header">
        <div class="header-elbow"></div>
        <div class="header-title">{{ i18n.t('app.nav.projects') }}</div>
        <div class="header-actions">
          <button mat-raised-button (click)="openPlanner()">
            <mat-icon>auto_awesome</mat-icon>
            {{ i18n.t('projects.new_plan') }}
          </button>
        </div>
      </div>

      <div class="projects-body">
        @if (loading()) {
          <div class="spinner-center"><mat-spinner diameter="40"></mat-spinner></div>
        } @else {
          <div class="search-bar">
            <mat-icon>search</mat-icon>
            <input
              [(ngModel)]="searchQuery"
              (ngModelChange)="onSearch()"
              [placeholder]="i18n.t('projects.search_placeholder')"
              class="search-input"
            />
          </div>

          @if (projects().length === 0) {
            <div class="empty-state">
              <mat-icon>folder_open</mat-icon>
              <p>{{ i18n.t('projects.empty') }}</p>
              <button mat-raised-button (click)="openPlanner()">
                {{ i18n.t('projects.start_planning') }}
              </button>
            </div>
          } @else {
            <div class="project-grid">
              @for (p of projects(); track p.id) {
                <div class="project-card lcars-card" (click)="openDetail(p.id)">
                  <div class="card-status-bar" [style.background]="statusColor(p.status)"></div>
                  <div class="card-body">
                    <h3 class="card-title">{{ p.name }}</h3>
                    @if (p.description) {
                      <p class="card-desc">{{ p.description }}</p>
                    }
                    <div class="card-meta">
                      <span class="status-chip" [style.color]="statusColor(p.status)">
                        {{ i18n.t('projects.status.' + p.status) }}
                      </span>
                      <span class="updated-at">{{ p.updated_at | date:'dd.MM.yy HH:mm' }}</span>
                    </div>
                  </div>
                  <div class="card-actions" (click)="$event.stopPropagation()">
                    <button mat-icon-button [matTooltip]="i18n.t('projects.open_workbench')" (click)="openWorkbench(p.id)">
                      <mat-icon>code</mat-icon>
                    </button>
                    <button mat-icon-button [matTooltip]="i18n.t('projects.delete')" (click)="deleteProject(p)">
                      <mat-icon>delete</mat-icon>
                    </button>
                  </div>
                </div>
              }
            </div>
          }
        }
      </div>
    </div>
  `,
  styles: [`
    .projects-container { display: flex; flex-direction: column; height: 100%; background: var(--cs-bg); }

    .lcars-header { display: flex; align-items: center; gap: 0; padding: 0; background: transparent; }
    .header-elbow {
      width: 32px; height: 56px;
      border-top-left-radius: 24px;
      background: var(--cs-accent, #FFCC99);
      flex-shrink: 0;
    }
    .header-title {
      padding: 0 24px;
      font-size: 1.4rem; font-weight: 700; letter-spacing: 0.05em;
      color: var(--cs-accent, #FFCC99);
      flex: 1;
    }
    .header-actions { padding-right: 16px; }

    .projects-body { flex: 1; overflow-y: auto; padding: 24px; }
    .spinner-center { display: flex; justify-content: center; padding: 60px 0; }

    .search-bar {
      display: flex; align-items: center; gap: 8px;
      background: var(--cs-surface, #1a1a2e);
      border: 1px solid var(--cs-border, #333);
      border-radius: 4px;
      padding: 8px 12px;
      margin-bottom: 24px;
    }
    .search-input { background: transparent; border: none; outline: none; color: var(--cs-text); flex: 1; font-size: 1rem; }
    mat-icon { color: var(--cs-text-muted, #888); }

    .empty-state {
      display: flex; flex-direction: column; align-items: center; gap: 16px;
      padding: 80px 0; color: var(--cs-text-muted);
    }
    .empty-state mat-icon { font-size: 48px; width: 48px; height: 48px; }

    .project-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 16px;
    }

    .lcars-card {
      display: flex; flex-direction: column;
      background: var(--cs-surface, #1a1a2e);
      border: 1px solid var(--cs-border, #333);
      border-radius: 6px; overflow: hidden;
      cursor: pointer; transition: border-color 0.15s;
    }
    .lcars-card:hover { border-color: var(--cs-accent, #FFCC99); }
    .card-status-bar { height: 4px; }
    .card-body { padding: 16px; flex: 1; }
    .card-title { margin: 0 0 8px; font-size: 1.05rem; font-weight: 600; color: var(--cs-text); }
    .card-desc { margin: 0 0 12px; font-size: 0.85rem; color: var(--cs-text-muted); line-height: 1.4; }
    .card-meta { display: flex; align-items: center; justify-content: space-between; font-size: 0.8rem; }
    .status-chip { font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
    .updated-at { color: var(--cs-text-muted); }
    .card-actions { display: flex; justify-content: flex-end; padding: 4px 8px; border-top: 1px solid var(--cs-border, #333); }
  `],
})
export class ProjectsComponent implements OnInit {
  private svc = inject(ProjectsService);
  private router = inject(Router);
  private snack = inject(MatSnackBar);
  i18n = inject(I18nService);

  projects = signal<ProjectResponse[]>([]);
  loading = signal(true);
  searchQuery = '';
  private searchTimer: ReturnType<typeof setTimeout> | null = null;

  ngOnInit() { this.load(); }

  load(search = '') {
    this.loading.set(true);
    this.svc.list(search).subscribe({
      next: ps => { this.projects.set(ps); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }

  onSearch() {
    if (this.searchTimer) clearTimeout(this.searchTimer);
    this.searchTimer = setTimeout(() => this.load(this.searchQuery), 300);
  }

  openDetail(id: string) { this.router.navigate(['/projects', id]); }

  openPlanner() { this.router.navigate(['/projects/planner']); }

  openWorkbench(id: string) {
    this.svc.openInWorkbench(id).subscribe({
      next: r => window.open(r.ide_url, '_blank'),
      error: () => this.snack.open('Werkbank konnte nicht geöffnet werden', 'OK', { duration: 3000 }),
    });
  }

  deleteProject(p: ProjectResponse) {
    if (!confirm(`Projekt "${p.name}" wirklich löschen?`)) return;
    this.svc.delete(p.id).subscribe({ next: () => this.load(this.searchQuery) });
  }

  statusColor(status: string): string {
    return { planning: '#99CCFF', active: '#FFCC66', done: '#90EE90', archived: '#888' }[status] ?? '#888';
  }
}
