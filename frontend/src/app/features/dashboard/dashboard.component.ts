import {
  AfterViewInit,
  Component,
  ElementRef,
  Injector,
  OnDestroy,
  ViewChild,
  afterNextRender,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { forkJoin } from 'rxjs';
import { GridItemHTMLElement, GridStack } from 'gridstack';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatIconModule } from '@angular/material/icon';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatTooltipModule } from '@angular/material/tooltip';
import { environment } from '../../../environments/environment';
import { AddWidgetDialogComponent } from './add-widget-dialog.component';
import { DashboardWidgetComponent } from './dashboard-widget.component';
import {
  DashboardWidget,
  DashboardWidgetCreate,
  Dashboard,
  WidgetData,
  GenerativePayload,
} from './dashboard-widget.model';
import { WebsocketService } from '../../core/services/websocket.service';

@Component({
  selector: 'cs-dashboard',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatDialogModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
    MatSelectModule,
    MatProgressSpinnerModule,
    MatSnackBarModule,
    MatTooltipModule,
    DashboardWidgetComponent,
  ],
  template: `
    <!-- War Room Overlay — erscheint automatisch bei Critical-KI-Insight -->
    @if (warRoomActive()) {
      <div class="war-room-overlay" (click)="dismissWarRoom()">
        <div class="war-room-panel" (click)="$event.stopPropagation()">
          <div class="wr-overlay-header">
            <mat-icon class="wr-pulse">warning</mat-icon>
            <span>INCIDENT DETECTED</span>
            <button mat-icon-button (click)="dismissWarRoom()"><mat-icon>close</mat-icon></button>
          </div>
          <p>Der KI-Agent hat einen kritischen Vorfall erkannt. Bitte öffne den War-Room-Widget für Details.</p>
          <div class="wr-overlay-actions">
            <button mat-flat-button color="warn" (click)="scrollToWarRoom(); dismissWarRoom()">
              <mat-icon>radar</mat-icon> War Room öffnen
            </button>
            <button mat-button (click)="dismissWarRoom()">Schließen</button>
          </div>
        </div>
      </div>
    }

    <div class="dashboard-shell">
      <section class="hero">
        <div>
          <p class="eyebrow">CentralStation</p>
          <h1>Operations Cockpit</h1>
          <p class="subtitle">
            Gespeicherte Suchen, Live-Listen und Metriken als frei arrangierbare Widgets.
          </p>
        </div>
        <div class="hero-actions">
          <mat-form-field appearance="outline" class="dashboard-select">
            <mat-label>Dashboard</mat-label>
            <mat-select [ngModel]="selectedDashboardId()" (ngModelChange)="selectDashboard($event)">
              @for (dashboard of dashboards(); track dashboard.id) {
                <mat-option [value]="dashboard.id">{{ dashboard.name }}</mat-option>
              }
            </mat-select>
          </mat-form-field>
          @if (!generativeMode()) {
            @if (configMode()) {
              <button mat-flat-button color="primary" (click)="addWidget()">
                <mat-icon>add</mat-icon>
                Widget hinzufügen
              </button>
              <button mat-stroked-button color="warn" (click)="resetDefaults()" title="Alle Widgets löschen und Standard-Layout wiederherstellen">
                <mat-icon>restore</mat-icon>
                Defaults
              </button>
              <button mat-stroked-button (click)="cancelConfigMode()">
                <mat-icon>close</mat-icon>
                Abbrechen
              </button>
            }
            <button mat-stroked-button [color]="configMode() ? 'warn' : 'primary'" (click)="toggleConfigMode()">
              <mat-icon>{{ configMode() ? 'done' : 'dashboard_customize' }}</mat-icon>
              {{ configMode() ? 'Layout speichern' : 'Dashboard anpassen' }}
            </button>
          }

          <!-- Generative / Classic toggle — switches the whole view (classic
               dashboards are untouched; generative is a separate AI canvas) -->
          <button mat-stroked-button
            [class.generative-active]="generativeMode()"
            [class.mode-klassisch]="!generativeMode()"
            (click)="toggleGenerativeMode()"
            [matTooltip]="generativeMode() ? 'Generativer Modus — KI komponiert das Dashboard für die aktuelle Lage. Klicken für Klassisch.' : 'Generativen Modus aktivieren — die KI komponiert ein Lagebild aus der aktuellen Situation'">
            <mat-icon>auto_awesome</mat-icon>
            {{ generativeMode() ? 'Generativ' : 'Klassisch' }}
          </button>

          <button mat-icon-button (click)="refreshAll()" [disabled]="loading()" title="Aktualisieren">
            <mat-icon>refresh</mat-icon>
          </button>
        </div>
      </section>

      @if (loading()) {
        <mat-card class="loading-card">
          <mat-spinner diameter="32"></mat-spinner>
          <span>Lade Dashboard...</span>
        </mat-card>
      }

      @if (configMode()) {
        <mat-card class="ai-builder">
          <div>
            <h3>Dashboard per KI-Prompt erstellen oder erweitern</h3>
            <p>Beschreibe ein neues Dashboard oder ein einzelnes Widget, z.B. "Wazuh und Graylog Fehler fuer Docker Hosts".</p>
          </div>
          <mat-form-field appearance="outline">
            <mat-label>Prompt</mat-label>
            <textarea matInput rows="2" [(ngModel)]="dashboardPrompt"></textarea>
          </mat-form-field>
          <button mat-flat-button color="primary" (click)="createWidgetFromPrompt()" [disabled]="creatingFromPrompt() || !dashboardPrompt.trim()">
            @if (creatingFromPrompt()) { <mat-spinner diameter="18"></mat-spinner> }
            @else { <mat-icon>auto_awesome</mat-icon> }
            Widget erstellen
          </button>
          <button mat-stroked-button color="primary" (click)="createDashboardFromPrompt()" [disabled]="creatingFromPrompt() || !dashboardPrompt.trim()">
            <mat-icon>dashboard_customize</mat-icon>
            Neues Dashboard
          </button>
        </mat-card>
      }

      @if (generativeMode()) {
        <div class="gen-banner">
          <!-- Header bar — same visual weight as a widget header -->
          <div class="gen-header">
            <mat-icon class="gen-icon" [class.spinning]="generativeLoading()">auto_awesome</mat-icon>
            <span class="gen-header-title">KI-Komponiertes Lagebild</span>
            @if (generativeAgo()) { <span class="gen-ago">{{ generativeAgo() }}</span> }
            <button mat-flat-button color="primary" (click)="regenerate()" [disabled]="generativeLoading()">
              @if (generativeLoading()) { <mat-spinner diameter="18"></mat-spinner> }
              @else { <mat-icon>refresh</mat-icon> }
              Neu generieren
            </button>
          </div>
          <!-- Body — dark background like widget body -->
          @if (generativeRationale()) {
            <div class="gen-body">
              <p class="gen-rationale" [class.collapsed]="!rationaleExpanded()">{{ generativeRationale() }}</p>
              <button mat-button class="gen-why" (click)="rationaleExpanded.set(!rationaleExpanded())">
                {{ rationaleExpanded() ? '▲ Weniger' : '▼ Mehr' }}
              </button>
            </div>
          }
        </div>
      }

      <div #grid class="grid-stack" [class.config-mode]="configMode()">
        @for (widget of widgets(); track widget.id) {
          <div class="grid-stack-item"
               [attr.gs-id]="widget.id"
               [attr.gs-x]="widget.gs_x"
               [attr.gs-y]="widget.gs_y"
               [attr.gs-w]="widget.gs_w"
               [attr.gs-h]="widget.gs_h">
            <div class="grid-stack-item-content">
              <cs-dashboard-widget
                [widget]="widget"
                [data]="widgetData()[widget.id]"
                [editMode]="configMode()"
                [generativeMode]="generativeMode()"
                (click)="openWidget(widget)"
                (remove)="deleteWidget(widget.id)"
                (edit)="editWidget(widget)"
                (pinToggle)="toggleWidgetPin(widget)"
                (itemClick)="openFeedItem($event)"
                (findingClick)="openFeedFinding($event)"
                (insightOpen)="openInsight($event)"
                (donutClick)="openFeedSeverity($event)"
                (barClick)="openFeedBar($event, widget)"
                (warRoomJira)="createWarRoomTicket($event)" />
            </div>
          </div>
        }
      </div>
    </div>
  `,
  styles: [`
    .dashboard-shell {
      min-height: 100%;
      padding: 8px 10px 18px;
      background: #000;
      color: #ffe8a0;
      font-family: 'Antonio', 'Eurostile', 'Roboto Condensed', sans-serif;
      overflow-x: hidden;
    }
    .hero {
      display: grid;
      grid-template-columns: minmax(330px, 520px) 1fr auto;
      align-items: stretch;
      gap: 8px;
      margin-bottom: 12px;
      padding: 0;
      background: #000;
      color: #ffcc99;
      min-height: 104px;
    }
    .hero > div:first-child {
      background: #000;
      color: #ff9966;
      border-radius: 44px 0 0 0;
      padding: 18px 24px 12px 74px;
      position: relative;
      min-width: 0;
      border-top: 18px solid #ff9966;
      border-left: 36px solid #ff9966;
      border-bottom: 12px solid #ffcc66;
    }
    .hero > div:first-child::before {
      content: '';
      position: absolute;
      left: -36px;
      top: -18px;
      width: 36px;
      height: calc(100% + 30px);
      background: #ff9966;
      border-radius: 44px 0 0 0;
    }
    .hero > div:first-child::after {
      content: '';
      position: absolute;
      right: -140px;
      top: -18px;
      width: 132px;
      height: 18px;
      background: #ff9966;
      border-radius: 0 12px 12px 0;
    }
    .eyebrow {
      margin: 0 0 6px;
      color: #ff9966;
      font-size: 11px;
      font-weight: 800;
      letter-spacing: .22em;
      text-transform: uppercase;
      opacity: .75;
    }
    h1 {
      /* Bridge-style section heading — compact, uppercase, letter-spaced */
      margin: 0;
      font-size: clamp(20px, 2.2vw, 26px);
      line-height: 1.15;
      letter-spacing: .22em;
      font-weight: 800;
      text-transform: uppercase;
      color: #ffcc66;
      background: #000;                 /* LCARS "cut-through" dark background */
      display: inline-block;
      padding: 3px 12px 3px 0;
    }
    .subtitle {
      margin: 8px 0 0;
      max-width: 320px;
      color: rgba(255,204,153,.6);
      font-size: 11px;
      line-height: 1.35;
      letter-spacing: .04em;
    }
    .hero-actions {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: flex-end;
      background: #000;
      color: #ffcc99;
      padding: 12px 18px;
      min-width: 0;
      border-top: 18px solid #7fb3d3;
      border-bottom: 12px solid #7fb3d3;
    }
    .hero::after {
      content: '';
      display: block;
      background: #000;
      border-top: 18px solid #e8ec9a;
      border-bottom: 12px solid #e8ec9a;
      border-radius: 0 44px 0 0;
    }
    .hero-actions button {
      background: #ff9966 !important;
      color: #000 !important;
      border-color: #ff9966 !important;
      border-radius: 20px;
      font-weight: 900;
    }
    .hero-actions button mat-icon { color: #000 !important; }
    .dashboard-select {
      width: 240px;
      --mat-form-field-container-text-color: #ffcc99;
      --mat-form-field-label-text-color: #ffcc66;
      --mat-select-enabled-trigger-text-color: #ffcc99;
      --mdc-outlined-text-field-outline-color: #ff9966;
      --mdc-outlined-text-field-label-text-color: #ffcc66;
      --mdc-outlined-text-field-input-text-color: #ffcc99;
    }
    /* Generative toggle: gold when active; explicit text for inactive (Classic = dark, LCARS/Holo = theme) */
    .generative-active { background: #ffcc66 !important; color: #000 !important; border-color: #ffcc66 !important; }
    .mode-klassisch { color: var(--mat-sys-on-surface) !important; border-color: var(--mat-sys-outline) !important; }
    :host-context(html.cs-theme-lcars) .mode-klassisch { color: #e8a060 !important; border-color: #e87c3a !important; }
    :host-context(html.cs-theme-holo) .mode-klassisch { color: #8fb8cf !important; border-color: rgba(79,214,255,.5) !important; }
    /* War Room Overlay */
    .war-room-overlay { position: fixed; inset: 0; z-index: 9999; background: rgba(0,0,0,.55); display: flex; align-items: flex-start; justify-content: center; padding-top: 80px; animation: fadeIn .25s ease; }
    @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
    .war-room-panel { background: var(--mat-sys-surface); border-radius: 16px; padding: 24px; max-width: 440px; width: 90%; border: 2px solid #c62828; box-shadow: 0 8px 40px rgba(198,40,40,.35); }
    .wr-overlay-header { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; font-weight: 700; font-size: 16px; color: #c62828; }
    .wr-overlay-header mat-icon { font-size: 26px; height: 26px; width: 26px; }
    .wr-overlay-header button { margin-left: auto; }
    @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:.4; } }
    .wr-pulse { animation: pulse 1.2s infinite; }
    .wr-overlay-actions { display: flex; gap: 10px; margin-top: 16px; justify-content: flex-end; }
    .loading-card {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 14px 16px;
      margin-bottom: 16px;
      color: var(--mat-sys-on-surface-variant);
    }
    .ai-builder {
      display: grid;
      grid-template-columns: minmax(220px, 320px) 1fr auto auto;
      align-items: center;
      gap: 14px;
      padding: 14px 16px;
      margin-bottom: 16px;
      border: 1px solid color-mix(in srgb, var(--mat-sys-primary) 24%, transparent);
      background: color-mix(in srgb, var(--mat-sys-primary) 7%, var(--mat-sys-surface));
    }
    .ai-builder h3 { margin: 0 0 4px; font-size: 15px; }
    .ai-builder p { margin: 0; color: var(--mat-sys-on-surface-variant); font-size: 12px; line-height: 1.4; }
    .ai-builder mat-form-field { width: 100%; }
    .ai-builder mat-spinner { display: inline-block; margin-right: 6px; }
    /* gen-banner matches the lcars-widget design exactly:
       gold left border · gold header bar · dark body · button in header */
    .gen-banner {
      display: flex;
      flex-direction: column;
      margin: 0 0 10px;
      border: none;
      border-left: 8px solid #ffcc66;     /* same as nth-child(2) gold widgets */
      border-radius: 0 14px 14px 0;
      overflow: hidden;
      box-shadow: none;
    }
    .gen-header {
      /* mimics .widget-header in lcars-widget */
      display: flex;
      align-items: center;
      gap: 10px;
      background: #ffcc66;
      color: #000;
      padding: 8px 14px;
      flex-shrink: 0;
      min-height: 42px;
    }
    .gen-header-title {
      font-family: 'Antonio', 'Eurostile', 'Roboto Condensed', sans-serif;
      font-size: 12px;
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: .1em;
      flex: 1;
    }
    .gen-ago { font-size: 11px; opacity: .65; }
    .gen-icon {
      font-size: 18px; width: 18px; height: 18px;
      display: inline-flex; align-items: center;
    }
    .gen-icon.spinning { animation: spin 1.4s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .gen-body {
      background: #000;
      color: #ffe8a0;
      padding: 10px 14px 10px;
    }
    .gen-rationale { font-size: 13px; color: #ffcc99; line-height: 1.55; margin: 0; }
    .gen-rationale.collapsed { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
    .gen-why { font-size: 11px; min-height: 26px; line-height: 26px; padding: 0; color: #ffcc66; margin-top: 4px; display: inline-block; }
    .gen-banner button[color="primary"] {
      background: #e87c3a !important;
      color: #000 !important;
      border-radius: 14px;
      font-size: 12px;
      font-weight: 900;
      height: 30px;
      line-height: 30px;
      padding: 0 14px;
    }
    /* ── LCARS: dark canvas, gen-banner uses its own LCARS grid design already ── */
    :host-context(html.cs-theme-lcars) .dashboard-shell { background: #000 !important; }
    :host-context(html.cs-theme-lcars) h1 { color: #ffcc66; }
    /* NOTE: do NOT override gen-banner here — the base CSS is already LCARS-styled */

    /* ── Classic theme: restore light look (base CSS is LCARS-dark, needs override) ── */
    :host-context(:not(html.cs-theme-lcars):not(html.cs-theme-holo)) .dashboard-shell {
      background:
        radial-gradient(circle at 12% 8%, color-mix(in srgb, var(--mat-sys-primary) 15%, transparent), transparent 26rem),
        linear-gradient(145deg, color-mix(in srgb, var(--mat-sys-surface-container) 70%, #eef7f2), var(--mat-sys-surface)) !important;
      color: var(--mat-sys-on-surface) !important;
      font-family: Roboto, 'Helvetica Neue', sans-serif !important;
    }
    :host-context(:not(html.cs-theme-lcars):not(html.cs-theme-holo)) .hero {
      background: transparent;
      color: var(--mat-sys-on-surface);
      border: none;
      min-height: auto;
    }
    :host-context(:not(html.cs-theme-lcars):not(html.cs-theme-holo)) .hero > div:first-child {
      background: transparent;
      border: none;
      border-radius: 0;
      padding: 0;
      color: var(--mat-sys-on-surface);
    }
    :host-context(:not(html.cs-theme-lcars):not(html.cs-theme-holo)) .hero > div:first-child::before,
    :host-context(:not(html.cs-theme-lcars):not(html.cs-theme-holo)) .hero > div:first-child::after {
      display: none;
    }
    :host-context(:not(html.cs-theme-lcars):not(html.cs-theme-holo)) .hero-actions {
      background: transparent; border: none; padding: 0;
    }
    :host-context(:not(html.cs-theme-lcars):not(html.cs-theme-holo)) .hero::after { display: none; }
    :host-context(:not(html.cs-theme-lcars):not(html.cs-theme-holo)) .eyebrow { color: var(--mat-sys-primary); }
    :host-context(:not(html.cs-theme-lcars):not(html.cs-theme-holo)) h1 { color: var(--mat-sys-primary); background: transparent; padding: 0; font-size: clamp(20px, 2.2vw, 26px); letter-spacing: .12em; }
    :host-context(:not(html.cs-theme-lcars):not(html.cs-theme-holo)) .subtitle { color: var(--mat-sys-on-surface-variant); }
    :host-context(:not(html.cs-theme-lcars):not(html.cs-theme-holo)) .hero-actions button { background: revert !important; color: revert !important; border-color: revert !important; border-radius: revert !important; font-weight: revert !important; }
    :host-context(:not(html.cs-theme-lcars):not(html.cs-theme-holo)) .hero-actions button mat-icon { color: revert !important; }
    :host-context(:not(html.cs-theme-lcars):not(html.cs-theme-holo)) .dashboard-select { --mat-form-field-container-text-color: revert; --mat-select-enabled-trigger-text-color: revert; --mdc-outlined-text-field-outline-color: revert; }

    /* ── Classic theme: gen-banner light override ── */
    :host-context(:not(html.cs-theme-lcars):not(html.cs-theme-holo)) .gen-banner {
      border-left-color: var(--mat-sys-primary);
      border-radius: 0 14px 14px 0;
    }
    :host-context(:not(html.cs-theme-lcars):not(html.cs-theme-holo)) .gen-header {
      background: var(--mat-sys-primary-container);
      color: var(--mat-sys-on-primary-container);
    }
    :host-context(:not(html.cs-theme-lcars):not(html.cs-theme-holo)) .gen-header-title { color: var(--mat-sys-on-primary-container); }
    :host-context(:not(html.cs-theme-lcars):not(html.cs-theme-holo)) .gen-body { background: var(--mat-sys-surface-container); }
    :host-context(:not(html.cs-theme-lcars):not(html.cs-theme-holo)) .gen-rationale { color: var(--mat-sys-on-surface-variant); }
    :host-context(:not(html.cs-theme-lcars):not(html.cs-theme-holo)) .gen-why { color: var(--mat-sys-primary); }
    :host-context(:not(html.cs-theme-lcars):not(html.cs-theme-holo)) .gen-banner button[color="primary"] {
      background: var(--mat-sys-primary) !important; color: var(--mat-sys-on-primary) !important;
    }

    /* ── Holo: dark canvas + cyan gen-banner ── */
    :host-context(html.cs-theme-holo) .dashboard-shell {
      background: radial-gradient(circle at 50% 8%, rgba(20,60,90,.4), transparent 36rem), linear-gradient(160deg,#02060f,#050d1a 60%,#02060f) !important;
    }
    :host-context(html.cs-theme-holo) .gen-banner { border-left-color: #4fd6ff; }
    :host-context(html.cs-theme-holo) .gen-header { background: rgba(79,214,255,.15); color: #9fe8ff; }
    :host-context(html.cs-theme-holo) .gen-header-title { color: #9fe8ff !important; }
    :host-context(html.cs-theme-holo) .gen-body { background: rgba(5,20,35,.9); }
    :host-context(html.cs-theme-holo) .gen-rationale { color: #8fb8cf !important; }
    :host-context(html.cs-theme-holo) .gen-why { color: #4fd6ff !important; }
    :host-context(html.cs-theme-holo) .gen-banner button[color="primary"] {
      background: #4fd6ff !important; color: #00131f !important;
    }
    .grid-stack { min-height: 520px; }
    .grid-stack.config-mode {
      background-image:
        linear-gradient(#3a2810 1px, transparent 1px),
        linear-gradient(90deg, #3a2810 1px, transparent 1px);
      background-size: 80px 80px;
      border-radius: 0;
      padding-bottom: 12px;
    }
    .grid-stack-item-content { inset: 6px !important; overflow: hidden !important; }

    @media (max-width: 820px) {
      .dashboard-shell { padding: 16px; }
      .hero { grid-template-columns: 1fr; }
      .hero::after { display:none; }
      .hero > div:first-child { border-radius: 32px 0 0 0; }
      h1 { white-space: normal; }
      .hero-actions { justify-content: flex-start; }
      .gen-banner { grid-template-columns: 1fr; border-radius: 24px 8px 8px 0; background:#0a0804; }
      .gen-icon { color: #000; justify-self: stretch; min-height: 48px; }
      .ai-builder { grid-template-columns: 1fr; }
    }
  `],
})
export class DashboardComponent implements AfterViewInit, OnDestroy {
  @ViewChild('grid') private gridEl!: ElementRef<HTMLElement>;

