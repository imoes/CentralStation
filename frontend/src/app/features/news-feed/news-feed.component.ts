import { Component, OnInit, OnDestroy, AfterViewInit, ElementRef, ViewChild, signal, computed, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute, Router } from '@angular/router';
import { skip } from 'rxjs';
import { ComputerService } from '../../core/services/computer.service';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatChipsModule } from '@angular/material/chips';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatDividerModule } from '@angular/material/divider';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatBadgeModule } from '@angular/material/badge';
import { MatSliderModule } from '@angular/material/slider';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatSelectModule } from '@angular/material/select';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { TicketCreateDialogComponent } from '../../shared/ticket-dialog/ticket-create-dialog.component';
import { environment } from '../../../environments/environment';
import { App } from '../../app';
import { AuthService } from '../../core/auth/auth.service';
import { WebsocketService } from '../../core/services/websocket.service';

interface FeedItemCollab {
  claimed_by_name: string | null;
  claimed_at: string | null;
  work_status: 'new' | 'investigating' | 'resolved';
  comment_count: number;
  incident_id?: string | null;
}

interface CausalEntry {
  service: string;
  incident_id: string;
  likely_cause: boolean;
  started_at: string;
  host: string;
}

interface IncidentTimeline {
  incident: {
    id: string;
    title: string;
    primary_host: string;
    severity: string;
    status: string;
    created_at: string;
    member_count: number;
    causal_context?: CausalEntry[] | null;
  };
  timeline: {
    at: string;
    kind: string;
    source: string;
    severity?: string;
    text: string;
    user?: string;
    external_id?: string;
  }[];
}

interface CollabTimeline {
  id: string;
  user_name: string;
  kind: 'comment' | 'claim' | 'release' | 'status' | 'ai';
  body: string;
  created_at: string;
}

interface FeedItem {
  id: string;
  type: 'alert' | 'email' | 'teams_message';
  source: 'checkmk' | 'graylog' | 'wazuh' | 'o365' | 'teams' | 'coroot';
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info';
  title: string;
  body: string | null;
  ai_insight: string | null;
  metadata: Record<string, any> | null;
  host?: string | null;
  created_at: string;
  status: 'new' | 'acknowledged';
  location_name: string | null;
  location_city: string | null;
  external_url: string | null;
  external_id?: string | null;
  collab?: FeedItemCollab;
}

interface FeedPrefs {
  checkmk_min_age_minutes: number;
  teams_channels: string[];
}

interface FeedSearch {
  id: string;
  name: string;
  index_pattern: string;
  query_string: string;
  enabled: boolean;
  is_system: boolean;
  is_exclusion: boolean;
  position: number;
}

const SOURCE_META: Record<string, { label: string; icon: string; color: string }> = {
  checkmk:  { label: 'CheckMK',       icon: 'monitor_heart',    color: '#1565c0' },
  graylog:  { label: 'Graylog',       icon: 'article',          color: '#6a1b9a' },
  wazuh:    { label: 'Wazuh',         icon: 'security',         color: '#b71c1c' },
  o365:     { label: 'E-Mail',        icon: 'mail',             color: '#e65100' },
  teams:    { label: 'Teams',         icon: 'groups',           color: '#0f4c96' },
  coroot:   { label: 'Coroot',        icon: 'insights',         color: '#00897b' },
};

const SEVERITY_COLOR: Record<string, string> = {
  critical: '#b71c1c',
  high:     '#e65100',
  medium:   '#f57c00',
  low:      '#388e3c',
  info:     '#0288d1',
};

