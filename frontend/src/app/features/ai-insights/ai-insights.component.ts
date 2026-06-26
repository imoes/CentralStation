import { Component, OnInit, OnDestroy, inject, signal } from '@angular/core';
import { I18nService } from '../../core/services/i18n.service';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { ThemeService } from '../../core/services/theme.service';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatChipsModule } from '@angular/material/chips';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatDividerModule } from '@angular/material/divider';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { ActivatedRoute } from '@angular/router';
import { Subject, takeUntil } from 'rxjs';
import { WebsocketService, WsMessage } from '../../core/services/websocket.service';
import { environment } from '../../../environments/environment';

const SOURCE_LABELS: Record<string, string> = {
  checkmk: 'CheckMK', graylog: 'Graylog', wazuh: 'Wazuh', o365: 'E-Mail', teams: 'Teams',
};
const SOURCE_COLORS: Record<string, string> = {
  checkmk: '#FF9933', graylog: '#ffcc66', wazuh: '#99CCFF',
  o365: '#FFCC99', teams: '#FFCC99',
};

const SEVERITY_COLORS: Record<string, string> = {
  critical: '#d32f2f', high: '#f57c00', medium: '#1976d2', low: '#388e3c', info: '#607d8b', none: '#9e9e9e',
};

@Component({
  selector: 'cs-ai-insights',
  standalone: true,
  imports: [
    CommonModule,
    MatCardModule, MatButtonModule, MatIconModule,
    MatChipsModule, MatProgressSpinnerModule,
    MatExpansionModule, MatDividerModule, MatSnackBarModule, MatTooltipModule,
  ],
  template: `
    <div class="page-container">
      <div class="page-header">
        <h2>{{ i18n.t('ai_insights.title') }}</h2>
        <button mat-raised-button color="primary" [disabled]="triggering()" (click)="trigger()">
          @if (triggering()) { <mat-spinner diameter="18"></mat-spinner> }
          @else { <mat-icon>play_arrow</mat-icon> }
          {{ i18n.t('ai_insights.run_agent') }}
        </button>
      </div>

      @if (loading()) {
        <div class="spinner-center"><mat-spinner diameter="40"></mat-spinner></div>
      } @else {
        @for (analysis of analyses(); track analysis.id) {
          <mat-card class="analysis-card" [class.highlighted]="analysis.id === highlightId()"
                    [attr.data-analysis-id]="analysis.id"
                    [attr.data-severity]="analysis.severity_summary">

            <!-- LCARS header: plain div, no Material component interference -->
            <div class="analysis-lcars-header">
              <span class="alh-sev">{{ (analysis.severity_summary || 'none') | uppercase }}</span>
              <span class="alh-dot">·</span>
              <span class="alh-agent">{{ analysis.agent_type | uppercase }}</span>
              <span class="alh-spacer"></span>
              <span class="alh-counts">{{ analysis.findings_count }} {{ i18n.t('ai_insights.findings') }} · {{ analysis.recommendations_count }} {{ i18n.t('ai_insights.recommendations') }}</span>
              @if (analysis.jira_tickets_created?.length) {
                <span class="alh-dot">·</span>
                <span class="alh-jira">{{ analysis.jira_tickets_created.length }} Jira</span>
              }
              <span class="alh-time">{{ analysis.run_at | date:'dd.MM HH:mm' }}</span>
            </div>

            <!-- Classic/Holo header: Material component, hidden in LCARS -->
            <mat-card-header class="classic-header">
              <div class="analysis-header">
                <div class="analysis-meta">
                  <span class="severity-badge"
                        [style.background-color]="badgeBg(analysis.severity_summary)"
                        [style.color]="badgeColor(analysis.severity_summary)">
                    {{ analysis.severity_summary || 'none' }}
                  </span>
                  <span class="run-time">{{ analysis.run_at | date:'dd.MM.yyyy HH:mm' }}</span>
                  <mat-chip class="agent-chip">{{ analysis.agent_type }}</mat-chip>
                </div>
                <div class="analysis-counts">
                  <span>{{ analysis.findings_count }} {{ i18n.t('ai_insights.findings') }}</span>
                  <span>{{ analysis.recommendations_count }} {{ i18n.t('ai_insights.recommendations') }}</span>
                  @if (analysis.clusters?.length) {
                    <span class="cluster-count">{{ analysis.clusters.length }} {{ i18n.t('ai_insights.error_clusters') }}</span>
                  }
                  @if (analysis.jira_tickets_created?.length) {
                    <span class="jira-count">{{ analysis.jira_tickets_created.length }} {{ i18n.t('ai_insights.jira_tickets') }}</span>
                  }
                </div>
              </div>
            </mat-card-header>
            <mat-card-content>
              <!-- ── Fehler-Cluster (root-cause Diagnosen) ── -->
              @if (analysis.clusters?.length) {
                <div class="cluster-section">
                  <div class="cluster-section-title">
                    <mat-icon>hub</mat-icon> {{ i18n.t('ai_insights.diagnosis_clusters') }}
                  </div>
                  @for (cl of analysis.clusters; track $index) {
                    <div class="cluster-item" [style.border-left-color]="sevColor(cl.severity)">
                      <div class="cluster-head">
                        <span class="cluster-sev" [style.color]="sevColor(cl.severity)">
                          [{{ cl.severity | uppercase }}]
                        </span>
                        <span class="cluster-diagnosis">{{ cl.diagnosis }}</span>
                      </div>
                      @if (cl.root_cause_host) {
                        <div class="cluster-root">
                          <mat-icon class="host-icon">my_location</mat-icon>
                          <span>{{ i18n.t('ai_insights.root_cause_label') }} </span>
                          <button class="host-link" (click)="openInFeedByHost(cl.root_cause_host)">{{ cl.root_cause_host }}</button>
                        </div>
                      }
                      @if (cl.affected_hosts?.length) {
                        <div class="cluster-hosts">
                          @for (h of cl.affected_hosts; track h) {
                            <button class="host-chip" (click)="openInFeedByHost(h)">{{ h }}</button>
                          }
                        </div>
                      }
                      @if (cl.explanation) {
                        <div class="cluster-expl">{{ cl.explanation }}</div>
                      }
                      @if (cl.recommendation) {
                        <div class="cluster-rec">
                          <mat-icon class="rec-arrow">arrow_forward</mat-icon>{{ cl.recommendation }}
                        </div>
                      }
                    </div>
                  }
                </div>
              }
              <mat-accordion>
                @if (analysis.findings?.length || analysis.recommendations?.length) {
                  <mat-expansion-panel [expanded]="analysis.id === highlightId()">
                    <mat-expansion-panel-header>
                      <mat-panel-title>
                        {{ i18n.t('ai_insights.findings_recommendations') }}
                        ({{ analysis.findings_count }} / {{ analysis.recommendations_count }})
                      </mat-panel-title>
                    </mat-expansion-panel-header>

                    @for (block of buildBlocks(analysis); track $index) {
                      <div class="finding-item">
                        <!-- ── Finding ── -->
                        <div class="finding-header">
                          <span class="finding-sev" [style.color]="sevColor(block.finding.severity)">
                            [{{ block.finding.severity | uppercase }}]
                          </span>
                          @if (block.finding.source) {
                            <span class="source-badge"
                              [style.background]="srcColor(block.finding.source) + '22'"
                              [style.color]="srcColor(block.finding.source)">
                              {{ srcLabel(block.finding.source) }}
                            </span>
                          }
                          <span class="finding-title">{{ block.finding.title }}</span>
                          @if (block.finding.location) {
                            <mat-chip class="location-chip">{{ block.finding.location }}</mat-chip>
                          }
                          <button mat-icon-button class="feed-link-btn"
                            matTooltip="Im News Feed öffnen"
                            (click)="openInFeed(block.finding)">
                            <mat-icon>open_in_new</mat-icon>
                          </button>
                        </div>
                        @if (block.finding.host) {
                          <div class="finding-host">
                            <mat-icon class="host-icon">dns</mat-icon>
                            @for (h of splitHosts(block.finding.host); track h) {
                              <button class="host-link" (click)="openInFeedByHost(h)">{{ h }}</button>
                            }
                          </div>
                        }
                        @if (block.finding.description) {
                          <div class="finding-desc">{{ block.finding.description }}</div>
                        }

                        <!-- ── Matching Recommendations directly below ── -->
                        @for (rec of block.recs; track $index) {
                          <div class="rec-inline">
                            <div class="rec-header">
                              <mat-icon class="rec-arrow">arrow_forward</mat-icon>
                              <span class="rec-prio" [style.color]="sevColor(rec.priority)">
                                {{ rec.priority | uppercase }}
                              </span>
                              <span class="rec-action">{{ rec.action }}</span>
                              @if (rec.jira_title) {
                                <mat-icon class="jira-icon" title="Als Jira-Ticket">confirmation_number</mat-icon>
                              }
                            </div>
                            @if (rec.rationale) {
                              <div class="rec-rationale">{{ rec.rationale }}</div>
                            }
                            @if (rec.references?.length) {
                              <div class="rec-refs">
                                @for (ref of rec.references; track ref) {
                                  @if (isUrl(ref)) {
                                    <a [href]="ref" target="_blank" rel="noopener" class="ref-link">
                                      <mat-icon class="ref-icon">open_in_new</mat-icon>{{ refLabel(ref) }}
                                    </a>
                                  } @else {
                                    <span class="ref-text">
                                      <mat-icon class="ref-icon">menu_book</mat-icon>{{ ref }}
                                    </span>
                                  }
                                }
                              </div>
                            }
                          </div>
                        }
                      </div>
                    }

                    <!-- Standalone recommendations with no matching finding -->
                    @if (unmatchedRecs(analysis).length) {
                      <div class="unmatched-label">{{ i18n.t('ai_insights.more_recommendations') }}</div>
                      @for (rec of unmatchedRecs(analysis); track $index) {
                        <div class="rec-item">
                          <div class="rec-header">
                            <mat-icon class="rec-arrow">arrow_forward</mat-icon>
                            <span class="rec-prio" [style.color]="sevColor(rec.priority)">{{ rec.priority | uppercase }}</span>
                            <span class="rec-action">{{ rec.action }}</span>
                            @if (rec.jira_title) { <mat-icon class="jira-icon">confirmation_number</mat-icon> }
                          </div>
                          @if (rec.rationale) { <div class="rec-rationale">{{ rec.rationale }}</div> }
                        </div>
                      }
                    }
                  </mat-expansion-panel>
                }

                @if (analysis.jira_tickets_created?.length) {
                  <mat-expansion-panel>
                    <mat-expansion-panel-header>
                      <mat-panel-title>{{ i18n.t('ai_insights.created_jira_tickets') }}</mat-panel-title>
                    </mat-expansion-panel-header>
                    <div class="jira-list">
                      @for (ticket of analysis.jira_tickets_created; track ticket) {
                        <mat-chip>{{ ticket }}</mat-chip>
                      }
                    </div>
                  </mat-expansion-panel>
                }

                @if (analysis.rag_queries_used?.length) {
                  <mat-expansion-panel>
                    <mat-expansion-panel-header>
                      <mat-panel-title>{{ i18n.t('ai_insights.rag_context') }}</mat-panel-title>
                    </mat-expansion-panel-header>
                    @for (ctx of analysis.rag_queries_used; track $index) {
                      <div class="rag-item">
                        <mat-chip class="rag-source-chip">{{ ctx.source }}</mat-chip>
                        <span class="rag-query">{{ ctx.query }}</span>
                        <span class="rag-results">({{ ctx.results?.length ?? 0 }} Ergebnisse)</span>
                      </div>
                    }
                  </mat-expansion-panel>
                }
              </mat-accordion>
            </mat-card-content>
          </mat-card>
        }
        @if (analyses().length === 0) {
          <div class="empty-state">{{ i18n.t('ai_insights.no_analyses') }}</div>
        }
      }
    </div>
  `,
  styles: [`
    .page-container { padding: 24px; max-width: 1000px; }
    .page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
    .page-header h2 { margin: 0; }
    .analysis-card { margin-bottom: 16px; transition: box-shadow .3s, background .3s; }
    .analysis-card.highlighted {
      box-shadow: 0 0 0 2px var(--mat-sys-primary), 0 4px 20px rgba(0,0,0,.18);
      background: color-mix(in srgb, var(--mat-sys-primary) 6%, var(--mat-sys-surface));
    }
    .analysis-header { display: flex; flex-direction: column; gap: 6px; width: 100%; }
    .analysis-meta { display: flex; align-items: center; gap: 10px; }
    .analysis-counts { display: flex; gap: 16px; font-size: 12px; color: var(--mat-sys-on-surface-variant); }
    .jira-count { color: #0052cc; font-weight: 600; }
    .cluster-count { color: #b8860b; font-weight: 600; }
    /* ── Fehler-Cluster (root-cause diagnoses) ── */
    .cluster-section { margin-bottom: 12px; }
    .cluster-section-title {
      display: flex; align-items: center; gap: 6px;
      font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em;
      color: var(--mat-sys-on-surface); margin: 4px 0 8px;
    }
    .cluster-section-title mat-icon { font-size: 18px; height: 18px; width: 18px; color: #b8860b; }
    .cluster-item {
      border-left: 4px solid #9e9e9e; padding: 8px 12px; margin-bottom: 8px;
      background: var(--mat-sys-surface-container-low); border-radius: 0 6px 6px 0;
    }
    .cluster-head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
    .cluster-sev { font-weight: 700; font-size: 11px; flex-shrink: 0; }
    .cluster-diagnosis { font-size: 14px; font-weight: 600; }
    .cluster-root {
      display: flex; align-items: center; gap: 4px; margin-top: 4px;
      font-size: 12px; color: var(--mat-sys-on-surface-variant);
    }
    .cluster-hosts { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
    .host-chip {
      background: var(--mat-sys-surface-container-high); border: 1px solid var(--mat-sys-outline-variant);
      border-radius: 10px; padding: 1px 8px; cursor: pointer;
      font-family: 'Fira Code', monospace; font-size: 11px; color: var(--mat-sys-primary);
    }
    .host-chip:hover { background: var(--mat-sys-surface-container-highest); }
    .cluster-expl { font-size: 12px; color: var(--mat-sys-on-surface-variant); margin-top: 6px; line-height: 1.5; }
    .cluster-rec {
      display: flex; align-items: center; gap: 4px; margin-top: 6px;
      font-size: 12px; font-weight: 500;
    }
    .cluster-rec .rec-arrow { font-size: 14px; height: 14px; width: 14px; color: #b8860b; }
    :host-context(html.cs-theme-lcars) .cluster-count { color: #FFCC99; }
    :host-context(html.cs-theme-lcars) .cluster-section-title mat-icon,
    :host-context(html.cs-theme-lcars) .cluster-rec .rec-arrow { color: #FFCC99; }
    :host-context(html.cs-theme-lcars) .host-chip { color: #FF9933; }
    .severity-badge { padding: 2px 10px; border-radius: 10px; font-size: 12px; font-weight: 600; text-transform: uppercase; }
    .run-time { font-size: 12px; color: var(--mat-sys-on-surface-variant); }
    .agent-chip { font-size: 10px; min-height: 18px; }
    .finding-item { padding: 8px 0; border-bottom: 1px solid var(--mat-sys-outline-variant); }
    .finding-header { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .finding-sev { font-weight: 700; font-size: 11px; flex-shrink: 0; }
    .source-badge {
      font-size: 10px; font-weight: 700; padding: 1px 7px; border-radius: 3px;
      text-transform: uppercase; letter-spacing: .06em; flex-shrink: 0;
    }
    .finding-title { font-size: 13px; font-weight: 500; flex: 1; min-width: 0; }
    .location-chip { font-size: 10px; min-height: 16px; flex-shrink: 0; }
    .feed-link-btn { width: 28px; height: 28px; flex-shrink: 0; }
    .feed-link-btn mat-icon { font-size: 16px; height: 16px; width: 16px; color: var(--mat-sys-primary); }
    .finding-host {
      display: flex; align-items: center; gap: 4px;
      margin-top: 3px; font-family: 'Fira Code', monospace; font-size: 12px;
    }
    .host-icon { font-size: 14px; height: 14px; width: 14px; color: var(--mat-sys-on-surface-variant); }
    .host-link {
      background: none; border: none; cursor: pointer; padding: 0;
      color: var(--mat-sys-primary); font-family: 'Fira Code', monospace; font-size: 12px;
      text-decoration: underline dotted;
    }
    .host-link:hover { text-decoration: underline; }
    .finding-desc { font-size: 12px; color: var(--mat-sys-on-surface-variant); margin-top: 4px; line-height: 1.5; }
    /* Inline recommendation — visually attached to the finding above */
    .rec-inline {
      margin: 8px 0 0 12px;
      padding: 8px 12px;
      border-left: 3px solid var(--mat-sys-primary);
      background: color-mix(in srgb, var(--mat-sys-primary) 6%, transparent);
      border-radius: 0 6px 6px 0;
    }
    .rec-arrow { font-size: 14px; height: 14px; width: 14px; color: var(--mat-sys-primary); flex-shrink: 0; }
    .unmatched-label { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; color: var(--mat-sys-on-surface-variant); padding: 12px 0 4px; }
    /* Standalone rec (unmatched) */
    .rec-item { padding: 8px 0; border-bottom: 1px solid var(--mat-sys-outline-variant); }
    .rec-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; flex-wrap: wrap; }
    .rec-prio { font-weight: 700; font-size: 11px; flex-shrink: 0; }
    .rec-action { font-size: 13px; font-weight: 500; flex: 1; }
    .jira-icon { font-size: 14px; width: 14px; height: 14px; color: #0052cc; }
    .rec-rationale { font-size: 12px; color: var(--mat-sys-on-surface-variant); margin-left: 22px; margin-top: 2px; }
    .rec-refs { margin-left: 22px; margin-top: 6px; display: flex; flex-direction: column; gap: 4px; }
    .ref-link { font-size: 11px; color: var(--mat-sys-primary); display: flex; align-items: center; gap: 3px; text-decoration: none; }
    .ref-link:hover { text-decoration: underline; }
    .ref-text { font-size: 11px; color: var(--mat-sys-on-surface-variant); display: flex; align-items: center; gap: 3px; }
    .ref-icon { font-size: 12px; width: 12px; height: 12px; flex-shrink: 0; }
    .jira-list { display: flex; gap: 6px; flex-wrap: wrap; padding: 8px 0; }
    .rag-item { display: flex; align-items: center; gap: 8px; padding: 6px 0; font-size: 12px; }
    .rag-source-chip { font-size: 10px; min-height: 18px; }
    .rag-query { flex: 1; font-style: italic; }
    .rag-results { color: var(--mat-sys-on-surface-variant); }
    .empty-state { text-align: center; padding: 40px; color: var(--mat-sys-on-surface-variant); }
    .spinner-center { display: flex; justify-content: center; padding: 40px; }
    mat-spinner { display: inline-block; }

    /* ══ LCARS THEME ══════════════════════════════════════════════════════ */
    :host-context(html.cs-theme-lcars) .page-container {
      font-family: 'Antonio','Eurostile','Roboto Condensed',sans-serif;
    }
    :host-context(html.cs-theme-lcars) .page-header h2 {
      font-size: 20px; font-weight: 800; letter-spacing: .22em; text-transform: uppercase;
      color: #FFCC66; background: #000; display: inline-block; padding: 3px 10px 3px 0;
    }
    /* Analysis card — LCARS panel */
    :host-context(html.cs-theme-lcars) .analysis-card {
      background: #15120c !important;
      border: none !important;
      border-left: 22px solid #FF9933 !important;
      border-radius: 22px 8px 8px 22px !important;
      box-shadow: none !important;
    }
    :host-context(html.cs-theme-lcars) .analysis-card.highlighted {
      border-left-color: #FFCC66 !important;
      outline: 2px solid #FFCC66;
      background: #1e1710 !important;
    }
    /* Severity-based card colors (per analysis) */
    :host-context(html.cs-theme-lcars) .analysis-card[data-severity="critical"] { border-left-color: #CC4444 !important; }
    :host-context(html.cs-theme-lcars) .analysis-card[data-severity="high"]     { border-left-color: #FF9933 !important; }
    :host-context(html.cs-theme-lcars) .analysis-card[data-severity="medium"]   { border-left-color: #FFCC66 !important; }
    :host-context(html.cs-theme-lcars) .analysis-card[data-severity="low"]      { border-left-color: #99CCFF !important; }
    /* LCARS header: pure div — no Material component issues */
    .analysis-lcars-header { display: none; }  /* hidden in Classic/Holo */
    :host-context(html.cs-theme-lcars) .analysis-lcars-header {
      display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
      background: #FF9933;       /* default orange */
      color: #000;               /* guaranteed black — no Material interference */
      padding: 8px 14px;
      border-radius: 0 7px 0 0;
      font-family: 'Antonio','Eurostile',sans-serif;
      font-size: 11px; font-weight: 900; letter-spacing: .08em;
      flex-shrink: 0;
    }
    /* Severity-based header colors */
    :host-context(html.cs-theme-lcars) .analysis-card[data-severity="critical"] .analysis-lcars-header { background: #CC4444; }
    :host-context(html.cs-theme-lcars) .analysis-card[data-severity="medium"]   .analysis-lcars-header { background: #FFCC66; }
    :host-context(html.cs-theme-lcars) .analysis-card[data-severity="low"]      .analysis-lcars-header { background: #99CCFF; }
    /* LCARS header text elements */
    :host-context(html.cs-theme-lcars) .alh-sev    { font-size: 12px; }
    :host-context(html.cs-theme-lcars) .alh-agent  { font-size: 10px; opacity: .75; }
    :host-context(html.cs-theme-lcars) .alh-counts { font-size: 10px; font-weight: 700; }
    :host-context(html.cs-theme-lcars) .alh-jira   { font-size: 10px; background: rgba(0,0,0,.18); padding: 0 5px; border-radius: 3px; }
    :host-context(html.cs-theme-lcars) .alh-time   { opacity: .55; font-size: 10px; font-weight: 400; }
    :host-context(html.cs-theme-lcars) .alh-spacer { flex: 1; }
    :host-context(html.cs-theme-lcars) .alh-dot    { opacity: .45; }
    /* Hide Material header in LCARS — use the plain div above instead */
    :host-context(html.cs-theme-lcars) .classic-header { display: none !important; }
    /* mat-card-content = dark body */
    :host-context(html.cs-theme-lcars) mat-card-content { background: #000; padding: 8px 14px 12px; }
    /* Expansion panels */
    :host-context(html.cs-theme-lcars) mat-expansion-panel {
      background: #0a0804 !important;
      border-left: 3px solid #FF9933;
      border-radius: 0 6px 6px 0 !important;
      margin-bottom: 6px;
    }
    :host-context(html.cs-theme-lcars) mat-expansion-panel-header {
      background: #1e1710 !important;
    }
    :host-context(html.cs-theme-lcars) mat-panel-title {
      color: #FFCC66 !important;
      font-family: 'Antonio','Eurostile',sans-serif;
      text-transform: uppercase; letter-spacing: .08em;
      font-size: 11px; font-weight: 900;
    }
    /* Findings */
    :host-context(html.cs-theme-lcars) .finding-item { border-bottom-color: #2a1d0a; }
    :host-context(html.cs-theme-lcars) .finding-sev { font-size: 11px; }
    :host-context(html.cs-theme-lcars) .finding-title { color: #ffe8a0; font-weight: 600; }
    :host-context(html.cs-theme-lcars) .finding-desc { color: #e8a060; }
    :host-context(html.cs-theme-lcars) .host-link { color: #FF9933; }
    :host-context(html.cs-theme-lcars) .feed-link-btn mat-icon { color: #FF9933; }
    /* Inline recommendation */
    :host-context(html.cs-theme-lcars) .rec-inline {
      border-left-color: #FF9933;
      background: rgba(255,153,51,.08);
    }
    :host-context(html.cs-theme-lcars) .rec-arrow { color: #FF9933; }
    :host-context(html.cs-theme-lcars) .rec-action { color: #ffe8a0; }
    :host-context(html.cs-theme-lcars) .rec-rationale { color: #e8a060; }
    :host-context(html.cs-theme-lcars) .unmatched-label { color: #FFCC66; }
    :host-context(html.cs-theme-lcars) .rec-item { border-bottom-color: #2a1d0a; }
    :host-context(html.cs-theme-lcars) .ref-link { color: #FF9933; }
    :host-context(html.cs-theme-lcars) .ref-text { color: #e8a060; }
    :host-context(html.cs-theme-lcars) .rag-query { color: #ffcc99; }
    :host-context(html.cs-theme-lcars) .rag-results { color: #e8a060; }
    :host-context(html.cs-theme-lcars) .jira-icon { color: #99CCFF; }
    :host-context(html.cs-theme-lcars) .empty-state { color: #5a3a18; }
  `],
})
export class AiInsightsComponent implements OnInit, OnDestroy {
  readonly i18n = inject(I18nService);
  analyses = signal<any[]>([]);
  loading = signal(true);
  triggering = signal(false);
  highlightId = signal<string | null>(null);
  private destroy$ = new Subject<void>();

