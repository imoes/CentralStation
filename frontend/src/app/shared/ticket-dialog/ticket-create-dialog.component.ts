import { Component, Inject, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import {
  MatDialogModule, MatDialogRef, MAT_DIALOG_DATA,
} from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatIconModule } from '@angular/material/icon';
import { MatSnackBar } from '@angular/material/snack-bar';
import { environment } from '../../../environments/environment';
import { ThemeService } from '../../core/services/theme.service';

/** How the dialog was opened — drives where the AI draft context comes from. */
export interface TicketDialogData {
  mode: 'feed' | 'computer';
  feedExternalId?: string;
  transcript?: string;       // Computer-Console conversation transcript
  host?: string;
  severity?: string;
}

interface TicketTarget {
  connector_type: string;
  label: string;
  projects: { key: string; name: string }[];
  default_project: string;
  issue_types: string[];
  default_issue_type: string;
}
interface TargetsResponse {
  targets: TicketTarget[];
  default_connector: string;
  priorities: string[];
}
interface DraftResponse { summary: string; description: string; priority: string; }
interface CreateResponse { ok: boolean; jira_key?: string; url?: string | null; }

@Component({
  selector: 'app-ticket-create-dialog',
  standalone: true,
  imports: [
    CommonModule, FormsModule, MatDialogModule, MatButtonModule,
    MatFormFieldModule, MatInputModule, MatSelectModule,
    MatProgressSpinnerModule, MatIconModule,
  ],
  template: `
    <div class="tkt-dialog"
         [class.t-classic]="theme()==='classic'"
         [class.t-lcars]="theme()==='lcars'"
         [class.t-holo]="theme()==='holo'">

      <div class="tkt-head">
        <mat-icon class="tkt-head-icon">confirmation_number</mat-icon>
        <div class="tkt-head-text">
          <span class="tkt-title">Ticket erstellen</span>
          <span class="tkt-sub">KI-vorausgefüllt · Ziel wählen, prüfen & anpassen</span>
        </div>
        <button class="tkt-x" (click)="close()" aria-label="Schließen">✕</button>
      </div>

      @if (loadError()) {
        <div class="tkt-error">{{ loadError() }}</div>
      }

      <div class="tkt-body">
        <!-- Target: Jira / Service Desk -->
        <label class="tkt-label">Ziel-System</label>
        <div class="tkt-targets">
          @for (t of targets(); track t.connector_type) {
            <button class="tkt-target-btn"
                    [class.active]="t.connector_type === connectorType()"
                    [disabled]="creating()"
                    (click)="selectConnector(t.connector_type)">
              {{ t.label }}
            </button>
          }
        </div>

        <div class="tkt-row">
          <div class="tkt-field">
            <label class="tkt-label">Projekt</label>
            <select class="tkt-input" [(ngModel)]="project" [disabled]="creating()">
              @for (p of projects(); track p.key) {
                <option [value]="p.key">{{ p.key }} — {{ p.name }}</option>
              }
            </select>
          </div>
          <div class="tkt-field">
            <label class="tkt-label">Typ</label>
            <select class="tkt-input" [(ngModel)]="issueType" [disabled]="creating()">
              @for (it of issueTypes(); track it) {
                <option [value]="it">{{ it }}</option>
              }
            </select>
          </div>
          <div class="tkt-field">
            <label class="tkt-label">Priorität</label>
            <select class="tkt-input" [(ngModel)]="priority" [disabled]="creating()">
              @for (p of priorities(); track p) {
                <option [value]="p">{{ p }}</option>
              }
            </select>
          </div>
        </div>

        <label class="tkt-label">Zusammenfassung</label>
        <input class="tkt-input" [(ngModel)]="summary" maxlength="200"
               [disabled]="creating()" placeholder="Kurzer Titel" />

        <label class="tkt-label">
          Beschreibung
          @if (loadingDraft()) {
            <span class="tkt-drafting"><mat-spinner diameter="13"></mat-spinner> KI formuliert…</span>
          }
        </label>
        <textarea class="tkt-input tkt-textarea" [(ngModel)]="description" rows="10"
                  [disabled]="creating()" placeholder="Beschreibung (Jira-Markup)"></textarea>
      </div>

      <div class="tkt-actions">
        <button class="tkt-cancel" (click)="close()" [disabled]="creating()">Abbrechen</button>
        <button class="tkt-submit" (click)="create()"
                [disabled]="creating() || loadingDraft() || !summary().trim() || !project()">
          @if (creating()) {
            <mat-spinner diameter="16"></mat-spinner> Erstelle…
          } @else {
            Ticket erstellen
          }
        </button>
      </div>
    </div>
  `,
  styleUrl: './ticket-create-dialog.component.scss',
})
export class TicketCreateDialogComponent implements OnInit {
  private http = inject(HttpClient);
  private snack = inject(MatSnackBar);
  private themeService = inject(ThemeService);
  theme = this.themeService.theme;

