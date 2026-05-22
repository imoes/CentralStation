import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule, ReactiveFormsModule, FormBuilder, FormGroup, Validators } from '@angular/forms';
import { Router } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { MatStepperModule } from '@angular/material/stepper';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatChipsModule } from '@angular/material/chips';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { environment } from '../../../environments/environment';

@Component({
  selector: 'cs-setup-wizard',
  standalone: true,
  imports: [
    CommonModule, FormsModule, ReactiveFormsModule,
    MatStepperModule, MatButtonModule, MatCardModule, MatIconModule,
    MatFormFieldModule, MatInputModule, MatChipsModule,
    MatProgressSpinnerModule, MatSnackBarModule, MatSlideToggleModule,
  ],
  template: `
    <div class="wizard-container">
      <div class="wizard-header">
        <mat-icon class="logo-icon">hub</mat-icon>
        <h1>CentralStation</h1>
        <p>Einrichtungsassistent — wenige Schritte bis zur ersten Nutzung</p>
      </div>

      <mat-stepper [linear]="true" orientation="horizontal" #stepper class="wizard-stepper">

        <!-- Step 1: Welcome -->
        <mat-step label="Willkommen" [completed]="true">
          <div class="step-content">
            <mat-icon class="step-icon accent">waving_hand</mat-icon>
            <h2>Willkommen bei CentralStation</h2>
            <p class="step-desc">
              Dieses Dashboard aggregiert Alerts aus Wazuh, Graylog und CheckMK,
              synchronisiert Jira-Tickets und unterstützt Sie mit KI bei der ITIL-konformen
              Arbeitsdokumentation.
            </p>
            <ul class="feature-list">
              <li><mat-icon>notifications</mat-icon> Alert-Aggregation aus mehreren Quellen</li>
              <li><mat-icon>view_kanban</mat-icon> Kanban-Board mit bidirektionalem Jira-Sync</li>
              <li><mat-icon>psychology</mat-icon> KI-Assistent für Tickets und Kommentare</li>
              <li><mat-icon>assignment</mat-icon> ITIL Arbeitsdokumentation mit 5-Why-Analyse</li>
              <li><mat-icon>mail</mat-icon> O365 E-Mail-Integration in Workflow</li>
            </ul>
            <div class="step-actions">
              <button mat-flat-button color="primary" matStepperNext>
                Los geht's <mat-icon>arrow_forward</mat-icon>
              </button>
            </div>
          </div>
        </mat-step>

        <!-- Step 2: LLM Check -->
        <mat-step label="KI-Verbindung" [completed]="llmOk()">
          <div class="step-content">
            <mat-icon class="step-icon" [class.ok]="llmOk()" [class.warn]="!llmOk()">
              {{ llmOk() ? 'check_circle' : 'smart_toy' }}
            </mat-icon>
            <h2>KI-Modell überprüfen</h2>
            <p class="step-desc">
              CentralStation benötigt einen OpenAI-kompatiblen LLM-Endpunkt (z.B. Qwen über
              llama.cpp oder Ollama). Der Admin konfiguriert die URL unter Einstellungen → KI.
            </p>

            @if (llmChecking()) {
              <div class="check-row"><mat-spinner diameter="24"></mat-spinner> Prüfe LLM-Verbindung…</div>
            } @else if (llmOk()) {
              <div class="check-row ok"><mat-icon>check_circle</mat-icon> LLM erreichbar — KI-Funktionen verfügbar</div>
            } @else {
              <div class="check-row warn"><mat-icon>warning</mat-icon> LLM nicht konfiguriert — KI-Funktionen eingeschränkt</div>
              <p class="hint">Sie können trotzdem fortfahren. KI-Funktionen werden aktiviert, sobald der Admin den LLM-Endpunkt eingerichtet hat.</p>
            }

            <div class="step-actions">
              <button mat-stroked-button matStepperPrevious>Zurück</button>
              <button mat-flat-button color="primary" matStepperNext (click)="checkLlm()">
                {{ llmChecking() ? 'Prüfe…' : 'Weiter' }} <mat-icon>arrow_forward</mat-icon>
              </button>
            </div>
          </div>
        </mat-step>

        <!-- Step 3: Jira JQL -->
        <mat-step label="Meine Tickets" [completed]="jqlReady()">
          <div class="step-content">
            <mat-icon class="step-icon accent">filter_list</mat-icon>
            <h2>Ticket-Filter einrichten</h2>
            <p class="step-desc">
              Definieren Sie JQL-Filter für den "Meine Tickets"-Bereich.
              Standard-Filter wurden bereits vorkonfiguriert — Sie können diese anpassen oder
              per KI neue erstellen.
            </p>

            <div class="jql-list">
              @for (q of jqlQueries(); track q.id; let i = $index) {
                <div class="jql-item">
                  <mat-icon class="drag-handle">drag_indicator</mat-icon>
                  <div class="jql-fields">
                    <input class="jql-name-input" [(ngModel)]="q.name" placeholder="Name">
                    <input class="jql-input" [(ngModel)]="q.jql" placeholder="JQL">
                  </div>
                  <button mat-icon-button (click)="removeJql(i)"><mat-icon>delete</mat-icon></button>
                </div>
              }
            </div>

            <div class="jql-actions">
              <button mat-stroked-button (click)="addJql()">
                <mat-icon>add</mat-icon> Filter hinzufügen
              </button>
              <button mat-stroked-button (click)="openAiJqlDialog()" [disabled]="!llmOk()">
                <mat-icon>psychology</mat-icon> Per KI erstellen
              </button>
            </div>

            @if (showAiJql()) {
              <div class="ai-jql-box">
                <mat-form-field appearance="outline" class="full-width">
                  <mat-label>Beschreiben Sie den Filter auf Deutsch</mat-label>
                  <input matInput [(ngModel)]="aiJqlDesc" placeholder="z.B. meine offenen Bugs aus dieser Woche">
                </mat-form-field>
                <button mat-flat-button color="accent" (click)="generateJql()" [disabled]="aiJqlLoading()">
                  @if (aiJqlLoading()) { <mat-spinner diameter="16"></mat-spinner> }
                  KI generieren
                </button>
              </div>
            }

            <div class="step-actions">
              <button mat-stroked-button matStepperPrevious>Zurück</button>
              <button mat-flat-button color="primary" (click)="saveJqlAndNext(stepper)">
                Speichern & Weiter <mat-icon>arrow_forward</mat-icon>
              </button>
            </div>
          </div>
        </mat-step>

        <!-- Step 4: Done -->
        <mat-step label="Fertig">
          <div class="step-content center">
            <mat-icon class="step-icon ok big">celebration</mat-icon>
            <h2>Einrichtung abgeschlossen!</h2>
            <p class="step-desc">
              CentralStation ist bereit. Unter <strong>Einstellungen → Konnektoren</strong>
              können Admins weitere Systeme (CheckMK, Graylog, Wazuh, O365) anschließen.
            </p>
            <div class="step-actions">
              <button mat-flat-button color="primary" (click)="finish()">
                Dashboard öffnen <mat-icon>dashboard</mat-icon>
              </button>
            </div>
          </div>
        </mat-step>

      </mat-stepper>
    </div>
  `,
  styles: [`
    .wizard-container { min-height: 100vh; display: flex; flex-direction: column; align-items: center; padding: 32px 16px; background: var(--mat-sys-surface); }
    .wizard-header { text-align: center; margin-bottom: 32px; }
    .wizard-header .logo-icon { font-size: 48px; width: 48px; height: 48px; color: var(--mat-sys-primary); }
    .wizard-header h1 { margin: 8px 0 4px; font-size: 28px; }
    .wizard-header p { color: var(--mat-sys-on-surface-variant); margin: 0; }
    .wizard-stepper { width: 100%; max-width: 700px; }
    .step-content { padding: 24px 0; display: flex; flex-direction: column; gap: 16px; }
    .step-content.center { align-items: center; text-align: center; }
    .step-icon { font-size: 40px; width: 40px; height: 40px; color: var(--mat-sys-on-surface-variant); }
    .step-icon.ok { color: #4caf50; }
    .step-icon.warn { color: #ff9800; }
    .step-icon.accent { color: var(--mat-sys-primary); }
    .step-icon.big { font-size: 64px; width: 64px; height: 64px; }
    h2 { margin: 0; font-size: 20px; }
    .step-desc { color: var(--mat-sys-on-surface-variant); margin: 0; line-height: 1.6; }
    .feature-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; }
    .feature-list li { display: flex; align-items: center; gap: 8px; font-size: 14px; }
    .feature-list mat-icon { font-size: 18px; width: 18px; height: 18px; color: var(--mat-sys-primary); }
    .check-row { display: flex; align-items: center; gap: 8px; font-size: 14px; }
    .check-row.ok { color: #4caf50; }
    .check-row.warn { color: #ff9800; }
    .hint { font-size: 12px; color: var(--mat-sys-on-surface-variant); margin: 0; }
    .step-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 8px; }
    .jql-list { display: flex; flex-direction: column; gap: 8px; }
    .jql-item { display: flex; align-items: center; gap: 8px; padding: 8px; border: 1px solid var(--mat-sys-outline-variant); border-radius: 8px; }
    .drag-handle { color: var(--mat-sys-on-surface-variant); cursor: grab; }
    .jql-fields { flex: 1; display: flex; flex-direction: column; gap: 4px; }
    .jql-name-input { border: none; background: transparent; font-weight: 500; font-size: 13px; color: var(--mat-sys-on-surface); outline: none; }
    .jql-input { border: none; background: transparent; font-family: monospace; font-size: 11px; color: var(--mat-sys-on-surface-variant); outline: none; }
    .jql-actions { display: flex; gap: 8px; }
    .ai-jql-box { background: var(--mat-sys-surface-variant); border-radius: 8px; padding: 12px; display: flex; gap: 8px; align-items: flex-end; }
    .full-width { flex: 1; }
  `],
})
export class SetupWizardComponent implements OnInit {
  llmOk = signal(false);
  llmChecking = signal(false);
  jqlReady = signal(true);
  jqlQueries = signal<any[]>([]);
  showAiJql = signal(false);
  aiJqlLoading = signal(false);
  aiJqlDesc = '';