  widgets = signal<DashboardWidget[]>([]);
  dashboards = signal<Dashboard[]>([]);
  selectedDashboardId = signal<string>('');
  widgetData = signal<Record<string, WidgetData>>({});
  configMode = signal(false);
  generativeMode = signal(false);
  generativeLoading = signal(false);
  generativeRationale = signal<string | null>(null);
  generativeGeneratedAt = signal<string | null>(null);
  rationaleExpanded = signal(true);
  generativeAgo = computed(() => {
    const ts = this.generativeGeneratedAt();
    if (!ts) return '';
    const mins = Math.max(0, Math.round((Date.now() - new Date(ts).getTime()) / 60000));
    if (mins < 1) return 'gerade eben';
    if (mins < 60) return `vor ${mins} Min`;
    const h = Math.round(mins / 60);
    return `vor ${h} Std`;
  });
  warRoomActive = signal(false);
  loading = signal(true);
  creatingFromPrompt = signal(false);
  dashboardPrompt = '';
  private grid?: GridStack;
  private wsRegenTimer?: ReturnType<typeof setTimeout>;
  private injector = inject(Injector);
  private wsSubscription?: import('rxjs').Subscription;
  private readonly GEN_KEY = 'cs_generative_mode';

  constructor(
    private http: HttpClient,
    private router: Router,
    private dialog: MatDialog,
    private snackBar: MatSnackBar,
    private ws: WebsocketService,
  ) {}

