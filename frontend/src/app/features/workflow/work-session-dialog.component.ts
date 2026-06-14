import { Component, Inject, OnInit, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { MAT_DIALOG_DATA, MatDialogRef, MatDialogModule } from '@angular/material/dialog';
import { MatTabsModule } from '@angular/material/tabs';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatChipsModule } from '@angular/material/chips';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatDividerModule } from '@angular/material/divider';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatBadgeModule } from '@angular/material/badge';
import { Router } from '@angular/router';
import { inject } from '@angular/core';
import { environment } from '../../../environments/environment';
import { ComputerService } from '../../core/services/computer.service';

const CLOSURE_CODES = [
  { value: 'solved_permanently', label: 'Dauerlösung' },
  { value: 'solved_workaround', label: 'Workaround' },
  { value: 'no_fault_found', label: 'Kein Fehler gefunden' },
  { value: 'duplicate', label: 'Duplikat' },
  { value: 'user_error', label: 'Benutzerfehler' },
  { value: 'cancelled', label: 'Storniert' },
];

const STATUS_OPTIONS = [
  { value: 'in_progress', label: 'In Bearbeitung' },
  { value: 'pending', label: 'Ausstehend' },
  { value: 'resolved', label: 'Gelöst' },
  { value: 'closed', label: 'Geschlossen' },
];

const CATEGORIES = [
  'Hardware', 'Software', 'Netzwerk', 'Sicherheit',
  'E-Mail / Kommunikation', 'Berechtigungen / Zugang',
  'Backup / Storage', 'Monitoring / Alerting',
  'Server / Virtualisierung', 'Datenbank', 'Sonstiges',
];

const PRIORITY_META: Record<string, { color: string; label: string }> = {
  P1: { color: '#c62828', label: 'Kritisch' },
  P2: { color: '#ef6c00', label: 'Hoch' },
  P3: { color: '#f9a825', label: 'Mittel' },
  P4: { color: '#388e3c', label: 'Niedrig' },
};

