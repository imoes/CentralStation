import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  OnDestroy,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatTooltipModule } from '@angular/material/tooltip';
import { environment } from '../../../environments/environment';

interface ProblemService {
  host: string;
  service: string;
  severity: string;
  output: string;
  last_state_change: number | null;
  host_address: string;
}

interface Counts {
  crit: number;
  warn: number;
  unknown: number;
  total: number;
}

interface ProblemHost {
  host: string;
  address: string;
  services: ProblemService[];
  counts: Counts;
}

interface ProblemDomain {
  domain: string;
  hosts: ProblemHost[];
  counts: Counts;
  host_count: number;
}

interface ProblemsResponse {
  domains: ProblemDomain[];
  counts: Counts;
  host_count: number;
}

const SEV_COLOR: Record<string, string> = {
  critical: '#ff4433',
  warning:  '#ffcc00',
  unknown:  '#99CCFF',
};

const SEV_LABEL: Record<string, string> = {
  critical: 'CRIT',
  warning:  'WARN',
  unknown:  '?',
};

@Component({
  selector: 'cs-problems',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, FormsModule, MatIconModule, MatButtonModule, MatTooltipModule],
  template: `
    <!-- LCARS top cap bar -->
    <div class="cap-bar">
      <div class="cap-tl"></div>
      <span class="cap-title">PROBLEMBOARD</span>
      <div class="cap-spacer"></div>
      @if (data(); as d) {
        <span class="pill crit">{{ d.counts.crit }} CRIT</span>
        <span class="pill warn">{{ d.counts.warn }} WARN</span>
        <span class="pill unk">{{ d.counts.unknown }} ?</span>
        <span class="pill neutral">{{ d.host_count }} Hosts</span>
      }
      @if (loading()) {
        <span class="badge-loading">SYNC…</span>
      } @else {
        <span class="badge-live">LIVE ●</span>
      }
      <button class="cap-btn" (click)="load()" matTooltip="Neu laden">
        <mat-icon>refresh</mat-icon>
      </button>
      <div class="cap-tr"></div>
    </div>

    <!-- Filter toolbar -->
    <div class="toolbar">
      @for (f of filters; track f.key) {
        <button
          class="chip"
          [class.active]="filterSev() === f.key"
          [style.border-color]="f.color"
          [style.color]="filterSev() === f.key ? '#000' : f.color"
          [style.background]="filterSev() === f.key ? f.color : 'transparent'"
          (click)="filterSev.set(f.key)">
          {{ f.label }}
        </button>
      }
      <div class="search-wrap">
        <mat-icon class="search-icon">search</mat-icon>
        <input class="search-input" [(ngModel)]="searchText" placeholder="Host oder Service suchen…" />
        @if (searchText) {
          <button class="clear-btn" (click)="searchText = ''">
            <mat-icon>close</mat-icon>
          </button>
        }
      </div>
      <button class="chip" (click)="toggleAll()">
        <mat-icon>{{ allExpanded() ? 'unfold_less' : 'unfold_more' }}</mat-icon>
        {{ allExpanded() ? 'Alle einklappen' : 'Alle aufklappen' }}
      </button>
    </div>

    @if (error()) {
      <div class="error-bar">
        <mat-icon>error_outline</mat-icon> {{ error() }}
      </div>
    }

    <!-- Domain groups -->
    <div class="domains">
      @for (domain of filteredDomains(); track domain.domain) {
        <div class="domain-group">
          <!-- Domain header -->
          <div class="domain-header" (click)="toggleDomain(domain.domain)">
            <mat-icon class="toggle-icon">
              {{ expandedDomains().has(domain.domain) ? 'expand_less' : 'expand_more' }}
            </mat-icon>
            <span class="domain-name">{{ domain.domain }}</span>
            <span class="domain-count">{{ domain.host_count }} Hosts</span>
            <div class="spacer"></div>
            <span class="pill crit">{{ domain.counts.crit }}</span>
            <span class="pill warn">{{ domain.counts.warn }}</span>
            <span class="pill unk">{{ domain.counts.unknown }}</span>
          </div>

          @if (expandedDomains().has(domain.domain)) {
            <div class="host-list">
              @for (host of domain.hosts; track host.host) {
                <!-- Host row -->
                <div class="host-row" (click)="toggleHost(host.host)">
                  <mat-icon class="toggle-icon small">
                    {{ expandedHosts().has(host.host) ? 'expand_less' : 'expand_more' }}
                  </mat-icon>
                  <span class="host-name">{{ host.host }}</span>
                  @if (host.address) {
                    <span class="host-addr">{{ host.address }}</span>
                  }
                  <div class="spacer"></div>
                  <span class="pill crit">{{ host.counts.crit }}</span>
                  <span class="pill warn">{{ host.counts.warn }}</span>
                  <span class="pill unk">{{ host.counts.unknown }}</span>
                  <button
                    class="cockpit-btn"
                    (click)="openCockpit(host.host, $event)"
                    matTooltip="Cockpit öffnen">
                    <mat-icon>monitor</mat-icon>
                  </button>
                </div>

                <!-- Service rows -->
                @if (expandedHosts().has(host.host)) {
                  <div class="service-list">
                    @for (svc of host.services; track svc.service) {
                      <div class="svc-row">
                        <span
                          class="svc-dot"
                          [style.background]="sevColor(svc.severity)">
                        </span>
                        <span
                          class="svc-state"
                          [style.color]="sevColor(svc.severity)">
                          {{ sevLabel(svc.severity) }}
                        </span>
                        <span class="svc-name">{{ svc.service }}</span>
                        <span class="svc-output">{{ svc.output | slice:0:120 }}</span>
                      </div>
                    }
                  </div>
                }
              }
            </div>
          }
        </div>
      }
      @if (!loading() && filteredDomains().length === 0 && !error()) {
        <div class="empty">
          <mat-icon>check_circle</mat-icon>
          <span>Keine offenen Probleme</span>
        </div>
      }
    </div>
  `,
  styles: [`
    :host {
      display: block;
      padding: 0 12px 24px;
      background: #0a0a1a;
      min-height: 100vh;
      color: #e0e0e0;
      font-family: 'Share Tech Mono', 'Courier New', monospace;
    }

    /* ── Cap bar ── */
    .cap-bar {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 6px 0 10px;
      border-bottom: 2px solid #FFCC99;
      margin-bottom: 12px;
    }
    .cap-tl, .cap-tr {
      width: 16px; height: 16px;
      border-radius: 50%;
      background: #FFCC99;
      flex-shrink: 0;
    }
    .cap-title {
      font-size: 1.1rem;
      letter-spacing: 0.12em;
      color: #FFCC99;
      font-weight: 600;
    }
    .cap-spacer { flex: 1; }
    .badge-live {
      font-size: 0.7rem;
      color: #66cc66;
      letter-spacing: 0.08em;
    }
    .badge-loading {
      font-size: 0.7rem;
      color: #ffcc00;
      animation: blink 1s infinite;
    }
    @keyframes blink { 50% { opacity: 0.3 } }
    .cap-btn {
      background: transparent;
      border: 1px solid #FFCC99;
      color: #FFCC99;
      border-radius: 4px;
      cursor: pointer;
      padding: 2px 4px;
      display: flex;
      align-items: center;
    }
    .cap-btn:hover { background: rgba(255,204,153,0.15); }
    .cap-btn mat-icon { font-size: 18px; height: 18px; width: 18px; line-height: 18px; }

    /* ── Pills ── */
    .pill {
      font-size: 0.72rem;
      letter-spacing: 0.06em;
      padding: 2px 7px;
      border-radius: 10px;
      font-weight: 600;
    }
    .pill.crit    { background: rgba(255,68,51,0.2);  color: #ff4433; border: 1px solid #ff4433; }
    .pill.warn    { background: rgba(255,204,0,0.15); color: #ffcc00; border: 1px solid #ffcc00; }
    .pill.unk     { background: rgba(153,204,255,0.15); color: #99CCFF; border: 1px solid #99CCFF; }
    .pill.neutral { background: rgba(255,255,255,0.08); color: #aaa; border: 1px solid #555; }

    /* ── Toolbar ── */
    .toolbar {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-family: inherit;
      font-size: 0.78rem;
      padding: 4px 10px;
      border-radius: 12px;
      border: 1px solid #555;
      background: transparent;
      color: #aaa;
      cursor: pointer;
      transition: all 0.15s;
    }
    .chip:hover { border-color: #FFCC99; color: #FFCC99; }
    .chip mat-icon { font-size: 16px; height: 16px; width: 16px; line-height: 16px; }
    .search-wrap {
      display: flex;
      align-items: center;
      background: rgba(255,255,255,0.05);
      border: 1px solid #444;
      border-radius: 6px;
      padding: 2px 8px;
      gap: 4px;
    }
    .search-icon { font-size: 16px; height: 16px; width: 16px; line-height: 16px; color: #888; }
    .search-input {
      background: transparent;
      border: none;
      outline: none;
      color: #e0e0e0;
      font-family: inherit;
      font-size: 0.82rem;
      width: 220px;
    }
    .search-input::placeholder { color: #555; }
    .clear-btn {
      background: transparent; border: none; cursor: pointer; color: #888;
      display: flex; align-items: center; padding: 0;
    }
    .clear-btn mat-icon { font-size: 16px; height: 16px; width: 16px; line-height: 16px; }

    /* ── Error ── */
    .error-bar {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      background: rgba(255,68,51,0.1);
      border: 1px solid #ff4433;
      border-radius: 4px;
      color: #ff4433;
      font-size: 0.82rem;
      margin-bottom: 12px;
    }

    /* ── Domains ── */
    .domains { display: flex; flex-direction: column; gap: 4px; }
    .domain-group {
      border: 1px solid #2a2a3a;
      border-radius: 4px;
      overflow: hidden;
    }
    .domain-header {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      background: rgba(255,204,153,0.06);
      border-bottom: 1px solid #2a2a3a;
      cursor: pointer;
      user-select: none;
      transition: background 0.15s;
    }
    .domain-header:hover { background: rgba(255,204,153,0.12); }
    .toggle-icon { font-size: 18px; height: 18px; width: 18px; line-height: 18px; color: #FFCC99; }
    .toggle-icon.small { font-size: 16px; height: 16px; width: 16px; line-height: 16px; color: #888; }
    .domain-name { font-size: 0.9rem; color: #FFCC99; letter-spacing: 0.04em; }
    .domain-count { font-size: 0.72rem; color: #666; }
    .spacer { flex: 1; }

    /* ── Host list ── */
    .host-list { padding: 4px 0; }
    .host-row {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 6px 12px 6px 28px;
      cursor: pointer;
      border-bottom: 1px solid #1a1a2a;
      transition: background 0.1s;
    }
    .host-row:hover { background: rgba(255,255,255,0.04); }
    .host-name { font-size: 0.82rem; color: #ccc; min-width: 200px; }
    .host-addr { font-size: 0.72rem; color: #555; }
    .cockpit-btn {
      background: transparent;
      border: 1px solid #333;
      color: #666;
      border-radius: 3px;
      cursor: pointer;
      padding: 1px 3px;
      display: flex;
      align-items: center;
      transition: all 0.15s;
    }
    .cockpit-btn:hover { border-color: #99CCFF; color: #99CCFF; }
    .cockpit-btn mat-icon { font-size: 15px; height: 15px; width: 15px; line-height: 15px; }

    /* ── Service rows ── */
    .service-list {
      background: rgba(0,0,0,0.2);
      padding: 2px 0;
    }
    .svc-row {
      display: flex;
      align-items: baseline;
      gap: 8px;
      padding: 4px 12px 4px 52px;
      border-bottom: 1px solid #111;
      font-size: 0.78rem;
    }
    .svc-dot {
      width: 7px; height: 7px;
      border-radius: 50%;
      flex-shrink: 0;
      position: relative;
      top: -1px;
    }
    .svc-state {
      font-size: 0.68rem;
      letter-spacing: 0.06em;
      width: 36px;
      flex-shrink: 0;
    }
    .svc-name {
      color: #aaa;
      min-width: 180px;
      flex-shrink: 0;
    }
    .svc-output {
      color: #555;
      font-size: 0.72rem;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    /* ── Empty state ── */
    .empty {
      display: flex;
      align-items: center;
      gap: 10px;
      justify-content: center;
      padding: 48px;
      color: #66cc66;
      font-size: 1rem;
    }
    .empty mat-icon { font-size: 28px; height: 28px; width: 28px; line-height: 28px; }
  `],
})
export class ProblemsComponent implements OnInit, OnDestroy {
  private http   = inject(HttpClient);
  private router = inject(Router);

