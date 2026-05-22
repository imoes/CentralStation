import { Component, Inject, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, FormGroup, Validators, ReactiveFormsModule } from '@angular/forms';
import { MatDialogModule, MatDialogRef, MAT_DIALOG_DATA } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';
import { MatIconModule } from '@angular/material/icon';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { KanbanService } from '../../core/services/kanban.service';
import { KanbanCard, KanbanStatus, KanbanPriority } from '../../core/models/kanban.model';

@Component({
  selector: 'cs-kanban-card-dialog',
  standalone: true,
  imports: [
    CommonModule, ReactiveFormsModule,
    MatDialogModule, MatFormFieldModule, MatInputModule,
    MatSelectModule, MatButtonModule, MatProgressSpinnerModule,
    MatChipsModule, MatIconModule, MatSnackBarModule,
  ],
  template: `
    <h2 mat-dialog-title>{{ isEdit ? 'Karte bearbeiten' : 'Neue Karte' }}</h2>
    <mat-dialog-content>
      <form [formGroup]="form" class="form-col">
        <mat-form-field appearance="outline">
          <mat-label>Titel</mat-label>
          <input matInput formControlName="title">
        </mat-form-field>

        <mat-form-field appearance="outline">
          <mat-label>Beschreibung</mat-label>
          <textarea matInput formControlName="description" rows="4"></textarea>
        </mat-form-field>

        <div class="row-fields">
          <mat-form-field appearance="outline">
            <mat-label>Status</mat-label>
            <mat-select formControlName="status">
              <mat-option value="backlog">Backlog</mat-option>
              <mat-option value="todo">To Do</mat-option>
              <mat-option value="in_progress">In Arbeit</mat-option>
              <mat-option value="review">Review</mat-option>
              <mat-option value="done">Erledigt</mat-option>
            </mat-select>
          </mat-form-field>

          <mat-form-field appearance="outline">
            <mat-label>Priorität</mat-label>
            <mat-select formControlName="priority">
              <mat-option value="low">Niedrig</mat-option>
              <mat-option value="medium">Mittel</mat-option>
              <mat-option value="high">Hoch</mat-option>
              <mat-option value="critical">Kritisch</mat-option>
            </mat-select>
          </mat-form-field>
        </div>

        @if (isEdit && card?.jira_key) {
          <div class="jira-info">
            <mat-icon>link</mat-icon>
            <span>Jira: <strong>{{ card!.jira_key }}</strong></span>
          </div>
        }

        @if (isEdit && !card?.jira_key) {
          <button mat-stroked-button (click)="syncToJira()" [disabled]="syncingJira()">
            @if (syncingJira()) {
              <mat-spinner diameter="16"></mat-spinner>
            } @else {
              <mat-icon>cloud_upload</mat-icon>
            }
            Als Jira-Ticket erstellen
          </button>
        }
      </form>
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button mat-dialog-close>Abbrechen</button>
      <button mat-raised-button color="primary" [disabled]="form.invalid || saving()" (click)="save()">
        @if (saving()) {
          <mat-spinner diameter="18"></mat-spinner>
        } @else {
          Speichern
        }
      </button>
    </mat-dialog-actions>
  `,
  styles: [`
    .form-col { display: flex; flex-direction: column; gap: 8px; min-width: 440px; padding-top: 8px; }
    mat-form-field { width: 100%; }
    .row-fields { display: flex; gap: 12px; }
    .row-fields mat-form-field { flex: 1; }
    .jira-info { display: flex; align-items: center; gap: 6px; padding: 8px 0; }
    mat-spinner { display: inline-block; }
  `],
})
export class KanbanCardDialogComponent implements OnInit {
  isEdit: boolean;
  card: KanbanCard | undefined;
  form!: FormGroup;
  saving = signal(false);
  syncingJira = signal(false);

  constructor(
    private fb: FormBuilder,
    private svc: KanbanService,
    private ref: MatDialogRef<KanbanCardDialogComponent>,
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
  }

  save() {
    if (this.form.invalid) return;
    this.saving.set(true);
    const v = this.form.value;

    if (this.isEdit && this.card) {
      this.svc.update(this.card.id, {
        title: v.title,
        description: v.description || undefined,
        priority: v.priority,
      }).subscribe({
        next: () => { this.saving.set(false); this.ref.close(true); },
        error: (err) => {
          this.saving.set(false);
          this.snack.open(err?.error?.detail ?? 'Speichern fehlgeschlagen', 'OK', { duration: 4000 });
        },
      });
    } else {
      this.svc.create({
        title: v.title,
        description: v.description || undefined,
        status: v.status,
        priority: v.priority,
      }).subscribe({
        next: () => { this.saving.set(false); this.ref.close(true); },
        error: (err) => {
          this.saving.set(false);
          this.snack.open(err?.error?.detail ?? 'Speichern fehlgeschlagen', 'OK', { duration: 4000 });
        },
      });
    }
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
      error: (err) => {
        this.syncingJira.set(false);
        this.snack.open(err?.error?.detail ?? 'Jira-Sync fehlgeschlagen', 'OK', { duration: 4000 });
      },
    });
  }
}