@Component({
  selector: 'cs-work-session-dialog',
  standalone: true,
  imports: [
    CommonModule, FormsModule, MatDialogModule, MatTabsModule,
    MatButtonModule, MatIconModule, MatFormFieldModule, MatInputModule,
    MatSelectModule, MatChipsModule, MatProgressSpinnerModule, MatDividerModule,
    MatExpansionModule, MatSnackBarModule, MatTooltipModule, MatBadgeModule,
  ],
  template: `
    <div class="dialog-root">
      <!-- Header -->
      <div class="dialog-header" [style.border-left-color]="priorityColor()">
        <div class="header-left">
          @if (session()?.jira_key) {
            <a class="jira-link" [href]="jiraUrl()" target="_blank">
              {{ session()?.jira_key }} <mat-icon inline>open_in_new</mat-icon>
            </a>
          }
          <span class="priority-badge" [style.background]="priorityColor()">
            {{ session()?.priority ?? '–' }}
          </span>
          <span class="status-badge">{{ statusLabel() }}</span>
        </div>
        <div class="header-right">
          <button mat-stroked-button (click)="openInWorkbench()" style="margin-right:8px" title="IDE, Terminal & Git in der Werkbank öffnen">
            <mat-icon>construction</mat-icon> In Werkbank öffnen
          </button>
          @if (hasComputerSession()) {
            <button mat-stroked-button (click)="resumeInComputer()" style="margin-right:8px" title="Session im Computer fortsetzen">
              <mat-icon>terminal</mat-icon> Im Computer fortsetzen
            </button>
          }
          <button mat-icon-button (click)="dialogRef.close()"><mat-icon>close</mat-icon></button>
        </div>
      </div>

      @if (loading()) {
        <div class="spinner-center"><mat-spinner diameter="40"></mat-spinner></div>
      } @else {

      <mat-tab-group animationDuration="200ms" class="session-tabs">

        <!-- ── Tab 0: Jira Ticket Content ── -->
        <mat-tab label="Ticket">
          <div class="ticket-tab-layout">
            <div class="ticket-scroll-area">
              @if (!session()?.jira_key) {
                <div class="empty-notes">Kein Jira-Ticket verknüpft.</div>
              } @else if (!jiraDetail()) {
                <div class="spinner-center"><mat-spinner diameter="32"></mat-spinner></div>
              } @else {
                <div class="ticket-toolbar">
                  <button mat-stroked-button (click)="refreshJiraDetail()" [disabled]="jiraRefreshing()">
                    @if (jiraRefreshing()) { <mat-spinner diameter="14"></mat-spinner> }
                    @else { <mat-icon>refresh</mat-icon> }
                    Jira aktualisieren
                  </button>
                </div>
                <!-- Meta row -->
                <div class="jira-meta-row">
                  <span class="jira-meta-field"><strong>Status:</strong> {{ jiraDetail().status }}</span>
                  <span class="jira-meta-field"><strong>Priorität:</strong> {{ jiraDetail().priority }}</span>
                  @if (jiraDetail().assignee) {
                    <span class="jira-meta-field"><strong>Zugewiesen:</strong> {{ jiraDetail().assignee }}</span>
                  }
                  <span class="jira-meta-field"><strong>Erstellt:</strong> {{ jiraDetail().created | date:'dd.MM.yyyy HH:mm' }}</span>
                </div>

                <!-- Description -->
                <div class="jira-section">
                  <div class="jira-section-title">Beschreibung</div>
                  @if (jiraDetail().description) {
                    <pre class="jira-body-text">{{ jiraDetail().description }}</pre>
                  } @else {
                    <span class="empty-notes">Keine Beschreibung.</span>
                  }
                </div>

                <!-- Comments -->
                @if (jiraDetail().comments?.length) {
                  <div class="jira-section">
                    <div class="jira-section-title">Kommentare ({{ jiraDetail().comments.length }})</div>
                    <div class="comment-list">
                      @for (c of jiraDetail().comments; track c.id) {
                        <div class="comment-entry">
                          <div class="comment-meta">
                            <mat-icon class="note-icon">person</mat-icon>
                            <span class="note-author">{{ c.author }}</span>
                            <span class="note-time">{{ c.created | date:'dd.MM.yyyy HH:mm' }}</span>
                          </div>
                          <pre class="note-content">{{ c.body }}</pre>
                        </div>
                      }
                    </div>
                  </div>
                }
              }
            </div>

            <!-- Sticky comment bar at bottom -->
            @if (session()?.jira_key) {
              <div class="ticket-comment-bar">
                <mat-form-field appearance="outline" class="full-width comment-bar-field">
                  <mat-label>Kommentar in Jira posten</mat-label>
                  <textarea matInput [(ngModel)]="manualComment" rows="3" placeholder="Kommentar eingeben…"></textarea>
                </mat-form-field>
                <div class="comment-bar-actions">
                  <button mat-flat-button color="primary"
                    (click)="postManualComment()"
                    [disabled]="aiLoading.posting() || !manualComment.trim()">
                    @if (aiLoading.posting()) {
                      <mat-spinner diameter="16"></mat-spinner>
                    } @else {
                      <mat-icon>send</mat-icon>
                    }
                    Posten
                  </button>
                  @if (manualCommentPosted()) {
                    <span class="post-success"><mat-icon>check_circle</mat-icon> Gepostet</span>
                  }
                </div>
              </div>
            }
          </div>
        </mat-tab>

        <!-- ── Tab 1: Overview ── -->
        <mat-tab label="Übersicht">
          <div class="tab-content">
            <mat-form-field appearance="outline" class="full-width">
              <mat-label>Titel</mat-label>
              <input matInput [(ngModel)]="form.title">
            </mat-form-field>

            <div class="row-2">
              <mat-form-field appearance="outline">
                <mat-label>Kategorie</mat-label>
                <mat-select [(ngModel)]="form.category">
                  @for (c of categories; track c) { <mat-option [value]="c">{{ c }}</mat-option> }
                </mat-select>
              </mat-form-field>
              <mat-form-field appearance="outline">
                <mat-label>Unterkategorie</mat-label>
                <input matInput [(ngModel)]="form.subcategory">
              </mat-form-field>
            </div>

            <div class="row-3">
              <mat-form-field appearance="outline">
                <mat-label>Impact</mat-label>
                <mat-select [(ngModel)]="form.impact" (selectionChange)="onImpactUrgencyChange()">
                  <mat-option value="high">Hoch</mat-option>
                  <mat-option value="medium">Mittel</mat-option>
                  <mat-option value="low">Niedrig</mat-option>
                </mat-select>
              </mat-form-field>
              <mat-form-field appearance="outline">
                <mat-label>Urgency</mat-label>
                <mat-select [(ngModel)]="form.urgency" (selectionChange)="onImpactUrgencyChange()">
                  <mat-option value="high">Hoch</mat-option>
                  <mat-option value="medium">Mittel</mat-option>
                  <mat-option value="low">Niedrig</mat-option>
                </mat-select>
              </mat-form-field>
              <mat-form-field appearance="outline">
                <mat-label>Status</mat-label>
                <mat-select [(ngModel)]="form.status">
                  @for (s of statusOptions; track s.value) { <mat-option [value]="s.value">{{ s.label }}</mat-option> }
                </mat-select>
              </mat-form-field>
            </div>

            @if (form.status === 'resolved' || form.status === 'closed') {
              <div class="row-2">
                <mat-form-field appearance="outline">
                  <mat-label>Abschlusstyp</mat-label>
                  <mat-select [(ngModel)]="form.closure_code">
                    @for (c of closureCodes; track c.value) { <mat-option [value]="c.value">{{ c.label }}</mat-option> }
                  </mat-select>
                </mat-form-field>
                <mat-form-field appearance="outline">
                  <mat-label>Lösungstyp</mat-label>
                  <mat-select [(ngModel)]="form.resolution_type">
                    <mat-option value="permanent_fix">Dauerlösung</mat-option>
                    <mat-option value="workaround">Workaround</mat-option>
                  </mat-select>
                </mat-form-field>
              </div>
            }

            <!-- SLA Indicator -->
            @if (session()?.sla_response_at || session()?.sla_resolved_at) {
              <div class="sla-row">
                <mat-icon class="sla-icon">timer</mat-icon>
                <span class="sla-label">SLA Response:</span>
                <span [class.sla-breach]="isSlaBreached(session()?.sla_response_at)">
                  {{ session()?.sla_response_at | date:'dd.MM. HH:mm' }}
                </span>
                <mat-icon class="sla-icon" style="margin-left:12px">schedule</mat-icon>
                <span class="sla-label">Lösung:</span>
                <span [class.sla-breach]="isSlaBreached(session()?.sla_resolved_at)">
                  {{ session()?.sla_resolved_at | date:'dd.MM. HH:mm' }}
                </span>
              </div>
            }

            <!-- KI Auto-Kategorisierung -->
            <button mat-stroked-button (click)="autoCategorize()" [disabled]="aiLoading.categorize()">
              @if (aiLoading.categorize()) { <mat-spinner diameter="16"></mat-spinner> }
              @else { <mat-icon>psychology</mat-icon> }
              KI Auto-Kategorisierung
            </button>

            <div class="form-actions">
              <button mat-flat-button color="primary" (click)="saveOverview()">
                <mat-icon>save</mat-icon> Speichern
              </button>
            </div>
          </div>
        </mat-tab>

        <!-- ── Tab 2: Work Notes ── -->
        <mat-tab [label]="'Notizen (' + (session()?.work_notes?.length ?? 0) + ')'">
          <div class="tab-content">
            <div class="notes-log">
              @for (note of session()?.work_notes ?? []; track $index) {
                <div class="note-entry" [class.ai-note]="note.type === 'ai'">
                  <div class="note-meta">
                    <mat-icon class="note-icon">{{ note.type === 'ai' ? 'smart_toy' : 'person' }}</mat-icon>
                    <span class="note-author">{{ note.author }}</span>
                    <span class="note-time">{{ note.timestamp | date:'dd.MM.yyyy HH:mm' }}</span>
                  </div>
                  <pre class="note-content">{{ note.content }}</pre>
                </div>
              }
              @if (!session()?.work_notes?.length) {
                <div class="empty-notes">Noch keine Notizen.</div>
              }
            </div>

            <mat-divider></mat-divider>

            <div class="add-note">
              <mat-form-field appearance="outline" class="full-width">
                <mat-label>Neue Notiz</mat-label>
                <textarea matInput [(ngModel)]="newNote" rows="3" placeholder="Arbeitsschritt, Beobachtung, …"></textarea>
              </mat-form-field>
              <button mat-flat-button color="primary" (click)="addNote()" [disabled]="!newNote.trim()">
                <mat-icon>add_comment</mat-icon> Notiz hinzufügen
              </button>
            </div>
          </div>
        </mat-tab>

        <!-- ── Tab 3: KI-Kommentar ── -->
        <mat-tab label="KI-Assistent">
          <div class="tab-content">

            <!-- Comment Generator -->
            <mat-expansion-panel expanded>
              <mat-expansion-panel-header>
                <mat-panel-title><mat-icon>comment</mat-icon> Ticket-Kommentar generieren</mat-panel-title>
              </mat-expansion-panel-header>
              <div class="panel-body">
                <div class="comment-type-row">
                  @for (ct of commentTypes; track ct.value) {
                    <button mat-stroked-button
                      [class.selected]="selectedCommentType() === ct.value"
                      (click)="selectedCommentType.set(ct.value)">
                      {{ ct.label }}
                    </button>
                  }
                </div>
                <mat-form-field appearance="outline" class="full-width">
                  <mat-label>Aktuelle Entwicklungen (optional)</mat-label>
                  <textarea matInput [(ngModel)]="additionalContext" rows="3"
                    placeholder="z.B. Service heute Nacht neu gestartet, Logs zeigen keine weiteren Fehler. Sprint-Ziel: Migration bis Freitag abschließen."></textarea>
                  <mat-hint>Beschreiben Sie aktuelle Entwicklungen — die KI nimmt diese in den Kommentar auf.</mat-hint>
                </mat-form-field>
                <button mat-flat-button color="accent" (click)="generateComment()" [disabled]="aiLoading.comment()">
                  @if (aiLoading.comment()) { <mat-spinner diameter="16"></mat-spinner> Generiere… }
                  @else { <ng-container><mat-icon>auto_awesome</mat-icon> Kommentar erstellen</ng-container> }
                </button>
                @if (generatedComment()) {
                  <div class="ai-result">
                    <div class="ai-result-header">
                      <span>Generierter Kommentar</span>
                      <button mat-icon-button (click)="copyToClipboard(generatedComment()!)" matTooltip="Kopieren">
                        <mat-icon>content_copy</mat-icon>
                      </button>
                    </div>
                    <textarea class="ai-text-edit" rows="8"
                      [value]="generatedComment()!"
                      (input)="generatedComment.set($any($event.target).value)"></textarea>
                    <div class="ai-result-actions">
                      <button mat-flat-button color="primary"
                        (click)="postCommentToJira()"
                        [disabled]="aiLoading.posting() || !session()?.jira_key"
                        [matTooltip]="session()?.jira_key ? 'Kommentar in Jira ' + session()?.jira_key + ' posten' : 'Kein Jira-Ticket verknüpft'">
                        @if (aiLoading.posting()) {
                          <mat-spinner diameter="16"></mat-spinner> Wird gepostet…
                        } @else {
                          <mat-icon>send</mat-icon> Kommentar übernehmen
                        }
                      </button>
                      @if (commentPosted()) {
                        <span class="post-success"><mat-icon>check_circle</mat-icon> In Jira gepostet</span>
                      }
                    </div>
                  </div>
                }
              </div>
            </mat-expansion-panel>

            <!-- Resolution Generator -->
            <mat-expansion-panel>
              <mat-expansion-panel-header>
                <mat-panel-title><mat-icon>task_alt</mat-icon> Abschluss-Dokumentation generieren</mat-panel-title>
              </mat-expansion-panel-header>
              <div class="panel-body">
                <mat-form-field appearance="outline" class="full-width">
                  <mat-label>Root Cause (optional)</mat-label>
                  <textarea matInput [(ngModel)]="form.root_cause" rows="2"></textarea>
                </mat-form-field>
                <div class="row-2">
                  <mat-form-field appearance="outline">
                    <mat-label>Abschlusstyp</mat-label>
                    <mat-select [(ngModel)]="form.closure_code">
                      @for (c of closureCodes; track c.value) { <mat-option [value]="c.value">{{ c.label }}</mat-option> }
                    </mat-select>
                  </mat-form-field>
                  <mat-form-field appearance="outline">
                    <mat-label>Lösungstyp</mat-label>
                    <mat-select [(ngModel)]="form.resolution_type">
                      <mat-option value="permanent_fix">Dauerlösung</mat-option>
                      <mat-option value="workaround">Workaround</mat-option>
                    </mat-select>
                  </mat-form-field>
                </div>
                <button mat-flat-button color="accent" (click)="generateResolution()" [disabled]="aiLoading.resolution()">
                  @if (aiLoading.resolution()) { <mat-spinner diameter="16"></mat-spinner> Generiere… }
                  @else { <ng-container><mat-icon>auto_awesome</mat-icon> Dokumentation erstellen</ng-container> }
                </button>
                @if (generatedResolution()) {
                  <div class="ai-result">
                    <div class="ai-result-header">
                      <span>Lösungsdokumentation</span>
                      <button mat-icon-button (click)="copyToClipboard(generatedResolution()!)" matTooltip="Kopieren">
                        <mat-icon>content_copy</mat-icon>
                      </button>
                    </div>
                    <pre class="ai-text">{{ generatedResolution() }}</pre>
                  </div>
                }
              </div>
            </mat-expansion-panel>

            <!-- Solution Suggester -->
            <mat-expansion-panel>
              <mat-expansion-panel-header>
                <mat-panel-title><mat-icon>search</mat-icon> Lösungsvorschläge (RAG + Web)</mat-panel-title>
              </mat-expansion-panel-header>
              <div class="panel-body">
                <button mat-flat-button color="accent" (click)="suggestSolution()" [disabled]="aiLoading.solution()">
                  @if (aiLoading.solution()) { <mat-spinner diameter="16"></mat-spinner> Suche… }
                  @else { <ng-container><mat-icon>travel_explore</mat-icon> Lösungen suchen</ng-container> }
                </button>
                @if (solutionData()) {
                  @if (solutionData()!.solution_steps?.length) {
                    <div class="solution-section">
                      <strong>Lösungsschritte</strong>
                      <ol>@for (step of solutionData()!.solution_steps; track $index) { <li>{{ step }}</li> }</ol>
                    </div>
                  }
                  @if (solutionData()!.possible_causes?.length) {
                    <div class="solution-section">
                      <strong>Mögliche Ursachen</strong>
                      <ul>@for (c of solutionData()!.possible_causes; track $index) { <li>{{ c }}</li> }</ul>
                    </div>
                  }
                  @if (solutionData()!.rag_results?.length) {
                    <div class="solution-section">
                      <strong>Wissensdatenbank</strong>
                      @for (r of solutionData()!.rag_results; track $index) {
                        <div class="rag-item"><mat-icon>article</mat-icon> {{ r.title ?? r }}</div>
                      }
                    </div>
                  }
                  @if (solutionData()!.web_results?.length) {
                    <div class="solution-section">
                      <strong>Web-Ergebnisse</strong>
                      @for (r of solutionData()!.web_results; track r.url) {
                        <div class="rag-item"><mat-icon>language</mat-icon>
                          <a [href]="r.url" target="_blank">{{ r.title }}</a>
                        </div>
                      }
                    </div>
                  }
                }
              </div>
            </mat-expansion-panel>

          </div>
        </mat-tab>

        <!-- ── Tab: GitLab ── -->
        <mat-tab label="GitLab">
          <div class="tab-content">
            @if (gitlabStatus()) {
              @if (gitlabStatus()?.linked) {
                <div class="gitlab-info">
                  <div class="info-row">
                    <mat-icon>call_split</mat-icon>
                    <span>Branch: <strong>{{ gitlabStatus()?.branch ?? '—' }}</strong></span>
                  </div>
                  @if (gitlabStatus()?.mr_url) {
                    <div class="info-row">
                      <mat-icon>merge_type</mat-icon>
                      <a [href]="gitlabStatus()?.mr_url" target="_blank">
                        MR !{{ gitlabStatus()?.mr_iid }} — {{ gitlabStatus()?.mr_state }}
                      </a>
                    </div>
                  }
                  @for (p of gitlabStatus()?.pipelines ?? []; track p.id) {
                    <div class="info-row">
                      <mat-icon>smart_toy</mat-icon>
                      <span>Pipeline #{{ p.id }}: <strong>{{ p.status }}</strong></span>
                    </div>
                  }
                </div>
              }
            }

            <div class="gitlab-actions">
              @if (!gitlabStatus()?.branch) {
                <mat-form-field appearance="outline" class="full-width">
                  <mat-label>Projekt-ID</mat-label>
                  <input matInput [(ngModel)]="glProjectId" placeholder="z.B. 211">
                </mat-form-field>
                <mat-form-field appearance="outline" class="full-width">
                  <mat-label>Branch-Name</mat-label>
                  <input matInput [(ngModel)]="glBranch" placeholder="z.B. fix/my-session">
                </mat-form-field>
                <mat-form-field appearance="outline" class="full-width">
                  <mat-label>Von Branch (ref)</mat-label>
                  <input matInput [(ngModel)]="glRef" placeholder="main">
                </mat-form-field>
                <button mat-flat-button color="primary" (click)="createGitLabBranch()" [disabled]="!glProjectId || !glBranch">
                  <mat-icon>call_split</mat-icon> Branch anlegen
                </button>
              }

              @if (gitlabStatus()?.branch && !gitlabStatus()?.mr_iid) {
                <mat-form-field appearance="outline" class="full-width">
                  <mat-label>MR-Titel</mat-label>
                  <input matInput [(ngModel)]="glMrTitle" placeholder="z.B. Fix: ...">
                </mat-form-field>
                <mat-form-field appearance="outline" class="full-width">
                  <mat-label>Ziel-Branch</mat-label>
                  <input matInput [(ngModel)]="glTargetBranch" placeholder="main">
                </mat-form-field>
                <button mat-flat-button color="accent" (click)="openGitLabMR()" [disabled]="!glMrTitle">
                  <mat-icon>merge_type</mat-icon> Merge Request öffnen
                </button>
              }

              @if (gitlabStatus()?.branch) {
                <button mat-stroked-button (click)="loadGitLabStatus()" style="margin-top:8px">
                  <mat-icon>refresh</mat-icon> Status aktualisieren
                </button>
              }
            </div>
          </div>
        </mat-tab>

      </mat-tab-group>
      }
    </div>
  `,
  styles: [`
    .dialog-root { display: flex; flex-direction: column; height: 100%; max-height: 85vh; }
    .dialog-header { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; border-left: 4px solid #ccc; background: var(--mat-sys-surface-variant); }
    .header-left { display: flex; align-items: center; gap: 8px; }
    .jira-link { font-family: monospace; font-size: 13px; color: var(--mat-sys-primary); text-decoration: none; display: flex; align-items: center; gap: 2px; }
    .priority-badge { font-size: 11px; font-weight: 700; color: #fff; padding: 2px 8px; border-radius: 12px; }
    .status-badge { font-size: 12px; color: var(--mat-sys-on-surface-variant); }
    .header-right { display: flex; gap: 4px; }
    .spinner-center { display: flex; justify-content: center; padding: 60px; }
    .session-tabs { flex: 1; overflow: hidden; }
    .tab-content { padding: 16px; display: flex; flex-direction: column; gap: 12px; max-height: calc(85vh - 120px); overflow-y: auto; }
    .tab-desc { color: var(--mat-sys-on-surface-variant); font-size: 13px; margin: 0; }
    .full-width { width: 100%; }
    .row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
    .sla-row { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--mat-sys-on-surface-variant); }
    .sla-icon { font-size: 16px; width: 16px; height: 16px; }
    .sla-label { font-weight: 500; }
    .sla-breach { color: #c62828; font-weight: 700; }
    .form-actions { display: flex; justify-content: flex-end; }
    .ticket-tab-layout { display: flex; flex-direction: column; height: calc(85vh - 120px); }
    .ticket-scroll-area { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
    .ticket-comment-bar { flex-shrink: 0; border-top: 1px solid var(--mat-sys-outline-variant); padding: 10px 16px 12px; background: var(--mat-sys-surface); display: flex; flex-direction: column; gap: 4px; }
    .comment-bar-field { width: 100%; }
    .comment-bar-actions { display: flex; align-items: center; gap: 10px; justify-content: flex-end; }
    .ticket-toolbar { display: flex; justify-content: flex-end; margin-bottom: 4px; }
    .ticket-toolbar button { font-size: 12px; }
    /* Jira detail tab */
    .jira-meta-row { display: flex; flex-wrap: wrap; gap: 12px; font-size: 13px; padding: 4px 0; }
    .jira-meta-field { color: var(--mat-sys-on-surface-variant); }
    .jira-meta-field strong { color: var(--mat-sys-on-surface); margin-right: 4px; }
    .jira-section { display: flex; flex-direction: column; gap: 6px; }
    .jira-section-title { font-weight: 600; font-size: 13px; color: var(--mat-sys-on-surface-variant); text-transform: uppercase; letter-spacing: .5px; }
    pre.jira-body-text { margin: 0; font-size: 12px; white-space: pre-wrap; word-break: break-word; font-family: inherit; line-height: 1.6; background: var(--mat-sys-surface-variant); border-radius: 6px; padding: 10px 12px; }
    .comment-list { display: flex; flex-direction: column; gap: 8px; }
    .comment-entry { border-radius: 8px; padding: 8px 12px; background: var(--mat-sys-surface-variant); }
    .comment-meta { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
    /* Notes */
    .notes-log { display: flex; flex-direction: column; gap: 8px; max-height: 300px; overflow-y: auto; }
    .note-entry { border-radius: 8px; padding: 8px 12px; background: var(--mat-sys-surface-variant); }
    .note-entry.ai-note { background: #e3f2fd; border-left: 3px solid #1565c0; }
    .note-meta { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
    .note-icon { font-size: 16px; width: 16px; height: 16px; }
    .note-author { font-weight: 500; font-size: 12px; }
    .note-time { font-size: 11px; color: var(--mat-sys-on-surface-variant); margin-left: auto; }
    pre.note-content { margin: 0; font-size: 12px; white-space: pre-wrap; word-break: break-word; font-family: inherit; }
    .empty-notes { text-align: center; padding: 20px; color: var(--mat-sys-on-surface-variant); }
    .add-note { display: flex; flex-direction: column; gap: 8px; }
    /* AI Panels */
    mat-expansion-panel { margin-bottom: 4px; }
    mat-panel-title { display: flex; align-items: center; gap: 6px; }
    .panel-body { padding: 12px 0; display: flex; flex-direction: column; gap: 10px; }
    .comment-type-row { display: flex; gap: 6px; flex-wrap: wrap; }
    .comment-type-row button.selected { background: var(--mat-sys-primary-container); }
    .ai-result { background: var(--mat-sys-surface-variant); border-radius: 8px; padding: 12px; }
    .ai-result-header { display: flex; align-items: center; justify-content: space-between; font-weight: 500; font-size: 13px; margin-bottom: 6px; }
    pre.ai-text { margin: 0; font-size: 12px; white-space: pre-wrap; word-break: break-word; font-family: inherit; line-height: 1.5; }
    .ai-text-edit { width: 100%; box-sizing: border-box; font-size: 12px; font-family: inherit; line-height: 1.5; padding: 8px 10px; border: 1px solid var(--mat-sys-outline-variant); border-radius: 6px; background: var(--mat-sys-surface); color: var(--mat-sys-on-surface); resize: vertical; outline: none; }
    .ai-text-edit:focus { border-color: var(--mat-sys-primary); }
    .ai-result-actions { display: flex; align-items: center; gap: 10px; margin-top: 10px; }
    .post-success { display: flex; align-items: center; gap: 4px; color: #2e7d32; font-size: 13px; font-weight: 500; }
    .post-success mat-icon { font-size: 16px; width: 16px; height: 16px; }
    .solution-section { font-size: 13px; }
    .solution-section strong { display: block; margin-bottom: 4px; }
    .rag-item { display: flex; align-items: center; gap: 6px; font-size: 12px; padding: 3px 0; }
    .rag-item mat-icon { font-size: 14px; width: 14px; height: 14px; }
    .gitlab-info { display: flex; flex-direction: column; gap: 6px; padding: 8px 0; }
    .gitlab-actions { display: flex; flex-direction: column; gap: 8px; }
    .info-row { display: flex; align-items: center; gap: 8px; font-size: 13px; }
    .info-row mat-icon { font-size: 18px; width: 18px; height: 18px; color: var(--mat-sys-primary); }
  `],
})
export class WorkSessionDialogComponent implements OnInit {
  session = signal<any | null>(null);
  jiraDetail = signal<any | null>(null);
  gitlabStatus = signal<any | null>(null);
  loading = signal(true);
  jiraRefreshing = signal(false);

