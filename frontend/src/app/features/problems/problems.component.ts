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
import { environment } from '../../../environments/environment';
import { ThemeService } from '../../core/services/theme.service';
import { AuthService } from '../../core/auth/auth.service';

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

const SEV_LABEL: Record<string, string> = {
  critical: 'CRIT',
  warning:  'WARN',
  unknown:  '?',
};

@Component({
  selector: 'cs-problems',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="pb" [class.t-lcars]="theme()==='lcars'" [class.t-holo]="theme()==='holo'" [class.t-classic]="theme()==='classic'"
         [attr.data-alert]="topState()">

      @if (navOpen()) {
        <div class="bridge-menu-backdrop" (click)="navOpen.set(false)"></div>
        <nav class="bridge-menu" aria-label="Navigation">
          <div class="bridge-menu-head">
            <span>NAVIGATION</span>
            <button class="bridge-menu-close" (click)="navOpen.set(false)" title="Close">×</button>
          </div>
          @for (item of nav(); track item.path) {
            <button class="bridge-menu-item" (click)="go(item.path)">
              <span class="bridge-menu-icon">{{ item.icon }}</span>
              <span>{{ item.label }}</span>
            </button>
          }
        </nav>
      }

      <!-- ══ LCARS top sweep ══ -->
      <div class="topbar">
        <button class="bridge-menu-btn" (click)="navOpen.set(true)" title="Navigation">
          <span></span><span></span><span></span>
        </button>
        <div class="cap cap-tl"></div>
        <div class="bar-seg seg-a">PROBLEMBOARD</div>
        @if (data(); as d) {
          <div class="num-cell crit"><b>{{ d.counts.crit }}</b><span>KRITISCH</span></div>
          <div class="num-cell warn"><b>{{ d.counts.warn }}</b><span>WARNUNG</span></div>
          <div class="num-cell unk"><b>{{ d.counts.unknown }}</b><span>UNBEKANNT</span></div>
          <div class="num-cell host"><b>{{ d.host_count }}</b><span>HOSTS</span></div>
        }
        <div class="bar-spacer"></div>
        <button class="pill-btn refresh" (click)="load()" [disabled]="loading()">
          {{ loading() ? 'SYNC…' : '⟳ NEU' }}
        </button>
        <div class="cap cap-tr"></div>
      </div>

      @if (error()) {
        <div class="error-bar">⚠ {{ error() }}</div>
      }

      <!-- ══ Brennpunkte: top critical hosts, always visible ══ -->
      @if (topHosts().length) {
        <div class="hotspots">
          <span class="hot-label">BRENNPUNKTE</span>
          @for (h of topHosts(); track h.host) {
            <button class="hot-pill" [attr.data-sev]="worstSev(h)" (click)="focusHost(h.host)">
              <span class="hot-host">{{ h.host }}</span>
              <span class="hot-count">{{ h.counts.crit || h.counts.warn || h.counts.unknown }}</span>
            </button>
          }
        </div>
      }

      <div class="cols">
        <!-- ══ Left rail: domain pills ══ -->
        <aside class="rail">
          <div class="rail-label">DOMAINS</div>
          <button class="rail-pill" [class.active]="activeDomain()===''" (click)="activeDomain.set('')">
            <span class="rp-name">▸ ALLE</span>
            <span class="rp-val">{{ data()?.counts?.crit || data()?.counts?.total || 0 }}</span>
          </button>
          @for (dom of data()?.domains ?? []; track dom.domain) {
            <button class="rail-pill" [attr.data-state]="domState(dom)"
                    [class.active]="activeDomain()===dom.domain"
                    (click)="activeDomain.set(dom.domain)">
              <span class="rp-name">{{ dom.domain }}</span>
              <span class="rp-val">{{ dom.counts.crit || dom.counts.warn || dom.host_count }}</span>
            </button>
          }
          <div class="rail-fill"></div>
        </aside>

        <!-- ══ Main: filter + host cards ══ -->
        <main class="main">
          <div class="toolbar">
            @for (f of filters; track f.key) {
              <button class="chip" [class.active]="filterSev()===f.key" [attr.data-sev]="f.key"
                      (click)="filterSev.set(f.key)">{{ f.label }}</button>
            }
            <div class="search-wrap">
              <input class="search-input" [(ngModel)]="searchText" (ngModelChange)="searchSig.set($event)"
                     placeholder="HOST ODER SERVICE…" />
              @if (searchText) {
                <button class="clear-btn" (click)="searchText=''; searchSig.set('')">✕</button>
              }
            </div>
            <button class="chip ghost" (click)="toggleAll()">
              {{ allExpanded() ? '▴ ALLE ZU' : '▾ ALLE AUF' }}
            </button>
          </div>

          <div class="host-cards">
            @for (host of visibleHosts(); track host.host) {
              <div class="host-card" [attr.data-sev]="worstSev(host)">
                <div class="host-head" (click)="toggleHost(host.host)">
                  <span class="hh-toggle">{{ expandedHosts().has(host.host) ? '▾' : '▸' }}</span>
                  <span class="hh-name">{{ host.host }}</span>
                  @if (host.address) { <span class="hh-addr">{{ host.address }}</span> }
                  <div class="hh-spacer"></div>
                  @if (host.counts.crit) { <span class="cnt crit">{{ host.counts.crit }}</span> }
                  @if (host.counts.warn) { <span class="cnt warn">{{ host.counts.warn }}</span> }
                  @if (host.counts.unknown) { <span class="cnt unk">{{ host.counts.unknown }}</span> }
                  <button class="cockpit-btn" (click)="openCockpit(host.host, $event)" title="Open Cockpit">⛶ COCKPIT</button>
                </div>
                @if (expandedHosts().has(host.host)) {
                  <div class="svc-list">
                    @for (svc of host.services; track svc.service) {
                      <div class="svc-row" [attr.data-sev]="svc.severity">
                        <span class="svc-state">{{ sevLabel(svc.severity) }}</span>
                        <span class="svc-name">{{ svc.service }}</span>
                        <span class="svc-output">{{ svc.output | slice:0:140 }}</span>
                      </div>
                    }
                  </div>
                }
              </div>
            }
            @if (!loading() && visibleHosts().length === 0 && !error()) {
              <div class="empty">
                <span class="empty-ic">✔</span>
                <span class="empty-tx">KEINE OFFENEN PROBLEME</span>
              </div>
            }
          </div>
        </main>
      </div>
    </div>
  `,
  styles: [`
    :host { display:block; }
    .pb { position:fixed; inset:0; z-index:100; display:flex; flex-direction:column; gap:6px; padding:8px;
      overflow:hidden; box-sizing:border-box; font-family:Roboto,'Helvetica Neue',sans-serif; }
    .pb *, .pb *::before { box-sizing:border-box; }

    /* ── hamburger nav (identical pattern to bridge.component.ts) ── */
    .bridge-menu-btn { width:46px; height:46px; border:0; flex:0 0 46px; display:flex; flex-direction:column;
      align-items:center; justify-content:center; gap:5px; cursor:pointer; flex-shrink:0; }
    .bridge-menu-btn span { display:block; width:23px; height:3px; border-radius:2px; }
    .bridge-menu-backdrop { position:fixed; inset:0; z-index:105; background:rgba(0,0,0,.35); }
    .bridge-menu { position:fixed; z-index:106; top:8px; left:8px; width:min(320px,calc(100vw - 16px));
      max-height:calc(100vh - 16px); display:flex; flex-direction:column; gap:6px; padding:8px;
      overflow:auto; box-shadow:0 20px 80px rgba(0,0,0,.55); }
    .bridge-menu-head { min-height:44px; display:flex; align-items:center; justify-content:space-between;
      padding:0 8px 0 18px; font-size:14px; font-weight:900; letter-spacing:.18em; }
    .bridge-menu-close { width:40px; height:40px; border:0; cursor:pointer; font:inherit; font-size:24px; font-weight:900; }
    .bridge-menu-item { display:flex; align-items:center; gap:12px; min-height:42px; border:0; cursor:pointer;
      font:inherit; font-size:13px; font-weight:800; letter-spacing:.08em; text-align:left; }
    .bridge-menu-icon { width:34px; height:28px; display:inline-flex; align-items:center; justify-content:center; font-size:18px; }

    /* ── top sweep ── */
    .topbar { display:flex; align-items:stretch; gap:6px; height:54px; flex-shrink:0; }
    .cap { width:46px; flex-shrink:0; }
    .bar-seg { display:flex; align-items:center; padding:0 20px; font-weight:800; letter-spacing:.18em; font-size:16px; }
    .seg-a { flex-shrink:0; min-width:230px; }
    .bar-spacer { flex:1; }
    .num-cell { min-width:96px; display:flex; flex-direction:column; align-items:flex-start; justify-content:center; padding:0 16px; }
    .num-cell b { font-size:26px; font-weight:800; line-height:1; }
    .num-cell span { font-size:9px; font-weight:700; letter-spacing:.16em; margin-top:3px; }
    .pill-btn { border:none; cursor:pointer; font-family:inherit; font-weight:800; letter-spacing:.1em; font-size:13px; padding:0 18px; flex-shrink:0; }
    .pill-btn:disabled { opacity:.5; cursor:wait; }

    .error-bar { padding:8px 14px; border-radius:6px; font-size:13px; font-weight:700; flex-shrink:0;
      background:rgba(255,68,51,.12); border:1px solid #ff4433; color:#ff4433; }

    /* ── hotspots ── */
    .hotspots { display:flex; align-items:center; gap:8px; flex-wrap:wrap; padding:8px 12px; border-radius:8px; flex-shrink:0; }
    .hot-label { font-size:11px; font-weight:800; letter-spacing:.18em; margin-right:4px; }
    .hot-pill { display:flex; align-items:center; gap:8px; padding:5px 12px; border-radius:14px; cursor:pointer;
      font-family:inherit; font-size:13px; border:none; transition:filter .15s; }
    .hot-pill:hover { filter:brightness(1.2); }
    .hot-host { font-weight:700; font-family:'Fira Code',monospace; }
    .hot-count { font-weight:800; font-variant-numeric:tabular-nums; }

    /* ── columns ── */
    .cols { flex:1; display:grid; grid-template-columns:230px 1fr; gap:8px; min-height:0; }
    .rail { display:flex; flex-direction:column; gap:5px; min-height:0; overflow-y:auto; padding:2px; }
    .rail-label { font-size:11px; font-weight:800; letter-spacing:.18em; padding:6px 8px 2px; }
    .rail-pill { display:flex; align-items:center; justify-content:space-between; gap:8px; border:none; cursor:pointer;
      font-family:inherit; min-height:38px; padding:0 14px; font-weight:700; font-size:13px; flex-shrink:0; text-align:left; }
    .rail-pill.active { outline:2px solid currentColor; outline-offset:2px; }
    .rp-name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .rp-val { font-weight:800; font-variant-numeric:tabular-nums; }
    .rail-fill { flex:1; min-height:8px; }

    /* ── main ── */
    .main { display:flex; flex-direction:column; gap:8px; min-height:0; }
    .toolbar { display:flex; align-items:center; gap:8px; flex-wrap:wrap; flex-shrink:0; }
    .chip { font-family:inherit; font-size:12px; font-weight:700; letter-spacing:.08em; padding:6px 14px; border-radius:14px;
      border:none; cursor:pointer; }
    .search-wrap { display:flex; align-items:center; gap:6px; padding:3px 10px; border-radius:8px; }
    .search-input { background:transparent; border:none; outline:none; font-family:inherit; font-size:13px; width:230px; }
    .clear-btn { background:transparent; border:none; cursor:pointer; font-size:13px; }

    .host-cards { flex:1; overflow-y:auto; display:flex; flex-direction:column; gap:6px; padding-right:4px; }
    .host-card { border-radius:0 8px 8px 0; }
    .host-head { display:flex; align-items:center; gap:10px; padding:9px 14px; cursor:pointer; border-radius:0 8px 0 0; }
    .host-head:hover { filter:brightness(1.1); }
    .hh-toggle { font-size:13px; width:14px; flex-shrink:0; }
    .hh-name { font-size:15px; font-weight:700; font-family:'Fira Code',monospace; }
    .hh-addr { font-size:11px; opacity:.6; }
    .hh-spacer { flex:1; }
    .cnt { font-size:13px; font-weight:800; padding:2px 9px; border-radius:10px; font-variant-numeric:tabular-nums; }
    .cockpit-btn { font-family:inherit; font-size:11px; font-weight:800; letter-spacing:.08em; padding:4px 11px;
      border-radius:12px; border:none; cursor:pointer; flex-shrink:0; }
    .cockpit-btn:hover { filter:brightness(1.2); }

    .svc-list { display:flex; flex-direction:column; overflow:hidden; border-radius:0 0 8px 0; }
    .svc-row { display:flex; align-items:center; gap:12px; padding:5px 14px 5px 30px; font-size:13px; line-height:1.4; min-height:28px; }
    .svc-state { font-size:10px; font-weight:800; letter-spacing:.06em; width:40px; flex-shrink:0; }
    .svc-name { min-width:200px; flex-shrink:0; font-weight:600; }
    .svc-output { font-size:12px; opacity:.7; text-overflow:ellipsis; white-space:nowrap; overflow:hidden; min-width:0; flex:1; }

    .empty { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:10px; opacity:.7; padding:48px; }
    .empty-ic { font-size:56px; } .empty-tx { font-size:20px; font-weight:800; letter-spacing:.18em; }

    @keyframes redPulse { 0%,100%{opacity:1} 50%{opacity:.5} }

    /* ═══════════════ HAMBURGER NAV — per-theme ═══════════════ */
    .t-classic .bridge-menu-btn { background:#fff; color:#1565c0; border:1px solid #cdd9e5; border-radius:14px; }
    .t-classic .bridge-menu-btn span { background:#1565c0; }
    .t-classic .bridge-menu { background:#fff; border:1px solid #d7e0ea; border-radius:14px; color:#1f2933; }
    .t-classic .bridge-menu-head { color:#1565c0; border-bottom:1px solid #d7e0ea; }
    .t-classic .bridge-menu-close { background:#eef4fb; color:#1565c0; border-radius:12px; }
    .t-classic .bridge-menu-item { background:#f1f5fa; color:#1f2933; border-radius:12px; padding:0 12px; }
    .t-classic .bridge-menu-icon { color:#1565c0; }

    .t-lcars .bridge-menu-btn { background:#ffcc66; color:#000; border-radius:24px 0 0 0; }
    .t-lcars .bridge-menu-btn span { background:#000; }
    .t-lcars .bridge-menu { background:#000; border-left:18px solid #FF9933; color:#ffcc99; border-radius:44px 0 18px 0; }
    .t-lcars .bridge-menu-head { background:#ffcc66; color:#000; border-radius:24px 0 0 0; }
    .t-lcars .bridge-menu-close { background:#000; color:#ffcc66; }
    .t-lcars .bridge-menu-item { background:#15120c; color:#ffcc99; border-radius:0 20px 20px 0; padding:0 14px; }
    .t-lcars .bridge-menu-item:nth-child(3n) { background:#99CCFF; color:#000; }
    .t-lcars .bridge-menu-item:nth-child(3n+1) { background:#FF9933; color:#000; }
    .t-lcars .bridge-menu-item:hover { filter:brightness(1.14); }
    .t-lcars .bridge-menu-icon { color:inherit; }

    .t-holo .bridge-menu-btn { background:rgba(79,214,255,.15); color:#9fe8ff; border:1px solid #4fd6ff; border-radius:8px; }
    .t-holo .bridge-menu-btn span { background:#9fe8ff; }
    .t-holo .bridge-menu { background:#050d1a; border:1px solid rgba(79,214,255,.4); border-radius:10px; color:#bfefff; }
    .t-holo .bridge-menu-head { color:#9fe8ff; border-bottom:1px solid rgba(79,214,255,.25); }
    .t-holo .bridge-menu-close { background:rgba(79,214,255,.08); color:#9fe8ff; border-radius:8px; }
    .t-holo .bridge-menu-item { background:rgba(79,214,255,.08); color:#bfefff; border:1px solid rgba(79,214,255,.12); border-radius:8px; padding:0 12px; }
    .t-holo .bridge-menu-item:hover { filter:brightness(1.2); }
    .t-holo .bridge-menu-icon { color:#5fc8ee; }

    /* ═══════════════ THEME: CLASSIC ═══════════════ */
    .t-classic { color:#1f2933;
      background:linear-gradient(150deg,#eef4fb,#e6eef7 60%,#eef4fb); border-radius:10px; }
    .t-classic .cap { display:none; }
    .t-classic .seg-a { background:#1565c0; color:#fff; border-radius:14px; }
    .t-classic .num-cell { background:#fff; border:1px solid #dde6ef; border-radius:14px; }
    .t-classic .num-cell.crit b { color:#c62828; } .t-classic .num-cell.warn b { color:#ef6c00; }
    .t-classic .num-cell.unk b { color:#1565c0; } .t-classic .num-cell.host b { color:#37474f; }
    .t-classic .num-cell span { color:#5b6b7b; }
    .t-classic .pill-btn.refresh { background:#1565c0; color:#fff; border-radius:14px; }
    .t-classic .hotspots { background:#fff; border:1px solid #dde6ef; }
    .t-classic .hot-label { color:#c62828; }
    .t-classic .hot-pill { background:#fdecea; color:#1f2933; border:1px solid #f3c0bb; }
    .t-classic .hot-pill[data-sev="warning"] { background:#fff7e6; border-color:#ffd9a0; }
    .t-classic .hot-pill[data-sev="unknown"] { background:#e8f0fc; border-color:#bcd4f5; }
    .t-classic .rail-label { color:#5b6b7b; }
    .t-classic .rail-pill { background:#fff; color:#1f2933; border:1px solid #d7e0ea; border-radius:14px; box-shadow:0 1px 3px rgba(0,0,0,.06); }
    .t-classic .rail-pill[data-state="red"] { border-left:5px solid #c62828; }
    .t-classic .rail-pill[data-state="yellow"] { border-left:5px solid #ef6c00; }
    .t-classic .chip { background:#fff; color:#5b6b7b; border:1px solid #d7e0ea; }
    .t-classic .chip.active { background:#1565c0; color:#fff; }
    .t-classic .chip.active[data-sev="critical"] { background:#c62828; }
    .t-classic .chip.active[data-sev="warning"] { background:#ef6c00; }
    .t-classic .chip.active[data-sev="unknown"] { background:#1565c0; }
    .t-classic .search-wrap { background:#fff; border:1px solid #d7e0ea; } .t-classic .search-input { color:#1f2933; }
    .t-classic .host-card { background:#fff; border:1px solid #dde6ef; border-left:6px solid #90a4b8; }
    .t-classic .host-card[data-sev="critical"] { border-left-color:#c62828; }
    .t-classic .host-card[data-sev="warning"] { border-left-color:#ef6c00; }
    .t-classic .host-card[data-sev="unknown"] { border-left-color:#1565c0; }
    .t-classic .hh-name { color:#1f2933; }
    .t-classic .cnt.crit { background:#c62828; color:#fff; } .t-classic .cnt.warn { background:#ef6c00; color:#fff; } .t-classic .cnt.unk { background:#1565c0; color:#fff; }
    .t-classic .cockpit-btn { background:#eef4fb; color:#1565c0; }
    .t-classic .svc-row { border-top:1px solid #eef2f7; }
    .t-classic .svc-state { color:#90a4b8; } .t-classic .svc-row[data-sev="critical"] .svc-state { color:#c62828; }
    .t-classic .svc-row[data-sev="warning"] .svc-state { color:#ef6c00; } .t-classic .svc-row[data-sev="unknown"] .svc-state { color:#1565c0; }
    .t-classic .svc-name { color:#37474f; } .t-classic .empty { color:#2e7d32; }

    /* ═══════════════ THEME: LCARS ═══════════════ */
    .t-lcars { background:#000; color:#FF9933; font-family:'Antonio','Eurostile',sans-serif; text-transform:uppercase; }
    /* Service name + plugin output are message texts → readable Roboto, not condensed Antonio
       (same convention as Bridge .work-verdict / News Feed .card-body-text). */
    .t-lcars .svc-name, .t-lcars .svc-output, .t-lcars .hh-addr {
      text-transform:none; font-family:Roboto,'Helvetica Neue',sans-serif; }
    .t-lcars .cap { background:#FF9933; }
    .t-lcars .cap-tl { border-radius:42px 0 0 0; }
    .t-lcars .cap-tr { border-radius:0 42px 0 0; width:30px; }
    .t-lcars .seg-a { background:#ffcc66; color:#000; min-width:230px; justify-content:flex-end; }
    .t-lcars .num-cell.crit { background:#ff5544; color:#000; } .t-lcars .num-cell.warn { background:#ffcc00; color:#000; }
    .t-lcars .num-cell.unk { background:#99CCFF; color:#000; } .t-lcars .num-cell.host { background:#ffcc66; color:#000; }
    .t-lcars .pill-btn.refresh { background:#99CCFF; color:#000; border-radius:0 18px 18px 0; }
    .t-lcars .hotspots { background:#1f0d0a; border-left:7px solid #ff5544; border-radius:0 8px 8px 0; }
    .t-lcars .hot-label { color:#ff5544; }
    .t-lcars .hot-pill { background:#ff5544; color:#000; }
    .t-lcars .hot-pill[data-sev="warning"] { background:#ffcc00; } .t-lcars .hot-pill[data-sev="unknown"] { background:#99CCFF; }
    .t-lcars .rail-label { color:#ffcc66; }
    .t-lcars .rail-pill { background:#FF9933; color:#000; border-radius:0 18px 18px 0; }
    .t-lcars .rail-pill:nth-child(3n) { background:#99CCFF; }
    .t-lcars .rail-pill:nth-child(3n+1) { background:#ffcc66; }
    .t-lcars .rail-pill[data-state="red"] { background:#ff5544; } .t-lcars .rail-pill[data-state="yellow"] { background:#ffcc00; }
    .t-lcars .chip { background:#ffcc66; color:#000; } .t-lcars .chip.active { outline:2px solid #fff; }
    .t-lcars .chip[data-sev="critical"].active { background:#ff5544; } .t-lcars .chip[data-sev="warning"].active { background:#ffcc00; }
    .t-lcars .chip[data-sev="unknown"].active { background:#99CCFF; }
    .t-lcars .chip.ghost { background:#99CCFF; }
    .t-lcars .search-wrap { background:#15120c; border:1px solid #FF9933; border-radius:8px; } .t-lcars .search-input { color:#ffcc99; } .t-lcars .clear-btn { color:#FF9933; }
    .t-lcars .host-card { background:#15120c; border-left:7px solid #ffcc66; }
    .t-lcars .host-card[data-sev="critical"] { border-left-color:#ff5544; background:#1f0d0a; }
    .t-lcars .host-card[data-sev="warning"] { border-left-color:#ffcc00; }
    .t-lcars .host-card[data-sev="unknown"] { border-left-color:#99CCFF; }
    .t-lcars .hh-name { color:#ffcc99; } .t-lcars .hh-toggle { color:#FF9933; }
    .t-lcars .cnt.crit { background:#ff5544; color:#000; } .t-lcars .cnt.warn { background:#ffcc00; color:#000; } .t-lcars .cnt.unk { background:#99CCFF; color:#000; }
    .t-lcars .cockpit-btn { background:#99CCFF; color:#000; }
    .t-lcars .svc-row { border-top:1px solid #000; }
    .t-lcars .svc-state { color:#ffcc66; } .t-lcars .svc-row[data-sev="critical"] .svc-state { color:#ff5544; }
    .t-lcars .svc-row[data-sev="warning"] .svc-state { color:#ffcc00; } .t-lcars .svc-row[data-sev="unknown"] .svc-state { color:#99CCFF; }
    .t-lcars .svc-name { color:#ffcc99; } .t-lcars .svc-output { color:#e8a060; }
    .t-lcars .empty { color:#66cc66; }

    /* ═══════════════ THEME: HOLO ═══════════════ */
    .t-holo { color:#7fdfff; background:radial-gradient(circle at 50% 20%,rgba(20,60,90,.5),transparent 60%),linear-gradient(160deg,#02060f,#050d1a 60%,#02060f); border-radius:10px; }
    .t-holo .cap { display:none; }
    .t-holo .seg-a { background:rgba(79,214,255,.12); color:#9fe8ff; border:1px solid rgba(79,214,255,.3); border-radius:8px; }
    .t-holo .num-cell { border:1px solid rgba(79,214,255,.25); border-radius:8px; }
    .t-holo .num-cell.crit b { color:#ff5b6e; } .t-holo .num-cell.warn b { color:#ffd84a; }
    .t-holo .num-cell.unk b { color:#4fd6ff; } .t-holo .num-cell.host b { color:#cff6ff; } .t-holo .num-cell span { color:#5fc8ee; }
    .t-holo .pill-btn.refresh { background:rgba(79,214,255,.15); color:#9fe8ff; border:1px solid #4fd6ff; border-radius:8px; }
    .t-holo .hotspots { background:rgba(255,91,110,.08); border:1px solid rgba(255,91,110,.4); }
    .t-holo .hot-label { color:#ff8b98; }
    .t-holo .hot-pill { background:rgba(255,91,110,.2); color:#ff8b98; border:1px solid #ff5b6e; }
    .t-holo .hot-pill[data-sev="warning"] { background:rgba(255,216,74,.15); color:#ffe27a; border-color:#ffd84a; }
    .t-holo .hot-pill[data-sev="unknown"] { background:rgba(79,214,255,.15); color:#9fe8ff; border-color:#4fd6ff; }
    .t-holo .rail-label { color:#5fc8ee; }
    .t-holo .rail-pill { background:rgba(10,28,46,.6); color:#bfefff; border:1px solid rgba(79,214,255,.25); border-radius:8px; }
    .t-holo .rail-pill[data-state="red"] { border-color:#ff5b6e; color:#ff8b98; } .t-holo .rail-pill[data-state="yellow"] { border-color:#ffd84a; color:#ffe27a; }
    .t-holo .chip { background:rgba(10,28,46,.6); color:#9fe8ff; border:1px solid rgba(79,214,255,.25); }
    .t-holo .chip.active { background:rgba(79,214,255,.25); border-color:#4fd6ff; }
    .t-holo .chip[data-sev="critical"].active { background:rgba(255,91,110,.25); border-color:#ff5b6e; color:#ff8b98; }
    .t-holo .chip[data-sev="warning"].active { background:rgba(255,216,74,.2); border-color:#ffd84a; color:#ffe27a; }
    .t-holo .search-wrap { background:rgba(10,28,46,.6); border:1px solid rgba(79,214,255,.25); } .t-holo .search-input { color:#cff6ff; } .t-holo .clear-btn { color:#9fe8ff; }
    .t-holo .host-card { background:rgba(10,28,46,.6); border:1px solid rgba(79,214,255,.22); border-left:6px solid rgba(79,214,255,.5); }
    .t-holo .host-card[data-sev="critical"] { border-left-color:#ff5b6e; box-shadow:0 0 22px rgba(255,91,110,.15); }
    .t-holo .host-card[data-sev="warning"] { border-left-color:#ffd84a; }
    .t-holo .host-card[data-sev="unknown"] { border-left-color:#4fd6ff; }
    .t-holo .hh-name { color:#cff6ff; } .t-holo .hh-toggle { color:#5fc8ee; }
    .t-holo .cnt.crit { background:rgba(255,91,110,.2); color:#ff8b98; border:1px solid #ff5b6e; }
    .t-holo .cnt.warn { background:rgba(255,216,74,.2); color:#ffe27a; border:1px solid #ffd84a; }
    .t-holo .cnt.unk { background:rgba(79,214,255,.2); color:#9fe8ff; border:1px solid #4fd6ff; }
    .t-holo .cockpit-btn { background:rgba(79,214,255,.15); color:#9fe8ff; border:1px solid #4fd6ff; }
    .t-holo .svc-row { border-top:1px solid rgba(79,214,255,.12); }
    .t-holo .svc-state { color:#5fc8ee; } .t-holo .svc-row[data-sev="critical"] .svc-state { color:#ff8b98; }
    .t-holo .svc-row[data-sev="warning"] .svc-state { color:#ffe27a; } .t-holo .svc-row[data-sev="unknown"] .svc-state { color:#9fe8ff; }
    .t-holo .svc-name { color:#bfefff; } .t-holo .svc-output { color:#8fb8cf; } .t-holo .empty { color:#3dffa8; }

    @media (max-width:1100px){ .cols{ grid-template-columns:1fr; } .rail{ flex-direction:row; flex-wrap:wrap; overflow:visible; } .rail-fill{ display:none; } }
  `],
})
export class ProblemsComponent implements OnInit, OnDestroy {
  private http     = inject(HttpClient);
  private router   = inject(Router);
  private themeSvc = inject(ThemeService);
  private auth     = inject(AuthService);
  theme = this.themeSvc.theme;

  data    = signal<ProblemsResponse | null>(null);
  loading = signal(false);
  error   = signal('');
  navOpen = signal(false);

  filterSev     = signal<string>('all');
  searchSig     = signal<string>('');
  searchText    = '';
  activeDomain  = signal<string>('');
  expandedHosts = signal<Set<string>>(new Set());

  private refreshTimer: ReturnType<typeof setInterval> | null = null;

  // Same role-gating as the app sidenav (app.ts navItems); current view (/problems) excluded.
  private readonly NAV_ALL = [
    { path: '/dashboard',   label: 'Dashboard',     icon: '▦', roles: ['admin','sysadmin','network_technician','viewer'] },
    { path: '/bridge',      label: 'Bridge',        icon: '◈', roles: ['admin','sysadmin','network_technician','viewer'] },
    { path: '/feed',        label: 'News Feed',     icon: '≋', roles: ['admin','sysadmin','network_technician'] },
    { path: '/problems',    label: 'Problemboard',  icon: '⚠', roles: ['admin','sysadmin','network_technician'] },
    { path: '/alerts',      label: 'Alerts',        icon: '!', roles: ['admin'] },
    { path: '/my-tickets',  label: 'Meine Tickets', icon: '✓', roles: ['admin','sysadmin'] },
    { path: '/kanban',      label: 'Kanban',        icon: '▤', roles: ['admin','sysadmin','network_technician'] },
    { path: '/ai-insights', label: 'KI-Insights',   icon: '◎', roles: ['admin','sysadmin'] },
    { path: '/settings',    label: 'Einstellungen', icon: '⚙', roles: ['admin','sysadmin','network_technician','viewer'] },
    { path: '/help',        label: 'Hilfe',         icon: '?', roles: ['admin','sysadmin','network_technician','viewer'] },
  ];

  nav = computed(() => {
    const role = this.auth.userRole();
    return this.NAV_ALL.filter(i => i.path !== '/problems' && role && i.roles.includes(role));
  });

  readonly filters = [
    { key: 'all',      label: 'ALLE' },
    { key: 'critical', label: 'CRIT' },
    { key: 'warning',  label: 'WARN' },
    { key: 'unknown',  label: '?' },
  ];

  /** Flatten all hosts across domains for the "Brennpunkte" strip — worst first. */
  private allHosts = computed<ProblemHost[]>(() => {
    const d = this.data();
    if (!d) return [];
    return d.domains.flatMap(dom => dom.hosts);
  });

  topHosts = computed<ProblemHost[]>(() =>
    [...this.allHosts()]
      .filter(h => h.counts.crit > 0)
      .sort((a, b) => b.counts.crit - a.counts.crit || b.counts.total - a.counts.total)
      .slice(0, 8)
  );

  topState = computed<string>(() => {
    const c = this.data()?.counts;
    if (!c) return 'green';
    if (c.crit > 0) return 'red';
    if (c.warn > 0) return 'yellow';
    return 'green';
  });

  /** Hosts shown in the main panel: scoped to active domain, filtered by severity + search. */
  visibleHosts = computed<ProblemHost[]>(() => {
    const d = this.data();
    if (!d) return [];
    const dom = this.activeDomain();
    const sev = this.filterSev();
    const q   = this.searchSig().toLowerCase().trim();

    const domains = dom ? d.domains.filter(x => x.domain === dom) : d.domains;
    const out: ProblemHost[] = [];
    for (const domain of domains) {
      for (const host of domain.hosts) {
        const services = host.services.filter(svc => {
          if (sev !== 'all' && svc.severity !== sev) return false;
          if (q && !host.host.toLowerCase().includes(q) && !svc.service.toLowerCase().includes(q)) return false;
          return true;
        });
        if (services.length === 0) continue;
        const counts = { crit: 0, warn: 0, unknown: 0, total: services.length };
        for (const s of services) {
          if (s.severity === 'critical') counts.crit++;
          else if (s.severity === 'warning') counts.warn++;
          else counts.unknown++;
        }
        out.push({ ...host, services, counts });
      }
    }
    return out;
  });

  allExpanded = computed(() =>
    this.visibleHosts().length > 0 && this.visibleHosts().every(h => this.expandedHosts().has(h.host))
  );

  ngOnInit(): void {
    this.load();
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
        // Auto-expand all hosts on load so problems are visible immediately.
        const expand = new Set(this.expandedHosts());
        for (const dom of resp.domains)
          for (const host of dom.hosts)
            expand.add(host.host);
        this.expandedHosts.set(expand);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(err?.error?.detail ?? err?.message ?? 'Fehler beim Laden');
        this.loading.set(false);
      },
    });
  }

  worstSev(host: ProblemHost): string {
    if (host.counts.crit) return 'critical';
    if (host.counts.warn) return 'warning';
    return 'unknown';
  }

  domState(dom: ProblemDomain): string {
    if (dom.counts.crit) return 'red';
    if (dom.counts.warn) return 'yellow';
    return '';
  }

  focusHost(host: string): void {
    // Jump to the host: scope to its domain, clear filters, expand it.
    const domain = host.includes('.') ? host.slice(host.indexOf('.') + 1) : host;
    this.activeDomain.set(this.data()?.domains.some(d => d.domain === domain) ? domain : '');
    this.filterSev.set('all');
    this.searchText = '';
    this.searchSig.set('');
    this.expandedHosts.update(s => new Set(s).add(host));
  }

  toggleHost(host: string): void {
    this.expandedHosts.update(s => {
      const next = new Set(s);
      if (next.has(host)) next.delete(host); else next.add(host);
      return next;
    });
  }

  toggleAll(): void {
    if (this.allExpanded()) {
      this.expandedHosts.set(new Set());
    } else {
      this.expandedHosts.set(new Set(this.visibleHosts().map(h => h.host)));
    }
  }

  openCockpit(host: string, event: Event): void {
    event.stopPropagation();
    window.open(
      '/cockpit/' + encodeURIComponent(host),
      'cockpit-' + host,
      'width=1300,height=820,menubar=no,toolbar=no,location=no,status=no',
    );
  }

  go(path: string): void {
    this.navOpen.set(false);
    this.router.navigateByUrl(path);
  }

  sevLabel(sev: string): string {
    return SEV_LABEL[sev] ?? '?';
  }
}