  constructor(
    private http: HttpClient,
    private router: Router,
    private snackBar: MatSnackBar,
  ) {}

  ngOnInit() {
    this.loadJqlQueries();
    this.checkLlm();
  }

  checkLlm() {
    this.llmChecking.set(true);
    this.http.get(`${environment.apiUrl}/settings`).subscribe({
      next: (data: any) => {
        this.llmOk.set(!!(data?.['llm.base_url'] && data?.['llm.model']));
        this.llmChecking.set(false);
      },
      error: () => this.llmChecking.set(false),
    });
  }

  loadJqlQueries() {
    this.http.get<any[]>(`${environment.apiUrl}/preferences/jira-queries`).subscribe({
      next: data => this.jqlQueries.set(data),
    });
  }

  addJql() {
    this.jqlQueries.update(q => [...q, { id: crypto.randomUUID(), name: 'Neue Query', jql: 'assignee = currentUser() ORDER BY updated DESC', _new: true }]);
  }

  removeJql(i: number) {
    const q = this.jqlQueries()[i];
    if (!q._new) {
      this.http.delete(`${environment.apiUrl}/preferences/jira-queries/${q.id}`).subscribe();
    }
    this.jqlQueries.update(qs => qs.filter((_, idx) => idx !== i));
  }

  openAiJqlDialog() {
    this.showAiJql.update(v => !v);
  }