  glProjectId = '';
  glBranch = '';
  glRef = 'main';
  glMrTitle = '';
  glTargetBranch = 'main';

  form: any = {
    title: '', category: null, subcategory: '', impact: null, urgency: null,
    status: 'in_progress', closure_code: 'solved_permanently', resolution_type: 'permanent_fix',
    root_cause: '',
  };

  newNote = '';
  additionalContext = '';
  manualComment = '';
  manualCommentPosted = signal(false);
  selectedCommentType = signal('progress');
  generatedComment = signal<string | null>(null);
  generatedResolution = signal<string | null>(null);
  solutionData = signal<any | null>(null);
  commentPosted = signal(false);
  aiLoading = {
    comment: signal(false),
    resolution: signal(false),
    solution: signal(false),
    categorize: signal(false),
    posting: signal(false),
  };

  readonly categories = CATEGORIES;
  readonly closureCodes = CLOSURE_CODES;
  readonly statusOptions = STATUS_OPTIONS;
  readonly commentTypes = [
    { value: 'progress', label: 'Fortschritt' },
    { value: 'pending', label: 'Pending' },
    { value: 'escalation', label: 'Eskalation' },
    { value: 'handoff', label: 'Übergabe' },
  ];

  private sessionId: string | null = null;
  private router = inject(Router);
  private computerService = inject(ComputerService);