  ngAfterViewInit() {
    this.loadDashboards();
    // Always listen for critical AI insights to show War Room overlay
    this.wsSubscription = this.ws.messages().subscribe((msg: any) => {
      if (msg?.type === 'ai_insight' && (msg.severity === 'critical' || msg.severity === 'high')) {
        this.warRoomActive.set(true);
        this.refreshWarRoomWidgets();
        // Escalation → recompose the generative dashboard (debounced so a wave
        // of critical insights triggers a single regeneration, not many).
        if (this.generativeMode()) {
          if (this.wsRegenTimer) clearTimeout(this.wsRegenTimer);
          this.wsRegenTimer = setTimeout(() => this.regenerate(), 8000);
        }
      }
    });
  }

  ngOnDestroy() {
    this.grid?.destroy(false);
    this.wsSubscription?.unsubscribe();
    if (this.wsRegenTimer) clearTimeout(this.wsRegenTimer);
  }

  private readonly STORAGE_KEY = 'cs_selected_dashboard_id';

  loadDashboards() {
    this.http.get<Dashboard[]>(`${environment.apiUrl}/dashboard-widgets/dashboards`).subscribe({
      next: dashboards => {
        this.dashboards.set(dashboards);
        const saved = localStorage.getItem(this.STORAGE_KEY) ?? '';
        const validSaved = saved && dashboards.some(d => d.id === saved) ? saved : '';
        const selected = validSaved || dashboards.find(d => d.is_default)?.id || dashboards[0]?.id || '';
        this.selectedDashboardId.set(selected);
        // The generative view is a separate AI canvas, persisted per-browser.
        if (localStorage.getItem(this.GEN_KEY) === '1') {
          this.generativeMode.set(true);
          this.loadGenerative();
        } else {
          this.loadWidgets();
        }
      },
      error: () => {
        this.loading.set(false);
        this.snackBar.open('Dashboards konnten nicht geladen werden', 'OK', { duration: 4000 });
      },
    });
  }

