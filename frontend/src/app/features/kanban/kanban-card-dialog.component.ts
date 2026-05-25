import { Component, Inject, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, FormGroup, Validators, ReactiveFormsModule } from '@angular/forms';
import { FormsModule } from '@angular/forms';
import { TextFieldModule } from '@angular/cdk/text-field';
import { MatDialogModule, MatDialogRef, MAT_DIALOG_DATA } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatDividerModule } from '@angular/material/divider';
import { MatChipsModule } from '@angular/material/chips';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { KanbanService } from '../../core/services/kanban.service';
import { JiraComment, JiraDetail, KanbanCard, KanbanPriority, KanbanStatus } from '../../core/models/kanban.model';

@Component({
  selector: 'cs-kanban-card-dialog',
  standalone: true,
  imports: [
    CommonModule, ReactiveFormsModule, FormsModule, TextFieldModule,
    MatDialogModule, MatFormFieldModule, MatInputModule, MatSelectModule,
    MatButtonModule, MatProgressSpinnerModule, MatIconModule,
    MatTooltipModule, MatDividerModule, MatChipsModule, MatSnackBarModule,
  ],
  template: `
    <!-- ── Header ── -->
    <div class="dialog-header">
      <div class="header-left">
        @if (card?.jira_key) {
          <a class="jira-key-chip" [href]="jiraDetail()?.jira_browse_url" target="_blank"
             matTooltip="In Jira öffnen">
            {{ card!.jira_key }}<mat-icon class="ext-icon">open_in_new</mat-icon>
          </a>
        }
        <span class="dialog-title">{{ isEdit ? 'Ticket bearbeiten' : 'Neue Karte' }}</span>
      </div>
      <button mat-icon-button (click)="ref.close()"><mat-icon>close</mat-icon></button>
    </div>

    <mat-divider></mat-divider>

    <!-- ── Scrollable Content ── -->
    <div class="dialog-body">
      <form [formGroup]="form">

        <!-- Title -->
        <mat-form-field appearance="outline" class="full-width">
          <mat-label>Titel</mat-label>
          <input matInput formControlName="title" placeholder="Ticket-Titel">
        </mat-form-field>

        <!-- Status + Priority row -->
        <div class="meta-row">
          <mat-form-field appearance="outline" class="meta-field">
            <mat-label>Status</mat-label>
            <mat-select formControlName="status">
              <mat-option value="backlog">Backlog</mat-option>
              <mat-option value="todo">To Do</mat-option>
              <mat-option value="in_progress">In Arbeit</mat-option>
              <mat-option value="review">Review</mat-option>
              <mat-option value="done">Erledigt</mat-option>
            </mat-select>
          </mat-form-field>
          <mat-form-field appearance="outline" class="meta-field">
            <mat-label>Priorität</mat-label>
            <mat-select formControlName="priority">
              <mat-option value="low">Niedrig</mat-option>
              <mat-option value="medium">Mittel</mat-option>
              <mat-option value="high">Hoch</mat-option>
              <mat-option value="critical">Kritisch</mat-option>
            </mat-select>
          </mat-form-field>
          @if (card?.ai_generated) {
            <mat-chip class="ai-chip"><mat-icon>smart_toy</mat-icon>KI-generiert</mat-chip>
          }
        </div>

        <!-- Description -->
        <div class="section-label">Beschreibung</div>
        @if (card?.jira_key && jiraDetail()?.description) {
          <!-- Jira description read-only -->
          <div class="description-block">{{ jiraDetail()!.description }}</div>
        } @else {
          <mat-form-field appearance="outline" class="full-width">
            <textarea matInput formControlName="description"
                      cdkTextareaAutosize cdkAutosizeMinRows="4" cdkAutosizeMaxRows="12"
                      placeholder="Beschreibung..."></textarea>
          </mat-form-field>
        }
      </form>

      <!-- ── Jira Comments Section ── -->
      @if (isEdit && card?.jira_key) {
        <mat-divider class="section-divider"></mat-divider>

        <div class="section-header">
          <span class="section-label">Kommentare
            @if (jiraDetail()?.comments?.length) {
              <span class="comment-count">({{ jiraDetail()!.comments!.length }})</span>
            }
          </span>
          @if (loadingJira()) {
            <mat-spinner diameter="16"></mat-spinner>
          }
        </div>

        @if (jiraDetail()?.error) {
          <div class="jira-error">
            <mat-icon>warning</mat-icon> {{ jiraDetail()!.error }}
          </div>
        } @else if (!loadingJira()) {
          <!-- Comment list -->
          @if (jiraDetail()?.comments?.length) {
            <div class="comment-list">
              @for (c of jiraDetail()!.comments!; track c.id) {
                <div class="comment-item">
                  <div class="comment-meta">
                    <mat-icon class="avatar-icon">account_circle</mat-icon>
                    <span class="comment-author">{{ c.author }}</span>
                    <span class="comment-date">{{ c.created | date:'dd.MM.yyyy, HH:mm' }}</span>
                  </div>
                  <div class="comment-body">{{ c.body }}</div>
                </div>
              }
            </div>
          } @else if (jiraDetail()?.has_jira) {
            <div class="no-comments">Noch keine Kommentare.</div>
          }

        }
      }

      <!-- Jira-Sync for cards without jira_key -->
      @if (isEdit && !card?.jira_key) {
        <div class="jira-sync-row">
          <button mat-stroked-button (click)="syncToJira()" [disabled]="syncingJira()">
            @if (syncingJira()) { <mat-spinner diameter="16"></mat-spinner> }
            @else { <mat-icon>cloud_upload</mat-icon> }
            Als Jira-Ticket erstellen
          </button>
        </div>
      }
    </div>

    <!-- ── New Comment Footer (only when jira card, outside scroll) ── -->
    @if (isEdit && card?.jira_key && !loadingJira() && !jiraDetail()?.error) {
      <mat-divider></mat-divider>
      <div class="comment-footer">
        <div class="new-comment-box">
          <mat-form-field appearance="outline" class="full-width">
            <mat-label>Kommentar hinzufügen</mat-label>
            <textarea matInput [(ngModel)]="newComment"
                      cdkTextareaAutosize cdkAutosizeMinRows="2" cdkAutosizeMaxRows="5"
                      placeholder="Kommentar eingeben…"
                      (keydown.control.enter)="submitComment()"
                      (keydown.meta.enter)="submitComment()"></textarea>
            <mat-hint>Strg+Enter zum Senden</mat-hint>
          </mat-form-field>
          <button mat-flat-button color="accent" class="send-btn"
                  [disabled]="!newComment.trim() || sendingComment()"
                  (click)="submitComment()">
            @if (sendingComment()) {
              <mat-spinner diameter="16"></mat-spinner>
            } @else {
              <mat-icon>send</mat-icon>
            }
            Senden
          </button>
        </div>
      </div>
    }

    <!-- ── Actions ── -->
    <mat-divider></mat-divider>
    <div class="dialog-actions">
      <button mat-button (click)="ref.close()">Abbrechen</button>
      <button mat-flat-button color="primary" [disabled]="form.invalid || saving()" (click)="save()">
        @if (saving()) { <mat-spinner diameter="18"></mat-spinner> }
        @else { <ng-container><mat-icon>save</mat-icon> Speichern</ng-container> }
      </button>
    </div>
  `,
  styles: [`
    /* Layout */
    :host { display: flex; flex-direction: column; max-height: 90vh; }
    .dialog-header { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px 12px 20px; flex-shrink: 0; gap: 12px; }
    .header-left { display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0; }
    .dialog-title { font-size: 16px; font-weight: 500; }
    .dialog-body { flex: 1; overflow-y: auto; padding: 16px 20px; display: flex; flex-direction: column; gap: 4px; }
    .dialog-actions { display: flex; justify-content: flex-end; align-items: center; gap: 8px; padding: 12px 16px; flex-shrink: 0; }

    /* Jira key chip */
    .jira-key-chip { display: inline-flex; align-items: center; gap: 3px; padding: 3px 8px; background: #0052cc18; color: #0052cc; border-radius: 4px; font-family: monospace; font-size: 12px; font-weight: 600; text-decoration: none; border: 1px solid #0052cc30; transition: background .15s; cursor: pointer; }
    .jira-key-chip:hover { background: #0052cc28; }
    .ext-icon { font-size: 12px; width: 12px; height: 12px; }

    /* Form */
    .full-width { width: 100%; }
    .meta-row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 4px; }
    .meta-field { flex: 1; min-width: 120px; }
    .ai-chip { font-size: 11px; min-height: 28px; background: var(--mat-sys-tertiary-container); }
    .ai-chip mat-icon { font-size: 14px; width: 14px; height: 14px; }

    /* Sections */
    .section-label { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .08em; color: var(--mat-sys-on-surface-variant); margin-bottom: 6px; }
    .section-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
    .comment-count { font-size: 11px; font-weight: 400; margin-left: 4px; }
    .section-divider { margin: 12px 0; }

    /* Description */
    .description-block { white-space: pre-wrap; font-size: 13px; line-height: 1.6; padding: 12px; background: var(--mat-sys-surface-variant); border-radius: 6px; max-height: 200px; overflow-y: auto; color: var(--mat-sys-on-surface); margin-bottom: 8px; }

    /* Comments */
    .comment-list { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; }
    .comment-item { background: var(--mat-sys-surface-container); border-radius: 8px; padding: 10px 12px; }
    .comment-meta { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
    .avatar-icon { font-size: 18px; width: 18px; height: 18px; color: var(--mat-sys-primary); }
    .comment-author { font-weight: 600; font-size: 12px; }
    .comment-date { font-size: 11px; color: var(--mat-sys-on-surface-variant); margin-left: auto; }
    .comment-body { font-size: 13px; white-space: pre-wrap; line-height: 1.5; }
    .no-comments { text-align: center; padding: 12px; font-size: 13px; color: var(--mat-sys-on-surface-variant); }
    .jira-error { display: flex; align-items: center; gap: 6px; color: #c62828; font-size: 13px; padding: 8px 0; }

    /* Comment footer */
    .comment-footer { padding: 12px 20px 4px; flex-shrink: 0; background: var(--mat-sys-surface-container-low); }
    .new-comment-box { display: flex; gap: 8px; align-items: flex-start; }
    .new-comment-box .full-width { flex: 1; }
    .send-btn { margin-top: 4px; flex-shrink: 0; }

    /* Misc */
    .jira-sync-row { padding: 8px 0; }
    mat-spinner { display: inline-block; }
  `],
})
export class KanbanCardDialogComponent implements OnInit {
  isEdit: boolean;
  card: KanbanCard | undefined;
  form!: FormGroup;
  saving = signal(false);
  syncingJira = signal(false);
  loadingJira = signal(false);
  sendingComment = signal(false);
  jiraDetail = signal<JiraDetail | null>(null);
  newComment = '';