  data     = signal<ProblemsResponse | null>(null);
  loading  = signal(false);
  error    = signal('');

  filterSev    = signal<string>('all');
  searchText   = '';
  expandedDomains = signal<Set<string>>(new Set());
  expandedHosts   = signal<Set<string>>(new Set());

  private refreshTimer: ReturnType<typeof setInterval> | null = null;

  readonly filters = [
    { key: 'all',      label: 'ALLE',    color: '#FFCC99' },
    { key: 'critical', label: 'CRIT',    color: '#ff4433' },
    { key: 'warning',  label: 'WARN',    color: '#ffcc00' },
    { key: 'unknown',  label: '?',       color: '#99CCFF' },
  ];

  filteredDomains = computed(() => {
    const d = this.data();
    if (!d) return [];
    const sev = this.filterSev();
    const q   = this.searchText.toLowerCase().trim();

    return d.domains
      .map(domain => {
        const hosts = domain.hosts
          .map(host => {
            const services = host.services.filter(svc => {
              if (sev !== 'all' && svc.severity !== sev) return false;
              if (q && !host.host.toLowerCase().includes(q) && !svc.service.toLowerCase().includes(q)) return false;
              return true;
            });
            if (services.length === 0) return null;
            return { ...host, services };
          })
          .filter((h): h is ProblemHost => h !== null);
        if (hosts.length === 0) return null;
        return { ...domain, hosts };
      })
      .filter((d): d is ProblemDomain => d !== null);
  });