  constructor(
    private http: HttpClient,
    private ws: WebsocketService,
    private snack: MatSnackBar,
    private route: ActivatedRoute,
    private router: Router,
    private themeSvc: ThemeService,
  ) {}

  ngOnInit() {
    this.highlightId.set(this.route.snapshot.queryParamMap.get('analysis'));
    this.load();
    this.ws.messages().pipe(takeUntil(this.destroy$)).subscribe((msg: WsMessage) => {
      if (msg.type === 'ai_insight') this.load();
    });
  }

  private scrollToHighlight() {
    const id = this.highlightId();
    if (!id) return;
    setTimeout(() => {
      const el = document.querySelector(`[data-analysis-id="${id}"]`);
      el?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 200);
  }

  ngOnDestroy() { this.destroy$.next(); this.destroy$.complete(); }

  load() {
    this.loading.set(true);
    this.http.get<any[]>(`${environment.apiUrl}/ai/analyses`).subscribe({
      next: data => { this.analyses.set(data); this.loading.set(false); this.scrollToHighlight(); },
      error: () => this.loading.set(false),
    });
  }

  trigger() {
    this.triggering.set(true);
    this.http.post(`${environment.apiUrl}/ai/trigger/sysadmin`, {}).subscribe({
      next: () => {
        this.triggering.set(false);
        this.snack.open('KI-Agent gestartet — Ergebnisse erscheinen in Kürze', 'OK', { duration: 5000 });
      },
      error: () => {
        this.triggering.set(false);
        this.snack.open('Fehler beim Starten des Agenten', 'OK', { duration: 4000 });
      },
    });
  }

  sevColor(sev: string): string { return SEVERITY_COLORS[sev] ?? '#9e9e9e'; }
  srcLabel(src: string): string { return SOURCE_LABELS[src] ?? src?.toUpperCase() ?? ''; }
  srcColor(src: string): string { return SOURCE_COLORS[src] ?? '#9e9e9e'; }

  /** In LCARS mode return null so CSS (not inline style) controls the badge color. */
  badgeBg(sev: string): string | null {
    return this.themeSvc.theme() === 'lcars' ? null : this.sevColor(sev) + '22';
  }
  badgeColor(sev: string): string | null {
    return this.themeSvc.theme() === 'lcars' ? null : this.sevColor(sev);
  }

  /** Group findings with their matching recommendations.
   *  Matching priority: 1) host appears in rec.action/rationale  2) sequential index fallback */
  buildBlocks(analysis: any): Array<{finding: any; recs: any[]}> {
    const findings: any[] = analysis.findings ?? [];
    const recs: any[] = analysis.recommendations ?? [];
    const used = new Set<number>();

    return findings.map((finding, fi) => {
      const host = (finding.host ?? '').toLowerCase();
      // 1. Host-match: rec mentions the finding's host
      let matched: any[] = [];
      if (host) {
        recs.forEach((rec, ri) => {
          if (!used.has(ri)) {
            const text = ((rec.action ?? '') + ' ' + (rec.rationale ?? '')).toLowerCase();
            if (text.includes(host)) { matched.push(rec); used.add(ri); }
          }
        });
      }
      // 2. Index fallback if no host-match: pair rec[fi] with finding[fi]
      if (!matched.length && fi < recs.length && !used.has(fi)) {
        matched = [recs[fi]];
        used.add(fi);
      }
      return { finding, recs: matched };
    });
  }

  /** Recommendations that were not matched to any finding. */
  unmatchedRecs(analysis: any): any[] {
    const findings: any[] = analysis.findings ?? [];
    const recs: any[] = analysis.recommendations ?? [];
    const used = new Set<number>();
    // Re-run the same matching logic to find which are used
    findings.forEach((finding, fi) => {
      const host = (finding.host ?? '').toLowerCase();
      if (host) {
        recs.forEach((rec, ri) => {
          if (!used.has(ri)) {
            const text = ((rec.action ?? '') + ' ' + (rec.rationale ?? '')).toLowerCase();
            if (text.includes(host)) used.add(ri);
          }
        });
      } else if (fi < recs.length && !used.has(fi)) {
        used.add(fi);
      }
    });
    return recs.filter((_, ri) => !used.has(ri));
  }

  /** Open news feed filtered by source + severity of this finding. */
  openInFeed(finding: any) {
    const qp: Record<string, string> = {};
    if (finding.source) qp['source'] = finding.source;
    if (finding.severity) qp['severity'] = finding.severity;
    if (finding.host) qp['host'] = finding.host;
    this.router.navigate(['/feed'], { queryParams: qp });
  }

  /** Open news feed filtered to a specific host. */
  openInFeedByHost(host: string) {
    if (host) this.router.navigate(['/feed'], { queryParams: { host } });
  }

  splitHosts(host: string): string[] {
    return host.split(',').map(h => h.trim()).filter(Boolean);
  }

  isUrl(ref: string): boolean {
    return ref.startsWith('http://') || ref.startsWith('https://');
  }

  refLabel(url: string): string {
    try {
      const u = new URL(url);
      // Confluence: title in query params
      const title = u.searchParams.get('title') || u.searchParams.get('pageTitle');
      if (title) return decodeURIComponent(title.replace(/\+/g, ' '));
      // Last meaningful path segment — skip view.action and similar
      const segments = u.pathname.split('/').filter(s => s && !s.includes('.action') && !s.includes('.jsp'));
      if (segments.length) return decodeURIComponent(segments[segments.length - 1]);
      return u.hostname;
    } catch {
      return url;
    }
  }
}