  selectDashboard(dashboardId: string) {
    this.selectedDashboardId.set(dashboardId);
    localStorage.setItem(this.STORAGE_KEY, dashboardId);
    this.widgetData.set({});
    // Choosing a classic dashboard leaves the generative view.
    if (this.generativeMode()) {
      this.generativeMode.set(false);
      localStorage.removeItem(this.GEN_KEY);
    }
    this.loadWidgets();
  }

  loadWidgets() {
    this.loading.set(true);
    const params: Record<string, string> = {};
    if (this.selectedDashboardId()) params['dashboard_id'] = this.selectedDashboardId();
    this.http.get<DashboardWidget[]>(`${environment.apiUrl}/dashboard-widgets/`, { params }).subscribe({
      next: widgets => {
        this.widgets.set(widgets);
        this.loading.set(false);
        this.rebuildGrid(true);
      },
      error: () => {
        this.loading.set(false);
        this.snackBar.open('Dashboard konnte nicht geladen werden', 'OK', { duration: 4000 });
      },
    });
  }

  rebuildGrid(loadData = false) {
    // afterNextRender fires after Angular has committed the current DOM update,
    // guaranteeing all grid-stack-item elements from the @for loop are present
    // before GridStack measures them and sets pixel heights.
    afterNextRender(() => {
      this.grid?.destroy(false);
      this.grid = GridStack.init({
        cellHeight: 80,
        minRow: 4,
        margin: 8,
        float: false,
        disableDrag: !this.configMode(),
        disableResize: !this.configMode(),
      }, this.gridEl.nativeElement);
      // Fetch widget data after GridStack has sized all containers so echarts
      // initialises into elements that already have correct pixel heights.
      if (loadData) {
        this.widgets().forEach(w => this.loadWidgetData(w.id));
      }
    }, { injector: this.injector });
  }

