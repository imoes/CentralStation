import { Component, OnInit, OnDestroy, signal, computed, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { environment } from '../../../environments/environment';
import { WebsocketService } from '../../core/services/websocket.service';

interface SourceStatus { name: string; state: string; critical: number; high: number; total: number; }
interface SectorStatus { name: string; state: string; critical: number; high: number; total: number; }
interface SensorEntry { severity: string; source: string; title: string; host: string; created_at: string; }
interface PrimaryIncident { severity: string; source: string; title: string; host: string; location: string; ai_insight: string; created_at: string; }
interface BridgeStatus {
  alert_state: 'red' | 'yellow' | 'green';
  counts: { critical: number; high: number; medium: number; total: number };
  sources: SourceStatus[];
  sectors: SectorStatus[];
  primary_incident: PrimaryIncident | null;
  sensor_log: SensorEntry[];
  stardate: string;
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
        <div class="brand">
          <span class="brand-mark"></span>
          <span class="brand-text">CENTRALSTATION · BRIDGE</span>
        </div>
        <div class="alert-banner" [attr.data-state]="status()?.alert_state ?? 'green'">
          {{ alertLabel() }}
        </div>
        <div class="top-right">
          <span class="stardate">{{ stardate() }}</span>
          <button class="theme-btn" (click)="toggleTheme()">{{ theme() === 'lcars' ? 'HOLO' : 'LCARS' }}</button>
          <button class="exit-btn" (click)="exit()">✕</button>
        </div>
      </header>

      <div class="bridge-grid">

        <!-- ── Left: ship systems (sources) ───────────────────────────── -->
        <aside class="panel systems">
          <div class="panel-label">SYSTEME</div>
          @for (s of status()?.sources ?? []; track s.name) {
            <div class="system-row" [attr.data-state]="s.state">
              <span class="system-light"></span>
              <span class="system-name">{{ sourceLabel(s.name) }}</span>
              <span class="system-stat">
                @if (s.critical) { <span class="badge crit">{{ s.critical }}</span> }
                @if (s.high) { <span class="badge high">{{ s.high }}</span> }
                @if (!s.critical && !s.high) { <span class="badge ok">OK</span> }
              </span>
            </div>
          }
          <div class="panel-label sectors-label">SEKTOREN</div>
          @for (sec of status()?.sectors ?? []; track sec.name) {
            <div class="system-row" [attr.data-state]="sec.state">
              <span class="system-light"></span>
              <span class="system-name">{{ sec.name }}</span>
              <span class="system-stat">
                @if (sec.critical) { <span class="badge crit">{{ sec.critical }}</span> }
                @else if (sec.high) { <span class="badge high">{{ sec.high }}</span> }
                @else { <span class="badge ok">OK</span> }
              </span>
            </div>
          } @empty {
            <div class="sector-empty">Keine Standortdaten</div>
          }
        </aside>

        <!-- ── Center: the one thing you look at ──────────────────────── -->
        <main class="viewscreen">
          <!-- Big status numbers -->
          <div class="vitals">
            <div class="vital crit" [class.pulse]="(status()?.counts?.critical ?? 0) > 0">
              <span class="vital-num">{{ status()?.counts?.critical ?? 0 }}</span>
              <span class="vital-label">KRITISCH</span>
            </div>
            <div class="vital high">
              <span class="vital-num">{{ status()?.counts?.high ?? 0 }}</span>
              <span class="vital-label">HOCH</span>
            </div>
            <div class="vital total">
              <span class="vital-num">{{ status()?.counts?.total ?? 0 }}</span>
              <span class="vital-label">AKTIV</span>
            </div>
          </div>

          <!-- Primary incident -->
          @if (status()?.primary_incident; as inc) {
            <div class="incident" [attr.data-sev]="inc.severity" (click)="openIncident(inc)">
              <div class="incident-head">
                <span class="incident-sev">{{ inc.severity | uppercase }}</span>
                <span class="incident-src">{{ sourceLabel(inc.source) }}</span>
                @if (inc.location) { <span class="incident-loc">◈ {{ inc.location }}</span> }
              </div>
              <div class="incident-title">{{ inc.title }}</div>
              @if (inc.host) { <div class="incident-host">▸ {{ inc.host }}</div> }
              @if (inc.ai_insight) {
                <div class="incident-ai"><span class="ai-tag">KI-LAGE</span> {{ inc.ai_insight }}</div>
              }
            </div>
          } @else {
            <div class="incident nominal">
              <div class="nominal-icon">✓</div>
              <div class="nominal-text">ALLE SYSTEME NOMINAL</div>
              <div class="nominal-sub">Keine aktiven kritischen Vorfälle</div>
            </div>
          }
        </main>

        <!-- ── Right: live sensor log ─────────────────────────────────── -->
        <aside class="panel sensor">
          <div class="panel-label">SENSOR-LOG</div>
          <div class="sensor-stream">
            @for (e of status()?.sensor_log ?? []; track e.created_at + e.title) {
              <div class="sensor-line" [attr.data-sev]="e.severity">
                <span class="sensor-dot"></span>
                <div class="sensor-body">
                  <span class="sensor-title">{{ e.title }}</span>
                  <span class="sensor-meta">{{ sourceLabel(e.source) }}@if (e.host) { · {{ e.host }} } · {{ relTime(e.created_at) }}</span>
                </div>
              </div>
            } @empty {
              <div class="sector-empty">Keine Sensordaten</div>
            }
          </div>
        </aside>
      </div>

      @if (loading()) { <div class="boot">INITIALISIERE BRÜCKENSYSTEME…</div> }
    </div>
  `,
  styles: [`
    :host { display: block; }
    .bridge {
      position: fixed; inset: 0; z-index: 100;
      display: flex; flex-direction: column;
      font-family: 'Eurostile', 'Michroma', 'Orbitron', 'Segoe UI', sans-serif;
      overflow: hidden;
    }

    /* ══════════════ Layout (theme-agnostic) ══════════════ */
    .bridge-top { display: flex; align-items: center; gap: 16px; padding: 10px 20px; flex-shrink: 0; }
    .brand { display: flex; align-items: center; gap: 10px; }
    .brand-mark { width: 26px; height: 26px; border-radius: 50%; }
    .brand-text { font-weight: 700; letter-spacing: .18em; font-size: 14px; }
    .alert-banner { flex: 1; text-align: center; font-weight: 800; letter-spacing: .35em; font-size: 18px; }
    .top-right { display: flex; align-items: center; gap: 12px; }
    .stardate { font-size: 12px; letter-spacing: .1em; opacity: .8; }
    .theme-btn, .exit-btn { border: none; cursor: pointer; font-family: inherit; font-weight: 700; letter-spacing: .1em; padding: 5px 12px; border-radius: 4px; font-size: 12px; }
    .exit-btn { padding: 5px 10px; }

    .bridge-grid {
      flex: 1; display: grid; gap: 14px; padding: 0 20px 20px;
      grid-template-columns: 270px 1fr 320px;
      min-height: 0;
    }
    .panel { display: flex; flex-direction: column; gap: 8px; padding: 14px; border-radius: 10px; overflow: hidden; }
    .panel-label { font-size: 11px; font-weight: 800; letter-spacing: .25em; opacity: .75; margin-bottom: 4px; }
    .sectors-label { margin-top: 16px; }

    .system-row { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 6px; }
    .system-light { width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }
    .system-name { flex: 1; font-size: 13px; font-weight: 600; letter-spacing: .05em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .badge { font-size: 11px; font-weight: 800; padding: 1px 8px; border-radius: 10px; }
    .sector-empty { font-size: 12px; opacity: .5; padding: 8px 10px; }

    /* Viewscreen */
    .viewscreen { display: flex; flex-direction: column; gap: 14px; min-height: 0; }
    .vitals { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; flex-shrink: 0; }
    .vital { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 18px; border-radius: 12px; }
    .vital-num { font-size: 64px; font-weight: 800; line-height: 1; }
    .vital-label { font-size: 13px; font-weight: 700; letter-spacing: .25em; margin-top: 8px; opacity: .85; }

    .incident { flex: 1; border-radius: 14px; padding: 26px; display: flex; flex-direction: column; gap: 12px; cursor: pointer; min-height: 0; overflow: auto; }
    .incident-head { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
    .incident-sev { font-size: 14px; font-weight: 800; letter-spacing: .2em; padding: 3px 12px; border-radius: 6px; }
    .incident-src, .incident-loc { font-size: 13px; letter-spacing: .1em; opacity: .8; }
    .incident-title { font-size: 30px; font-weight: 700; line-height: 1.2; }
    .incident-host { font-size: 16px; font-family: monospace; opacity: .85; }
    .incident-ai { font-size: 15px; line-height: 1.6; padding: 14px 16px; border-radius: 8px; }
    .ai-tag { font-size: 11px; font-weight: 800; letter-spacing: .15em; padding: 2px 8px; border-radius: 4px; margin-right: 8px; }

    .incident.nominal { align-items: center; justify-content: center; text-align: center; cursor: default; }
    .nominal-icon { font-size: 90px; line-height: 1; }
    .nominal-text { font-size: 30px; font-weight: 800; letter-spacing: .2em; margin-top: 10px; }
    .nominal-sub { font-size: 15px; opacity: .7; margin-top: 6px; letter-spacing: .1em; }

    /* Sensor */
    .sensor { }
    .sensor-stream { display: flex; flex-direction: column; gap: 6px; overflow-y: auto; flex: 1; }
    .sensor-line { display: flex; gap: 8px; padding: 7px 8px; border-radius: 6px; }
    .sensor-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; margin-top: 5px; }
    .sensor-body { display: flex; flex-direction: column; min-width: 0; }
    .sensor-title { font-size: 12px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .sensor-meta { font-size: 10px; opacity: .6; letter-spacing: .03em; }

    .boot { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-size: 18px; letter-spacing: .3em; }

    @keyframes pulseGlow { 0%,100% { opacity: 1; } 50% { opacity: .45; } }
    .pulse { animation: pulseGlow 1.1s infinite; }
    @keyframes redBannerPulse { 0%,100% { opacity: 1; } 50% { opacity: .55; } }

    /* ══════════════ THEME: LCARS (Star Trek TNG) ══════════════ */
    .theme-lcars { background: #000; color: #ffcc66; }
    .theme-lcars .brand-mark { background: #ff9966; }
    .theme-lcars .brand-text { color: #ff9966; }
    .theme-lcars .stardate { color: #cc99cc; }
    .theme-lcars .theme-btn { background: #cc99cc; color: #000; }
    .theme-lcars .exit-btn { background: #ff9966; color: #000; }
    .theme-lcars .alert-banner { color: #ffcc66; }
    .theme-lcars[data-alert="red"] .alert-banner { color: #ff4444; animation: redBannerPulse 1s infinite; }
    .theme-lcars[data-alert="yellow"] .alert-banner { color: #ffcc00; }
    .theme-lcars .panel { background: #1a1a1a; border-left: 8px solid #cc6666; border-radius: 0 16px 16px 0; }
    .theme-lcars .sensor { border-left: none; border-right: 8px solid #9999cc; border-radius: 16px 0 0 16px; }
    .theme-lcars .panel-label { color: #ff9966; }
    .theme-lcars .system-row { background: #262010; }
    .theme-lcars .system-name { color: #ffcc66; }
    .theme-lcars .system-light { background: #66cc66; box-shadow: 0 0 8px #66cc66; }
    .theme-lcars .system-row[data-state="red"] .system-light { background: #ff4444; box-shadow: 0 0 10px #ff4444; }
    .theme-lcars .system-row[data-state="yellow"] .system-light { background: #ffcc00; box-shadow: 0 0 10px #ffcc00; }
    .theme-lcars .badge.crit { background: #ff4444; color: #000; }
    .theme-lcars .badge.high { background: #ffcc00; color: #000; }
    .theme-lcars .badge.ok { background: #336633; color: #99ff99; }
    .theme-lcars .vital { background: #1a1a1a; border-radius: 16px; }
    .theme-lcars .vital.crit { background: #2a0a0a; } .theme-lcars .vital.crit .vital-num { color: #ff5555; }
    .theme-lcars .vital.high .vital-num { color: #ffcc00; }
    .theme-lcars .vital.total .vital-num { color: #99ccff; }
    .theme-lcars .vital-label { color: #ff9966; }
    .theme-lcars .incident { background: #1a1a1a; border-radius: 24px; }
    .theme-lcars .incident[data-sev="critical"] { background: #2a0a0a; box-shadow: inset 0 0 0 2px #ff4444; }
    .theme-lcars .incident-sev { background: #ff9966; color: #000; }
    .theme-lcars .incident[data-sev="critical"] .incident-sev { background: #ff4444; }
    .theme-lcars .incident-title { color: #ffcc66; }
    .theme-lcars .incident-ai { background: #0d0d0d; color: #cc99cc; }
    .theme-lcars .ai-tag { background: #cc99cc; color: #000; }
    .theme-lcars .nominal-icon, .theme-lcars .nominal-text { color: #66cc66; }
    .theme-lcars .sensor-line { background: #16160c; }
    .theme-lcars .sensor-dot { background: #66cc66; }
    .theme-lcars .sensor-line[data-sev="critical"] .sensor-dot { background: #ff4444; }
    .theme-lcars .sensor-line[data-sev="high"] .sensor-dot { background: #ffcc00; }
    .theme-lcars .boot { color: #ff9966; }

    /* ══════════════ THEME: HOLO-HUD (modern blue) ══════════════ */
    .theme-holo {
      color: #7fdfff;
      background:
        radial-gradient(circle at 50% 30%, rgba(20,60,90,.5), transparent 60%),
        linear-gradient(160deg, #02060f, #050d1a 60%, #02060f);
    }
    .theme-holo::before {
      content: ''; position: absolute; inset: 0; pointer-events: none; opacity: .25;
      background-image: linear-gradient(rgba(64,180,230,.12) 1px, transparent 1px),
                        linear-gradient(90deg, rgba(64,180,230,.12) 1px, transparent 1px);
      background-size: 44px 44px;
    }
    .theme-holo .brand-mark { background: radial-gradient(circle, #4fd6ff, #1a6c9c); box-shadow: 0 0 14px #4fd6ff; }
    .theme-holo .brand-text { color: #9fe8ff; text-shadow: 0 0 10px rgba(79,214,255,.6); }
    .theme-holo .stardate { color: #5fa8d0; }
    .theme-holo .theme-btn { background: rgba(79,214,255,.15); color: #9fe8ff; border: 1px solid #4fd6ff; }
    .theme-holo .exit-btn { background: transparent; color: #9fe8ff; border: 1px solid #4fd6ff; }
    .theme-holo .alert-banner { color: #7fdfff; text-shadow: 0 0 14px rgba(79,214,255,.5); }
    .theme-holo[data-alert="red"] .alert-banner { color: #ff5b6e; text-shadow: 0 0 18px rgba(255,91,110,.8); animation: redBannerPulse 1s infinite; }
    .theme-holo[data-alert="yellow"] .alert-banner { color: #ffd84a; text-shadow: 0 0 16px rgba(255,216,74,.7); }
    .theme-holo .panel { background: rgba(10,28,46,.55); border: 1px solid rgba(79,214,255,.25); backdrop-filter: blur(6px); box-shadow: inset 0 0 30px rgba(79,214,255,.06); }
    .theme-holo .panel-label { color: #5fc8ee; }
    .theme-holo .system-row { background: rgba(79,214,255,.05); border: 1px solid rgba(79,214,255,.12); }
    .theme-holo .system-name { color: #bfefff; }
    .theme-holo .system-light { background: #3dffa8; box-shadow: 0 0 10px #3dffa8; }
    .theme-holo .system-row[data-state="red"] .system-light { background: #ff5b6e; box-shadow: 0 0 12px #ff5b6e; }
    .theme-holo .system-row[data-state="yellow"] .system-light { background: #ffd84a; box-shadow: 0 0 12px #ffd84a; }
    .theme-holo .badge.crit { background: rgba(255,91,110,.2); color: #ff8b98; border: 1px solid #ff5b6e; }
    .theme-holo .badge.high { background: rgba(255,216,74,.18); color: #ffe27a; border: 1px solid #ffd84a; }
    .theme-holo .badge.ok { background: rgba(61,255,168,.12); color: #7dffc6; border: 1px solid #3dffa8; }
    .theme-holo .vital { background: rgba(10,28,46,.6); border: 1px solid rgba(79,214,255,.25); box-shadow: inset 0 0 24px rgba(79,214,255,.08); }
    .theme-holo .vital.crit .vital-num { color: #ff5b6e; text-shadow: 0 0 18px rgba(255,91,110,.7); }
    .theme-holo .vital.high .vital-num { color: #ffd84a; text-shadow: 0 0 18px rgba(255,216,74,.6); }
    .theme-holo .vital.total .vital-num { color: #4fd6ff; text-shadow: 0 0 18px rgba(79,214,255,.6); }
    .theme-holo .vital-label { color: #5fc8ee; }
    .theme-holo .incident { background: rgba(10,28,46,.6); border: 1px solid rgba(79,214,255,.3); backdrop-filter: blur(6px); }
    .theme-holo .incident[data-sev="critical"] { border-color: #ff5b6e; box-shadow: 0 0 40px rgba(255,91,110,.25), inset 0 0 30px rgba(255,91,110,.08); }
    .theme-holo .incident-sev { background: rgba(79,214,255,.2); color: #9fe8ff; border: 1px solid #4fd6ff; }
    .theme-holo .incident[data-sev="critical"] .incident-sev { background: rgba(255,91,110,.2); color: #ff8b98; border-color: #ff5b6e; }
    .theme-holo .incident-title { color: #cff6ff; text-shadow: 0 0 12px rgba(79,214,255,.3); }
    .theme-holo .incident-ai { background: rgba(2,12,22,.7); color: #8fd0e8; border: 1px solid rgba(79,214,255,.2); }
    .theme-holo .ai-tag { background: rgba(79,214,255,.2); color: #9fe8ff; border: 1px solid #4fd6ff; }
    .theme-holo .nominal-icon, .theme-holo .nominal-text { color: #3dffa8; text-shadow: 0 0 20px rgba(61,255,168,.5); }
    .theme-holo .sensor-line { background: rgba(79,214,255,.04); }
    .theme-holo .sensor-dot { background: #3dffa8; box-shadow: 0 0 6px #3dffa8; }
    .theme-holo .sensor-line[data-sev="critical"] .sensor-dot { background: #ff5b6e; box-shadow: 0 0 8px #ff5b6e; }
    .theme-holo .sensor-line[data-sev="high"] .sensor-dot { background: #ffd84a; box-shadow: 0 0 8px #ffd84a; }
    .theme-holo .boot { color: #9fe8ff; }

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
  theme = signal<'lcars' | 'holo'>(
    (localStorage.getItem('bridge_theme') as 'lcars' | 'holo') || 'lcars'
  );

  private pollTimer?: ReturnType<typeof setInterval>;
  private wsSub?: import('rxjs').Subscription;

  alertLabel = computed(() => {
    const s = this.status()?.alert_state;
    if (s === 'red') return '🔴 RED ALERT';
    if (s === 'yellow') return '🟡 YELLOW ALERT';
    return '🟢 ALLE SYSTEME NOMINAL';
  });

  stardate = computed(() => {
    const iso = this.status()?.stardate;
    if (!iso) return '';
    const d = new Date(iso);
    // playful "stardate": YYDDD.HHMM
    const start = new Date(d.getFullYear(), 0, 0);
    const day = Math.floor((d.getTime() - start.getTime()) / 86400000);
    const hm = `${String(d.getHours()).padStart(2, '0')}${String(d.getMinutes()).padStart(2, '0')}`;
    return `STERNZEIT ${String(d.getFullYear()).slice(2)}${day}.${hm}`;
  });

  ngOnInit() {
    this.load();
    this.pollTimer = setInterval(() => this.load(), 10_000);
    this.wsSub = this.ws.messages().subscribe((msg: any) => {
      if (msg?.type === 'ai_insight' || msg?.type === 'alert') this.load();
    });
  }

  ngOnDestroy() {
    if (this.pollTimer) clearInterval(this.pollTimer);
    this.wsSub?.unsubscribe();
  }

  load() {
    this.http.get<BridgeStatus>(`${environment.apiUrl}/bridge/status`).subscribe({
      next: s => { this.status.set(s); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }

  toggleTheme() {
    const next = this.theme() === 'lcars' ? 'holo' : 'lcars';
    this.theme.set(next);
    localStorage.setItem('bridge_theme', next);
  }

  exit() { this.router.navigate(['/dashboard']); }

  openIncident(inc: PrimaryIncident) {
    this.router.navigate(['/feed'], {
      queryParams: { severity: inc.severity, host: inc.host || undefined },
    });
  }

  sourceLabel(src: string): string {
    const m: Record<string, string> = {
      checkmk: 'CHECKMK', graylog: 'GRAYLOG', wazuh: 'WAZUH', o365: 'E-MAIL', teams: 'TEAMS',
    };
    return m[src] ?? (src || '').toUpperCase();
  }

  relTime(iso: string): string {
    if (!iso) return '';
    const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
    if (mins < 1) return 'jetzt';
    if (mins < 60) return `${mins}m`;
    const h = Math.floor(mins / 60);
    if (h < 24) return `${h}h`;
    return `${Math.floor(h / 24)}d`;
  }
}