@Component({
  selector: 'cs-news-feed',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatCardModule, MatButtonModule, MatIconModule, MatChipsModule,
    MatProgressSpinnerModule, MatDividerModule, MatTooltipModule,
    MatSnackBarModule, MatBadgeModule, MatSliderModule,
    MatSlideToggleModule, MatSelectModule, MatFormFieldModule, MatInputModule,
    MatDialogModule,
  ],
  template: `
    @if (showScrollTop()) {
      <button mat-raised-button color="primary" class="scroll-top-btn" (click)="scrollToTop()">
        <mat-icon>arrow_upward</mat-icon>
        Neueste Meldungen
      </button>
    }

    <div class="feed-page">

      <!-- ── Top bar ────────────────────────────────────────────────────── -->
      <div class="feed-topbar">
        <h2>News Feed</h2>
        <div class="topbar-right">
          <mat-chip-listbox multiple aria-label="Quellen"
            [value]="activeFilter()"
            (change)="onSourceChipChange($event.value)">
            @for (src of allSources; track src.id) {
              <mat-chip-option
                [value]="src.id"
                [style.--mdc-chip-selected-container-color]="src.color + '33'"
                [style.--mdc-chip-selected-label-text-color]="src.color"
                [style.border]="activeFilter().includes(src.id) ? '1px solid ' + src.color : '1px solid transparent'">
                <mat-icon style="font-size:16px;height:16px;width:16px;margin-right:4px">{{ src.icon }}</mat-icon>
                {{ src.label }}
              </mat-chip-option>
            }
          </mat-chip-listbox>
          <button mat-icon-button (click)="toggleFilters()" matTooltip="Filter" [class.active-icon]="hasActiveFilter()">
            <mat-icon>filter_list</mat-icon>
          </button>
          <button mat-icon-button (click)="showSettings.set(!showSettings())" matTooltip="Feed-Einstellungen">
            <mat-icon>tune</mat-icon>
          </button>
          <button mat-icon-button (click)="toggleSearchManager()" matTooltip="OpenSearch-Suchen" [class.active-icon]="showSearchManager() || !!activeSearch()">
            <mat-icon>manage_search</mat-icon>
          </button>
          <button mat-icon-button (click)="load(true)" matTooltip="Aktualisieren" [disabled]="loading()">
            <mat-icon>refresh</mat-icon>
          </button>
        </div>
      </div>

      <!-- ── Cross-source search bar (all cs-feed-* indices) ────────────────── -->
      <div class="glog-bar">
        <mat-icon class="glog-bar-icon">search</mat-icon>
        <input class="glog-input" type="text" [(ngModel)]="searchQuery"
               (keyup.enter)="runFeedSearch()"
               placeholder='Alle Quellen durchsuchen — z.B. metadata.host:"host.example.com" AND severity:high'>
        <select class="glog-range" [(ngModel)]="searchRangeSec">
          @for (r of searchRanges; track r.sec) {
            <option [ngValue]="r.sec">{{ r.label }}</option>
          }
        </select>
        <button mat-flat-button color="primary" class="glog-go"
                (click)="runFeedSearch()" [disabled]="searching()">
          @if (searching()) { <mat-spinner diameter="18"></mat-spinner> }
          @else { <mat-icon>search</mat-icon> }
          Suchen
        </button>
        @if (searchActive()) {
          <button mat-icon-button class="glog-clear" (click)="clearFeedSearch()" matTooltip="Suche zurücksetzen">
            <mat-icon>close</mat-icon>
          </button>
        }
      </div>

      <!-- ── Filter panel ───────────────────────────────────────────────────── -->
      @if (showFilters()) {
        <mat-card class="settings-card filter-card">
          <div class="filter-grid">
            <mat-form-field appearance="outline" class="filter-field">
              <mat-label>System / Hostname</mat-label>
              <input matInput [(ngModel)]="hostFilter" placeholder="z.B. srv-web01" (ngModelChange)="onFilterChange()">
              @if (hostFilter) {
                <button matSuffix mat-icon-button aria-label="Löschen" (click)="hostFilter=''; onFilterChange()">
                  <mat-icon>close</mat-icon>
                </button>
              } @else {
                <mat-icon matSuffix>computer</mat-icon>
              }
            </mat-form-field>
            <mat-form-field appearance="outline" class="filter-field">
              <mat-label>Alert-Schwere</mat-label>
              <mat-select [(ngModel)]="severityFilter" (ngModelChange)="onFilterChange()">
                <mat-option value="">Alle</mat-option>
                <mat-option value="critical">Critical</mat-option>
                <mat-option value="high">High</mat-option>
                <mat-option value="medium">Medium</mat-option>
                <mat-option value="low">Low</mat-option>
                <mat-option value="info">Info</mat-option>
              </mat-select>
            </mat-form-field>
            <mat-form-field appearance="outline" class="filter-field">
              <mat-label>Betriebssystem (CheckMK)</mat-label>
              <mat-select [(ngModel)]="osFilter" (ngModelChange)="onFilterChange()">
                <mat-option value="">Alle</mat-option>
                @for (v of filterValues.os; track v) {
                  <mat-option [value]="v">{{ v }}</mat-option>
                }
              </mat-select>
            </mat-form-field>
            <mat-form-field appearance="outline" class="filter-field">
              <mat-label>Standort / Location (CheckMK)</mat-label>
              <mat-select [(ngModel)]="locationFilter" (ngModelChange)="onFilterChange()">
                <mat-option value="">Alle</mat-option>
                @for (v of filterValues.location; track v) {
                  <mat-option [value]="v">{{ v }}</mat-option>
                }
              </mat-select>
            </mat-form-field>
            <mat-form-field appearance="outline" class="filter-field">
              <mat-label>Kritikalität (CheckMK)</mat-label>
              <mat-select [(ngModel)]="criticalityFilter" (ngModelChange)="onFilterChange()">
                <mat-option value="">Alle</mat-option>
                @for (v of filterValues.criticality; track v) {
                  <mat-option [value]="v">{{ v }}</mat-option>
                }
              </mat-select>
            </mat-form-field>
            <mat-form-field appearance="outline" class="filter-field">
              <mat-label>VE / Umgebung (CheckMK)</mat-label>
              <mat-select [(ngModel)]="veFilter" (ngModelChange)="onFilterChange()">
                <mat-option value="">Alle</mat-option>
                @for (v of filterValues.ve; track v) {
                  <mat-option [value]="v">{{ v }}</mat-option>
                }
              </mat-select>
            </mat-form-field>
            <mat-form-field appearance="outline" class="filter-field">
              <mat-label>Hostgruppe (CheckMK)</mat-label>
              <mat-select [(ngModel)]="hostgroupFilter" (ngModelChange)="onFilterChange()">
                <mat-option value="">Alle</mat-option>
                @for (v of filterValues.hostgroups; track v) {
                  <mat-option [value]="v">{{ v }}</mat-option>
                }
              </mat-select>
            </mat-form-field>
          </div>
          @if (hasActiveFilter()) {
            <div style="padding: 0 4px 8px">
              <button mat-button color="warn" (click)="clearFilters()">
                <mat-icon>clear</mat-icon> Filter zurücksetzen
              </button>
            </div>
          }
        </mat-card>
      }

      <!-- ── Settings panel ──────────────────────────────────────────────── -->
      @if (showSettings()) {
        <mat-card class="settings-card">
          <div class="settings-grid">
            <div class="settings-field">
              <label>CheckMK Mindestalter (Minuten)</label>
              <div class="slider-row">
                <input type="range" min="1" max="60" [(ngModel)]="editPrefs.checkmk_min_age_minutes" class="age-slider">
                <span class="slider-value">{{ editPrefs.checkmk_min_age_minutes }} min</span>
              </div>
            </div>
          </div>
          <div class="settings-actions">
            <button mat-stroked-button (click)="showSettings.set(false)">Abbrechen</button>
            <button mat-flat-button color="primary" (click)="savePrefs()">Speichern</button>
          </div>
        </mat-card>
      }

      <!-- ── Active search indicator ──────────────────────────────────── -->
      @if (activeSearch()) {
        <div class="active-search-bar">
          <mat-icon>manage_search</mat-icon>
          <span class="active-search-name">{{ activeSearch()!.name }}</span>
          <code class="active-search-index">{{ activeSearch()!.index_pattern }}</code>
          @if (activeSearch()!.query_string) {
            <code class="active-search-query">{{ activeSearch()!.query_string }}</code>
          }
          <button mat-icon-button class="clear-search-btn" (click)="clearSearch()" matTooltip="Suche zurücksetzen">
            <mat-icon>close</mat-icon>
          </button>
        </div>
      }

      @if (showSearchManager()) {
        <mat-card class="settings-card search-manager-card">
          <div class="search-manager-header">
            <div>
              <h3>OpenSearch-Suchen</h3>
              <p>
                Lucene Query-Strings gegen <code>cs-feed-graylog</code>, <code>cs-feed-wazuh</code>,
                <code>cs-feed-checkmk</code> oder <code>cs-feed-*</code>.
                Deine CheckMK-Filter wählen die berücksichtigten Systeme vor.
              </p>
            </div>
          </div>

          <!-- System searches -->
          @if (systemSearches().length > 0) {
            <div class="system-searches-section">
              <div class="section-label">Vorgefertigte System-Suchen</div>
              <div class="system-search-grid">
                @for (s of systemSearches(); track s.id) {
                  <button type="button" class="system-search-tile"
                          [class.active]="activeSearch()?.id === s.id"
                          (click)="applySavedSearch(s)">
                    <div class="system-search-name">{{ s.name }}</div>
                    <div class="system-search-meta">
                      <code>{{ s.index_pattern }}</code>
                      @if (s.query_string) { <span>· {{ s.query_string }}</span> }
                    </div>
                  </button>
                }
              </div>
            </div>
          }

          <div class="ai-search-box">
            <mat-form-field appearance="outline">
              <mat-label>Suche per KI-Prompt erzeugen</mat-label>
              <textarea matInput rows="2" [(ngModel)]="aiSearchPrompt"
                placeholder="z.B. alle Wazuh Security Alerts von docker Hosts mit Level 7+"></textarea>
            </mat-form-field>
            <button mat-flat-button color="primary" (click)="generateSearchWithAi()" [disabled]="generatingSearch() || !aiSearchPrompt.trim()">
              @if (generatingSearch()) { <mat-spinner diameter="16"></mat-spinner> }
              @else { <mat-icon>auto_awesome</mat-icon> }
              Generieren
            </button>
          </div>

          <div class="search-editor">
            <mat-form-field appearance="outline">
              <mat-label>Name</mat-label>
              <input matInput [(ngModel)]="searchDraft.name">
            </mat-form-field>
            <mat-form-field appearance="outline">
              <mat-label>Index</mat-label>
              <mat-select [(ngModel)]="searchDraft.index_pattern">
                <mat-option value="cs-feed-*">Alle Quellen</mat-option>
                <mat-option value="cs-feed-graylog">Graylog</mat-option>
                <mat-option value="cs-feed-wazuh">Wazuh</mat-option>
                <mat-option value="cs-feed-checkmk">CheckMK</mat-option>
                <mat-option value="cs-feed-coroot">Coroot</mat-option>
              </mat-select>
            </mat-form-field>
            <mat-form-field appearance="outline" class="query-field">
              <mat-label>Lucene Query</mat-label>
              <textarea matInput rows="3" [(ngModel)]="searchDraft.query_string"></textarea>
            </mat-form-field>
            <div class="search-editor-options">
              <mat-slide-toggle [(ngModel)]="searchDraft.is_exclusion" color="warn">
                Ausblenden — passende Meldungen aus dem Feed verstecken
              </mat-slide-toggle>
            </div>
            <div class="search-editor-actions">
              <button mat-stroked-button (click)="resetSearchDraft()">Zurücksetzen</button>
              <button mat-flat-button color="primary" (click)="saveSearchDraft()" [disabled]="!searchDraft.name.trim()">
                <mat-icon>save</mat-icon>
                Speichern
              </button>
            </div>
          </div>

          <div class="saved-searches">
            @for (search of personalSearches(); track search.id) {
              <div class="saved-search-row" [class.exclusion-search-row]="search.is_exclusion">
                <div class="saved-search-main">
                  <div class="saved-search-name-row">
                    <span class="saved-search-name">{{ search.name }}</span>
                    @if (search.is_exclusion) {
                      <span class="exclusion-badge"><mat-icon>block</mat-icon>Ausblenden</span>
                    }
                  </div>
                  <span class="saved-search-query">{{ search.index_pattern }} · {{ search.query_string || '*' }}</span>
                </div>
                <div class="saved-search-actions">
                  @if (!search.is_exclusion) {
                    <button mat-icon-button matTooltip="Anwenden" (click)="applySavedSearch(search)">
                      <mat-icon>play_arrow</mat-icon>
                    </button>
                  }
                  <button mat-icon-button matTooltip="Bearbeiten" (click)="editSavedSearch(search)">
                    <mat-icon>edit</mat-icon>
                  </button>
                  <button mat-icon-button color="warn" matTooltip="Löschen" (click)="deleteSavedSearch(search)">
                    <mat-icon>delete</mat-icon>
                  </button>
                </div>
              </div>
            } @empty {
              <div class="no-searches">Noch keine persönlichen OpenSearch-Suchen gespeichert.</div>
            }
          </div>
        </mat-card>
      }

      <!-- ── Feed ────────────────────────────────────────────────────────── -->
      <div class="feed-column">

        @if (searchActive()) {
          <div class="search-active-banner">
            <mat-icon>search</mat-icon>
            <span>Suche über alle Quellen: <b>{{ activeSearchQuery() }}</b> · {{ searchRangeLabel() }} · {{ items().length }} Treffer</span>
            <span class="glog-spacer"></span>
            <button mat-icon-button (click)="clearFeedSearch()" matTooltip="Suche zurücksetzen"><mat-icon>close</mat-icon></button>
          </div>
        }

        @if (loading() || searching()) {
          <div class="refresh-indicator">
            <mat-spinner diameter="24"></mat-spinner>
            <span>{{ searching() ? 'Suche…' : 'Aktualisiere…' }}</span>
          </div>
        }

        @for (item of visibleItems(); track item.id; let idx = $index) {
          @if (isFirstSeen(item, idx)) {
            <div class="last-seen-divider"><span>Zuletzt gesehen ↑</span></div>
          }
          <mat-card class="feed-card" [class.card-acknowledged]="item.status === 'acknowledged'" [attr.data-feed-id]="item.id" [attr.data-severity]="item.severity" [attr.data-source]="item.source">

            <!-- LCARS header bar: plain text only — no Material components.
                 Hidden in Classic/Holo, shown in LCARS via CSS. -->
            <div class="lcars-header">
              <span class="lh-source">{{ sourceLabel(item.source) }}</span>
              <span class="lh-dot">·</span>
              <span class="lh-sev" [attr.data-sev]="item.severity">{{ item.severity | uppercase }}</span>
              @if (itemHostLabel(item)) {
                <span class="lh-dot">·</span>
                <span class="lh-host host-clickable" (click)="openHostCockpit($event, itemHostLabel(item))" [matTooltip]="'Server Cockpit öffnen'">{{ itemHostLabel(item) }}</span>
              }
              @if (item.location_name) {
                <span class="lh-dot">·</span>
                <span class="lh-loc">{{ item.location_name }}</span>
              }
              <span class="lh-spacer"></span>
              <span class="lh-time" [title]="absTime(item.created_at)">{{ relTime(item.created_at) }}</span>
              @if (item.status === 'acknowledged') {
                <span class="lh-ack">✓</span>
              }
            </div>

            <!-- Incident badge — shown when this alert belongs to an incident group -->
            @if (item.collab?.incident_id) {
              <div class="incident-badge" (click)="openIncidentTimeline(item.collab!.incident_id!)">
                <mat-icon class="incident-badge-icon">hub</mat-icon>
                <span>Teil eines Incidents</span>
                <mat-icon class="incident-badge-arrow">chevron_right</mat-icon>
              </div>
            }

            <!-- Collab ownership badge — visible in all themes when claimed/investigating -->
            @if (item.collab?.claimed_by_name || (item.collab?.work_status && item.collab?.work_status !== 'new')) {
              <div class="collab-badge" [attr.data-status]="item.collab?.work_status">
                @if (item.collab?.claimed_by_name) {
                  <mat-icon class="collab-badge-icon">person</mat-icon>
                  <span>{{ item.collab!.claimed_by_name }}</span>
                  <span class="collab-sep">·</span>
                }
                <span class="collab-status-label">{{ collabStatusLabel(item.collab?.work_status) }}</span>
                @if ((item.collab?.comment_count ?? 0) > 0) {
                  <span class="collab-sep">·</span>
                  <mat-icon class="collab-badge-icon">comment</mat-icon>
                  <span>{{ item.collab!.comment_count }}</span>
                }
              </div>
            }

            <!-- Classic / Holo card header: avatar + meta -->
            <div class="card-top">
              <div class="source-avatar" [style.background]="sourceColor(item.source)">
                <mat-icon>{{ sourceIcon(item.source) }}</mat-icon>
              </div>
              <div class="card-meta">
                <div class="card-meta-row">
                  <span class="source-label"
                    [style.color]="sourceColor(item.source)"
                    [attr.data-source-label]="item.source">
                    {{ sourceLabel(item.source) }}
                  </span>
                  <span class="severity-badge"
                    [style.background]="severityColor(item.severity) + '22'"
                    [style.color]="severityColor(item.severity)"
                    [attr.data-sev-badge]="item.severity">
                    {{ item.severity }}
                  </span>
                  @if (item.location_name) {
                    <span class="location-tag">
                      <mat-icon style="font-size:12px;height:12px;width:12px">location_on</mat-icon>
                      {{ item.location_name }}{{ item.location_city ? ' · ' + item.location_city : '' }}
                    </span>
                  }
                  @if (itemHostLabel(item)) {
                    <span class="host-tag host-clickable" (click)="openHostCockpit($event, itemHostLabel(item))" [matTooltip]="'Server Cockpit öffnen'">
                      <mat-icon style="font-size:12px;height:12px;width:12px">dns</mat-icon>
                      {{ itemHostLabel(item) }}
                    </span>
                  }
                </div>
                <span class="timestamp" [title]="absTime(item.created_at)">{{ relTime(item.created_at) }}</span>
              </div>
              @if (item.status === 'acknowledged') {
                <span class="ack-stamp"><mat-icon>check_circle</mat-icon> Bestätigt</span>
              }
            </div>

            <!-- Title — clickable for O365/Teams to open original -->
            @if (item.external_url && (item.type === 'email' || item.type === 'teams_message')) {
              <a class="card-title card-title-link" [class.severity-critical]="item.severity === 'critical'"
                 (click)="openUrl(item.external_url)" role="button" matTooltip="Original öffnen">
                {{ item.title }}
                <mat-icon class="open-icon">open_in_new</mat-icon>
              </a>
            } @else {
              <div class="card-title" [class.severity-critical]="item.severity === 'critical'">
                {{ item.title }}
              </div>
            }

            <!-- Body -->
            @if (item.body) {
              <div class="card-body-text" [class.collapsed]="!expanded.has(item.id)"
                   [class.body-clickable]="item.external_url && (item.type === 'email' || item.type === 'teams_message')"
                   (click)="item.external_url && (item.type === 'email' || item.type === 'teams_message') ? openUrl(item.external_url!) : null">
                {{ item.body }}
              </div>
              @if (item.body.length > 200) {
                <button mat-button class="expand-btn" (click)="toggleExpand(item.id)">
                  {{ expanded.has(item.id) ? 'Weniger anzeigen' : 'Mehr anzeigen' }}
                </button>
              }
            }

            <!-- AI Insight -->
            @if (item.ai_insight) {
              <div class="ai-insight">
                <mat-icon class="ai-insight-icon">psychology</mat-icon>
                <span>{{ item.ai_insight }}</span>
              </div>
            }
            @if (item.type === 'alert') {
              <div class="ai-demand-row">
                <button mat-stroked-button class="ki-btn" (click)="requestEnrich(item)"
                        [disabled]="isEnriching(item.id)">
                  @if (isEnriching(item.id)) {
                    <mat-spinner diameter="14" class="ki-spinner"></mat-spinner>
                  } @else {
                    <mat-icon>psychology</mat-icon>
                  }
                  {{ item.ai_insight ? 'Neu analysieren' : 'KI Analyse' }}
                </button>
                @if (item.external_id) {
                  <button mat-stroked-button class="ki-btn diagnose-btn"
                          (click)="diagnoseAlert(item)"
                          [disabled]="isDiagnosing(item.id)"
                          matTooltip="Hermes untersucht den Host: CheckMK-Services + Docker-Logs">
                    @if (isDiagnosing(item.id)) {
                      <mat-spinner diameter="14" class="ki-spinner"></mat-spinner>
                    } @else {
                      <mat-icon>manage_search</mat-icon>
                    }
                    Computer, prüfe das
                  </button>
                }
              </div>
            }

            <!-- Sender (email / teams) -->
            @if ((item.type === 'email' || item.type === 'teams_message') && item.metadata?.['from']) {
              <div class="mail-from">
                <mat-icon style="font-size:14px;height:14px;width:14px">
                  {{ item.type === 'email' ? 'person' : 'chat' }}
                </mat-icon>
                {{ item.metadata!['from'] }}
              </div>
            }

            <mat-divider></mat-divider>

            <!-- Actions -->
            <div class="card-actions">
              @if (item.type === 'alert' && item.status === 'new') {
                <button mat-button class="action-btn" (click)="acknowledge(item)">
                  <mat-icon>check_circle_outline</mat-icon>
                  Bestätigen
                </button>
              }
              @if (item.external_url && item.type !== 'email' && item.type !== 'teams_message') {
                <button mat-button class="action-btn" (click)="openUrl(item.external_url!)">
                  <mat-icon>open_in_new</mat-icon> Details
                </button>
              }
              <button mat-button class="action-btn" (click)="createTicket(item)"
                      [disabled]="isCreatingTicket(item.id)">
                @if (isCreatingTicket(item.id)) {
                  <mat-spinner diameter="16"></mat-spinner>
                  KI bereitet vor…
                } @else {
                  <mat-icon>add_task</mat-icon>
                  Ticket erstellen
                }
              </button>
              <!-- Claim / Release -->
              @if (item.external_id) {
                @if (!item.collab?.claimed_by_name) {
                  <button mat-button class="action-btn collab-claim-btn" (click)="claimAlert(item)"
                          matTooltip="Ich übernehme dieses Problem">
                    <mat-icon>person_add</mat-icon>
                    Übernehmen
                  </button>
                } @else if (isMyAlert(item)) {
                  <button mat-button class="action-btn collab-release-btn" (click)="releaseAlert(item)"
                          matTooltip="Problem freigeben">
                    <mat-icon>person_remove</mat-icon>
                    Freigeben
                  </button>
                  <select class="collab-status-select" [value]="item.collab!.work_status"
                          (change)="setAlertStatus(item, $any($event.target).value)">
                    <option value="investigating">In Bearbeitung</option>
                    <option value="resolved">Gelöst</option>
                    <option value="new">Zurücksetzen</option>
                  </select>
                }
                <button mat-button class="action-btn" (click)="toggleComments(item.id)"
                        matTooltip="Kommentare & Aktivität">
                  <mat-icon>comment</mat-icon>
                  Kommentar@if ((item.collab?.comment_count ?? 0) > 0) { ({{ item.collab!.comment_count }}) }
                </button>
              }
              <button mat-button class="action-btn ignore-btn" (click)="ignoreItem(item)"
                      [disabled]="isIgnoring(item.id)"
                      matTooltip="KI erstellt Ausschluss-Filter für ähnliche Meldungen">
                @if (isIgnoring(item.id)) {
                  <mat-spinner diameter="14" class="ki-spinner"></mat-spinner>
                } @else {
                  <mat-icon>block</mat-icon>
                }
                Ignorieren
              </button>
              <span class="spacer"></span>
              <span class="item-type-hint">{{ typeLabel(item.type) }}</span>
            </div>

            <!-- Inline comment thread -->
            @if (showComments.has(item.id) && item.external_id) {
              <div class="collab-thread">
                @if (collabTimeline.get(item.id) === undefined) {
                  <div class="collab-loading"><mat-spinner diameter="20"></mat-spinner></div>
                } @else {
                  @for (entry of collabTimeline.get(item.id) ?? []; track entry.id) {
                    <div class="collab-entry" [attr.data-kind]="entry.kind">
                      @if (entry.kind === 'comment') {
                        <mat-icon class="ce-icon">chat_bubble_outline</mat-icon>
                        <div class="ce-body">
                          <span class="ce-author">{{ entry.user_name }}</span>
                          <span class="ce-text">{{ entry.body }}</span>
                          <span class="ce-time" [title]="absTime(entry.created_at)">{{ relTime(entry.created_at) }}</span>
                        </div>
                      } @else if (entry.kind === 'ai') {
                        <mat-icon class="ce-icon ce-ai">smart_toy</mat-icon>
                        <div class="ce-body ce-ai-body">
                          <span class="ce-author">Computer</span>
                          <span class="ce-text ce-ai-text">{{ aiBodyMain(entry.body) }}</span>
                          @if (aiBodyEvidence(entry.body).length) {
                            <div class="ce-evidence">
                              <span class="ce-ev-label">📎 Belege</span>
                              @for (ev of aiBodyEvidence(entry.body); track ev.raw) {
                                <span class="ce-ev-chip" [class.clickable]="ev.clickable"
                                      (click)="ev.clickable && jumpToEvidence(ev.ref)">
                                  <span class="ce-ev-type">{{ ev.type }}</span>
                                  <span class="ce-ev-text">{{ ev.text }}</span>
                                </span>
                              }
                            </div>
                          }
                          <span class="ce-time" [title]="absTime(entry.created_at)">{{ relTime(entry.created_at) }}</span>
                        </div>
                      } @else {
                        <mat-icon class="ce-icon ce-system">info_outline</mat-icon>
                        <div class="ce-body ce-system-text" [title]="absTime(entry.created_at)">{{ entry.body }} · {{ relTime(entry.created_at) }}</div>
                      }
                    </div>
                  }
                }
                <div class="collab-input-row">
                  <input class="collab-input" [id]="'ci-'+item.id"
                         placeholder="Kommentar schreiben…"
                         (keydown.enter)="postComment(item, $any($event.target).value); $any($event.target).value=''">
                  <button mat-icon-button (click)="postCommentFromInput(item, 'ci-'+item.id)">
                    <mat-icon>send</mat-icon>
                  </button>
                </div>
              </div>
            }

          </mat-card>
        }

        <!-- Infinite scroll sentinel -->
        <div #scrollSentinel class="scroll-sentinel"></div>

        <!-- Loading more indicator -->
        @if (loadingMore()) {
          <div class="load-more-spinner"><mat-spinner diameter="32"></mat-spinner></div>
        }

        <!-- Empty state -->
        @if (!loading() && visibleItems().length === 0) {
          <div class="empty-state">
            <mat-icon>check_circle_outline</mat-icon>
            @if (hostFilter) {
              <p>Keine offenen Alerts für „{{ hostFilter }}"</p>
              <span>Dieser Host hat aktuell keine Meldungen im Feed (ggf. nur Metrik-Auslastung).</span>
            } @else {
              <p>Keine neuen Meldungen</p>
              <span>Alle Systeme sind ruhig.</span>
            }
          </div>
        }

      </div>
    </div>

    <!-- Incident Timeline Drawer -->
    @if (incidentDrawerOpen()) {
      <div class="incident-drawer-overlay" (click)="closeIncidentDrawer()"></div>
      <div class="incident-drawer">
        <div class="incident-drawer-header">
          <mat-icon>hub</mat-icon>
          @if (incidentTimeline()) {
            <div class="incident-drawer-title">
              <span class="incident-drawer-host">{{ incidentTimeline()!.incident.primary_host }}</span>
              <span class="incident-drawer-meta">{{ incidentTimeline()!.incident.member_count }} Alerts · {{ incidentTimeline()!.incident.severity | uppercase }}</span>
            </div>
            <button mat-stroked-button class="claude-handoff-btn"
                    [disabled]="copyingPrompt()"
                    (click)="handoffToComputer(incidentTimeline()!.incident.id)"
                    matTooltip="Incident-Kontext an den Computer-Agenten übergeben — Hermes analysiert und kann per SSH eingreifen">
              @if (copyingPrompt()) {
                <mat-spinner diameter="14"></mat-spinner>
              } @else {
                <mat-icon>precision_manufacturing</mat-icon>
              }
              {{ promptCopied() ? 'Übergeben ✓' : 'Computer, untersuche das' }}
            </button>
          } @else {
            <span>Incident Timeline</span>
          }
          <button mat-icon-button class="incident-drawer-close" (click)="closeIncidentDrawer()">
            <mat-icon>close</mat-icon>
          </button>
        </div>
        @if (loadingIncident()) {
          <div class="incident-drawer-loading"><mat-spinner diameter="32"></mat-spinner></div>
        } @else if (incidentTimeline()) {
          @if (incidentTimeline()!.incident.causal_context?.length) {
            <div class="causal-panel">
              <div class="causal-panel-header">
                <mat-icon>account_tree</mat-icon>
                <span>Wahrscheinliche Ursache</span>
              </div>
              @for (cause of incidentTimeline()!.incident.causal_context!; track cause.incident_id) {
                <div class="causal-entry" (click)="openIncidentTimeline(cause.incident_id)">
                  <mat-icon class="causal-icon">hub</mat-icon>
                  <div class="causal-info">
                    <span class="causal-service">{{ cause.service }}</span>
                    <span class="causal-host">{{ cause.host }}</span>
                  </div>
                  <span class="causal-since">seit {{ cause.started_at | date:'HH:mm' }}</span>
                  <mat-icon class="causal-arrow">chevron_right</mat-icon>
                </div>
              }
            </div>
          }
          <div class="incident-timeline-list">
            @for (entry of incidentTimeline()!.timeline; track entry.at + entry.text) {
              <div class="tl-entry" [attr.data-kind]="entry.kind">
                <div class="tl-icon-col">
                  <mat-icon class="tl-icon">{{ timelineKindIcon(entry.kind) }}</mat-icon>
                </div>
                <div class="tl-content">
                  <div class="tl-meta">
                    <span class="tl-kind">{{ timelineKindLabel(entry.kind) }}</span>
                    @if (entry.source && entry.source !== 'collaboration') {
                      <span class="tl-source">{{ entry.source }}</span>
                    }
                    @if (entry.user) {
                      <span class="tl-user">{{ entry.user }}</span>
                    }
                    <span class="tl-time">{{ entry.at | date:'HH:mm:ss' }}</span>
                  </div>
                  @if (entry.severity) {
                    <span class="tl-sev" [attr.data-sev]="entry.severity">{{ entry.severity | uppercase }}</span>
                  }
                  <div class="tl-text">{{ entry.text }}</div>
                </div>
              </div>
            }
          </div>
        }
      </div>
    }

  `,
  styles: [`
    .scroll-top-btn {
      position: fixed;
      top: 68px;
      left: 50%;
      transform: translateX(-50%);
      z-index: 200;
      box-shadow: 0 4px 16px rgba(0,0,0,.35) !important;
      border-radius: 24px !important;
      padding: 0 20px !important;
      height: 40px;
      font-size: 13px;
      font-weight: 600;
      white-space: nowrap;
      animation: slideDown .2s ease-out;
    }
    .scroll-top-btn mat-icon { font-size: 18px; height: 18px; width: 18px; margin-right: 6px; }
    @keyframes slideDown { from { opacity: 0; transform: translateX(-50%) translateY(-12px); } to { opacity: 1; transform: translateX(-50%) translateY(0); } }

    .feed-page { padding: 24px; max-width: 720px; margin: 0 auto; }

    /* Top bar */
    .feed-topbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; flex-wrap: wrap; gap: 12px; }
    .feed-topbar h2 { margin: 0; font-size: 22px; font-weight: 600; }
    .topbar-right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

    /* ── Cross-source search bar ─────────────────────────────────────── */
    .glog-bar { display: flex; align-items: center; gap: 8px; margin-bottom: 16px; padding: 8px 12px;
      background: var(--mat-sys-surface-container, #f1f1f4); border-radius: 10px; }
    .glog-bar-icon { color: var(--mat-sys-on-surface-variant); flex-shrink: 0; }
    .glog-input { flex: 1; min-width: 120px; border: none; background: transparent; outline: none;
      font-size: 14px; font-family: inherit; color: var(--mat-sys-on-surface); padding: 6px 4px; }
    .glog-range { border: 1px solid var(--mat-sys-outline-variant, #ccc); border-radius: 6px; padding: 6px 8px;
      background: var(--mat-sys-surface, #fff); color: var(--mat-sys-on-surface); font-size: 13px; cursor: pointer; }
    .glog-go { flex-shrink: 0; }
    .glog-spacer { flex: 1; }
    .search-active-banner { display: flex; align-items: center; gap: 8px; padding: 6px 12px; margin-bottom: 12px;
      border-radius: 8px; font-size: 13px; background: color-mix(in srgb, var(--mat-sys-primary) 10%, transparent);
      color: var(--mat-sys-on-surface); }
    .search-active-banner mat-icon { font-size: 18px; height: 18px; width: 18px; color: var(--mat-sys-primary); }

    /* LCARS theme */
    :host-context(html.cs-theme-lcars) .glog-bar { background: #15120c; border: 1px solid #2a1d0a; border-radius: 0 12px 12px 0; border-left: 6px solid #ffcc66; }
    :host-context(html.cs-theme-lcars) .glog-bar-icon { color: #ffcc66; }
    :host-context(html.cs-theme-lcars) .glog-input { color: #ffe8a0; font-family: 'Antonio','Eurostile','Roboto Condensed',sans-serif; letter-spacing: .04em; }
    :host-context(html.cs-theme-lcars) .glog-input::placeholder { color: #8a7a55; }
    :host-context(html.cs-theme-lcars) .glog-range { background: #0c0a06; color: #ffcc66; border-color: #2a1d0a; }
    :host-context(html.cs-theme-lcars) .search-active-banner { background: #1c1607; color: #ffe8a0; border-left: 6px solid #ffcc66; border-radius: 0 10px 10px 0; }
    :host-context(html.cs-theme-lcars) .search-active-banner mat-icon { color: #ffcc66; }

    /* Holo theme */
    :host-context(html.cs-theme-holo) .glog-bar { background: rgba(13,34,54,.55); border: 1px solid rgba(79,214,255,.18); }
    :host-context(html.cs-theme-holo) .glog-bar-icon { color: #4fd6ff; }
    :host-context(html.cs-theme-holo) .glog-input { color: #cfeeff; }
    :host-context(html.cs-theme-holo) .glog-range { background: rgba(8,20,32,.7); color: #cfeeff; border-color: rgba(79,214,255,.25); }

    .active-icon { color: var(--mat-sys-primary) !important; }

    /* Filter + Settings panels */
    .settings-card { padding: 16px 20px; margin-bottom: 20px; }
    .filter-card { padding: 12px 16px 4px; }
    .filter-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
    .filter-field { width: 100%; }
    .settings-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 12px; }
    .settings-field label { font-size: 13px; font-weight: 500; color: var(--mat-sys-on-surface-variant); display: block; margin-bottom: 8px; }
    .slider-row { display: flex; align-items: center; gap: 12px; }
    .age-slider { flex: 1; }
    .slider-value { font-size: 14px; font-weight: 600; min-width: 40px; }
    .source-toggles { display: flex; flex-direction: column; gap: 8px; }
    .settings-actions { display: flex; justify-content: flex-end; gap: 8px; padding-top: 8px; }
    code { background: var(--mat-sys-surface-variant); padding: 1px 4px; border-radius: 4px; }
    .active-search-bar {
      display: flex; align-items: center; gap: 8px; padding: 8px 14px;
      background: color-mix(in srgb, var(--mat-sys-primary) 10%, transparent);
      border: 1px solid color-mix(in srgb, var(--mat-sys-primary) 30%, transparent);
      border-radius: 12px; font-size: 13px; flex-wrap: wrap;
    }
    .active-search-bar mat-icon { color: var(--mat-sys-primary); font-size: 18px; height: 18px; width: 18px; flex-shrink: 0; }
    .active-search-name { font-weight: 700; color: var(--mat-sys-primary); }
    .active-search-index { background: var(--mat-sys-surface-variant); padding: 1px 6px; border-radius: 4px; font-size: 11px; }
    .active-search-query { background: var(--mat-sys-surface-variant); padding: 1px 6px; border-radius: 4px; font-size: 11px; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .clear-search-btn { margin-left: auto; }
    .system-searches-section { display: flex; flex-direction: column; gap: 8px; }
    .section-label { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; color: var(--mat-sys-on-surface-variant); }
    .system-search-grid { display: flex; flex-direction: column; gap: 6px; }
    .system-search-tile {
      display: flex; flex-direction: column; gap: 2px;
      padding: 10px 12px; border-radius: 10px; text-align: left;
      border: 1px solid var(--mat-sys-outline-variant);
      background: var(--mat-sys-surface); cursor: pointer;
      transition: background 0.15s;
    }
    .system-search-tile:hover { background: var(--mat-sys-surface-variant); }
    .system-search-tile.active {
      border-color: var(--mat-sys-primary);
      background: color-mix(in srgb, var(--mat-sys-primary) 10%, transparent);
    }
    .system-search-name { font-size: 13px; font-weight: 700; }
    .system-search-meta { font-size: 11px; color: var(--mat-sys-on-surface-variant); font-family: monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .search-manager-card { display: flex; flex-direction: column; gap: 14px; }
    .search-manager-header h3 { margin: 0 0 4px; }
    .search-manager-header p { margin: 0; color: var(--mat-sys-on-surface-variant); font-size: 12px; line-height: 1.5; }
    .ai-search-box { display: grid; grid-template-columns: 1fr auto; gap: 10px; align-items: center; }
    .ai-search-box mat-form-field { width: 100%; }
    .ai-search-box mat-spinner { display: inline-block; margin-right: 4px; }
    .search-editor {
      display: grid;
      grid-template-columns: 1fr 180px;
      gap: 10px;
      padding: 12px;
      border: 1px solid var(--mat-sys-outline-variant);
      border-radius: 12px;
    }
    .query-field { grid-column: 1 / -1; }
    .search-editor-actions { grid-column: 1 / -1; display: flex; justify-content: flex-end; gap: 8px; }
    .saved-searches { display: flex; flex-direction: column; gap: 8px; }
    .saved-search-row {
      display: flex; align-items: center; justify-content: space-between; gap: 10px;
      padding: 9px 11px; border-radius: 10px; background: var(--mat-sys-surface-variant);
    }
    .exclusion-search-row {
      background: color-mix(in srgb, #b71c1c 10%, var(--mat-sys-surface-variant));
      border-left: 3px solid #ef5350;
    }
    .saved-search-main { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
    .saved-search-name-row { display: flex; align-items: center; gap: 8px; }
    .saved-search-name { font-weight: 700; font-size: 13px; }
    .exclusion-badge {
      display: inline-flex; align-items: center; gap: 3px;
      background: #b71c1c; color: white; border-radius: 4px;
      padding: 1px 6px; font-size: 10px; font-weight: 600;
    }
    .exclusion-badge mat-icon { font-size: 12px; height: 12px; width: 12px; }
    .saved-search-query { font-family: monospace; font-size: 11px; color: var(--mat-sys-on-surface-variant); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .saved-search-actions { display: flex; align-items: center; gap: 2px; flex-shrink: 0; }
    .no-searches { color: var(--mat-sys-on-surface-variant); font-size: 13px; padding: 10px; text-align: center; }
    .search-editor-options { padding: 0 0 12px; }

    /* Feed cards */
    .feed-column { display: flex; flex-direction: column; gap: 12px; }
    .refresh-indicator {
      display: flex; align-items: center; gap: 8px;
      padding: 8px 4px; font-size: 13px;
      color: var(--mat-sys-on-surface-variant);
    }

    .feed-card {
      border-radius: 12px !important;
      overflow: hidden;
      transition: box-shadow 0.2s;
    }
    .feed-card:hover { box-shadow: 0 4px 20px rgba(0,0,0,.15) !important; }
    .card-acknowledged { opacity: 0.6; }
    @keyframes feedHighlight {
      0%   { box-shadow: 0 0 0 3px var(--mat-sys-primary), 0 4px 20px rgba(0,0,0,.15); background: color-mix(in srgb, var(--mat-sys-primary) 18%, var(--mat-sys-surface)); }
      70%  { box-shadow: 0 0 0 3px var(--mat-sys-primary), 0 4px 20px rgba(0,0,0,.15); background: color-mix(in srgb, var(--mat-sys-primary) 18%, var(--mat-sys-surface)); }
      100% { box-shadow: none; background: var(--mat-sys-surface); }
    }
    .feed-highlight { animation: feedHighlight 2.8s ease-out forwards; }

    /* Card top */
    .card-top { display: flex; align-items: flex-start; gap: 12px; padding: 16px 16px 12px; }
    .source-avatar {
      width: 42px; height: 42px; border-radius: 50%;
      display: flex; align-items: center; justify-content: center;
      flex-shrink: 0;
      color: #fff;
    }
    .source-avatar mat-icon { font-size: 20px; height: 20px; width: 20px; }
    .card-meta { flex: 1; min-width: 0; }
    .card-meta-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 2px; }
    .source-label { font-weight: 600; font-size: 13px; }
    .severity-badge {
      font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .5px;
      padding: 2px 7px; border-radius: 10px;
    }
    .location-tag {
      font-size: 11px; color: var(--mat-sys-on-surface-variant);
      display: flex; align-items: center; gap: 2px;
    }
    .host-tag {
      font-size: 13px; color: var(--mat-sys-on-surface-variant);
      display: flex; align-items: center; gap: 2px;
      font-family: 'Fira Code', monospace;
    }
    .host-clickable { cursor: pointer; }
    .host-clickable:hover { text-decoration: underline; }
    .timestamp { font-size: 12px; color: var(--mat-sys-on-surface-variant); }
    .ack-stamp {
      display: flex; align-items: center; gap: 4px;
      font-size: 12px; color: #388e3c; font-weight: 600;
    }
    .ack-stamp mat-icon { font-size: 14px; height: 14px; width: 14px; }

    /* Title */
    .card-title {
      padding: 0 16px 10px;
      font-size: 14px; font-weight: 500; line-height: 1.4;
    }
    .card-title-link {
      display: flex; align-items: center; gap: 6px;
      cursor: pointer; text-decoration: none; color: inherit;
      border-radius: 4px; transition: color 0.15s;
    }
    .card-title-link:hover { color: var(--mat-sys-primary); }
    .open-icon { font-size: 14px; height: 14px; width: 14px; opacity: 0.6; }
    .severity-critical { color: #b71c1c; }
    .body-clickable { cursor: pointer; }
    .body-clickable:hover { color: var(--mat-sys-on-surface); }

    /* Body */
    .card-body-text {
      padding: 0 16px 8px;
      font-size: 13px; line-height: 1.6;
      color: var(--mat-sys-on-surface-variant);
      white-space: pre-wrap;
      word-break: break-word;
    }
    .card-body-text.collapsed {
      max-height: 80px;
      overflow: hidden;
      -webkit-mask-image: linear-gradient(to bottom, black 50%, transparent 100%);
      mask-image: linear-gradient(to bottom, black 50%, transparent 100%);
    }
    .expand-btn { margin: 0 8px 4px; font-size: 12px; }
    .ai-insight {
      margin: 0 16px 10px;
      padding: 8px 12px;
      border-radius: 8px;
      background: color-mix(in srgb, var(--mat-sys-primary) 8%, transparent);
      border-left: 3px solid var(--mat-sys-primary);
      font-size: 12px; line-height: 1.5;
      color: var(--mat-sys-on-surface-variant);
      display: flex; gap: 8px; align-items: flex-start;
    }
    .ai-insight-icon {
      font-size: 16px; height: 16px; width: 16px;
      color: var(--mat-sys-primary); flex-shrink: 0; margin-top: 1px;
    }
    .ai-demand-row { padding: 4px 16px 8px; }
    .ki-btn { font-size: 12px; height: 30px; line-height: 30px; color: var(--mat-sys-primary); border-color: var(--mat-sys-primary); }
    .ki-btn mat-icon { font-size: 15px; height: 15px; width: 15px; margin-right: 4px; vertical-align: middle; }
    .ki-spinner { display: inline-block; margin-right: 4px; vertical-align: middle; }

    .mail-from {
      padding: 0 16px 8px;
      font-size: 12px; color: var(--mat-sys-on-surface-variant);
      display: flex; align-items: center; gap: 4px;
    }

    /* Actions */
    .card-actions {
      display: flex; align-items: center;
      padding: 4px 8px;
      gap: 4px;
    }
    .action-btn { font-size: 13px; color: var(--mat-sys-on-surface-variant); }
    .action-btn mat-icon { font-size: 16px; height: 16px; width: 16px; margin-right: 4px; }
    .ignore-btn { color: var(--mat-sys-error) !important; opacity: 0.7; }
    .ignore-btn:hover { opacity: 1; }
    .spacer { flex: 1; }
    .item-type-hint { font-size: 11px; color: var(--mat-sys-outline); padding-right: 8px; }

    /* Last-seen divider */
    .last-seen-divider {
      display: flex; align-items: center; gap: 8px; padding: 4px 16px;
      color: var(--mat-sys-on-surface-variant); font-size: 12px; font-weight: 500;
    }
    .last-seen-divider::before, .last-seen-divider::after {
      content: ''; flex: 1; height: 1px; background: var(--mat-sys-outline-variant);
    }

    /* Infinite scroll */
    .scroll-sentinel { height: 1px; }
    .load-more-spinner { display: flex; justify-content: center; padding: 16px; }
    .empty-state {
      display: flex; flex-direction: column; align-items: center;
      padding: 60px 20px; color: var(--mat-sys-on-surface-variant);
      gap: 8px;
    }
    .empty-state mat-icon { font-size: 48px; height: 48px; width: 48px; opacity: 0.4; }
    .empty-state p { font-size: 16px; font-weight: 500; margin: 0; }
    .empty-state span { font-size: 13px; }
    @media (max-width: 760px) {
      .ai-search-box, .search-editor { grid-template-columns: 1fr; }
    }

    /* ══ LCARS THEME ══════════════════════════════════════════════════════════ */
    :host-context(html.cs-theme-lcars) .feed-page {
      /* Only structural/label elements use the condensed LCARS font.
         Body text (card-title, card-body-text) uses standard Roboto for readability — same as Alerts. */
      padding: 12px 16px; max-width: 900px;
    }
    /* LCARS label elements: condensed font for header badges/labels */
    :host-context(html.cs-theme-lcars) .lcars-header,
    :host-context(html.cs-theme-lcars) .feed-topbar h2,
    :host-context(html.cs-theme-lcars) .lh-source,
    :host-context(html.cs-theme-lcars) .lh-sev,
    :host-context(html.cs-theme-lcars) .lh-loc,
    :host-context(html.cs-theme-lcars) .last-seen-divider,
    :host-context(html.cs-theme-lcars) .collab-badge,
    :host-context(html.cs-theme-lcars) .analysis-lcars-header {
      font-family: 'Antonio','Eurostile','Roboto Condensed',sans-serif;
    }
    :host-context(html.cs-theme-lcars) .feed-topbar h2 {
      font-size: 20px; font-weight: 800; letter-spacing: .22em; text-transform: uppercase;
      color: #ffcc66; background: #000; display: inline-block; padding: 3px 10px 3px 0;
    }
    /* ── feed cards ── */
    :host-context(html.cs-theme-lcars) .feed-card {
      background: #15120c !important;
      border: none !important;
      border-left: 8px solid #FF9933 !important;
      border-radius: 0 14px 14px 0 !important;
      box-shadow: none !important;
      transition: background .15s !important;
    }
    :host-context(html.cs-theme-lcars) .feed-card:hover { background: #1e1710 !important; }
    :host-context(html.cs-theme-lcars) .feed-card.card-acknowledged { opacity: .45; }
    /* severity → left border color */
    :host-context(html.cs-theme-lcars) .feed-card[data-severity="critical"] { border-left-color: #ff5544 !important; }
    :host-context(html.cs-theme-lcars) .feed-card[data-severity="high"]     { border-left-color: #ffcc00 !important; }
    :host-context(html.cs-theme-lcars) .feed-card[data-severity="medium"]   { border-left-color: #FF9933 !important; }
    :host-context(html.cs-theme-lcars) .feed-card[data-severity="warning"]  { border-left-color: #ffcc00 !important; }
    :host-context(html.cs-theme-lcars) .feed-card[data-severity="low"]      { border-left-color: #99CCFF !important; }
    :host-context(html.cs-theme-lcars) .feed-card[data-severity="info"]     { border-left-color: #66cc66 !important; }
    /* ── LCARS header bar: hidden by default, shown only in LCARS ── */
    .lcars-header { display: none; }

    /* ── LCARS card overrides ── */
    :host-context(html.cs-theme-lcars) .card-top { display: none; }  /* replaced by lcars-header */
    :host-context(html.cs-theme-lcars) .lcars-header {
      display: flex; align-items: center; gap: 6px;
      padding: 7px 14px;
      background: #FF9933;   /* default: checkmk orange */
      border-radius: 0 13px 0 0;
      min-height: 36px; flex-shrink: 0;
      font-family: 'Antonio','Eurostile','Roboto Condensed',sans-serif;
      font-size: 11px; font-weight: 900; text-transform: uppercase; letter-spacing: .08em;
      color: #000;
    }
    /* Source-specific header colors */
    :host-context(html.cs-theme-lcars) .feed-card[data-source="graylog"] .lcars-header  { background: #ffcc66; }
    :host-context(html.cs-theme-lcars) .feed-card[data-source="wazuh"]   .lcars-header  { background: #99CCFF; }
    :host-context(html.cs-theme-lcars) .feed-card[data-source="o365"]    .lcars-header  { background: #FFCC99; }
    :host-context(html.cs-theme-lcars) .feed-card[data-source="teams"]   .lcars-header  { background: #FFCC99; }
    :host-context(html.cs-theme-lcars) .feed-card[data-source="coroot"]  .lcars-header  { background: #99FFCC; }
    /* Header text elements */
    :host-context(html.cs-theme-lcars) .lh-source { font-weight: 900; font-size: 12px; }
    :host-context(html.cs-theme-lcars) .lh-dot    { opacity: .5; }
    :host-context(html.cs-theme-lcars) .lh-sev    { font-size: 10px; padding: 1px 6px; border-radius: 2px; background: rgba(0,0,0,.18); }
    :host-context(html.cs-theme-lcars) .lh-host   { font-family: 'Fira Code',monospace; font-size: 11px; opacity: .85; text-transform: none; }
    :host-context(html.cs-theme-lcars) .lh-loc    { opacity: .7; font-size: 10px; }
    :host-context(html.cs-theme-lcars) .lh-spacer { flex: 1; }
    :host-context(html.cs-theme-lcars) .lh-time   { opacity: .55; font-size: 10px; }
    :host-context(html.cs-theme-lcars) .lh-ack    { opacity: .8; font-size: 12px; }
    :host-context(html.cs-theme-lcars) .timestamp  { display: none; }  /* shown in lcars-header instead */
    /* Card title: same font/weight as alerts (readable, not condensed) */
    :host-context(html.cs-theme-lcars) .card-title {
      color: #ffe8a0; padding: 8px 14px 6px; font-size: 14px; font-weight: 500;
      font-family: Roboto, 'Helvetica Neue', sans-serif;
    }
    :host-context(html.cs-theme-lcars) .card-title-link { color: #ffe8a0; font-family: Roboto, 'Helvetica Neue', sans-serif; }
    :host-context(html.cs-theme-lcars) .severity-critical { color: #ff7766 !important; }
    /* ── body ── */
    :host-context(html.cs-theme-lcars) .card-body-text {
      color: #e8a060; padding: 0 14px 8px; font-size: 12px;
      font-family: Roboto, 'Helvetica Neue', sans-serif;  /* readable — no condensed font for body */
    }
    :host-context(html.cs-theme-lcars) .card-body-text.collapsed {
      -webkit-mask-image: linear-gradient(to bottom, #ffe8a0 40%, transparent 100%);
    }
    :host-context(html.cs-theme-lcars) .expand-btn { color: #ffcc66 !important; }
    /* ── AI insight ── */
    :host-context(html.cs-theme-lcars) .ai-insight {
      background: rgba(232,124,58,.1); border-left: 3px solid #FF9933;
      color: #ffcc99; margin: 0 14px 8px; border-radius: 0;
    }
    :host-context(html.cs-theme-lcars) .ai-insight-icon { color: #FF9933; }
    :host-context(html.cs-theme-lcars) .ai-demand-row { padding: 4px 14px 8px; }
    :host-context(html.cs-theme-lcars) .ki-btn { color: #FF9933 !important; border-color: #FF9933 !important; }
    /* ── actions bar ── */
    :host-context(html.cs-theme-lcars) mat-divider { --mat-divider-color: #2a1d0a; }
    :host-context(html.cs-theme-lcars) .card-actions { background: #0a0804; padding: 4px 10px; border-top: 1px solid #2a1d0a; }
    :host-context(html.cs-theme-lcars) .action-btn { color: #e8a060 !important; font-size: 12px; }
    :host-context(html.cs-theme-lcars) .ignore-btn { color: #ff7766 !important; }
    :host-context(html.cs-theme-lcars) .item-type-hint { color: rgba(255,204,153,.3); font-size: 10px; }
    /* ── divider & empty ── */
    :host-context(html.cs-theme-lcars) .last-seen-divider { color: #ffcc66; font-family: 'Antonio','Eurostile',sans-serif; letter-spacing: .08em; text-transform: uppercase; font-size: 11px; }
    :host-context(html.cs-theme-lcars) .last-seen-divider::before, :host-context(html.cs-theme-lcars) .last-seen-divider::after { background: #3a2810; }
    :host-context(html.cs-theme-lcars) .empty-state { color: #5a3a18; }
    :host-context(html.cs-theme-lcars) .empty-state mat-icon { color: #3a2810; }
    /* ── panels (filter / settings / search manager) ── */
    :host-context(html.cs-theme-lcars) .settings-card { background: #15120c !important; border: 1px solid #2a1d0a !important; border-radius: 0 14px 14px 0 !important; box-shadow: none !important; }
    :host-context(html.cs-theme-lcars) .active-search-bar { background: rgba(232,124,58,.1); border: 1px solid rgba(232,124,58,.3); border-radius: 0 10px 10px 0; color: #ffcc99; }
    :host-context(html.cs-theme-lcars) .active-search-name { color: #FF9933; }
    :host-context(html.cs-theme-lcars) .system-search-tile { background: #15120c; border-color: #2a1d0a; color: #ffe8a0; border-radius: 0 8px 8px 0; }
    :host-context(html.cs-theme-lcars) .system-search-tile:hover { background: #1e1710; }
    :host-context(html.cs-theme-lcars) .system-search-tile.active { border-color: #FF9933; background: rgba(232,124,58,.12); }
    :host-context(html.cs-theme-lcars) .system-search-name { color: #ffe8a0; }
    :host-context(html.cs-theme-lcars) .system-search-meta { color: #e8a060; }
    :host-context(html.cs-theme-lcars) .saved-search-row { background: #15120c; border-radius: 0 8px 8px 0; }
    :host-context(html.cs-theme-lcars) .saved-search-name { color: #ffe8a0; }
    :host-context(html.cs-theme-lcars) .saved-search-query { color: #e8a060; }
    :host-context(html.cs-theme-lcars) .section-label { color: #ffcc66; }
    :host-context(html.cs-theme-lcars) .no-searches { color: #5a3a18; }
    /* highlight animation override */
    @keyframes feedHighlightLcars {
      0%   { box-shadow: 0 0 0 2px #ffcc66; background: #2a1d0a; }
      80%  { box-shadow: 0 0 0 2px #ffcc66; }
      100% { box-shadow: none; background: #15120c; }
    }
    :host-context(html.cs-theme-lcars) .feed-highlight { animation: feedHighlightLcars 2.8s ease-out forwards; }

    /* ══ HOLO THEME ══════════════════════════════════════════════════════════ */
    :host-context(html.cs-theme-holo) .feed-topbar h2 { color: #9fe8ff; font-size: 18px; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
    :host-context(html.cs-theme-holo) .feed-card {
      background: rgba(10,28,46,.85) !important;
      border: none !important;
      border-left: 6px solid #4fd6ff !important;
      border-radius: 0 12px 12px 0 !important;
      box-shadow: 0 0 12px rgba(79,214,255,.06) !important;
    }
    :host-context(html.cs-theme-holo) .feed-card[data-severity="critical"] { border-left-color: #ff5b6e !important; }
    :host-context(html.cs-theme-holo) .feed-card[data-severity="high"]     { border-left-color: #ffd84a !important; }
    :host-context(html.cs-theme-holo) .feed-card[data-severity="medium"]   { border-left-color: #4fd6ff !important; }
    :host-context(html.cs-theme-holo) .feed-card[data-severity="low"]      { border-left-color: #3dffa8 !important; }
    :host-context(html.cs-theme-holo) .card-top {
      background: rgba(79,214,255,.18); border-bottom: 1px solid rgba(79,214,255,.2);
      padding: 7px 14px; border-radius: 0 11px 0 0;
    }
    :host-context(html.cs-theme-holo) .source-avatar { display: none; }
    :host-context(html.cs-theme-holo) .source-label { color: #cfeeff !important; font-weight: 900; text-transform: uppercase; font-size: 11px; }
    :host-context(html.cs-theme-holo) .severity-badge { background: rgba(255,255,255,.12) !important; color: #cfeeff !important; }
    :host-context(html.cs-theme-holo) .host-tag { color: #9fe8ff !important; }
    :host-context(html.cs-theme-holo) .location-tag { color: #5fc8ee !important; }
    :host-context(html.cs-theme-holo) .timestamp { color: rgba(143,184,207,.5) !important; }
    :host-context(html.cs-theme-holo) .card-title { color: #cfeeff; padding: 8px 14px 6px; }
    :host-context(html.cs-theme-holo) .card-body-text { color: #8fb8cf; padding: 0 14px 8px; }
    :host-context(html.cs-theme-holo) .ai-insight { background: rgba(79,214,255,.08); border-left: 3px solid #4fd6ff; color: #bfefff; margin: 0 14px 8px; }
    :host-context(html.cs-theme-holo) .ai-insight-icon { color: #4fd6ff; }
    :host-context(html.cs-theme-holo) .card-actions { background: rgba(5,15,30,.5); border-top: 1px solid rgba(79,214,255,.1); }
    :host-context(html.cs-theme-holo) .action-btn { color: #8fb8cf !important; }
    :host-context(html.cs-theme-holo) .ki-btn { color: #4fd6ff !important; border-color: rgba(79,214,255,.5) !important; }
    :host-context(html.cs-theme-holo) .last-seen-divider { color: #4fd6ff; }
    :host-context(html.cs-theme-holo) .last-seen-divider::before, :host-context(html.cs-theme-holo) .last-seen-divider::after { background: rgba(79,214,255,.2); }
    :host-context(html.cs-theme-holo) .empty-state { color: rgba(79,214,255,.3); }
    :host-context(html.cs-theme-holo) .settings-card { background: rgba(10,28,46,.85) !important; border: 1px solid rgba(79,214,255,.2) !important; box-shadow: none !important; }

    /* ── Collaboration styles ── */
    .collab-badge {
      display: flex; align-items: center; gap: 5px;
      padding: 3px 12px;
      font-size: 11px; font-weight: 700;
      background: color-mix(in srgb, var(--mat-sys-primary) 10%, var(--mat-sys-surface));
      color: var(--mat-sys-primary);
      border-bottom: 1px solid color-mix(in srgb, var(--mat-sys-primary) 20%, transparent);
    }
    .collab-badge[data-status="investigating"] { background: color-mix(in srgb, #f57c00 10%, var(--mat-sys-surface)); color: #f57c00; }
    .collab-badge[data-status="resolved"] { background: color-mix(in srgb, #388e3c 10%, var(--mat-sys-surface)); color: #388e3c; }
    .collab-badge-icon { font-size: 13px; height: 13px; width: 13px; }
    .collab-sep { opacity: .4; }
    .collab-status-label { font-weight: 600; }
    .collab-claim-btn { color: var(--mat-sys-primary) !important; }
    .collab-release-btn { color: #f57c00 !important; }
    .collab-status-select {
      font-size: 11px; border: 1px solid var(--mat-sys-outline-variant);
      border-radius: 4px; padding: 2px 4px; background: transparent;
      color: var(--mat-sys-on-surface); cursor: pointer; margin: 0 4px;
    }
    /* Inline comment thread */
    .collab-thread {
      border-top: 1px solid var(--mat-sys-outline-variant);
      padding: 8px 14px 10px;
      display: flex; flex-direction: column; gap: 6px;
    }
    .collab-loading { display: flex; justify-content: center; padding: 8px; }
    .collab-entry { display: flex; align-items: flex-start; gap: 6px; }
    .ce-icon { font-size: 15px; height: 15px; width: 15px; flex-shrink: 0; margin-top: 2px; color: var(--mat-sys-on-surface-variant); }
    .ce-ai { color: var(--mat-sys-primary); }
    .ce-body { display: flex; flex-wrap: wrap; gap: 4px; align-items: baseline; font-size: 12px; }
    .ce-author { font-weight: 700; color: var(--mat-sys-on-surface); }
    .ce-text { color: var(--mat-sys-on-surface-variant); flex: 1; min-width: 120px; }
    .ce-time { font-size: 10px; color: var(--mat-sys-outline); }
    .ce-system-text { font-size: 11px; color: var(--mat-sys-on-surface-variant); font-style: italic; }
    .collab-input-row { display: flex; align-items: center; gap: 4px; margin-top: 4px; }
    .collab-input {
      flex: 1; font-size: 13px; padding: 5px 10px;
      border: 1px solid var(--mat-sys-outline-variant); border-radius: 6px;
      background: var(--mat-sys-surface-container); color: var(--mat-sys-on-surface);
      outline: none;
    }
    .collab-input:focus { border-color: var(--mat-sys-primary); }
    /* LCARS collab overrides */
    :host-context(html.cs-theme-lcars) .collab-badge { background: #1a1206; color: #FF9933; border-bottom-color: #2a1d0a; }
    :host-context(html.cs-theme-lcars) .collab-badge[data-status="investigating"] { background: rgba(255,153,51,.1); color: #FF9933; }
    :host-context(html.cs-theme-lcars) .collab-badge[data-status="resolved"] { background: rgba(102,204,102,.1); color: #66cc66; }
    :host-context(html.cs-theme-lcars) .collab-thread { border-top-color: #2a1d0a; }
    :host-context(html.cs-theme-lcars) .ce-author { color: #ffe8a0; }
    :host-context(html.cs-theme-lcars) .ce-text { color: #e8a060; }
    .ce-ai-body { flex-direction: column; align-items: flex-start; gap: 4px; }
    .ce-ai-text { white-space: pre-line; }
    .ce-evidence { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 4px; width: 100%; }
    .ce-ev-label { font-size: 10px; font-weight: 700; letter-spacing: .05em; color: var(--mat-sys-tertiary); opacity: .8; width: 100%; }
    .ce-ev-chip { display: inline-flex; align-items: center; gap: 5px; padding: 2px 8px; border-radius: 10px; font-size: 11px; background: var(--mat-sys-surface-variant); max-width: 100%; }
    .ce-ev-chip.clickable { cursor: pointer; border: 1px solid color-mix(in srgb, var(--mat-sys-tertiary) 40%, transparent); }
    .ce-ev-chip.clickable:hover { background: color-mix(in srgb, var(--mat-sys-tertiary) 14%, var(--mat-sys-surface-variant)); }
    .ce-ev-type { font-weight: 700; font-size: 9px; text-transform: uppercase; opacity: .6; flex-shrink: 0; }
    .ce-ev-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    :host-context(html.cs-theme-lcars) .ce-ev-chip { background: #1a1206; color: #e8a060; }
    :host-context(html.cs-theme-lcars) .ce-ev-label { color: #99CCFF; }
    :host-context(html.cs-theme-lcars) .ce-time { color: rgba(255,232,160,.3); }
    :host-context(html.cs-theme-lcars) .collab-input { background: #0a0804; border-color: #2a1d0a; color: #ffe8a0; }
    :host-context(html.cs-theme-lcars) .collab-status-select { border-color: #2a1d0a; color: #e8a060; }
    .diagnose-btn { color: var(--mat-sys-tertiary) !important; border-color: var(--mat-sys-tertiary) !important; }
    :host-context(html.cs-theme-lcars) .diagnose-btn { color: #99CCFF !important; border-color: #99CCFF !important; }

    /* ── Incident Badge ── */
    .incident-badge {
      display: flex; align-items: center; gap: 6px;
      padding: 4px 12px; cursor: pointer;
      background: color-mix(in srgb, var(--mat-sys-tertiary) 8%, var(--mat-sys-surface));
      border-bottom: 1px solid color-mix(in srgb, var(--mat-sys-tertiary) 20%, transparent);
      color: var(--mat-sys-tertiary); font-size: 12px; font-weight: 500;
      transition: background .15s;
    }
    .incident-badge:hover { background: color-mix(in srgb, var(--mat-sys-tertiary) 16%, var(--mat-sys-surface)); }
    .incident-badge-icon { font-size: 14px; height: 14px; width: 14px; }
    .incident-badge-arrow { font-size: 16px; height: 16px; width: 16px; margin-left: auto; }

    /* ── Incident Drawer ── */
    /* ── AI Ticket Dialog ── */
    .ticket-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 950; }
    .ticket-dialog {
      position: fixed; top: 50%; left: 50%; transform: translate(-50%,-50%);
      width: min(640px, 94vw); max-height: 90vh; z-index: 951;
      background: var(--mat-sys-surface-container); border-radius: 14px;
      display: flex; flex-direction: column; box-shadow: 0 12px 48px rgba(0,0,0,.4);
    }
    .ticket-dialog-header {
      display: flex; align-items: center; gap: 10px; padding: 14px 16px;
      border-bottom: 1px solid var(--mat-sys-outline-variant);
    }
    .ticket-dialog-header > mat-icon { color: var(--mat-sys-primary); }
    .ticket-dialog-header > span:first-of-type { font-weight: 700; font-size: 15px; }
    .ticket-ai-hint { margin-left: auto; font-size: 11px; opacity: .6; }
    .ticket-dialog-body { padding: 16px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
    .ticket-field { display: flex; flex-direction: column; gap: 4px; }
    .ticket-field > span { font-size: 12px; font-weight: 600; opacity: .8; }
    .ticket-row { display: flex; gap: 12px; }
    .ticket-input {
      font: inherit; padding: 8px 10px; border-radius: 8px;
      border: 1px solid var(--mat-sys-outline-variant);
      background: var(--mat-sys-surface); color: var(--mat-sys-on-surface);
    }
    .ticket-textarea { resize: vertical; min-height: 140px; font-family: 'Roboto', monospace; line-height: 1.4; }
    .ticket-dialog-actions {
      display: flex; justify-content: flex-end; gap: 10px; padding: 12px 16px;
      border-top: 1px solid var(--mat-sys-outline-variant);
    }
    .ticket-dialog-actions mat-spinner, .action-btn mat-spinner { display: inline-block; margin-right: 6px; }
    :host-context(html.cs-theme-lcars) .ticket-dialog { background: #0a0804; border: 1px solid #2a1d0a; }
    :host-context(html.cs-theme-lcars) .ticket-input { background: #120e06; border-color: #2a1d0a; color: #ffe8a0; }

    .incident-drawer-overlay {
      position: fixed; inset: 0; background: rgba(0,0,0,.4); z-index: 900;
    }
    .incident-drawer {
      position: fixed; top: 0; right: 0; bottom: 0; width: min(480px, 100vw);
      background: var(--mat-sys-surface-container); z-index: 901;
      display: flex; flex-direction: column; box-shadow: -4px 0 24px rgba(0,0,0,.3);
    }
    .incident-drawer-header {
      display: flex; align-items: center; gap: 10px; padding: 16px;
      background: var(--mat-sys-surface-container-high);
      border-bottom: 1px solid var(--mat-sys-outline-variant);
    }
    .incident-drawer-header > mat-icon { color: var(--mat-sys-tertiary); }
    .incident-drawer-title { display: flex; flex-direction: column; flex: 1; }
    .incident-drawer-host { font-weight: 700; font-size: 15px; }
    .incident-drawer-meta { font-size: 12px; opacity: .7; }
    .incident-drawer-close { margin-left: auto; }
    .claude-handoff-btn { font-size: 12px; line-height: 1.2; min-width: 0; padding: 0 12px; color: var(--mat-sys-tertiary); border-color: color-mix(in srgb, var(--mat-sys-tertiary) 45%, transparent); }
    .claude-handoff-btn mat-icon { font-size: 16px; height: 16px; width: 16px; margin-right: 4px; }
    .claude-handoff-btn mat-spinner { display: inline-block; margin-right: 4px; }
    :host-context(html.cs-theme-lcars) .claude-handoff-btn { color: #99CCFF; border-color: rgba(153,204,255,.45); }
    .incident-drawer-loading { display: flex; justify-content: center; padding: 40px; }

    /* ── Causal Panel ── */
    .causal-panel {
      margin: 10px 16px 4px;
      border: 1px solid color-mix(in srgb, var(--mat-sys-error) 40%, transparent);
      border-radius: 8px;
      background: color-mix(in srgb, var(--mat-sys-error) 6%, var(--mat-sys-surface));
      overflow: hidden;
    }
    .causal-panel-header {
      display: flex; align-items: center; gap: 8px;
      padding: 8px 12px;
      font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .05em;
      color: var(--mat-sys-error);
      border-bottom: 1px solid color-mix(in srgb, var(--mat-sys-error) 25%, transparent);
    }
    .causal-panel-header mat-icon { font-size: 16px; height: 16px; width: 16px; }
    .causal-entry {
      display: flex; align-items: center; gap: 10px;
      padding: 8px 12px;
      cursor: pointer;
      transition: background .15s;
    }
    .causal-entry:hover { background: color-mix(in srgb, var(--mat-sys-error) 10%, transparent); }
    .causal-icon { font-size: 18px; height: 18px; width: 18px; color: var(--mat-sys-error); opacity: .7; }
    .causal-info { display: flex; flex-direction: column; flex: 1; }
    .causal-service { font-weight: 600; font-size: 13px; }
    .causal-host { font-size: 11px; opacity: .6; }
    .causal-since { font-size: 11px; opacity: .7; white-space: nowrap; }
    .causal-arrow { font-size: 18px; height: 18px; width: 18px; opacity: .4; }
    :host-context(html.cs-theme-lcars) .causal-panel { border-color: rgba(255,153,0,.45); background: rgba(255,153,0,.06); }
    :host-context(html.cs-theme-lcars) .causal-panel-header { color: #FF9900; border-color: rgba(255,153,0,.25); }
    :host-context(html.cs-theme-lcars) .causal-icon { color: #FF9900; }
    :host-context(html.cs-theme-lcars) .causal-entry:hover { background: rgba(255,153,0,.1); }

    .incident-timeline-list { overflow-y: auto; flex: 1; padding: 8px 0; }
    .tl-entry {
      display: flex; gap: 12px; padding: 10px 16px;
      border-bottom: 1px solid var(--mat-sys-outline-variant);
    }
    .tl-icon-col { display: flex; flex-direction: column; align-items: center; }
    .tl-icon { font-size: 18px; height: 18px; width: 18px; color: var(--mat-sys-on-surface-variant); }
    .tl-entry[data-kind="alert"] .tl-icon { color: var(--mat-sys-error); }
    .tl-entry[data-kind="ai_analysis"] .tl-icon, .tl-entry[data-kind="ai"] .tl-icon { color: var(--mat-sys-tertiary); }
    .tl-entry[data-kind="claim"] .tl-icon { color: var(--mat-sys-primary); }
    .tl-content { flex: 1; display: flex; flex-direction: column; gap: 3px; }
    .tl-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 11px; opacity: .7; }
    .tl-kind { font-weight: 600; text-transform: uppercase; letter-spacing: .5px; }
    .tl-time { margin-left: auto; font-family: monospace; }
    .tl-sev { font-size: 10px; font-weight: 700; padding: 1px 5px; border-radius: 3px; align-self: flex-start; }
    .tl-sev[data-sev="critical"] { background: #ffebee; color: #b71c1c; }
    .tl-sev[data-sev="high"]     { background: #fff3e0; color: #e65100; }
    .tl-sev[data-sev="medium"]   { background: #fffde7; color: #f57f17; }
    .tl-text { font-size: 13px; line-height: 1.4; word-break: break-word; }
    :host-context(html.cs-theme-lcars) .incident-badge { background: rgba(153,204,255,.08); border-bottom-color: rgba(153,204,255,.2); color: #99CCFF; }
    :host-context(html.cs-theme-lcars) .incident-drawer { background: #0a0804; }
    :host-context(html.cs-theme-lcars) .incident-drawer-header { background: #120e06; border-bottom-color: #2a1d0a; }
    :host-context(html.cs-theme-lcars) .tl-entry { border-bottom-color: #1a1206; }
    :host-context(html.cs-theme-lcars) .tl-text { color: #e8a060; }
  `],
})
export class NewsFeedComponent implements OnInit, AfterViewInit, OnDestroy {
  readonly allSources = Object.entries(SOURCE_META).map(([id, m]) => ({ id, ...m }));