  refreshAll() {
    this.widgets().forEach(w => this.loadWidgetData(w.id));
  }

  toggleConfigMode() {
    const next = !this.configMode();
    this.configMode.set(next);
    if (next) {
      this.grid?.enable();
    } else {
      this.grid?.disable();
      this.saveLayout();
    }
  }

  cancelConfigMode() {
    this.configMode.set(false);
    this.grid?.disable();
    this.loadWidgets();
  }

  saveLayout() {
    const items = this.grid?.getGridItems() ?? [];
    const updates = items
      .map(el => this.layoutPatch(el))
      .filter((patch): patch is { id: string; body: Record<string, number> } => !!patch)
      .map(patch => this.http.patch(`${environment.apiUrl}/dashboard-widgets/${patch.id}`, patch.body));

    if (!updates.length) return;
    forkJoin(updates).subscribe({
      next: () => this.snackBar.open('Dashboard-Layout gespeichert', '', { duration: 2000 }),
      error: () => this.snackBar.open('Layout konnte nicht gespeichert werden', 'OK', { duration: 4000 }),
    });
  }

  private layoutPatch(el: GridItemHTMLElement): { id: string; body: Record<string, number> } | null {
    const id = el.getAttribute('gs-id');
    const n = el.gridstackNode;
    if (!id || !n) return null;
    return {
      id,
      body: {
        gs_x: n.x ?? 0,
        gs_y: n.y ?? 0,
        gs_w: n.w ?? 4,
        gs_h: n.h ?? 3,
      },
    };
  }

