import { Component, OnInit, OnDestroy, signal, computed, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { environment } from '../../../environments/environment';
import { WebsocketService } from '../../core/services/websocket.service';

interface SourceStatus { name: string; state: string; critical: number; high: number; total: number; }
interface SectorStatus { name: string; state: string; critical: number; high: number; total: number; }
interface LogEntry { severity: string; source: string; title: string; host: string; created_at: string; }
interface Vital { host: string; metric: string; label: string; value: number; unit: string; }
interface Forecast { host: string; metric: string; label: string; current: number; threshold: number; eta_hours: number; }
interface WorkItem {
  rank: number; external_id: string; severity: string; source: string; title: string;
  host: string; location: string; verdict: string; count: number; oldest: string; score: number;
}
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
    <div class="bridge" [class.t-lcars]="theme()==='lcars'" [class.t-holo]="theme()==='holo'"
         [attr.data-alert]="status()?.alert_state ?? 'green'">

      <!-- ══ LCARS frame: top sweep ══ -->
      <div class="topbar">
        <div class="cap cap-tl"></div>
        <div class="bar-seg seg-a">CENTRALSTATION</div>
        <div class="alert-banner" [attr.data-state]="status()?.alert_state ?? 'green'">{{ alertLabel() }}</div>
        <div class="bar-seg seg-b">{{ clock() }}</div>
        <button class="pill-btn theme" (click)="toggleTheme()">{{ theme()==='lcars' ? 'HOLO' : 'LCARS' }}</button>
        <button class="pill-btn exit" (click)="exit()">EXIT</button>
        <div class="cap cap-tr"></div>
      </div>

      <div class="cols">
        <!-- ══ Left rail (LCARS pill sidebar) ══ -->
        <aside class="rail">
          <div class="rail-label">SYSTEME</div>
          @for (s of status()?.sources ?? []; track s.name) {
            <button class="rail-pill" [attr.data-state]="s.state" (click)="openSource(s.name)">
              <span class="rp-name">{{ sourceLabel(s.name) }}</span>
              <span class="rp-val">{{ s.critical ? s.critical : (s.high ? s.high : 'OK') }}</span>
            </button>
          }
          <div class="rail-label">SEKTOREN</div>
          @for (sec of status()?.sectors ?? []; track sec.name) {
            <button class="rail-pill" [attr.data-state]="sec.state" (click)="openSource('')">
              <span class="rp-name">{{ sec.name }}</span>
              <span class="rp-val">{{ sec.critical ? sec.critical : (sec.high ? sec.high : 'OK') }}</span>
            </button>
          } @empty { <div class="rail-muted">—</div> }
          <div class="rail-fill"></div>
        </aside>

        <!-- ══ Center: priorities (hero) ══ -->
        <main class="hero">
          <div class="hero-head">
            <span class="hero-title">PRIORITÄTEN</span>
            <span class="hero-sub">KI-vorsortiert · {{ status()?.worklist_open_count ?? 0 }} offene Probleme · akt. {{ worklistAge() }}</span>
            <button class="pill-btn refresh" (click)="refreshWorklist()" [disabled]="refreshing()">⟳ NEU</button>
          </div>

          @if ((status()?.forecasts ?? []).length) {
            <div class="forecast-strip">
              <span class="fc-icon">⚠ PROGNOSE</span>
              @for (f of status()?.forecasts ?? []; track f.host + f.metric) {
                <span class="fc-pill" (click)="openHost(f.host)">
                  {{ f.label }} <b>{{ short(f.host) }}</b> {{ f.current }}%→{{ f.threshold }}% in {{ etaLabel(f.eta_hours) }}
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
                    <span class="work-sev">{{ w.severity | uppercase }}</span>
                    <span class="work-host">{{ short(w.host) || sourceLabel(w.source) }}</span>
                    <span class="work-svc">{{ workService(w) }}</span>
                  </div>
                  <div class="work-verdict" [class.muted]="!w.verdict">{{ w.verdict || w.title }}</div>
                  <div class="work-meta">
                    {{ sourceLabel(w.source) }}@if (w.location){ · ◈ {{ w.location }} } · seit {{ relTime(w.oldest) }}@if (w.count>1){ · <b>{{ w.count }}× wiederkehrend</b> }
                  </div>
                </div>
              </div>
            } @empty {
              @if (loading()) { <div class="empty">Lade Prioritätenliste…</div> }
              @else { <div class="empty nominal"><div class="nom-ic">✓</div><div class="nom-tx">ALLE SYSTEME NOMINAL</div></div> }
            }
          </div>
        </main>

        <!-- ══ Right: vitals + logs ══ -->
        <aside class="rightcol">
          <div class="block">
            <div class="block-head">FLEET-VITALS</div>
            <div class="block-body">
              @for (v of status()?.vitals ?? []; track v.host + v.metric) {
                <div class="vital" (click)="openHost(v.host)">
                  <span class="v-lab">{{ v.label }}</span>
                  <span class="v-host">{{ short(v.host) }}</span>
                  <div class="v-bar"><div class="v-fill" [attr.data-level]="vitalLevel(v)" [style.width.%]="vitalPct(v)"></div></div>
                  <span class="v-val">{{ v.value }}{{ v.unit }}</span>
                </div>
              } @empty { <div class="rail-muted">Keine Metrikdaten</div> }
            </div>
          </div>

          <div class="block logs">
            <div class="block-head">LOGS · LIVE</div>
            <div class="block-body log-stream">
              @for (e of status()?.logs ?? []; track e.created_at + e.title) {
                <div class="log-line" [attr.data-sev]="e.severity" (click)="openLog(e)">
                  <span class="log-dot"></span>
                  <div class="log-body">
                    <span class="log-title">{{ e.title }}</span>
                    <span class="log-meta">{{ sourceLabel(e.source) }}@if (e.host){ · {{ short(e.host) }} } · {{ relTime(e.created_at) }}</span>
                  </div>
                </div>
              } @empty { <div class="rail-muted">Keine Logdaten</div> }
            </div>
          </div>
        </aside>
      </div>

      <!-- ══ Bottom sweep: number cells ══ -->
      <div class="botbar">
        <div class="cap cap-bl"></div>
        <div class="num-cell crit"><b>{{ status()?.counts?.critical ?? 0 }}</b><span>KRITISCH</span></div>
        <div class="num-cell high"><b>{{ status()?.counts?.high ?? 0 }}</b><span>HOCH</span></div>
        <div class="num-cell med"><b>{{ status()?.counts?.medium ?? 0 }}</b><span>MITTEL</span></div>
        <div class="num-cell open"><b>{{ status()?.worklist_open_count ?? 0 }}</b><span>OFFEN</span></div>
        <div class="bar-seg seg-c"></div>
        <div class="cap cap-br"></div>
      </div>
    </div>
  `,
  styles: [`
    :host { display:block; }
    .bridge { position:fixed; inset:0; z-index:100; display:flex; flex-direction:column; gap:6px; padding:8px;
      font-family:'Eurostile','Antonio','Michroma','Segoe UI',sans-serif; overflow:hidden; box-sizing:border-box; }
    .bridge *, .bridge *::before { box-sizing:border-box; }

    /* ════ shared layout ════ */
    .topbar, .botbar { display:flex; align-items:center; gap:6px; flex-shrink:0; height:46px; }
    .botbar { height:64px; }
    .cap { width:60px; height:100%; flex-shrink:0; }
    .bar-seg { height:100%; display:flex; align-items:center; padding:0 16px; font-weight:700; letter-spacing:.14em; font-size:14px; }
    .seg-a { flex-shrink:0; }
    .seg-c { flex:1; }
    .alert-banner { flex:1; text-align:center; font-weight:800; letter-spacing:.28em; font-size:16px; height:100%; display:flex; align-items:center; justify-content:center; }
    .clock { font-variant-numeric:tabular-nums; }
    .pill-btn { border:none; cursor:pointer; font-family:inherit; font-weight:800; letter-spacing:.1em; font-size:12px; height:100%; padding:0 16px; flex-shrink:0; }

    .cols { flex:1; display:grid; grid-template-columns:220px 1fr 330px; gap:6px; min-height:0; }

    /* left rail */
    .rail { display:flex; flex-direction:column; gap:6px; min-height:0; overflow-y:auto; padding:4px; }
    .rail-label { font-size:11px; font-weight:800; letter-spacing:.18em; padding:6px 8px 2px; flex-shrink:0; }
    .rail-pill { display:flex; align-items:center; justify-content:space-between; gap:8px; border:none; cursor:pointer;
      font-family:inherit; height:36px; padding:0 14px; font-weight:700; font-size:13px; flex-shrink:0; }
    .rp-name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .rp-val { font-weight:800; font-variant-numeric:tabular-nums; }
    .rail-muted { font-size:12px; opacity:.5; padding:4px 8px; }
    .rail-fill { flex:1; min-height:8px; }

    /* hero */
    .hero { display:flex; flex-direction:column; gap:8px; min-height:0; }
    .hero-head { display:flex; align-items:center; gap:14px; flex-wrap:wrap; padding:2px 6px; flex-shrink:0; }
    .hero-title { font-size:21px; font-weight:800; letter-spacing:.2em; }
    .hero-sub { font-size:12px; opacity:.7; flex:1; }
    .pill-btn.refresh { height:28px; border-radius:14px; }

    .forecast-strip { display:flex; align-items:center; gap:8px; flex-wrap:wrap; padding:8px 12px; border-radius:8px; flex-shrink:0; }
    .fc-icon { font-size:12px; font-weight:800; letter-spacing:.1em; }
    .fc-pill { font-size:12px; padding:3px 11px; border-radius:13px; cursor:pointer; }
    .fc-pill b { font-family:'Fira Code',monospace; }

    .worklist { flex:1; overflow-y:auto; display:flex; flex-direction:column; gap:7px; padding-right:4px; }
    .work-row { display:flex; gap:12px; padding:11px 14px; border-radius:8px; cursor:pointer; }
    .work-row:hover { filter:brightness(1.12); }
    .work-rank { font-size:28px; font-weight:800; min-width:34px; text-align:center; line-height:1.5; }
    .work-body { flex:1; min-width:0; display:flex; flex-direction:column; gap:3px; }
    .work-line1 { display:flex; align-items:baseline; gap:9px; flex-wrap:wrap; }
    .work-sev { font-size:10px; font-weight:800; letter-spacing:.1em; padding:2px 8px; border-radius:4px; }
    .work-host { font-size:15px; font-weight:700; font-family:'Fira Code',monospace; }
    .work-svc { font-size:13px; opacity:.85; }
    .work-verdict { font-size:13px; line-height:1.5; }
    .work-verdict.muted { opacity:.6; }
    .work-meta { font-size:11px; opacity:.6; }
    .empty { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:8px; opacity:.7; }
    .nom-ic { font-size:72px; } .nom-tx { font-size:24px; font-weight:800; letter-spacing:.2em; }

    /* right column */
    .rightcol { display:flex; flex-direction:column; gap:6px; min-height:0; }
    .block { display:flex; flex-direction:column; min-height:0; border-radius:8px; overflow:hidden; }
    .block.logs { flex:1; }
    .block-head { font-size:11px; font-weight:800; letter-spacing:.18em; padding:8px 12px; flex-shrink:0; }
    .block-body { padding:8px; overflow-y:auto; display:flex; flex-direction:column; gap:5px; }
    .logs .block-body { flex:1; }

    .vital { display:flex; align-items:center; gap:8px; padding:4px 6px; border-radius:5px; cursor:pointer; }
    .v-lab { font-size:10px; font-weight:800; width:30px; flex-shrink:0; }
    .v-host { font-size:11px; font-family:'Fira Code',monospace; width:78px; flex-shrink:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .v-bar { flex:1; height:8px; border-radius:4px; overflow:hidden; }
    .v-fill { height:100%; border-radius:4px; }
    .v-val { font-size:11px; font-weight:700; width:46px; text-align:right; flex-shrink:0; font-variant-numeric:tabular-nums; }

    .log-line { display:flex; gap:8px; padding:6px 8px; border-radius:5px; cursor:pointer; }
    .log-line:hover { filter:brightness(1.2); }
    .log-dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; margin-top:4px; }
    .log-body { display:flex; flex-direction:column; min-width:0; gap:2px; }
    /* logs: WRAP so the problem is readable, max 2 lines */
    .log-title { font-size:12px; font-weight:600; line-height:1.35; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
    .log-meta { font-size:10px; opacity:.6; }

    /* number cells */
    .num-cell { height:100%; min-width:110px; display:flex; flex-direction:column; align-items:flex-start; justify-content:center; padding:0 18px; }
    .num-cell b { font-size:30px; font-weight:800; line-height:1; }
    .num-cell span { font-size:10px; font-weight:700; letter-spacing:.18em; margin-top:4px; }

    @keyframes redPulse { 0%,100%{opacity:1} 50%{opacity:.5} }

    /* ═══════════════ THEME: LCARS ═══════════════ */
    .t-lcars { background:#000; color:#ff9966; font-family:'Antonio','Eurostile',sans-serif; text-transform:uppercase; }
    .t-lcars .work-verdict, .t-lcars .log-title { text-transform:none; }  /* keep prose readable */
    .t-lcars .seg-a, .t-lcars .hero-title, .t-lcars .rail-pill, .t-lcars .num-cell b, .t-lcars .num-cell span,
    .t-lcars .block-head, .t-lcars .rail-label, .t-lcars .work-host { font-weight:700; letter-spacing:.06em; }
    /* top sweep: orange bar, rounded outer ends = the LCARS elbow caps */
    .t-lcars .cap { background:#ff9966; }
    .t-lcars .cap-tl { border-radius:46px 0 0 0; }
    .t-lcars .cap-tr { border-radius:0 46px 0 0; width:30px; }
    .t-lcars .cap-bl { border-radius:0 0 0 46px; }
    .t-lcars .cap-br { border-radius:0 0 46px 0; width:30px; }
    .t-lcars .seg-a { background:#ffcc66; color:#000; border-radius:0; min-width:200px; justify-content:flex-end; }
    .t-lcars .seg-b { background:#cc99cc; color:#000; }
    .t-lcars .seg-c { background:#9999cc; }
    .t-lcars .alert-banner { background:#000; color:#ffcc66; }
    .t-lcars[data-alert="red"] .alert-banner { color:#ff5544; animation:redPulse 1s infinite; }
    .t-lcars[data-alert="yellow"] .alert-banner { color:#ffcc00; }
    .t-lcars .pill-btn.theme { background:#9999cc; color:#000; }
    .t-lcars .pill-btn.exit { background:#cc6666; color:#000; }
    /* left rail pills — the classic LCARS sidebar */
    .t-lcars .rail-label { color:#ffcc66; }
    .t-lcars .rail-pill { background:#ff9966; color:#000; border-radius:0 18px 18px 0; }
    .t-lcars .rail-pill:nth-child(3n) { background:#cc99cc; }
    .t-lcars .rail-pill:nth-child(3n+1) { background:#ffcc66; }
    .t-lcars .rail-pill[data-state="red"] { background:#ff5544; }
    .t-lcars .rail-pill[data-state="yellow"] { background:#ffcc00; }
    .t-lcars .hero-title { color:#ff9966; }
    .t-lcars .pill-btn.refresh { background:#9999cc; color:#000; }
    .t-lcars .forecast-strip { background:#2a1d0a; border:1px solid #ffcc00; }
    .t-lcars .fc-icon { color:#ffcc00; } .t-lcars .fc-pill { background:#ffcc00; color:#000; }
    .t-lcars .work-row { background:#15120c; border-left:7px solid #ffcc66; border-radius:0 8px 8px 0; }
    .t-lcars .work-row[data-sev="critical"] { border-left-color:#ff5544; background:#1f0d0a; }
    .t-lcars .work-row[data-sev="high"] { border-left-color:#ffcc00; }
    .t-lcars .work-rank { color:#ff9966; }
    .t-lcars .work-sev { background:#ffcc66; color:#000; }
    .t-lcars .work-row[data-sev="critical"] .work-sev { background:#ff5544; }
    .t-lcars .work-host { color:#ffcc99; } .t-lcars .work-svc { color:#cc99cc; }
    .t-lcars .work-verdict { color:#ffcc99; }
    .t-lcars .nom-ic, .t-lcars .nom-tx { color:#66cc66; }
    .t-lcars .block { background:#15120c; }
    .t-lcars .block-head { background:#ff9966; color:#000; }
    .t-lcars .block.logs .block-head { background:#cc99cc; }
    .t-lcars .v-lab { color:#ff9966; } .t-lcars .v-host, .t-lcars .v-val { color:#ffcc99; }
    .t-lcars .v-bar { background:#000; }
    .t-lcars .v-fill[data-level="ok"]{ background:#66cc66; } .t-lcars .v-fill[data-level="high"]{ background:#ffcc00; } .t-lcars .v-fill[data-level="crit"]{ background:#ff5544; }
    .t-lcars .log-line { background:#000; }
    .t-lcars .log-title { color:#ffcc99; } .t-lcars .log-dot { background:#66cc66; }
    .t-lcars .log-line[data-sev="critical"] .log-dot { background:#ff5544; } .t-lcars .log-line[data-sev="high"] .log-dot { background:#ffcc00; }
    .t-lcars .num-cell.crit { background:#ff5544; color:#000; } .t-lcars .num-cell.high { background:#ffcc00; color:#000; }
    .t-lcars .num-cell.med { background:#ffcc66; color:#000; } .t-lcars .num-cell.open { background:#9999cc; color:#000; }

    /* ═══════════════ THEME: HOLO ═══════════════ */
    .t-holo { color:#7fdfff; background:radial-gradient(circle at 50% 20%,rgba(20,60,90,.5),transparent 60%),linear-gradient(160deg,#02060f,#050d1a 60%,#02060f); }
    .t-holo::before { content:''; position:fixed; inset:0; pointer-events:none; opacity:.2; background-image:linear-gradient(rgba(64,180,230,.12) 1px,transparent 1px),linear-gradient(90deg,rgba(64,180,230,.12) 1px,transparent 1px); background-size:44px 44px; }
    .t-holo .cap { display:none; }
    .t-holo .seg-a { background:rgba(79,214,255,.12); color:#9fe8ff; border:1px solid rgba(79,214,255,.3); border-radius:8px; }
    .t-holo .seg-b { background:rgba(79,214,255,.08); color:#9fe8ff; border:1px solid rgba(79,214,255,.25); border-radius:8px; }
    .t-holo .seg-c { display:none; }
    .t-holo .alert-banner { color:#7fdfff; text-shadow:0 0 14px rgba(79,214,255,.5); }
    .t-holo[data-alert="red"] .alert-banner { color:#ff5b6e; animation:redPulse 1s infinite; }
    .t-holo[data-alert="yellow"] .alert-banner { color:#ffd84a; }
    .t-holo .pill-btn.theme { background:rgba(79,214,255,.15); color:#9fe8ff; border:1px solid #4fd6ff; border-radius:8px; }
    .t-holo .pill-btn.exit { background:transparent; color:#9fe8ff; border:1px solid #4fd6ff; border-radius:8px; }
    .t-holo .rail-label { color:#5fc8ee; }
    .t-holo .rail-pill { background:rgba(10,28,46,.6); color:#bfefff; border:1px solid rgba(79,214,255,.25); border-radius:8px; }
    .t-holo .rail-pill[data-state="red"] { border-color:#ff5b6e; color:#ff8b98; }
    .t-holo .rail-pill[data-state="yellow"] { border-color:#ffd84a; color:#ffe27a; }
    .t-holo .hero-title { color:#cff6ff; text-shadow:0 0 12px rgba(79,214,255,.4); }
    .t-holo .pill-btn.refresh { background:rgba(79,214,255,.15); color:#9fe8ff; border:1px solid #4fd6ff; }
    .t-holo .forecast-strip { background:rgba(255,216,74,.08); border:1px solid rgba(255,216,74,.4); }
    .t-holo .fc-icon { color:#ffe27a; } .t-holo .fc-pill { background:rgba(255,216,74,.15); color:#ffe27a; border:1px solid rgba(255,216,74,.4); }
    .t-holo .work-row { background:rgba(10,28,46,.6); border:1px solid rgba(79,214,255,.22); }
    .t-holo .work-row[data-sev="critical"] { border-color:#ff5b6e; box-shadow:0 0 22px rgba(255,91,110,.18); }
    .t-holo .work-row[data-sev="high"] { border-color:rgba(255,216,74,.5); }
    .t-holo .work-rank { color:#4fd6ff; } .t-holo .work-sev { background:rgba(79,214,255,.2); color:#9fe8ff; border:1px solid #4fd6ff; }
    .t-holo .work-row[data-sev="critical"] .work-sev { background:rgba(255,91,110,.2); color:#ff8b98; border-color:#ff5b6e; }
    .t-holo .work-host { color:#cff6ff; } .t-holo .work-svc { color:#8fd0e8; } .t-holo .work-verdict { color:#8fd0e8; }
    .t-holo .nom-ic, .t-holo .nom-tx { color:#3dffa8; }
    .t-holo .block { background:rgba(10,28,46,.55); border:1px solid rgba(79,214,255,.22); }
    .t-holo .block-head { background:rgba(79,214,255,.1); color:#9fe8ff; }
    .t-holo .v-lab { color:#5fc8ee; } .t-holo .v-host,.t-holo .v-val { color:#cff6ff; }
    .t-holo .v-bar { background:rgba(79,214,255,.1); }
    .t-holo .v-fill[data-level="ok"]{ background:#3dffa8; } .t-holo .v-fill[data-level="high"]{ background:#ffd84a; } .t-holo .v-fill[data-level="crit"]{ background:#ff5b6e; }
    .t-holo .log-line { background:rgba(79,214,255,.04); } .t-holo .log-title { color:#bfefff; } .t-holo .log-dot { background:#3dffa8; }
    .t-holo .log-line[data-sev="critical"] .log-dot { background:#ff5b6e; } .t-holo .log-line[data-sev="high"] .log-dot { background:#ffd84a; }
    .t-holo .num-cell { border:1px solid rgba(79,214,255,.25); border-radius:8px; }
    .t-holo .num-cell.crit b { color:#ff5b6e; } .t-holo .num-cell.high b { color:#ffd84a; } .t-holo .num-cell.open b { color:#4fd6ff; }

    @media (max-width:1200px){ .cols{ grid-template-columns:1fr; grid-auto-rows:min-content; overflow-y:auto; } }
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
  theme = signal<'lcars' | 'holo'>((localStorage.getItem('bridge_theme') as 'lcars'|'holo') || 'lcars');

  private pollTimer?: ReturnType<typeof setInterval>;
  private clockTimer?: ReturnType<typeof setInterval>;
  private wsSub?: import('rxjs').Subscription;

  alertLabel = computed(() => {
    const s = this.status()?.alert_state;
    if (s === 'red') return '🔴 RED ALERT';
    if (s === 'yellow') return '🟡 ERHÖHTE WACHSAMKEIT';
    return '🟢 ALLE SYSTEME NOMINAL';
  });
  worklistAge = computed(() => { const u = this.status()?.worklist_updated; return u ? this.relTime(u) : '—'; });

  ngOnInit() {
    this.load();
    this.tickClock();
    this.pollTimer = setInterval(() => this.load(), 15_000);
    this.clockTimer = setInterval(() => this.tickClock(), 1000);
    this.wsSub = this.ws.messages().subscribe((m: any) => { if (m?.type === 'ai_insight' || m?.type === 'alert') this.load(); });
  }
  ngOnDestroy() {
    if (this.pollTimer) clearInterval(this.pollTimer);
    if (this.clockTimer) clearInterval(this.clockTimer);
    this.wsSub?.unsubscribe();
  }

  private tickClock() {
    const d = new Date(); const p = (n: number) => String(n).padStart(2, '0');
    this.clock.set(`${p(d.getDate())}.${p(d.getMonth()+1)} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`);
  }
  load() {
    this.http.get<BridgeStatus>(`${environment.apiUrl}/bridge/status`).subscribe({
      next: s => { this.status.set(s); this.loading.set(false); }, error: () => this.loading.set(false),
    });
  }
  refreshWorklist() {
    this.refreshing.set(true);
    this.http.post(`${environment.apiUrl}/bridge/refresh-worklist`, {}).subscribe({
      next: () => { this.refreshing.set(false); this.load(); }, error: () => this.refreshing.set(false),
    });
  }
  toggleTheme() { const n = this.theme()==='lcars'?'holo':'lcars'; this.theme.set(n); localStorage.setItem('bridge_theme', n); }
  exit() { this.router.navigate(['/dashboard']); }
  openItem(w: WorkItem) { this.router.navigate(['/feed'], { queryParams: { severity: w.severity, host: w.host || undefined } }); }
  openLog(e: LogEntry) { this.router.navigate(['/feed'], { queryParams: { source: e.source, host: e.host || undefined } }); }
  openSource(src: string) { if (src) this.router.navigate(['/feed'], { queryParams: { source: src } }); }
  openHost(h: string) { this.router.navigate(['/feed'], { queryParams: { host: h } }); }

  etaLabel(h: number): string { if (h < 1) return `${Math.round(h*60)}Min`; if (h < 48) return `~${Math.round(h)}Std`; return `~${Math.round(h/24)}Tg`; }
  vitalPct(v: Vital): number { return v.unit === '%' ? Math.min(100, v.value) : Math.min(100, (v.value/8)*100); }
  vitalLevel(v: Vital): string { const p = this.vitalPct(v); return p >= 90 ? 'crit' : p >= 75 ? 'high' : 'ok'; }
  short(h: string): string { return (h || '').split('.')[0]; }
  workService(w: WorkItem): string {
    const dash = w.title.indexOf(' — ');
    if (dash > 0 && w.host && w.title.toLowerCase().startsWith(this.short(w.host).toLowerCase())) return w.title.slice(dash + 3);
    return w.title;
  }
  sourceLabel(src: string): string {
    const m: Record<string,string> = { checkmk:'CheckMK', graylog:'Graylog', wazuh:'Wazuh', o365:'E-Mail', teams:'Teams' };
    return m[src] ?? (src||'').toUpperCase();
  }
  relTime(iso: string): string {
    if (!iso) return ''; const mins = Math.floor((Date.now() - new Date(iso).getTime())/60000);
    if (mins < 1) return 'gerade'; if (mins < 60) return `${mins} Min`;
    const h = Math.floor(mins/60); if (h < 24) return `${h} Std`; return `${Math.floor(h/24)} Tg`;
  }
}
