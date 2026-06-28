import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatTooltipModule } from '@angular/material/tooltip';
import { ProjectsService, ProjectResponse } from '../../core/services/projects.service';
import { I18nService } from '../../core/services/i18n.service';
import { ThemeService } from '../../core/services/theme.service';

const STATUS_FILTERS = ['all', 'planning', 'active', 'done', 'archived'] as const;
type StatusFilter = typeof STATUS_FILTERS[number];

@Component({
  selector: 'cs-projects',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatButtonModule, MatIconModule, MatSnackBarModule, MatTooltipModule,
  ],
  template: `
    <div class="pv" [class.t-lcars]="theme()==='lcars'" [class.t-holo]="theme()==='holo'" [class.t-classic]="theme()==='classic'">

      <!-- ══ Top sweep ══ -->
      <div class="topbar">
        <div class="cap cap-tl"></div>
        <div class="bar-seg seg-a">{{ i18n.t('app.nav.projects') }}</div>
        <div class="bar-seg seg-b">{{ projects().length }} {{ i18n.t('projects.count') }}</div>
        <div class="topbar-fill"></div>
        <button class="sweep-action" (click)="openPlanner()">
          <mat-icon>auto_awesome</mat-icon> {{ i18n.t('projects.new_plan') }}
        </button>
        <div class="cap cap-tr"></div>
      </div>

      <div class="cols">
        <!-- ══ Left pill rail (filters) ══ -->
        <aside class="rail">
          <div class="rail-label">{{ i18n.t('projects.filter') }}</div>
          @for (f of filters; track f) {
            <button class="rail-pill" [class.active]="statusFilter() === f"
                    [attr.data-f]="f" (click)="statusFilter.set(f)">
              <span class="rp-name">{{ filterLabel(f) }}</span>
              <span class="rp-val">{{ countFor(f) }}</span>
            </button>
          }
          <div class="rail-fill"></div>
        </aside>

        <!-- ══ Main ══ -->
        <main class="main">
          <div class="search-bar">
            <mat-icon>search</mat-icon>
            <input [(ngModel)]="searchQuery" (ngModelChange)="onSearch()"
                   [placeholder]="i18n.t('projects.search_placeholder')" class="search-input" />
          </div>

          @if (loading()) {
            <div class="empty"><mat-icon>hourglass_empty</mat-icon></div>
          } @else if (filtered().length === 0) {
            <div class="empty-state">
              <mat-icon>folder_open</mat-icon>
              <p>{{ i18n.t('projects.empty') }}</p>
              <button class="sweep-action solo" (click)="openPlanner()">{{ i18n.t('projects.start_planning') }}</button>
            </div>
          } @else {
            <div class="grid">
              @for (p of filtered(); track p.id) {
                <div class="panel" [attr.data-status]="p.status" (click)="openDetail(p.id)">
                  <div class="panel-body">
                    <h3 class="panel-title">{{ p.name }}</h3>
                    @if (p.description) { <p class="panel-desc">{{ p.description }}</p> }
                    <div class="panel-meta">
                      <span class="status-chip" [attr.data-status]="p.status">{{ i18n.t('projects.status.' + p.status) }}</span>
                      <span class="updated">{{ p.updated_at | date:'dd.MM.yy HH:mm' }}</span>
                    </div>
                  </div>
                  <div class="panel-actions" (click)="$event.stopPropagation()">
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
        </main>
      </div>

      <!-- ══ Bottom sweep ══ -->
      <div class="botbar">
        <div class="cap cap-bl"></div>
        <div class="bar-seg seg-foot">{{ i18n.t('projects.footer') }}</div>
        <div class="botbar-fill"></div>
        <div class="cap cap-br"></div>
      </div>
    </div>
  `,
  styles: [`
    .pv { display:flex; flex-direction:column; height:100%; min-height:0; box-sizing:border-box;
          font-family:Roboto,'Helvetica Neue',sans-serif; }

    /* ── shared structural geometry (theme-agnostic) ── */
    .topbar, .botbar { display:flex; align-items:center; gap:6px; flex-shrink:0; height:46px; padding:6px 6px 0; }
    .botbar { height:42px; padding:0 6px 6px; }
    .cap { width:60px; height:100%; flex-shrink:0; }
    .bar-seg { height:100%; display:flex; align-items:center; padding:0 18px; font-weight:800;
               letter-spacing:.14em; font-size:13px; text-transform:uppercase;
               font-family:'Antonio','Eurostile',sans-serif; }
    .topbar-fill, .botbar-fill { flex:1; height:100%; }
    .sweep-action { border:none; cursor:pointer; font-family:'Antonio','Eurostile',sans-serif;
                    font-weight:800; letter-spacing:.1em; font-size:13px; text-transform:uppercase;
                    height:100%; padding:0 20px; display:flex; align-items:center; gap:8px; flex-shrink:0; }
    .sweep-action mat-icon { font-size:18px; width:18px; height:18px; }

    .cols { flex:1; display:grid; grid-template-columns:210px 1fr; gap:6px; min-height:0; padding:0 6px; }
    .rail { display:flex; flex-direction:column; gap:6px; min-height:0; overflow-y:auto; padding:4px 0; }
    .rail-label { font-size:10px; font-weight:800; letter-spacing:.2em; padding:6px 14px 2px;
                  text-transform:uppercase; font-family:'Antonio','Eurostile',sans-serif; }
    .rail-pill { display:flex; align-items:center; justify-content:space-between; gap:8px; border:none;
                 cursor:pointer; font-family:'Antonio','Eurostile',sans-serif; height:38px; padding:0 16px;
                 font-weight:700; font-size:13px; letter-spacing:.06em; text-transform:uppercase; flex-shrink:0; }
    .rail-pill.active { outline:2px solid currentColor; outline-offset:-3px; font-weight:900; }
    .rp-val { font-variant-numeric:tabular-nums; opacity:.85; }
    .rail-fill { flex:1; min-height:8px; }

    .main { display:flex; flex-direction:column; min-height:0; overflow-y:auto; padding:4px 2px 8px; }
    .search-bar { display:flex; align-items:center; gap:8px; padding:8px 14px; margin-bottom:14px; flex-shrink:0; }
    .search-input { background:transparent; border:none; outline:none; flex:1; font-size:1rem; font-family:Roboto,sans-serif; }
    .search-bar mat-icon { font-size:20px; width:20px; height:20px; }

    .empty, .empty-state { display:flex; flex-direction:column; align-items:center; gap:14px; padding:60px 0; }
    .empty-state mat-icon, .empty mat-icon { font-size:46px; width:46px; height:46px; opacity:.5; }

    .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(310px, 1fr)); gap:14px; padding:2px; }
    .panel { display:flex; cursor:pointer; overflow:hidden; transition:filter .15s, transform .1s; }
    .panel:hover { filter:brightness(1.08); }
    .panel-body { flex:1; padding:14px 16px; min-width:0; }
    .panel-title { margin:0 0 8px; font-size:1.05rem; font-weight:700; letter-spacing:.04em;
                   font-family:'Antonio','Eurostile',sans-serif; text-transform:uppercase; }
    .panel-desc { margin:0 0 12px; font-size:0.85rem; line-height:1.45; opacity:.8;
                  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
    .panel-meta { display:flex; align-items:center; justify-content:space-between; font-size:0.78rem; }
    .status-chip { font-weight:800; text-transform:uppercase; letter-spacing:.06em; padding:2px 9px; border-radius:10px; font-size:.7rem; }
    .updated { opacity:.6; font-variant-numeric:tabular-nums; }
    .panel-actions { display:flex; flex-direction:column; justify-content:center; padding:0 4px; }

    /* ════════════ CLASSIC ════════════ */
    .t-classic { background:#f4f6f9; color:#1f2933; }
    .t-classic .cap { display:none; }
    .t-classic .topbar, .t-classic .botbar { padding:8px 12px; }
    .t-classic .seg-a { background:#1565c0; color:#fff; border-radius:14px; }
    .t-classic .seg-b { background:#fff; color:#1565c0; border:1px solid #cdd9e5; border-radius:14px; }
    .t-classic .seg-foot { color:#90a4b8; }
    .t-classic .sweep-action { background:#1565c0; color:#fff; border-radius:14px; }
    .t-classic .rail-label { color:#5b6b7b; }
    .t-classic .rail-pill { background:#fff; color:#1f2933; border:1px solid #d7e0ea; border-radius:14px; }
    .t-classic .rail-pill.active { background:#1565c0; color:#fff; border-color:#1565c0; }
    .t-classic .search-bar { background:#fff; border:1px solid #d7e0ea; border-radius:8px; }
    .t-classic .panel { background:#fff; border:1px solid #dde6ef; border-left:5px solid #90a4b8; border-radius:0 10px 10px 0; box-shadow:0 1px 4px rgba(0,0,0,.06); }
    .t-classic .panel[data-status="active"] { border-left-color:#ef6c00; }
    .t-classic .panel[data-status="planning"] { border-left-color:#1565c0; }
    .t-classic .panel[data-status="done"] { border-left-color:#2e7d32; }
    .t-classic .status-chip { background:#eef4fb; color:#1565c0; }
    .t-classic .panel-actions { color:#5b6b7b; }

    /* ════════════ HOLO ════════════ */
    .t-holo { color:#cfeeff; background:radial-gradient(circle at 50% 12%,rgba(20,60,90,.4),transparent 40rem),linear-gradient(160deg,#02060f,#050d1a 60%,#02060f); }
    .t-holo .cap { display:none; }
    .t-holo .seg-a { background:rgba(79,214,255,.14); color:#9fe8ff; border:1px solid rgba(79,214,255,.35); border-radius:8px; }
    .t-holo .seg-b { background:rgba(79,214,255,.06); color:#9fe8ff; border:1px solid rgba(79,214,255,.2); border-radius:8px; }
    .t-holo .seg-foot { color:#5fc8ee; }
    .t-holo .sweep-action { background:rgba(79,214,255,.15); color:#9fe8ff; border:1px solid #4fd6ff; border-radius:8px; }
    .t-holo .rail-label { color:#5fc8ee; }
    .t-holo .rail-pill { background:rgba(10,28,46,.6); color:#bfefff; border:1px solid rgba(79,214,255,.25); border-radius:8px; }
    .t-holo .rail-pill.active { border-color:#4fd6ff; background:rgba(79,214,255,.18); color:#cff6ff; }
    .t-holo .search-bar { background:rgba(10,28,46,.6); border:1px solid rgba(79,214,255,.25); border-radius:8px; }
    .t-holo .search-input { color:#cfeeff; }
    .t-holo .panel { background:rgba(10,28,46,.55); border:1px solid rgba(79,214,255,.22); border-left:4px solid #4fd6ff; border-radius:8px; }
    .t-holo .panel[data-status="active"] { border-left-color:#ffd84a; }
    .t-holo .panel[data-status="done"] { border-left-color:#66cc8a; }
    .t-holo .status-chip { background:rgba(79,214,255,.16); color:#9fe8ff; }
    .t-holo .panel-actions { color:#8fb8cf; }

    /* ════════════ LCARS (authentic TNG) ════════════ */
    .t-lcars { background:#000; color:#FF9933; }
    .t-lcars .cap { background:#FF9933; }
    .t-lcars .cap-tl { border-radius:46px 0 0 0; }
    .t-lcars .cap-tr { border-radius:0 46px 0 0; width:34px; }
    .t-lcars .cap-bl { border-radius:0 0 0 46px; }
    .t-lcars .cap-br { border-radius:0 0 46px 0; width:34px; }
    .t-lcars .seg-a { background:#ffcc66; color:#000; min-width:170px; }
    .t-lcars .seg-b { background:#99CCFF; color:#000; }
    .t-lcars .topbar-fill, .t-lcars .botbar-fill { background:#FF9933; }
    .t-lcars .seg-foot { background:#ffcc66; color:#000; min-width:200px; }
    .t-lcars .sweep-action { background:#FF9933; color:#000; }
    .t-lcars .rail-label { color:#ffcc66; }
    .t-lcars .rail-pill { background:#FF9933; color:#000; border-radius:0 18px 18px 0; }
    .t-lcars .rail-pill:nth-child(3n) { background:#99CCFF; }
    .t-lcars .rail-pill:nth-child(3n+1) { background:#ffcc66; }
    .t-lcars .rail-pill.active { outline-color:#fff; filter:brightness(1.1); }
    .t-lcars .search-bar { background:#15120c; border-radius:0 18px 18px 0; }
    .t-lcars .search-bar mat-icon { color:#FF9933; }
    .t-lcars .search-input { color:#ffe8a0; }
    .t-lcars .panel { background:#15120c; border-left:7px solid #FF9933; border-radius:0 8px 8px 0; }
    .t-lcars .panel[data-status="active"] { border-left-color:#ffcc66; }
    .t-lcars .panel[data-status="planning"] { border-left-color:#99CCFF; }
    .t-lcars .panel[data-status="done"] { border-left-color:#66cc66; }
    .t-lcars .panel[data-status="archived"] { border-left-color:#5a3a18; }
    .t-lcars .panel-title { color:#FF9933; }
    .t-lcars .panel-desc { color:#ffcc99; text-transform:none; font-family:Roboto,'Helvetica Neue',sans-serif; }
    .t-lcars .status-chip { background:#FF9933; color:#000; }
    .t-lcars .panel[data-status="active"] .status-chip { background:#ffcc66; }
    .t-lcars .panel[data-status="planning"] .status-chip { background:#99CCFF; }
    .t-lcars .panel[data-status="done"] .status-chip { background:#66cc66; }
    .t-lcars .updated { color:#e8a060; }
    .t-lcars .panel-actions { color:#ffcc66; }
    .t-lcars .empty-state, .t-lcars .empty { color:#e8a060; }
  `],
})
export class ProjectsComponent implements OnInit {
  private svc = inject(ProjectsService);
  private router = inject(Router);
  private snack = inject(MatSnackBar);
  i18n = inject(I18nService);
  private themeSvc = inject(ThemeService);
  theme = this.themeSvc.theme;

  readonly filters = STATUS_FILTERS;
  projects = signal<ProjectResponse[]>([]);
  loading = signal(true);
  statusFilter = signal<StatusFilter>('all');
  searchQuery = '';
  private searchTimer: ReturnType<typeof setTimeout> | null = null;

  filtered = computed(() => {
    const f = this.statusFilter();
    const ps = this.projects();
    return f === 'all' ? ps : ps.filter(p => p.status === f);
  });

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

  countFor(f: StatusFilter): number {
    const ps = this.projects();
    return f === 'all' ? ps.length : ps.filter(p => p.status === f).length;
  }

  filterLabel(f: StatusFilter): string {
    return f === 'all' ? this.i18n.t('projects.filter_all') : this.i18n.t('projects.status.' + f);
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
}