  allExpanded = computed(() => {
    const d = this.data();
    if (!d) return false;
    return d.domains.every(dom => this.expandedDomains().has(dom.domain));
  });

  ngOnInit(): void {
    this.load();
    // Auto-expand all domains on first load
    this.refreshTimer = setInterval(() => this.load(), 30_000);
  }

  ngOnDestroy(): void {
    if (this.refreshTimer) clearInterval(this.refreshTimer);
  }

  load(): void {
    this.loading.set(true);
    this.error.set('');
    this.http.get<ProblemsResponse>(`${environment.apiUrl}/hosts/service-problems`).subscribe({
      next: (resp) => {
        this.data.set(resp);
        // Expand all domains on first load
        const allDoms = new Set(resp.domains.map(d => d.domain));
        this.expandedDomains.set(allDoms);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(err?.error?.detail ?? err?.message ?? 'Fehler beim Laden');
        this.loading.set(false);
      },
    });
  }

  toggleDomain(domain: string): void {
    this.expandedDomains.update(s => {
      const next = new Set(s);
      if (next.has(domain)) next.delete(domain); else next.add(domain);
      return next;
    });
  }

  toggleHost(host: string): void {
    this.expandedHosts.update(s => {
      const next = new Set(s);
      if (next.has(host)) next.delete(host); else next.add(host);
      return next;
    });
  }

  toggleAll(): void {
    const d = this.data();
    if (!d) return;
    if (this.allExpanded()) {
      this.expandedDomains.set(new Set());
    } else {
      this.expandedDomains.set(new Set(d.domains.map(dom => dom.domain)));
    }
  }

  openCockpit(host: string, event: Event): void {
    event.stopPropagation();
    this.router.navigate(['/cockpit', host]);
  }

  sevColor(sev: string): string {
    return SEV_COLOR[sev] ?? '#888';
  }

  sevLabel(sev: string): string {
    return SEV_LABEL[sev] ?? '?';
  }
}