  @ViewChild('scrollSentinel') private sentinelRef!: ElementRef<HTMLElement>;
  private observer?: IntersectionObserver;
  private app = inject(App);
  private ws = inject(WebsocketService);
  private badgeCleared = false;

  items = signal<FeedItem[]>([]);
  lastSeenAt = signal<Date>(new Date(0));
  loading = signal(false);
  loadingMore = signal(false);

  // ── Cross-source search (all cs-feed-* indices, with time window) ─────
  searchQuery = '';
  searchRangeSec = 900;                        // default: 15 minutes
  searching = signal(false);
  searchActive = signal(false);                // true → feed shows search results
  activeSearchQuery = signal('');              // the query currently displayed
  readonly searchRanges = [                    // Graylog default relative-time presets
    { label: '5 minutes', sec: 300 },   { label: '15 minutes', sec: 900 },
    { label: '30 minutes', sec: 1800 }, { label: '1 hour', sec: 3600 },
    { label: '2 hours', sec: 7200 },    { label: '8 hours', sec: 28800 },
    { label: '1 day', sec: 86400 },     { label: '2 days', sec: 172800 },
    { label: '5 days', sec: 432000 },   { label: '7 days', sec: 604800 },
    { label: '14 days', sec: 1209600 }, { label: '30 days', sec: 2592000 },
  ];
  showSettings = signal(false);
  showFilters = signal(false);
  showSearchManager = signal(false);
  activeFilter = signal<string[]>([]);
  autoEnrich = signal<boolean>(true);
  enrichingIds = signal<Set<string>>(new Set());
  ignoringIds = signal<Set<string>>(new Set());
  feedSearches = signal<FeedSearch[]>([]);
  activeSearch = signal<FeedSearch | null>(null);
  // Exclusion searches are background filters — don't show them as clickable search tiles
  systemSearches = computed(() => this.feedSearches().filter(s => s.is_system && !s.is_exclusion));
  generatingSearch = signal(false);
  aiSearchPrompt = '';
  searchDraft: { id?: string; name: string; index_pattern: string; query_string: string; enabled: boolean; is_exclusion: boolean } = {
    name: '',
    index_pattern: 'cs-feed-graylog',
    query_string: '',
    enabled: true,
    is_exclusion: false,
  };
  expanded = new Set<string>();