  loadWidgetData(widgetId: string) {
    this.http.get<WidgetData>(`${environment.apiUrl}/dashboard-widgets/${widgetId}/data`).subscribe({
      next: data => this.widgetData.update(m => ({ ...m, [widgetId]: data })),
      error: () => this.widgetData.update(m => ({ ...m, [widgetId]: { error: 'Daten konnten nicht geladen werden', series: [] } })),
    });
  }

  editWidget(widget: DashboardWidget) {
    const ref = this.dialog.open<AddWidgetDialogComponent, unknown, DashboardWidgetCreate>(
      AddWidgetDialogComponent,
      { width: '680px', data: { existingWidget: widget } },
    );
    ref.afterClosed().subscribe(payload => {
      if (!payload) return;
      this.http.patch<DashboardWidget>(`${environment.apiUrl}/dashboard-widgets/${widget.id}`, {
        title: payload.title,
        config: payload.config,
      }).subscribe({
        next: updated => {
          this.widgets.update(ws => ws.map(w => w.id === updated.id ? { ...w, ...updated } : w));
          this.loadWidgetData(updated.id);
          this.snackBar.open('Widget aktualisiert', '', { duration: 2000 });
        },
        error: () => this.snackBar.open('Widget konnte nicht aktualisiert werden', 'OK', { duration: 4000 }),
      });
    });
  }