  targets = signal<TicketTarget[]>([]);
  priorities = signal<string[]>(['Kritisch', 'Hoch', 'Normal', 'Niedrig']);
  connectorType = signal<string>('jira_sd');

  project = signal<string>('');
  priority = signal<string>('Normal');
  issueType = signal<string>('Serviceanfrage');
  summary = signal<string>('');
  description = signal<string>('');

  issueTypes = computed(() =>
    this.targets().find(t => t.connector_type === this.connectorType())?.issue_types ?? []
  );

  loadingDraft = signal<boolean>(false);
  creating = signal<boolean>(false);
  loadError = signal<string>('');

  /** Projects of the currently selected connector. */
  projects = computed(() =>
    this.targets().find(t => t.connector_type === this.connectorType())?.projects ?? []
  );

  constructor(
    public dialogRef: MatDialogRef<TicketCreateDialogComponent>,
    @Inject(MAT_DIALOG_DATA) public data: TicketDialogData,
  ) {}

  ngOnInit(): void {
    this.loadTargets();
    this.loadDraft();
  }

  private loadTargets(): void {
    this.http.get<TargetsResponse>(`${environment.apiUrl}/tickets/targets`).subscribe({
      next: r => {
        this.targets.set(r.targets || []);
        if (r.priorities?.length) this.priorities.set(r.priorities);
        const pick = r.targets?.find(t => t.connector_type === r.default_connector)
          ?? r.targets?.[0];
        if (pick) {
          this.connectorType.set(pick.connector_type);
          this.project.set(pick.default_project || pick.projects[0]?.key || '');
          this.issueType.set(pick.default_issue_type || pick.issue_types?.[0] || '');
        }
      },
      error: () => this.loadError.set('Keine Ticket-Ziele verfügbar — Jira/Service-Desk-Connector prüfen.'),
    });
  }

  private loadDraft(): void {
    this.loadingDraft.set(true);
    const body = {
      feed_external_id: this.data.feedExternalId,
      transcript: this.data.transcript,
      host: this.data.host,
      severity: this.data.severity,
    };
    this.http.post<DraftResponse>(`${environment.apiUrl}/tickets/draft`, body).subscribe({
      next: d => {
        this.summary.set(d.summary || '');
        this.description.set(d.description || '');
        if (d.priority) this.priority.set(d.priority);
        this.loadingDraft.set(false);
      },
      error: () => this.loadingDraft.set(false),
    });
  }

  selectConnector(ct: string): void {
    if (ct === this.connectorType()) return;
    this.connectorType.set(ct);
    const t = this.targets().find(x => x.connector_type === ct);
    this.project.set(t?.default_project || t?.projects[0]?.key || '');
    this.issueType.set(t?.default_issue_type || t?.issue_types?.[0] || '');
  }

  create(): void {
    this.creating.set(true);
    this.loadError.set('');
    const body = {
      connector_type: this.connectorType(),
      project: this.project(),
      summary: this.summary().trim(),
      description: this.description().trim(),
      priority: this.priority(),
      issue_type: this.issueType(),
      feed_external_id: this.data.feedExternalId,
    };
    this.http.post<CreateResponse>(`${environment.apiUrl}/tickets/create`, body).subscribe({
      next: res => {
        this.creating.set(false);
        if (res.ok && res.url) {
          window.open(res.url, '_blank', 'noopener');
        }
        this.snack.open(`Ticket erstellt: ${res.jira_key ?? ''}`, 'OK', { duration: 5000 });
        this.dialogRef.close(res);
      },
      error: err => {
        this.creating.set(false);
        this.loadError.set(err?.error?.detail || 'Ticket konnte nicht erstellt werden.');
      },
    });
  }

  close(): void {
    this.dialogRef.close();
  }
}