  hostFilter = '';
  severityFilter = '';
  osFilter = '';
  locationFilter = '';
  criticalityFilter = '';
  veFilter = '';
  hostgroupFilter = '';

  filterValues: { os: string[]; location: string[]; criticality: string[]; ve: string[]; hostgroups: string[] } = {
    os: [], location: [], criticality: [], ve: [], hostgroups: [],
  };

  editPrefs: FeedPrefs = { checkmk_min_age_minutes: 5, teams_channels: [] };
  showScrollTop = signal(false);
  private offset = 0;
  private readonly pageSize = 50;
  private hasMore = false;
  private refreshTimer?: ReturnType<typeof setInterval>;
  private routeSearchId = '';
  private routeQuery = '';
  private routeIndex = '';
  private routeSourceSet = false;
  private highlightId = '';
  private hasTriedScrollToLastSeen = false;
  private scrollContainer: HTMLElement | null = null;
  private scrollListener = () => {
    this.showScrollTop.set((this.scrollContainer?.scrollTop ?? 0) > 350);
  };

  visibleItems = computed(() => {
    const f = this.activeFilter();
    if (f.length === 0) return this.items();
    return this.items().filter(i => f.includes(i.source));
  });

  private computerService = inject(ComputerService);
  private dialog = inject(MatDialog);