  constructor(
    private fb: FormBuilder,
    private svc: KanbanService,
    public ref: MatDialogRef<KanbanCardDialogComponent>,
    private snack: MatSnackBar,
    @Inject(MAT_DIALOG_DATA) public data: { card?: KanbanCard } | null,
  ) {
    this.isEdit = !!data?.card;
    this.card = data?.card;
  }

  ngOnInit() {
    const c = this.card;
    this.form = this.fb.group({
      title:       [c?.title ?? '', Validators.required],
      description: [c?.description ?? ''],
      status:      [c?.status ?? 'backlog'],
      priority:    [c?.priority ?? 'medium'],
    });

    if (this.isEdit && this.card?.jira_key) {
      this.loadJiraDetail();
    }
  }

  loadJiraDetail() {
    if (!this.card) return;
    this.loadingJira.set(true);
    this.svc.getJiraDetail(this.card.id).subscribe({
      next: detail => { this.jiraDetail.set(detail); this.loadingJira.set(false); },
      error: () => {
        this.jiraDetail.set({ has_jira: true, error: 'Jira-Details konnten nicht geladen werden' });
        this.loadingJira.set(false);
      },
    });
  }

  save() {
    if (this.form.invalid) return;
    this.saving.set(true);
    const v = this.form.value;

    if (this.isEdit && this.card) {
      this.svc.update(this.card.id, {
        title:       v.title,
        description: v.description || undefined,
        priority:    v.priority as KanbanPriority,
      }).subscribe({
        next: () => { this.saving.set(false); this.ref.close(true); },
        error: err => { this.saving.set(false); this.snack.open(err?.error?.detail ?? 'Speichern fehlgeschlagen', 'OK', { duration: 4000 }); },
      });
    } else {
      this.svc.create({
        title:       v.title,
        description: v.description || undefined,
        status:      v.status as KanbanStatus,
        priority:    v.priority as KanbanPriority,
      }).subscribe({
        next: () => { this.saving.set(false); this.ref.close(true); },
        error: err => { this.saving.set(false); this.snack.open(err?.error?.detail ?? 'Erstellen fehlgeschlagen', 'OK', { duration: 4000 }); },
      });
    }
  }

