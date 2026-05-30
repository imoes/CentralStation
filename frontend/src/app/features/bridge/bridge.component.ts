import { Component, OnInit, OnDestroy, signal, computed, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { environment } from '../../../environments/environment';
import { WebsocketService } from '../../core/services/websocket.service';

interface SourceStatus { name: string; state: string; critical: number; high: number; total: number; }
interface SectorStatus { name: string; state: string; critical: number; high: number; total: number; }
interface LogEntry { severity: string; source: string; title: string; host: string; created_at: string; }
interface WorkItem {
  rank: number; external_id: string; severity: string; source: string; title: string;
  host: string; location: string; verdict: string; count: number; oldest: string; score: number;
}
interface Vital { host: string; metric: string; label: string; value: number; unit: string; }
interface Forecast { host: string; metric: string; label: string; current: number; threshold: number; eta_hours: number; }
interface BridgeStatus {
  alert_state: 'red' | 'yellow' | 'green';
  counts: { critical: number; high: number; medium: number; total: number };
  sources: SourceStatus[];
  sectors: SectorStatus[];
  logs: LogEntry[];
  vitals: Vital[];
  forecasts: Forecast[];
  worklist: WorkItem[];
  worklist_open_count: number;
  worklist_updated: string | null;
  server_time: string;
}

@Component({
  selector: 'cs-bridge',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="bridge" [class.theme-lcars]="theme() === 'lcars'" [class.theme-holo]="theme() === 'holo'"
         [attr.data-alert]="status()?.alert_state ?? 'green'">

      <!-- ── Top bar ─────────────────────────────────────────────────── -->
      <header class="bridge-top">
        <div class="brand"><span class="brand-mark"></span><span class="brand-text">CENTRALSTATION</span></div>
        <div class="alert-banner" [attr.data-state]="status()?.alert_state ?? 'green'">{{ alertLabel() }}</div>
        <div class="top-right">
          <span class="clock">{{ clock() }}</span>
          <button class="theme-btn" (click)="toggleTheme()">{{ theme() === 'lcars' ? 'HOLO' : 'LCARS' }}</button>
          <button class="exit-btn" (click)="exit()">✕</button>
        </div>
      </header>

      <div class="bridge-grid">

        <!-- ── Left: systems + sectors ────────────────────────────────── -->
        <aside class="rail">
          <div class="rail-cap"></div>
          <div class="panel systems">
            <div class="panel-label">SYSTEME</div>
            @for (s of status()?.sources ?? []; track s.name) {
              <div class="system-row" [attr.data-state]="s.state" (click)="openSource(s.name)">
                <span class="system-light"></span>
                <span class="system-name">{{ sourceLabel(s.name) }}</span>
                @if (s.critical) { <span class="badge crit">{{ s.critical }}</span> }
                @else if (s.high) { <span class="badge high">{{ s.high }}</span> }
                @else { <span class="badge ok">OK</span> }
              </div>
            }
            <div class="panel-label sectors-label">SEKTOREN</div>
            @for (sec of status()?.sectors ?? []; track sec.name) {
              <div class="system-row" [attr.data-state]="sec.state">
                <span class="system-light"></span>
                <span class="system-name">{{ sec.name }}</span>
                @if (sec.critical) { <span class="badge crit">{{ sec.critical }}</span> }
                @else if (sec.high) { <span class="badge high">{{ sec.high }}</span> }
                @else { <span class="badge ok">OK</span> }
              </div>
            } @empty { <div class="muted">Keine Standortdaten</div> }
          </div>
        </aside>

        <!-- ── Center: AI-prioritised worklist (the hero) ─────────────── -->
        <main class="hero">
          <div class="hero-head">
            <span class="hero-title">PRIORITÄTEN</span>
            <span class="hero-sub">KI-vorsortiert · {{ status()?.worklist_open_count ?? 0 }} offene Probleme</span>
            <span class="hero-meta">
              <span class="stat-chip crit">{{ status()?.counts?.critical ?? 0 }} KRIT</span>
              <span class="stat-chip high">{{ status()?.counts?.high ?? 0 }} HOCH</span>
              <span class="upd">akt. {{ worklistAge() }}</span>
              <button class="refresh" (click)="refreshWorklist()" [disabled]="refreshing()">⟳</button>
            </span>
          </div>

          @if ((status()?.forecasts ?? []).length) {
            <div class="forecast-strip">
              <span class="forecast-icon">⚠ PROGNOSE</span>
              @for (f of status()?.forecasts ?? []; track f.host + f.metric) {
                <span class="forecast-pill" (click)="openHost(f.host)">
                  {{ f.label }} <b>{{ f.host.split('.')[0] }}</b> {{ f.current }}% → {{ f.threshold }}% in {{ etaLabel(f.eta_hours) }}
                </span>
              }
            </div>
          }

          <div class="worklist">
            @for (w of status()?.worklist ?? []; track w.external_id) {
              <div class="work-row" [attr.data-sev]="w.severity" (click)="openItem(w)">
                <div class="work-rank">{{ w.rank }}</div>
                <div class="work-body">
                  <div class="work-line1">
                    <span class="work-sev-tag">{{ w.severity | uppercase }}</span>
                    <span class="work-host">{{ w.host || w.source }}</span>
                    <span class="work-title">{{ workService(w) }}</span>
                  </div>
                  @if (w.verdict) {
                    <div class="work-verdict">{{ w.verdict }}</div>
                  } @else {
                    <div class="work-verdict muted-verdict">{{ w.title }}</div>
                  }
                  <div class="work-meta">
                    <span>{{ sourceLabel(w.source) }}</span>
                    @if (w.location) { <span>· ◈ {{ w.location }}</span> }
                    <span>· seit {{ relTime(w.oldest) }}</span>
                    @if (w.count > 1) { <span class="recur">· {{ w.count }}× wiederholt</span> }
                  </div>
                </div>
                <div class="work-arrow">›</div>
              </div>
            } @empty {
              @if (loading()) {
                <div class="empty-hero">Lade Prioritätenliste…</div>
              } @else {
                <div class="empty-hero nominal">
                  <div class="nominal-icon">✓</div>
                  <div class="nominal-text">ALLE SYSTEME NOMINAL</div>
                  <div class="muted">Keine priorisierten Vorfälle</div>
                </div>
              }
            }
          </div>
        </main>

        <!-- ── Right: fleet vitals + live logs ────────────────────────── -->
        <aside class="rightcol">
          <div class="panel vitals">
            <div class="panel-label">FLEET-VITALS</div>
            @for (v of status()?.vitals ?? []; track v.host + v.metric) {
              <div class="vital-row" (click)="openHost(v.host)">
                <span class="vital-label">{{ v.label }}</span>
                <span class="vital-host">{{ v.host.split('.')[0] }}</span>
                <div class="vital-bar">
                  <div class="vital-fill" [attr.data-level]="vitalLevel(v)" [style.width.%]="vitalPct(v)"></div>
                </div>
                <span class="vital-val">{{ v.value }}{{ v.unit }}</span>
              </div>
            } @empty { <div class="muted">Keine Metrikdaten</div> }
          </div>

          <div class="panel logs">
            <div class="panel-label">LOGS · LIVE</div>
            <div class="log-stream">
              @for (e of status()?.logs ?? []; track e.created_at + e.title) {
                <div class="log-line" [attr.data-sev]="e.severity" (click)="openLog(e)">
                  <span class="log-dot"></span>
                  <div class="log-body">
                    <span class="log-title">{{ e.title }}</span>
                    <span class="log-meta">{{ sourceLabel(e.source) }}@if (e.host) { · {{ e.host }} } · {{ relTime(e.created_at) }}</span>
                  </div>
                </div>
              } @empty { <div class="muted">Keine Logdaten</div> }
            </div>
          </div>
        </aside>
      </div>
    </div>
  `,
  styles: [`
    :host { display: block; }
    .bridge { position: fixed; inset: 0; z-index: 100; display: flex; flex-direction: column;
      font-family: 'Eurostile','Michroma','Segoe UI',sans-serif; overflow: hidden; }

    /* ═══════════ Layout ═══════════ */
    .bridge-top { display: flex; align-items: center; gap: 16px; padding: 10px 18px; flex-shrink: 0; }
    .brand { display: flex; align-items: center; gap: 10px; }
    .brand-mark { width: 24px; height: 24px; border-radius: 50%; }
    .brand-text { font-weight: 700; letter-spacing: .2em; font-size: 14px; }
    .alert-banner { flex: 1; text-align: center; font-weight: 800; letter-spacing: .3em; font-size: 17px; }
    .top-right { display: flex; align-items: center; gap: 12px; }
    .clock { font-size: 15px; letter-spacing: .12em; font-variant-numeric: tabular-nums; font-weight: 600; }
    .theme-btn, .exit-btn { border: none; cursor: pointer; font-family: inherit; font-weight: 700; letter-spacing: .1em; padding: 5px 12px; border-radius: 4px; font-size: 12px; }

    .bridge-grid { flex: 1; display: grid; gap: 14px; padding: 0 18px 16px; grid-template-columns: 248px 1fr 320px; min-height: 0; }
    .rail { display: flex; flex-direction: column; }
    .rightcol { display: flex; flex-direction: column; gap: 14px; min-height: 0; }
    .rightcol .vitals { flex: 0 0 auto; }
    .rightcol .logs { flex: 1; min-height: 0; }

    /* Forecast strip */
    .forecast-strip { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 8px 10px; border-radius: 8px; }
    .forecast-icon { font-size: 12px; font-weight: 800; letter-spacing: .1em; flex-shrink: 0; }
    .forecast-pill { font-size: 12px; padding: 3px 10px; border-radius: 12px; cursor: pointer; }
    .forecast-pill b { font-family: 'Fira Code',monospace; }

    /* Vitals */
    .vital-row { display: flex; align-items: center; gap: 8px; padding: 5px 8px; border-radius: 6px; cursor: pointer; }
    .vital-label { font-size: 10px; font-weight: 800; width: 32px; flex-shrink: 0; }
    .vital-host { font-size: 11px; font-family: 'Fira Code',monospace; width: 86px; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .vital-bar { flex: 1; height: 8px; border-radius: 4px; overflow: hidden; }
    .vital-fill { height: 100%; border-radius: 4px; }
    .vital-val { font-size: 11px; font-weight: 700; width: 46px; text-align: right; flex-shrink: 0; font-variant-numeric: tabular-nums; }
    .panel { display: flex; flex-direction: column; gap: 7px; padding: 14px; border-radius: 10px; overflow: hidden; min-height: 0; }
    .panel-label { font-size: 11px; font-weight: 800; letter-spacing: .22em; opacity: .8; margin-bottom: 4px; }
    .sectors-label { margin-top: 14px; }
    .systems { flex: 1; }

    .system-row { display: flex; align-items: center; gap: 9px; padding: 8px 10px; border-radius: 6px; cursor: pointer; }
    .system-light { width: 11px; height: 11px; border-radius: 50%; flex-shrink: 0; }
    .system-name { flex: 1; font-size: 13px; font-weight: 600; letter-spacing: .04em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .badge { font-size: 11px; font-weight: 800; padding: 1px 8px; border-radius: 10px; }
    .muted { font-size: 12px; opacity: .5; padding: 6px 10px; }

    /* Hero / worklist */
    .hero { display: flex; flex-direction: column; gap: 10px; min-height: 0; }
    .hero-head { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; padding: 4px 4px 0; }
    .hero-title { font-size: 22px; font-weight: 800; letter-spacing: .22em; }
    .hero-sub { font-size: 13px; opacity: .7; }
    .hero-meta { margin-left: auto; display: flex; align-items: center; gap: 10px; }
    .stat-chip { font-size: 12px; font-weight: 800; padding: 3px 10px; border-radius: 12px; letter-spacing: .05em; }
    .upd { font-size: 11px; opacity: .6; }
    .refresh { cursor: pointer; border: none; background: transparent; font-size: 18px; font-weight: 800; }
    .refresh:disabled { opacity: .4; }

    .worklist { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; padding-right: 4px; }
    .work-row { display: flex; align-items: stretch; gap: 12px; padding: 12px 14px; border-radius: 10px; cursor: pointer; transition: transform .1s; }
    .work-row:hover { transform: translateX(3px); }
    .work-rank { font-size: 30px; font-weight: 800; min-width: 38px; text-align: center; line-height: 1.4; opacity: .85; }
    .work-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
    .work-line1 { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .work-sev-tag { font-size: 11px; font-weight: 800; letter-spacing: .12em; padding: 2px 9px; border-radius: 5px; }
    .work-host { font-size: 16px; font-weight: 700; font-family: 'Fira Code',monospace; }
    .work-title { font-size: 14px; opacity: .85; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .work-verdict { font-size: 13px; line-height: 1.5; }
    .muted-verdict { opacity: .65; }
    .work-meta { font-size: 11px; opacity: .6; display: flex; gap: 5px; flex-wrap: wrap; }
    .recur { font-weight: 700; }
    .work-arrow { font-size: 26px; align-self: center; opacity: .5; }

    .empty-hero { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; }
    .nominal-icon { font-size: 80px; line-height: 1; }
    .nominal-text { font-size: 26px; font-weight: 800; letter-spacing: .2em; }

    /* Logs */
    .logs { }
    .log-stream { display: flex; flex-direction: column; gap: 5px; overflow-y: auto; flex: 1; }
    .log-line { display: flex; gap: 8px; padding: 7px 8px; border-radius: 6px; cursor: pointer; }
    .log-line:hover { filter: brightness(1.25); }
    .log-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; margin-top: 5px; }
    .log-body { display: flex; flex-direction: column; min-width: 0; }
    .log-title { font-size: 12px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .log-meta { font-size: 10px; opacity: .6; }

    @keyframes redPulse { 0%,100% { opacity: 1; } 50% { opacity: .5; } }

    /* ═══════════ THEME: LCARS ═══════════ */
    .theme-lcars { background: #000; color: #ff9966; }
    .theme-lcars .brand-mark { background: #ff9966; }
    .theme-lcars .brand-text { color: #ffcc99; }
    .theme-lcars .clock { color: #ffcc66; }
    .theme-lcars .theme-btn { background: #9999cc; color: #000; }
    .theme-lcars .exit-btn { background: #cc6666; color: #000; }
    .theme-lcars .alert-banner { color: #ffcc66; }
    .theme-lcars[data-alert="red"] .alert-banner { color: #ff5544; animation: redPulse 1s infinite; }
    .theme-lcars[data-alert="yellow"] .alert-banner { color: #ffcc00; }
    /* LCARS rail with the characteristic elbow */
    .theme-lcars .rail { background: #ff9966; border-radius: 18px 0 0 60px; padding: 6px 6px 6px 0; }
    .theme-lcars .rail-cap { height: 40px; background: #cc99cc; border-radius: 18px 0 0 0; margin: 0 0 6px 0; }
    .theme-lcars .systems { background: #000; border-radius: 0 0 0 54px; }
    .theme-lcars .panel-label { color: #ffcc66; }
    .theme-lcars .system-row { background: #1c1710; }
    .theme-lcars .system-name { color: #ffcc99; }
    .theme-lcars .system-light { background: #66cc66; }
    .theme-lcars .system-row[data-state="red"] .system-light { background: #ff5544; }
    .theme-lcars .system-row[data-state="yellow"] .system-light { background: #ffcc00; }
    .theme-lcars .badge.crit { background: #ff5544; color: #000; }
    .theme-lcars .badge.high { background: #ffcc00; color: #000; }
    .theme-lcars .badge.ok { background: #336633; color: #aaffaa; }
    .theme-lcars .hero-title { color: #ff9966; }
    .theme-lcars .stat-chip.crit { background: #ff5544; color: #000; }
    .theme-lcars .stat-chip.high { background: #ffcc00; color: #000; }
    .theme-lcars .refresh { color: #9999cc; }
    .theme-lcars .work-row { background: #15120c; border-left: 6px solid #ffcc66; border-radius: 0 10px 10px 0; }
    .theme-lcars .work-row[data-sev="critical"] { border-left-color: #ff5544; background: #1f0d0a; }
    .theme-lcars .work-row[data-sev="high"] { border-left-color: #ffcc00; }
    .theme-lcars .work-rank { color: #ff9966; }
    .theme-lcars .work-sev-tag { background: #ffcc66; color: #000; }
    .theme-lcars .work-row[data-sev="critical"] .work-sev-tag { background: #ff5544; }
    .theme-lcars .work-host { color: #ffcc99; }
    .theme-lcars .work-verdict { color: #cc99cc; }
    .theme-lcars .nominal-icon, .theme-lcars .nominal-text { color: #66cc66; }
    .theme-lcars .forecast-strip { background: #2a1d0a; border: 1px solid #ffcc00; }
    .theme-lcars .forecast-icon { color: #ffcc00; }
    .theme-lcars .forecast-pill { background: #ffcc00; color: #000; }
    .theme-lcars .vitals { background: #1c1710; }
    .theme-lcars .vitals .panel-label { color: #ffcc66; }
    .theme-lcars .vital-label { color: #ff9966; }
    .theme-lcars .vital-host { color: #ffcc99; }
    .theme-lcars .vital-val { color: #ffcc99; }
    .theme-lcars .vital-bar { background: #000; }
    .theme-lcars .vital-fill[data-level="ok"] { background: #66cc66; }
    .theme-lcars .vital-fill[data-level="high"] { background: #ffcc00; }
    .theme-lcars .vital-fill[data-level="crit"] { background: #ff5544; }
    .theme-lcars .logs { background: #ff9966; border-radius: 0 18px 60px 0; padding: 14px 6px 14px 14px; }
    .theme-lcars .logs .panel-label { color: #000; }
    .theme-lcars .log-line { background: #000; }
    .theme-lcars .log-title { color: #ffcc99; }
    .theme-lcars .log-dot { background: #66cc66; }
    .theme-lcars .log-line[data-sev="critical"] .log-dot { background: #ff5544; }
    .theme-lcars .log-line[data-sev="high"] .log-dot { background: #ffcc00; }

    /* ═══════════ THEME: HOLO ═══════════ */
    .theme-holo { color: #7fdfff; background: radial-gradient(circle at 50% 25%, rgba(20,60,90,.5), transparent 60%), linear-gradient(160deg,#02060f,#050d1a 60%,#02060f); }
    .theme-holo::before { content:''; position:absolute; inset:0; pointer-events:none; opacity:.22; background-image: linear-gradient(rgba(64,180,230,.12) 1px,transparent 1px), linear-gradient(90deg,rgba(64,180,230,.12) 1px,transparent 1px); background-size: 44px 44px; }
    .theme-holo .brand-mark { background: radial-gradient(circle,#4fd6ff,#1a6c9c); box-shadow: 0 0 14px #4fd6ff; }
    .theme-holo .brand-text { color: #9fe8ff; text-shadow: 0 0 10px rgba(79,214,255,.6); }
    .theme-holo .clock { color: #9fe8ff; }
    .theme-holo .theme-btn { background: rgba(79,214,255,.15); color: #9fe8ff; border: 1px solid #4fd6ff; }
    .theme-holo .exit-btn { background: transparent; color: #9fe8ff; border: 1px solid #4fd6ff; }
    .theme-holo .alert-banner { color: #7fdfff; text-shadow: 0 0 14px rgba(79,214,255,.5); }
    .theme-holo[data-alert="red"] .alert-banner { color: #ff5b6e; text-shadow: 0 0 18px rgba(255,91,110,.8); animation: redPulse 1s infinite; }
    .theme-holo[data-alert="yellow"] .alert-banner { color: #ffd84a; }
    .theme-holo .rail { gap: 0; }
    .theme-holo .panel { background: rgba(10,28,46,.55); border: 1px solid rgba(79,214,255,.25); backdrop-filter: blur(6px); }
    .theme-holo .rail-cap { display: none; }
    .theme-holo .panel-label { color: #5fc8ee; }
    .theme-holo .system-row { background: rgba(79,214,255,.05); border: 1px solid rgba(79,214,255,.12); }
    .theme-holo .system-name { color: #bfefff; }
    .theme-holo .system-light { background: #3dffa8; box-shadow: 0 0 9px #3dffa8; }
    .theme-holo .system-row[data-state="red"] .system-light { background: #ff5b6e; box-shadow: 0 0 10px #ff5b6e; }
    .theme-holo .system-row[data-state="yellow"] .system-light { background: #ffd84a; box-shadow: 0 0 10px #ffd84a; }
    .theme-holo .badge.crit { background: rgba(255,91,110,.2); color: #ff8b98; border: 1px solid #ff5b6e; }
    .theme-holo .badge.high { background: rgba(255,216,74,.18); color: #ffe27a; border: 1px solid #ffd84a; }
    .theme-holo .badge.ok { background: rgba(61,255,168,.12); color: #7dffc6; border: 1px solid #3dffa8; }
    .theme-holo .hero-title { color: #cff6ff; text-shadow: 0 0 12px rgba(79,214,255,.4); }
    .theme-holo .stat-chip.crit { background: rgba(255,91,110,.2); color: #ff8b98; border: 1px solid #ff5b6e; }
    .theme-holo .stat-chip.high { background: rgba(255,216,74,.18); color: #ffe27a; border: 1px solid #ffd84a; }
    .theme-holo .refresh { color: #9fe8ff; }
    .theme-holo .work-row { background: rgba(10,28,46,.6); border: 1px solid rgba(79,214,255,.22); }
    .theme-holo .work-row[data-sev="critical"] { border-color: #ff5b6e; box-shadow: 0 0 24px rgba(255,91,110,.18); }
    .theme-holo .work-row[data-sev="high"] { border-color: rgba(255,216,74,.5); }
    .theme-holo .work-rank { color: #4fd6ff; }
    .theme-holo .work-sev-tag { background: rgba(79,214,255,.2); color: #9fe8ff; border: 1px solid #4fd6ff; }
    .theme-holo .work-row[data-sev="critical"] .work-sev-tag { background: rgba(255,91,110,.2); color: #ff8b98; border-color: #ff5b6e; }
    .theme-holo .work-host { color: #cff6ff; }
    .theme-holo .work-verdict { color: #8fd0e8; }
    .theme-holo .nominal-icon, .theme-holo .nominal-text { color: #3dffa8; text-shadow: 0 0 18px rgba(61,255,168,.5); }
    .theme-holo .forecast-strip { background: rgba(255,216,74,.08); border: 1px solid rgba(255,216,74,.4); }
    .theme-holo .forecast-icon { color: #ffe27a; }
    .theme-holo .forecast-pill { background: rgba(255,216,74,.15); color: #ffe27a; border: 1px solid rgba(255,216,74,.4); }
    .theme-holo .vital-label { color: #5fc8ee; }
    .theme-holo .vital-host { color: #bfefff; }
    .theme-holo .vital-val { color: #cff6ff; }
    .theme-holo .vital-bar { background: rgba(79,214,255,.1); }
    .theme-holo .vital-fill[data-level="ok"] { background: #3dffa8; box-shadow: 0 0 8px rgba(61,255,168,.6); }
    .theme-holo .vital-fill[data-level="high"] { background: #ffd84a; box-shadow: 0 0 8px rgba(255,216,74,.6); }
    .theme-holo .vital-fill[data-level="crit"] { background: #ff5b6e; box-shadow: 0 0 8px rgba(255,91,110,.6); }
    .theme-holo .log-line { background: rgba(79,214,255,.04); }
    .theme-holo .log-title { color: #bfefff; }
    .theme-holo .log-dot { background: #3dffa8; box-shadow: 0 0 6px #3dffa8; }
    .theme-holo .log-line[data-sev="critical"] .log-dot { background: #ff5b6e; box-shadow: 0 0 7px #ff5b6e; }
    .theme-holo .log-line[data-sev="high"] .log-dot { background: #ffd84a; box-shadow: 0 0 7px #ffd84a; }

    @media (max-width: 1100px) {
      .bridge-grid { grid-template-columns: 1fr; grid-auto-rows: min-content; overflow-y: auto; }
    }
  `],
})
export class BridgeComponent implements OnInit, OnDestroy {
  private http = inject(HttpClient);
  private router = inject(Router);
  private ws = inject(WebsocketService);

  status = signal<BridgeStatus | null>(null);
  loading = signal(true);
  refreshing = signal(false);
  clock = signal('');
  theme = signal<'lcars' | 'holo'>((localStorage.getItem('bridge_theme') as 'lcars' | 'holo') || 'lcars');

  private pollTimer?: ReturnType<typeof setInterval>;
  private clockTimer?: ReturnType<typeof setInterval>;
  private wsSub?: import('rxjs').Subscription;

  alertLabel = computed(() => {
    const s = this.status()?.alert_state;
    if (s === 'red') return '🔴 RED ALERT';
    if (s === 'yellow') return '🟡 ERHÖHTE WACHSAMKEIT';
    return '🟢 ALLE SYSTEME NOMINAL';
  });

  worklistAge = computed(() => {
    const u = this.status()?.worklist_updated;
    return u ? this.relTime(u) : '—';
  });

  ngOnInit() {
    this.load();
    this.tickClock();
    this.pollTimer = setInterval(() => this.load(), 15_000);
    this.clockTimer = setInterval(() => this.tickClock(), 1000);
    this.wsSub = this.ws.messages().subscribe((msg: any) => {
      if (msg?.type === 'ai_insight' || msg?.type === 'alert') this.load();
    });
  }

  ngOnDestroy() {
    if (this.pollTimer) clearInterval(this.pollTimer);
    if (this.clockTimer) clearInterval(this.clockTimer);
    this.wsSub?.unsubscribe();
  }

  private tickClock() {
    const d = new Date();
    const p = (n: number) => String(n).padStart(2, '0');
    this.clock.set(`${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`);
  }

  load() {
    this.http.get<BridgeStatus>(`${environment.apiUrl}/bridge/status`).subscribe({
      next: s => { this.status.set(s); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }

  refreshWorklist() {
    this.refreshing.set(true);
    this.http.post(`${environment.apiUrl}/bridge/refresh-worklist`, {}).subscribe({
      next: () => { this.refreshing.set(false); this.load(); },
      error: () => this.refreshing.set(false),
    });
  }

  toggleTheme() {
    const next = this.theme() === 'lcars' ? 'holo' : 'lcars';
    this.theme.set(next);
    localStorage.setItem('bridge_theme', next);
  }

  exit() { this.router.navigate(['/dashboard']); }

  openItem(w: WorkItem) {
    this.router.navigate(['/feed'], { queryParams: { severity: w.severity, host: w.host || undefined } });
  }
  openLog(e: LogEntry) {
    this.router.navigate(['/feed'], { queryParams: { source: e.source, host: e.host || undefined } });
  }
  openSource(src: string) {
    this.router.navigate(['/feed'], { queryParams: { source: src } });
  }
  openHost(host: string) {
    this.router.navigate(['/feed'], { queryParams: { host } });
  }

  etaLabel(hours: number): string {
    if (hours < 1) return `${Math.round(hours * 60)} Min`;
    if (hours < 48) return `~${Math.round(hours)} Std`;
    return `~${Math.round(hours / 24)} Tg`;
  }

  vitalPct(v: Vital): number {
    // percentage metrics map directly; load is scaled against a nominal ceiling of 8
    if (v.unit === '%') return Math.min(100, v.value);
    return Math.min(100, (v.value / 8) * 100);
  }
  vitalLevel(v: Vital): string {
    const pct = this.vitalPct(v);
    if (pct >= 90) return 'crit';
    if (pct >= 75) return 'high';
    return 'ok';
  }

  workService(w: WorkItem): string {
    // CheckMK titles look like "host — service" — show just the service part if present
    const dash = w.title.indexOf(' — ');
    if (dash > 0 && w.host && w.title.startsWith(w.host.split('.')[0])) {
      return w.title.slice(dash + 3);
    }
    return w.title;
  }

  sourceLabel(src: string): string {
    const m: Record<string, string> = { checkmk: 'CheckMK', graylog: 'Graylog', wazuh: 'Wazuh', o365: 'E-Mail', teams: 'Teams' };
    return m[src] ?? (src || '').toUpperCase();
  }

  relTime(iso: string): string {
    if (!iso) return '';
    const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
    if (mins < 1) return 'gerade';
    if (mins < 60) return `${mins} Min`;
    const h = Math.floor(mins / 60);
    if (h < 24) return `${h} Std`;
    return `${Math.floor(h / 24)} Tg`;
  }
}