  constructor(
    private http: HttpClient,
    private snackBar: MatSnackBar,
    private route: ActivatedRoute,
    private router: Router,
  ) {}

  scrollToTop() {
    this.scrollContainer?.scrollTo({ top: 0, behavior: 'smooth' });
  }

  private scrollToLastSeen() {
    if (this.hasTriedScrollToLastSeen) return;
    this.hasTriedScrollToLastSeen = true;
    const attempt = (remaining: number) => {
      const divider = document.querySelector('.last-seen-divider') as HTMLElement;
      if (divider && this.scrollContainer) {
        const offset = divider.getBoundingClientRect().top
          - this.scrollContainer.getBoundingClientRect().top
          + this.scrollContainer.scrollTop
          - 80;
        this.scrollContainer.scrollTo({ top: offset, behavior: 'smooth' });
      } else if (!divider && remaining > 0) {
        setTimeout(() => attempt(remaining - 1), 200);
      }
    };
    setTimeout(() => attempt(10), 50);
  }

  ngOnInit() {
    const stored = localStorage.getItem('feed_last_seen');
    if (stored) this.lastSeenAt.set(new Date(stored));
    this.applyRouteParams();
    this.loadPrefs();
    this.loadAutoEnrichSetting();
    this.loadSearches();
    this.initWs();
    this.refreshTimer = setInterval(() => this.load(true, true), 30_000);

    // Deferred re-assert: if hostFilter or severityFilter was set by applyRouteParams(),
    // ensure the filter panel remains open after async initialisation (loadPrefs etc.).
    queueMicrotask(() => {
      if (this.hostFilter || this.severityFilter) this.showFilters.set(true);
    });

    // React to query-param changes when already on /feed (same-route navigation).
    // skip(1) ignores the initial emission already handled by applyRouteParams().
    this.route.queryParamMap.pipe(skip(1)).subscribe(params => {
      this.severityFilter = params.get('severity') ?? '';
      this.hostFilter     = params.get('host')     ?? '';
      const highlight     = params.get('highlight');
      if (highlight) this.highlightId = highlight;
      const source        = params.get('source');
      if (source) {
        this.routeSourceSet = true;
        this.activeFilter.set(source.split(',').filter(Boolean));
      }
      if (this.severityFilter || this.hostFilter) this.showFilters.set(true);
      this.load(true);
    });
  }