  generateJql() {
    if (!this.aiJqlDesc.trim()) return;
    this.aiJqlLoading.set(true);
    this.http.post<any>(`${environment.apiUrl}/preferences/jira-queries/generate`, { description: this.aiJqlDesc }).subscribe({
      next: result => {
        this.jqlQueries.update(q => [...q, { id: crypto.randomUUID(), name: result.name, jql: result.jql, _new: true }]);
        this.aiJqlDesc = '';
        this.showAiJql.set(false);
        this.aiJqlLoading.set(false);
        this.snackBar.open(`JQL generiert: ${result.name}`, '', { duration: 3000 });
      },
      error: () => this.aiJqlLoading.set(false),
    });
  }

  saveJqlAndNext(stepper: any) {
    const saves = this.jqlQueries().map(q => {
      if (q._new) {
        return this.http.post(`${environment.apiUrl}/preferences/jira-queries`, { name: q.name, jql: q.jql }).toPromise();
      } else {
        return this.http.patch(`${environment.apiUrl}/preferences/jira-queries/${q.id}`, { name: q.name, jql: q.jql }).toPromise();
      }
    });
    Promise.all(saves).then(() => stepper.next());
  }

  finish() {
    this.http.patch(`${environment.apiUrl}/preferences`, { setup_completed: true }).subscribe({
      next: () => this.router.navigate(['/dashboard']),
      error: () => this.router.navigate(['/dashboard']),
    });
  }
}