  submitComment() {
    const body = this.newComment.trim();
    if (!body || !this.card) return;
    this.sendingComment.set(true);
    this.svc.addJiraComment(this.card.id, body).subscribe({
      next: comment => {
        this.newComment = '';
        this.sendingComment.set(false);
        this.jiraDetail.update(d => d ? {
          ...d,
          comments: [...(d.comments ?? []), comment],
        } : d);
        this.snack.open('Kommentar hinzugefügt', '', { duration: 2000 });
      },
      error: err => {
        this.sendingComment.set(false);
        this.snack.open(err?.error?.detail ?? 'Kommentar fehlgeschlagen', 'OK', { duration: 4000 });
      },
    });
  }

  syncToJira() {
    if (!this.card) return;
    this.syncingJira.set(true);
    this.svc.syncJira(this.card.id).subscribe({
      next: res => {
        this.syncingJira.set(false);
        if (this.card) this.card = { ...this.card, jira_key: res.jira_key };
        this.snack.open(`Jira-Ticket erstellt: ${res.jira_key}`, 'OK', { duration: 4000 });
        this.ref.close(true);
      },
      error: err => {
        this.syncingJira.set(false);
        this.snack.open(err?.error?.detail ?? 'Jira-Sync fehlgeschlagen', 'OK', { duration: 4000 });
      },
    });
  }
}