  ngAfterViewInit() {
    this.observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && this.hasMore && !this.loadingMore() && !this.loading()) {
          this.loadMore();
        }
      },
      { threshold: 0.1 }
    );
    this.observer.observe(this.sentinelRef.nativeElement);

    this.scrollContainer = document.querySelector('mat-sidenav-content');
    this.scrollContainer?.addEventListener('scroll', this.scrollListener);
  }

  ngOnDestroy() {
    if (this.refreshTimer) clearInterval(this.refreshTimer);
    this.observer?.disconnect();
    this.scrollContainer?.removeEventListener('scroll', this.scrollListener);
    localStorage.setItem('feed_last_seen', new Date().toISOString());
    this.wsSub?.unsubscribe();
  }

  private wsSub?: import('rxjs').Subscription;
  private initWs() {
    this.wsSub = this.ws.messages().subscribe((msg: any) => {
      if (msg?.type !== 'feed_collab') return;
      const { external_id, kind, user_name, work_status, body } = msg;
      // Update collab state on the matching feed item in-place
      this.items.update(items => items.map(item => {
        if (item.external_id !== external_id) return item;
        const old = item.collab ?? { claimed_by_name: null, claimed_at: null, work_status: 'new', comment_count: 0 };
        const newCount = kind === 'comment' ? (old.comment_count ?? 0) + 1 : (old.comment_count ?? 0);
        return {
          ...item,
          collab: {
            ...old,
            work_status: work_status ?? old.work_status,
            comment_count: newCount,
            claimed_by_name: kind === 'claim' ? user_name : (kind === 'release' ? null : old.claimed_by_name),
            claimed_at: kind === 'claim' ? new Date().toISOString() : (kind === 'release' ? null : old.claimed_at),
          },
        };
      }));
      // Refresh open timeline if visible
      const openItem = this.items().find(i => i.external_id === external_id && this.showComments.has(i.id));
      if (openItem) this.loadTimeline(openItem);
    });
  }

  loadPrefs() {
    const allIds = this.allSources.map(s => s.id);
    this.http.get<any>(`${environment.apiUrl}/preferences`).subscribe({
      next: (p) => {
        this.editPrefs = {
          checkmk_min_age_minutes: p.feed_checkmk_min_age_minutes ?? 5,
          teams_channels: p.feed_teams_channels ?? [],
        };
        const enabled: string[] = p.feed_sources_enabled ?? [];
        if (!this.routeSourceSet) {
          this.activeFilter.set(enabled.length > 0 ? enabled : allIds);
        }
        this.load(true);
      },
      error: () => {
        if (!this.routeSourceSet) {
          this.activeFilter.set(allIds);
        }
        this.load(true);
      },
    });
  }

  applyRouteParams() {
    const params = this.route.snapshot.queryParamMap;
    const source = params.get('source');
    this.routeSearchId = params.get('search_id') || '';
    this.routeQuery = params.get('q') || '';
    this.routeIndex = params.get('index') || '';
    this.highlightId = params.get('highlight') || '';
    if (source) {
      this.routeSourceSet = true;
      this.activeFilter.set(source.split(',').filter(Boolean));
    }
    const severity = params.get('severity');
    if (severity) { this.severityFilter = severity; this.showFilters.set(true); }
    const host = params.get('host');
    if (host) { this.hostFilter = host; this.showFilters.set(true); }
    const incidentId = params.get('incident');
    if (incidentId) { this.openIncidentTimeline(incidentId); }
  }

  private scrollToHighlight() {
    const id = this.highlightId;
    if (!id) return;
    const attempt = (remaining: number) => {
      const el = document.querySelector(`[data-feed-id="${id}"]`);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.classList.add('feed-highlight');
        setTimeout(() => el.classList.remove('feed-highlight'), 2800);
      } else if (remaining > 0) {
        setTimeout(() => attempt(remaining - 1), 150);
      }
    };
    setTimeout(() => attempt(10), 150);
  }

  hasActiveFilter(): boolean {
    return !!(this.hostFilter || this.severityFilter || this.osFilter ||
              this.locationFilter || this.criticalityFilter || this.veFilter || this.hostgroupFilter);
  }

  toggleFilters() {
    const next = !this.showFilters();
    this.showFilters.set(next);
    if (next && this.filterValues.os.length === 0) {
      this.loadFilterValues();
    }
  }

  toggleSearchManager() {
    this.showSearchManager.update(v => !v);
  }

  personalSearches(): FeedSearch[] {
    return this.feedSearches().filter(s => !s.is_system);
  }

  loadSearches() {
    this.http.get<FeedSearch[]>(`${environment.apiUrl}/feed-searches/`).subscribe({
      next: searches => {
        this.feedSearches.set(searches);
        if (this.routeSearchId) {
          const found = searches.find(s => s.id === this.routeSearchId);
          if (found) this.activeSearch.set(found);
        }
      },
    });
  }

  clearSearch() {
    this.activeSearch.set(null);
    this.routeSearchId = '';
    this.routeQuery = '';
    this.routeIndex = '';
    this.router.navigate(['/feed'], { queryParams: {} });
    this.load(true);
  }

  resetSearchDraft() {
    this.searchDraft = {
      name: '',
      index_pattern: 'cs-feed-graylog',
      query_string: '',
      enabled: true,
      is_exclusion: false,
    };
    this.aiSearchPrompt = '';
  }

  generateSearchWithAi() {
    const prompt = this.aiSearchPrompt.trim();
    if (!prompt) return;
    this.generatingSearch.set(true);
    this.http.post<{ reply: string; index_pattern: string; query_string: string }>(
      `${environment.apiUrl}/ai/search-assistant`,
      {
        message: prompt,
        context: 'generate an OpenSearch Lucene query for a user-configurable feed search; prefer cs-feed-graylog or cs-feed-wazuh when mentioned',
      },
    ).subscribe({
      next: result => {
        this.searchDraft = {
          ...this.searchDraft,
          name: this.searchDraft.name || prompt.slice(0, 80),
          index_pattern: result.index_pattern || 'cs-feed-*',
          query_string: result.query_string || '',
        };
        this.generatingSearch.set(false);
        this.snackBar.open(result.reply || 'Query generiert', '', { duration: 2500 });
      },
      error: err => {
        this.generatingSearch.set(false);
        this.snackBar.open(err?.error?.detail ?? 'KI konnte keine Query erzeugen', 'OK', { duration: 3500 });
      },
    });
  }

  saveSearchDraft() {
    const payload = {
      name: this.searchDraft.name.trim(),
      index_pattern: this.searchDraft.index_pattern,
      query_string: this.searchDraft.query_string,
      enabled: this.searchDraft.enabled,
      is_exclusion: this.searchDraft.is_exclusion,
    };
    const request = this.searchDraft.id
      ? this.http.patch<FeedSearch>(`${environment.apiUrl}/feed-searches/${this.searchDraft.id}`, payload)
      : this.http.post<FeedSearch>(`${environment.apiUrl}/feed-searches/`, payload);
    request.subscribe({
      next: saved => {
        this.feedSearches.update(searches => {
          const idx = searches.findIndex(s => s.id === saved.id);
          if (idx < 0) return [...searches, saved];
          const next = [...searches];
          next[idx] = saved;
          return next;
        });
        this.resetSearchDraft();
        this.snackBar.open('OpenSearch-Suche gespeichert', '', { duration: 2500 });
      },
      error: err => this.snackBar.open(err?.error?.detail ?? 'Suche konnte nicht gespeichert werden', 'OK', { duration: 3500 }),
    });
  }

  editSavedSearch(search: FeedSearch) {
    this.searchDraft = {
      id: search.id,
      name: search.name,
      index_pattern: search.index_pattern,
      query_string: search.query_string,
      enabled: search.enabled,
      is_exclusion: search.is_exclusion,
    };
  }

  deleteSavedSearch(search: FeedSearch) {
    this.http.delete(`${environment.apiUrl}/feed-searches/${search.id}`).subscribe({
      next: () => {
        this.feedSearches.update(searches => searches.filter(s => s.id !== search.id));
        this.snackBar.open('Suche gelöscht', '', { duration: 2000 });
      },
      error: err => this.snackBar.open(err?.error?.detail ?? 'Suche konnte nicht gelöscht werden', 'OK', { duration: 3500 }),
    });
  }

  applySavedSearch(search: FeedSearch) {
    this.activeSearch.set(search);
    this.routeSearchId = search.id;
    this.routeQuery = '';
    this.routeIndex = '';
    this.router.navigate(['/feed'], { queryParams: { search_id: search.id } });
    this.load(true);
  }

  loadFilterValues() {
    this.http.get<any>(`${environment.apiUrl}/feed/checkmk-filter-values`).subscribe({
      next: (v) => { this.filterValues = v; },
    });
  }

  onFilterChange() {
    // A manual dropdown filter overrides any active saved-search/q= query.
    // Clearing routeQuery prevents the backend from taking the search_by_query()
    // path which ignores severity/host/OS filters entirely.
    this.routeQuery = '';
    this.routeSearchId = '';
    this.routeIndex = '';
    this.load(true);
  }

  clearFilters() {
    this.hostFilter = '';
    this.severityFilter = '';
    this.osFilter = '';
    this.locationFilter = '';
    this.criticalityFilter = '';
    this.veFilter = '';
    this.hostgroupFilter = '';
    this.load(true);
  }

  load(reset = false, silent = false) {
    // While a cross-source search is shown, ignore background/auto refreshes so
    // they don't overwrite the search results. clearFeedSearch() resets first.
    if (this.searchActive() && silent) return;
    if (reset) {
      this.offset = 0;
      if (!silent) this.loading.set(true);
    } else {
      this.loadingMore.set(true);
    }
    const params: Record<string, any> = { limit: this.pageSize, offset: this.offset };
    const activeSources = this.activeFilter();
    params['sources'] = (activeSources.length > 0 ? activeSources : this.allSources.map(s => s.id)).join(',');
    if (this.severityFilter)    params['severity']    = this.severityFilter;
    if (this.hostFilter)        params['host']        = this.hostFilter;
    if (this.osFilter)          params['os']          = this.osFilter;
    if (this.locationFilter)    params['location']    = this.locationFilter;
    if (this.criticalityFilter) params['criticality'] = this.criticalityFilter;
    if (this.veFilter)          params['ve']          = this.veFilter;
    if (this.hostgroupFilter)   params['hostgroup']   = this.hostgroupFilter;
    if (this.routeSearchId)     params['search_id']    = this.routeSearchId;
    if (this.routeQuery)        params['q']            = this.routeQuery;
    if (this.routeIndex)        params['index']        = this.routeIndex;
    if (this.highlightId)       params['highlight_id'] = this.highlightId;
    this.http.get<FeedItem[]>(`${environment.apiUrl}/feed/`, { params }).subscribe({
      next: (data) => {
        // Discard regular-feed responses that arrived after a text search became active.
        // loadMore() fires load(false) without a guard, so this races with runFeedSearch().
        if (this.searchActive()) {
          this.loading.set(false);
          this.loadingMore.set(false);
          return;
        }
        if (reset) {
          if (silent) {
            // Silent auto-refresh: only prepend genuinely new items to avoid scroll-jump
            const existingIds = new Set(this.items().map(i => i.id));
            const newItems = data.filter(i => !existingIds.has(i.id));
            if (newItems.length > 0) {
              this.items.update(prev => [...newItems, ...prev]);
            }
          } else {
            this.items.set(data);
            if (!this.badgeCleared) {
              this.badgeCleared = true;
              setTimeout(() => this.app.clearFeedBadge(), 3000);
            }
            const hadHighlight = !!this.highlightId;
            this.scrollToHighlight();
            this.highlightId = '';
            if (!hadHighlight) this.scrollToLastSeen();
          }
        } else {
          this.items.update(prev => [...prev, ...data]);
        }
        this.hasMore = data.length === this.pageSize;
        this.loading.set(false);
        this.loadingMore.set(false);
      },
      error: () => {
        this.hasMore = false;
        this.loading.set(false);
        this.loadingMore.set(false);
      },
    });
  }

  loadMore() {
    this.offset += this.pageSize;
    this.load(false);
  }

  savePrefs() {
    this.http.patch(`${environment.apiUrl}/preferences`, {
      feed_checkmk_min_age_minutes: this.editPrefs.checkmk_min_age_minutes,
      feed_teams_channels: this.editPrefs.teams_channels,
    }).subscribe({
      next: () => {
        this.snackBar.open('Einstellungen gespeichert', '', { duration: 2000 });
        this.showSettings.set(false);
        this.load(true);
      },
      error: () => this.snackBar.open('Fehler beim Speichern', '', { duration: 3000 }),
    });
  }

  loadAutoEnrichSetting() {
    this.http.get<{ settings: Array<{ key: string; value: string }> }>(`${environment.apiUrl}/settings/`).subscribe({
      next: (res) => {
        const s = res.settings.find(x => x.key === 'agent.auto_enrich');
        this.autoEnrich.set(s ? s.value !== 'false' : true);
      },
    });
  }

  requestEnrich(item: FeedItem) {
    const ids = new Set(this.enrichingIds());
    ids.add(item.id);
    this.enrichingIds.set(ids);

    this.http.post<{ ai_insight: string }>(`${environment.apiUrl}/feed/${item.id}/enrich`, {}).subscribe({
      next: (res) => {
        this.items.update(prev => prev.map(i =>
          i.id === item.id ? { ...i, ai_insight: res.ai_insight } : i
        ));
        const next = new Set(this.enrichingIds());
        next.delete(item.id);
        this.enrichingIds.set(next);
      },
      error: (err) => {
        const next = new Set(this.enrichingIds());
        next.delete(item.id);
        this.enrichingIds.set(next);
        this.snackBar.open(err?.error?.detail ?? 'KI-Anreicherung fehlgeschlagen', 'OK', { duration: 4000 });
      },
    });
  }

  isEnriching(id: string): boolean { return this.enrichingIds().has(id); }

  ignoreItem(item: FeedItem) {
    const ids = new Set(this.ignoringIds());
    ids.add(item.id);
    this.ignoringIds.set(ids);

    this.http.post<{ name: string; query_string: string }>(
      `${environment.apiUrl}/feed/${item.id}/ignore`, {}
    ).subscribe({
      next: (res) => {
        const next = new Set(this.ignoringIds());
        next.delete(item.id);
        this.ignoringIds.set(next);
        // Reload the full list so all items matching the new exclusion disappear at once
        this.load(true);
        this.snackBar.open(`Ignoriert: „${res.name}" — ähnliche Meldungen werden ausgeblendet`, 'OK', { duration: 5000 });
      },
      error: (err) => {
        const next = new Set(this.ignoringIds());
        next.delete(item.id);
        this.ignoringIds.set(next);
        this.snackBar.open(err?.error?.detail ?? 'Fehler beim Erstellen des Filters', 'OK', { duration: 4000 });
      },
    });
  }

  isIgnoring(id: string): boolean { return this.ignoringIds().has(id); }

  isFirstSeen(item: FeedItem, index: number): boolean {
    const ls = this.lastSeenAt();
    // No divider if never visited before or item is still new
    if (ls.getTime() === 0) return false;
    if (new Date(item.created_at) > ls) return false;
    // Only show divider if there is at least one newer item directly above
    const visible = this.visibleItems();
    const prev = visible[index - 1];
    return !prev || new Date(prev.created_at) > ls;
  }

  onSourceChipChange(selected: string[] | null) {
    const next = selected ?? this.allSources.map(s => s.id);
    this.activeFilter.set(next);
    this.http.patch(`${environment.apiUrl}/preferences`, {
      feed_sources_enabled: next,
    }).subscribe();
    this.load(true);
  }

  toggleExpand(id: string) {
    if (this.expanded.has(id)) {
      this.expanded.delete(id);
    } else {
      this.expanded.add(id);
    }
  }

  acknowledge(item: FeedItem) {
    this.http.post(`${environment.apiUrl}/feed/${item.id}/acknowledge`, {}).subscribe({
      next: () => {
        this.items.update(prev => prev.map(i =>
          i.id === item.id ? { ...i, status: 'acknowledged' as const } : i
        ));
        this.snackBar.open('Bestätigt', '', { duration: 2000 });
      },
      error: (err) => this.snackBar.open(err?.error?.detail ?? 'Fehler', '', { duration: 3000 }),
    });
  }

  // ── AI-assisted ticket creation ───────────────────────────────────────────
  creatingTicketIds = new Set<string>();
  isCreatingTicket(id: string) { return this.creatingTicketIds.has(id); }
  /** Open the shared ticket dialog (Jira/Service-Desk choice + AI draft) for a feed item. */
  createTicket(item: FeedItem) {
    if (!item.external_id) return;
    this.dialog.open(TicketCreateDialogComponent, {
      data: {
        mode: 'feed',
        feedExternalId: item.external_id,
        host: item.host || item.metadata?.['host'] || '',
        severity: item.severity,
      },
      panelClass: 'tkt-panel',
      autoFocus: false,
      maxWidth: '92vw',
    });
  }

  openUrl(url: string) {
    window.open(url, '_blank', 'noopener');
  }

  relTime(iso: string): string {
    if (!iso) return '';
    const d = new Date(iso);
    const diff = Date.now() - d.getTime();
    const mins = Math.floor(diff / 60_000);
    if (mins < 1)  return 'gerade eben';
    if (mins < 60) return `vor ${mins} Min.`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24)  return `vor ${hrs} Std.`;
    return d.toLocaleString('de-DE', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  }

  absTime(iso: string): string {
    if (!iso) return '';
    return new Date(iso).toLocaleString('de-DE', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  }

  sourceIcon(src: string)  { return SOURCE_META[src]?.icon  ?? 'info'; }
  sourceLabel(src: string) { return SOURCE_META[src]?.label ?? src; }
  sourceColor(src: string) { return SOURCE_META[src]?.color ?? '#757575'; }

  filterByHost(event: MouseEvent, host: string) {
    event.stopPropagation();
    if (!host) return;
    this.hostFilter = host;
    this.showFilters.set(true);
    this.router.navigate(['/feed'], { queryParams: { host } });
  }

  openHostCockpit(event: MouseEvent, host: string) {
    event.stopPropagation();
    if (!host) return;
    window.open(
      '/cockpit/' + encodeURIComponent(host),
      'cockpit-' + host,
      'width=1300,height=820,menubar=no,toolbar=no,location=no,status=no',
    );
  }
  severityColor(sev: string) { return SEVERITY_COLOR[sev] ?? '#757575'; }

  // ── Cross-source search (all cs-feed-* indices, with time window) ─────
  searchRangeLabel(): string {
    return this.searchRanges.find(r => r.sec === this.searchRangeSec)?.label ?? '';
  }

  runFeedSearch() {
    const q = (this.searchQuery || '').trim();
    if (!q) { this.clearFeedSearch(); return; }
    this.searching.set(true);
    this.searchActive.set(true);
    this.activeSearchQuery.set(q);
    const params: any = { q, time_range_seconds: this.searchRangeSec, limit: 200 };
    this.http.get<FeedItem[]>(`${environment.apiUrl}/feed/`, { params }).subscribe({
      next: data => { this.items.set(data); this.searching.set(false); },
      error: () => {
        this.searching.set(false);
        this.items.set([]);
        this.snackBar.open('Suche fehlgeschlagen', 'OK', { duration: 5000 });
      },
    });
  }

  clearFeedSearch() {
    this.searchActive.set(false);
    this.activeSearchQuery.set('');
    this.searchQuery = '';
    this.load(true);
  }

  itemHostLabel(item: FeedItem): string {
    const m = item.metadata;
    if (!m) return '';
    if (item.source === 'graylog') {
      const container = (m['container_name'] as string) || '';
      const host = (m['host'] as string) || '';
      const vendor = (m['vendor'] as string) || '';
      const showVendor = vendor && vendor !== 'Unknown';
      let label = '';
      if (container && host && host !== container) {
        label = `${host}/${container}`;
      } else {
        label = container || host;
      }
      return label && showVendor ? `${label} · ${vendor}` : label;
    }
    if (item.source === 'wazuh') {
      return (m['agent'] as string) || (m['host'] as string) || '';
    }
    if (item.source === 'checkmk') {
      return (m['host'] as string) || (item.host as string) || '';
    }
    return '';
  }
  typeLabel(type: string): string {
    const m: Record<string, string> = {
      alert: 'Monitoring Alert',
      email: 'E-Mail',
      teams_message: 'Teams Nachricht',
    };
    return m[type] ?? type;
  }

  // ── AI evidence parsing (Beweispflicht) ───────────────────────────────────
  // The diagnose endpoint appends a "📎 Belege:" block to the AI comment body.
  // We split it into the human summary and structured, clickable evidence chips.

  aiBodyMain(body: string): string {
    const idx = body.indexOf('📎 Belege:');
    return (idx >= 0 ? body.slice(0, idx) : body).trim();
  }

  aiBodyEvidence(body: string): { raw: string; type: string; ref: string; text: string; clickable: boolean }[] {
    const idx = body.indexOf('📎 Belege:');
    if (idx < 0) return [];
    const block = body.slice(idx + '📎 Belege:'.length);
    const out: { raw: string; type: string; ref: string; text: string; clickable: boolean }[] = [];
    for (const line of block.split('\n')) {
      const m = line.match(/^•\s*\[([^\]]+)\]\s*([^—]*?)\s*—\s*(.*)$/);
      if (!m) continue;
      const type = m[1].trim();
      const ref = m[2].trim();
      const text = m[3].trim();
      // log_line refs are OpenSearch doc ids → jump to that feed item
      const clickable = type === 'log_line' && /^[0-9a-f-]{8,}$/i.test(ref);
      out.push({ raw: line, type, ref, text, clickable });
    }
    return out;
  }

  jumpToEvidence(ref: string) {
    this.router.navigate(['/feed'], { queryParams: { highlight: ref } });
  }

  // ── Collaboration ────────────────────────────────────────────────────────

  showComments = new Set<string>();
  collabTimeline = new Map<string, CollabTimeline[]>();
  diagnosingIds = new Set<string>();
  isDiagnosing(id: string) { return this.diagnosingIds.has(id); }

  collabStatusLabel(status?: string): string {
    const labels: Record<string, string> = {
      new: 'Neu', investigating: 'In Bearbeitung', resolved: 'Gelöst',
    };
    return labels[status ?? 'new'] ?? 'Neu';
  }

  isMyAlert(item: FeedItem): boolean {
    const name = this.auth.user()?.full_name || this.auth.user()?.email;
    return !!name && item.collab?.claimed_by_name === name;
  }

  claimAlert(item: FeedItem) {
    if (!item.external_id) return;
    this.http.post(`${environment.apiUrl}/feed/${item.external_id}/claim`, {}).subscribe({
      next: (res: any) => {
        this.items.update(items => items.map(i =>
          i.id === item.id
            ? { ...i, collab: { ...i.collab, claimed_by_name: res.claimed_by_name, work_status: res.work_status, comment_count: i.collab?.comment_count ?? 0, claimed_at: new Date().toISOString() } }
            : i
        ));
        this.loadTimeline(item);
      },
      error: err => this.snackBar.open(err?.error?.detail ?? 'Fehler beim Übernehmen', 'OK', { duration: 3000 }),
    });
  }

  releaseAlert(item: FeedItem) {
    if (!item.external_id) return;
    this.http.post(`${environment.apiUrl}/feed/${item.external_id}/release`, {}).subscribe({
      next: () => {
        this.items.update(items => items.map(i =>
          i.id === item.id
            ? { ...i, collab: { claimed_by_name: null, claimed_at: null, work_status: 'new', comment_count: i.collab?.comment_count ?? 0 } }
            : i
        ));
        this.loadTimeline(item);
      },
    });
  }

  setAlertStatus(item: FeedItem, status: string) {
    if (!item.external_id) return;
    this.http.patch(`${environment.apiUrl}/feed/${item.external_id}/status`, { status }).subscribe({
      next: (res: any) => {
        this.items.update(items => items.map(i =>
          i.id === item.id ? { ...i, collab: { ...i.collab!, work_status: res.work_status } } : i
        ));
        this.loadTimeline(item);
      },
    });
  }

  toggleComments(itemId: string) {
    const item = this.items().find(i => i.id === itemId);
    if (!item) return;
    if (this.showComments.has(itemId)) {
      this.showComments.delete(itemId);
      this.showComments = new Set(this.showComments);
    } else {
      this.showComments.add(itemId);
      this.showComments = new Set(this.showComments);
      if (!this.collabTimeline.has(itemId)) this.loadTimeline(item);
    }
  }

  loadTimeline(item: FeedItem) {
    if (!item.external_id) return;
    this.http.get<any>(`${environment.apiUrl}/feed/${item.external_id}/collab`).subscribe({
      next: data => {
        this.collabTimeline.set(item.id, data.timeline ?? []);
        this.collabTimeline = new Map(this.collabTimeline);
      },
    });
  }

  postComment(item: FeedItem, text: string) {
    const trimmed = text.trim();
    if (!trimmed || !item.external_id) return;
    this.http.post(`${environment.apiUrl}/feed/${item.external_id}/comments`, { body: trimmed }).subscribe({
      next: () => {
        this.loadTimeline(item);
        this.items.update(items => items.map(i =>
          i.id === item.id ? { ...i, collab: { ...i.collab!, comment_count: (i.collab?.comment_count ?? 0) + 1 } } : i
        ));
      },
    });
  }

  postCommentFromInput(item: FeedItem, inputId: string) {
    const el = document.getElementById(inputId) as HTMLInputElement | null;
    if (!el) return;
    this.postComment(item, el.value);
    el.value = '';
  }

  diagnoseAlert(item: FeedItem) {
    if (!item.external_id || this.diagnosingIds.has(item.id)) return;
    this.diagnosingIds.add(item.id);
    this.diagnosingIds = new Set(this.diagnosingIds);

    const eid = encodeURIComponent(item.external_id ?? '');
    this.http.get<{ prompt: string; host: string }>(
      `${environment.apiUrl}/feed/hermes-context?external_id=${eid}`
    ).subscribe({
      next: data => {
        this.diagnosingIds.delete(item.id);
        this.diagnosingIds = new Set(this.diagnosingIds);
        this.computerService.openWithContext(data.prompt, data.host, data.host, item.external_id ?? undefined);
      },
      error: err => {
        this.diagnosingIds.delete(item.id);
        this.diagnosingIds = new Set(this.diagnosingIds);
        this.snackBar.open(err?.error?.detail ?? 'Diagnose fehlgeschlagen', 'OK', { duration: 4000 });
      },
    });
  }

  // ── Incident Timeline Drawer ──────────────────────────────────────────────
  incidentTimeline = signal<IncidentTimeline | null>(null);
  incidentDrawerOpen = signal(false);
  loadingIncident = signal(false);
  copyingPrompt = signal(false);
  promptCopied = signal(false);

  handoffToComputer(incidentId: string) {
    const incident = this.incidentTimeline()?.incident;
    const label = incident?.primary_host || 'Incident-Analyse';

    this.copyingPrompt.set(true);
    this.promptCopied.set(false);
    this.http.get<{ prompt: string }>(`${environment.apiUrl}/feed/incidents/${incidentId}/claude-prompt`)
      .subscribe({
        next: data => {
          this.copyingPrompt.set(false);
          this.promptCopied.set(true);
          setTimeout(() => this.promptCopied.set(false), 2000);
          this.computerService.openWithContext(data.prompt, label, incident?.primary_host);
        },
        error: () => {
          this.copyingPrompt.set(false);
          this.snackBar.open('Kontext konnte nicht geladen werden', 'OK', { duration: 3000 });
        },
      });
  }

  openIncidentTimeline(incidentId: string) {
    this.incidentDrawerOpen.set(true);
    this.loadingIncident.set(true);
    this.http.get<IncidentTimeline>(`${environment.apiUrl}/feed/incidents/${incidentId}/timeline`)
      .subscribe({
        next: data => {
          this.incidentTimeline.set(data);
          this.loadingIncident.set(false);
        },
        error: () => {
          this.loadingIncident.set(false);
          this.snackBar.open('Timeline konnte nicht geladen werden', 'OK', { duration: 3000 });
        },
      });
  }

  closeIncidentDrawer() {
    this.incidentDrawerOpen.set(false);
    this.incidentTimeline.set(null);
  }

  timelineKindIcon(kind: string): string {
    const icons: Record<string, string> = {
      alert: 'warning', ai_analysis: 'psychology', claim: 'person',
      release: 'person_off', status: 'swap_horiz', comment: 'comment',
      ai: 'computer',
    };
    return icons[kind] ?? 'circle';
  }

  timelineKindLabel(kind: string): string {
    const labels: Record<string, string> = {
      alert: 'Alert', ai_analysis: 'KI-Analyse', claim: 'Übernommen',
      release: 'Freigegeben', status: 'Status', comment: 'Kommentar', ai: 'KI-Diagnose',
    };
    return labels[kind] ?? kind;
  }

  private authSvc = inject(AuthService);
  get auth() { return this.authSvc; }
}