  addWidget() {
    const ref = this.dialog.open<AddWidgetDialogComponent, unknown, DashboardWidgetCreate>(
      AddWidgetDialogComponent,
      { width: '680px' },
    );
    ref.afterClosed().subscribe(payload => {
      if (!payload) return;
      this.http.post<DashboardWidget>(`${environment.apiUrl}/dashboard-widgets/`, {
        ...payload,
        dashboard_id: this.selectedDashboardId(),
      }).subscribe({
        next: widget => {
          this.widgets.update(ws => [...ws, widget]);
          this.rebuildGrid();
          this.loadWidgetData(widget.id);
        },
        error: () => this.snackBar.open('Widget konnte nicht angelegt werden', 'OK', { duration: 4000 }),
      });
    });
  }

  createWidgetFromPrompt() {
    const prompt = this.dashboardPrompt.trim();
    if (!prompt) return;
    this.creatingFromPrompt.set(true);
    this.http.post<{ actions: Array<{ type: string; id: string }>; reply?: string }>(
      `${environment.apiUrl}/ai/search-assistant`,
      {
        message: prompt,
        context: 'user is configuring the dashboard; create one useful dashboard widget',
        create_widget: true,
        dashboard_id: this.selectedDashboardId(),
        name: 'KI: ' + prompt.slice(0, 48),
        widget_type: 'list',
      },
    ).subscribe({
      next: res => {
        this.creatingFromPrompt.set(false);
        this.dashboardPrompt = '';
        this.snackBar.open(res.reply || 'Widget erstellt', '', { duration: 2500 });
        this.loadWidgets();
      },
      error: err => {
        this.creatingFromPrompt.set(false);
        this.snackBar.open(err?.error?.detail ?? 'KI konnte kein Widget erstellen', 'OK', { duration: 4000 });
      },
    });
  }

  createDashboardFromPrompt() {
    const prompt = this.dashboardPrompt.trim();
    if (!prompt) return;
    this.creatingFromPrompt.set(true);
    this.http.post<Dashboard>(`${environment.apiUrl}/dashboard-widgets/dashboards`, {
      name: 'KI: ' + prompt.slice(0, 64),
      description: prompt,
      is_default: false,
    }).subscribe({
      next: dashboard => {
        this.http.post<{ actions: Array<{ type: string; id: string }>; reply?: string }>(
          `${environment.apiUrl}/ai/search-assistant`,
          {
            message: prompt,
            context: 'create an initial list widget for a new dashboard from this prompt',
            create_widget: true,
            dashboard_id: dashboard.id,
            name: dashboard.name,
            widget_type: 'list',
          },
        ).subscribe({
          next: res => {
            this.creatingFromPrompt.set(false);
            this.dashboardPrompt = '';
            this.dashboards.update(ds => [...ds, dashboard]);
            this.selectedDashboardId.set(dashboard.id);
            this.snackBar.open(res.reply || 'Dashboard erstellt', '', { duration: 2500 });
            this.loadWidgets();
          },
          error: err => {
            this.creatingFromPrompt.set(false);
            this.snackBar.open(err?.error?.detail ?? 'Dashboard wurde erstellt, aber kein KI-Widget angelegt', 'OK', { duration: 4000 });
            this.loadDashboards();
          },
        });
      },
      error: err => {
        this.creatingFromPrompt.set(false);
        this.snackBar.open(err?.error?.detail ?? 'Dashboard konnte nicht erstellt werden', 'OK', { duration: 4000 });
      },
    });
  }

  resetDefaults() {
    const dashId = this.selectedDashboardId();
    if (!dashId) return;
    this.loading.set(true);
    this.http.post<DashboardWidget[]>(`${environment.apiUrl}/dashboard-widgets/dashboards/${dashId}/reset-defaults`, {}).subscribe({
      next: widgets => {
        this.widgets.set(widgets);
        this.widgetData.set({});
        this.loading.set(false);
        this.rebuildGrid();
        widgets.forEach(w => this.loadWidgetData(w.id));
        this.snackBar.open('Standard-Layout wiederhergestellt', 'OK', { duration: 2500 });
      },
      error: () => {
        this.loading.set(false);
        this.snackBar.open('Fehler beim Zurücksetzen', 'OK', { duration: 3000 });
      },
    });
  }

  deleteWidget(widgetId: string) {
    this.http.delete(`${environment.apiUrl}/dashboard-widgets/${widgetId}`).subscribe({
      next: () => {
        this.widgets.update(ws => ws.filter(w => w.id !== widgetId));
        this.widgetData.update(data => {
          const next = { ...data };
          delete next[widgetId];
          return next;
        });
        this.rebuildGrid();
      },
      error: () => this.snackBar.open('Widget konnte nicht gelöscht werden', 'OK', { duration: 4000 }),
    });
  }

  // ── Generative Mode (separate AI-composed dashboard) ─────────────────────────

  toggleGenerativeMode() {
    const next = !this.generativeMode();
    this.generativeMode.set(next);
    if (next) {
      // Leave config mode — the generative canvas is AI-driven, not hand-edited.
      if (this.configMode()) { this.configMode.set(false); this.grid?.disable(); }
      localStorage.setItem(this.GEN_KEY, '1');
      this.loadGenerative();
    } else {
      localStorage.removeItem(this.GEN_KEY);
      this.generativeRationale.set(null);
      this.generativeGeneratedAt.set(null);
      this.widgetData.set({});
      this.loadWidgets();
    }
  }