  hasComputerSession = computed(() => !!this.session()?.computer_session_id);

  constructor(
    public dialogRef: MatDialogRef<WorkSessionDialogComponent>,
    @Inject(MAT_DIALOG_DATA) public dialogData: any,
    private http: HttpClient,
    private snackBar: MatSnackBar,
  ) {}

  resumeInComputer(): void {
    const sid = this.session()?.computer_session_id;
    if (!sid) return;
    this.dialogRef.close();
    this.computerService.resumeSession(sid);
    this.router.navigate(['/computer']);
  }

  openInWorkbench(): void {
    const id = this.session()?.id;
    if (!id) return;
    this.dialogRef.close();
    this.router.navigate(['/workbench', id]);
  }

  ngOnInit() {
    if (this.dialogData?.id) {
      this.loadSession(this.dialogData.id);
    } else {
      this.createSession();
    }
  }

  private createSession() {
    this.http.post<any>(`${environment.apiUrl}/workflow`, {
      title: this.dialogData.title,
      jira_key: this.dialogData.jira_key,
      jira_issue_id: this.dialogData.jira_issue_id,
      alert_id: this.dialogData.alert_id,
    }).subscribe({
      next: s => { this.setSession(s); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }

  private loadSession(id: string) {
    this.http.get<any>(`${environment.apiUrl}/workflow/${id}`).subscribe({
      next: s => { this.setSession(s); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }

  private setSession(s: any) {
    this.session.set(s);
    this.sessionId = s.id;
    if (s.jira_key) {
      this.http.get<any>(`${environment.apiUrl}/jira-view/issue/${s.jira_key}`)
        .subscribe({ next: d => this.jiraDetail.set(d), error: () => {} });
    }
    if (s.gitlab_project_id || s.gitlab_branch) {
      this.loadGitLabStatus();
    }
    this.form = {
      title: s.title,
      category: s.category,
      subcategory: s.subcategory,
      impact: s.impact,
      urgency: s.urgency,
      status: s.status,
      closure_code: s.closure_code ?? 'solved_permanently',
      resolution_type: s.resolution_type ?? 'permanent_fix',
      root_cause: s.root_cause ?? '',
    };
  }

  loadGitLabStatus() {
    if (!this.sessionId) return;
    this.http.get<any>(`${environment.apiUrl}/workflow/${this.sessionId}/gitlab/status`)
      .subscribe({ next: d => this.gitlabStatus.set(d), error: () => {} });
  }

  createGitLabBranch() {
    if (!this.sessionId) return;
    this.http.post<any>(`${environment.apiUrl}/workflow/${this.sessionId}/gitlab/branch`, {
      project_id: this.glProjectId,
      branch: this.glBranch,
      ref: this.glRef || 'main',
    }).subscribe({
      next: () => { this.snackBar.open('Branch angelegt', '', { duration: 2000 }); this.loadSession(this.sessionId!); },
      error: (e) => this.snackBar.open('Fehler: ' + (e?.error?.detail ?? e.message), '', { duration: 3000 }),
    });
  }

  openGitLabMR() {
    if (!this.sessionId) return;
    this.http.post<any>(`${environment.apiUrl}/workflow/${this.sessionId}/gitlab/mr`, {
      target_branch: this.glTargetBranch || 'main',
      title: this.glMrTitle,
    }).subscribe({
      next: (d) => {
        this.snackBar.open('MR erstellt', 'Öffnen', { duration: 4000 }).onAction().subscribe(() => window.open(d.url, '_blank'));
        this.loadSession(this.sessionId!);
      },
      error: (e) => this.snackBar.open('Fehler: ' + (e?.error?.detail ?? e.message), '', { duration: 3000 }),
    });
  }

  saveOverview() {
    this.http.patch(`${environment.apiUrl}/workflow/${this.sessionId}`, {
      title: this.form.title,
      category: this.form.category,
      subcategory: this.form.subcategory,
      impact: this.form.impact,
      urgency: this.form.urgency,
      status: this.form.status,
      closure_code: this.form.closure_code,
      resolution_type: this.form.resolution_type,
      root_cause: this.form.root_cause,
    }).subscribe({
      next: () => { this.snackBar.open('Gespeichert', '', { duration: 2000 }); this.loadSession(this.sessionId!); },
    });
  }

  onImpactUrgencyChange() {
    if (this.form.impact && this.form.urgency) {
      this.http.patch(`${environment.apiUrl}/workflow/${this.sessionId}`, { impact: this.form.impact, urgency: this.form.urgency }).subscribe({
        next: () => this.loadSession(this.sessionId!),
      });
    }
  }

  postManualComment() {
    const text = this.manualComment.trim();
    if (!text || !this.sessionId) return;
    this.aiLoading.posting.set(true);
    this.manualCommentPosted.set(false);
    this.http.post<any>(`${environment.apiUrl}/workflow/${this.sessionId}/post-comment`, { comment: text })
      .subscribe({
        next: res => {
          this.aiLoading.posting.set(false);
          this.manualCommentPosted.set(true);
          this.manualComment = '';
          const key = res.jira_key ?? this.session()?.jira_key;
          this.snackBar.open(`Kommentar in ${key} gepostet`, '', { duration: 3000 });
          this.refreshJiraDetail();
        },
        error: () => {
          this.aiLoading.posting.set(false);
          this.snackBar.open('Fehler beim Posten', '', { duration: 3000 });
        },
      });
  }

  refreshJiraDetail() {
    const key = this.session()?.jira_key;
    if (!key) return;
    this.jiraRefreshing.set(true);
    this.http.get<any>(`${environment.apiUrl}/jira-view/issue/${key}`)
      .subscribe({
        next: d => { this.jiraDetail.set(d); this.jiraRefreshing.set(false); },
        error: () => this.jiraRefreshing.set(false),
      });
  }

  addNote() {
    if (!this.newNote.trim()) return;
    this.http.post<any>(`${environment.apiUrl}/workflow/${this.sessionId}/notes`, { content: this.newNote }).subscribe({
      next: res => { this.session.update(s => ({ ...s, work_notes: res.notes })); this.newNote = ''; },
    });
  }

  generateComment() {
    this.aiLoading.comment.set(true);
    this.commentPosted.set(false);
    this.http.post<any>(`${environment.apiUrl}/workflow/${this.sessionId}/generate-comment`, {
      comment_type: this.selectedCommentType(),
      additional_context: this.additionalContext.trim() || null,
    }).subscribe({
      next: res => { this.generatedComment.set(res.comment); this.aiLoading.comment.set(false); this.loadSession(this.sessionId!); },
      error: () => { this.aiLoading.comment.set(false); this.snackBar.open('Fehler beim Generieren', '', { duration: 3000 }); },
    });
  }

  generateResolution() {
    this.aiLoading.resolution.set(true);
    this.http.post<any>(`${environment.apiUrl}/workflow/${this.sessionId}/generate-resolution`, {
      root_cause: this.form.root_cause || null,
      resolution_type: this.form.resolution_type,
      closure_code: this.form.closure_code,
    }).subscribe({
      next: res => { this.generatedResolution.set(res.resolution); this.aiLoading.resolution.set(false); this.loadSession(this.sessionId!); },
      error: () => { this.aiLoading.resolution.set(false); this.snackBar.open('Fehler beim Generieren', '', { duration: 3000 }); },
    });
  }

  suggestSolution() {
    this.aiLoading.solution.set(true);
    this.http.post<any>(`${environment.apiUrl}/workflow/${this.sessionId}/suggest-solution`, { use_rag: true, use_web: true }).subscribe({
      next: res => { this.solutionData.set(res); this.aiLoading.solution.set(false); },
      error: () => { this.aiLoading.solution.set(false); this.snackBar.open('Fehler bei Lösungssuche', '', { duration: 3000 }); },
    });
  }

  autoCategorize() {
    this.aiLoading.categorize.set(true);
    this.http.post<any>(`${environment.apiUrl}/workflow/${this.sessionId}/auto-categorize`, {}).subscribe({
      next: res => {
        this.form.category = res.category ?? this.form.category;
        this.form.subcategory = res.subcategory ?? this.form.subcategory;
        this.form.impact = res.impact ?? this.form.impact;
        this.form.urgency = res.urgency ?? this.form.urgency;
        this.aiLoading.categorize.set(false);
        this.loadSession(this.sessionId!);
        this.snackBar.open('Kategorisierung übernommen', '', { duration: 2000 });
      },
      error: () => this.aiLoading.categorize.set(false),
    });
  }

  postCommentToJira() {
    const comment = this.generatedComment();
    if (!comment) return;
    this.aiLoading.posting.set(true);
    this.http.post<any>(`${environment.apiUrl}/workflow/${this.sessionId}/post-comment`, { comment }).subscribe({
      next: () => {
        this.aiLoading.posting.set(false);
        this.commentPosted.set(true);
        this.snackBar.open(`Kommentar in ${this.session()?.jira_key} gepostet`, 'OK', { duration: 3000 });
        this.loadSession(this.sessionId!);
      },
      error: (err) => {
        this.aiLoading.posting.set(false);
        this.snackBar.open(err?.error?.detail ?? 'Fehler beim Posten', '', { duration: 4000 });
      },
    });
  }

  copyToClipboard(text: string) {
    navigator.clipboard.writeText(text).then(() => this.snackBar.open('In Zwischenablage kopiert', '', { duration: 2000 }));
  }

  priorityColor() {
    return PRIORITY_META[this.session()?.priority ?? '']?.color ?? '#ccc';
  }

  statusLabel() {
    return STATUS_OPTIONS.find(s => s.value === this.session()?.status)?.label ?? this.session()?.status ?? '';
  }

  jiraUrl() {
    return this.session()?.jira_browse_url ?? null;
  }

  isSlaBreached(iso: string | null | undefined): boolean {
    if (!iso) return false;
    return new Date(iso) < new Date();
  }
}