  /** Load the existing AI-composed dashboard; generate one if it's still empty. */
  loadGenerative() {
    this.loading.set(true);
    this.http.get<GenerativePayload>(`${environment.apiUrl}/dashboard-widgets/dashboards/generative`).subscribe({
      next: payload => {
        if (!payload.widgets?.length) {
          this.regenerate();
          return;
        }
        this._applyGenerative(payload);
      },
      error: () => {
        this.loading.set(false);
        this.snackBar.open('Generatives Dashboard konnte nicht geladen werden', 'OK', { duration: 4000 });
      },
    });
  }

  /** Trigger a fresh AI composition (one LLM call). */
  regenerate() {
    this.generativeLoading.set(true);
    this.loading.set(true);
    this.http.post<GenerativePayload>(`${environment.apiUrl}/dashboard-widgets/dashboards/generate`, {}).subscribe({
      next: payload => {
        this._applyGenerative(payload);
        this.generativeLoading.set(false);
        this.snackBar.open('Dashboard neu komponiert', '', { duration: 2000 });
      },
      error: () => {
        this.generativeLoading.set(false);
        this.loading.set(false);
        this.snackBar.open('Komposition fehlgeschlagen', 'OK', { duration: 4000 });
      },
    });
  }

  private _applyGenerative(payload: GenerativePayload) {
    this.generativeRationale.set(payload.rationale ?? null);
    this.generativeGeneratedAt.set(payload.generated_at ?? null);
    this.widgetData.set({});
    this.widgets.set(payload.widgets ?? []);
    this.loading.set(false);
    this.rebuildGrid(true);
  }

  dismissWarRoom() { this.warRoomActive.set(false); }

  scrollToWarRoom() {
    setTimeout(() => {
      const el = document.querySelector('[gs-type="war_room"]') as HTMLElement | null;
      el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 100);
  }

  refreshWarRoomWidgets() {
    const warRoomWidgets = this.widgets().filter(w => w.widget_type === 'war_room');
    warRoomWidgets.forEach(w => this.loadWidgetData(w.id));
  }

  createWarRoomTicket(jiraTitle: string) {
    this.markHandled();
    this.router.navigate(['/workflow'], { queryParams: { title: jiraTitle, auto_jira: '1' } });
  }

  toggleWidgetPin(widget: DashboardWidget) {
    const next = !widget.pinned;
    this.widgets.update(ws => ws.map(w => w.id === widget.id ? { ...w, pinned: next } : w));
    this.http.patch(`${environment.apiUrl}/dashboard-widgets/${widget.id}`, { pinned: next }).subscribe();
  }

  // Set true by a dedicated inner-element handler (chart segment, finding, list item)
  // so the host-level (click)="openWidget" — fired by the same bubbling DOM click —
  // does not clobber the navigation with the widget's base config.
  private suppressWidgetOpen = false;
  private markHandled() {
    this.suppressWidgetOpen = true;
    setTimeout(() => (this.suppressWidgetOpen = false));
  }

  openFeedItem(itemId: string) {
    this.markHandled();
    this.router.navigate(['/feed'], { queryParams: { highlight: itemId } });
  }

  openFeedFinding(finding: { source: string; host: string | null; severity: string }) {
    this.markHandled();
    const qp: Record<string, string> = {};
    if (finding.source) qp['source'] = finding.source;
    if (finding.severity) qp['severity'] = finding.severity;
    if (finding.host) qp['host'] = finding.host;
    this.router.navigate(['/feed'], { queryParams: qp });
  }

  openFeedSeverity(severity: string) {
    this.markHandled();
    this.router.navigate(['/feed'], { queryParams: { severity: severity.toLowerCase() } });
  }

  openInsight(analysisId: string | null) {
    this.markHandled();
    const qp = analysisId ? { analysis: analysisId } : {};
    this.router.navigate(['/ai-insights'], { queryParams: qp });
  }

  openFeedBar(event: { field: string; value: string }, widget: DashboardWidget) {
    this.markHandled();
    const cfg = widget.config;
    const base = typeof cfg['query_string'] === 'string' && cfg['query_string']
      ? `(${cfg['query_string']}) AND `
      : '';
    const fieldMap: Record<string, string> = {
      severity: 'severity',
      source: 'source',
      'metadata.host': 'host',
    };
    const qField = fieldMap[event.field] ?? event.field;
    this.router.navigate(['/feed'], {
      queryParams: {
        q: `${base}${qField}:${event.value}`,
        index: typeof cfg['index_pattern'] === 'string' ? cfg['index_pattern'] : undefined,
      },
    });
  }

  openWidget(widget: DashboardWidget) {
    if (this.suppressWidgetOpen) return;
    if (this.configMode()) return;
    if (widget.widget_type === 'grafana_panel') return;

    const cfg = widget.config;
    this.router.navigate(['/feed'], {
      queryParams: {
        source: Array.isArray(cfg['sources']) ? cfg['sources'].join(',') : undefined,
        severity: typeof cfg['severity'] === 'string' ? cfg['severity'] : undefined,
        search_id: typeof cfg['search_id'] === 'string' ? cfg['search_id'] : undefined,
        q: typeof cfg['query_string'] === 'string' ? cfg['query_string'] : undefined,
        index: typeof cfg['index_pattern'] === 'string' ? cfg['index_pattern'] : undefined,
      },
    });
  }
}
