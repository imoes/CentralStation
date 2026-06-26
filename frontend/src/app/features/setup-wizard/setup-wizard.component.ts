import { Component, OnInit, signal, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule, ReactiveFormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { I18nService } from '../../core/services/i18n.service';
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
import { ConnectorService } from '../../core/services/connector.service';
import { ConnectorType } from '../../core/models/connector.model';

interface ConnectorField {
  key: string;
  label: string;
  type: 'text' | 'password';
  placeholder?: string;
}

interface PersonalConnectorMeta {
  type: ConnectorType;
  title: string;
  description: string;
  baseUrlLabel: string;
  baseUrlPlaceholder: string;
  credentials: ConnectorField[];
}

interface PersonalConnectorState {
  type: ConnectorType;
  title: string;
  description: string;
  baseUrlLabel: string;
  baseUrlPlaceholder: string;
  base_url: string;
  name: string;
  enabled: boolean;
  configured: boolean;
  saving: boolean;
  testing: boolean;
  values: Record<string, string>;
}

const PERSONAL_CONNECTORS: PersonalConnectorMeta[] = [
  {
    type: 'checkmk',
    title: 'CheckMK',
    description: 'Persoenlicher Monitoring-Zugriff fuer Hosts, Services und Probleme.',
    baseUrlLabel: 'CheckMK URL',
    baseUrlPlaceholder: 'https://checkmk.example.local',
    credentials: [
      { key: 'username', label: 'Benutzername', type: 'text' },
      { key: 'password', label: 'Passwort', type: 'password' },
    ],
  },
  {
    type: 'graylog',
    title: 'Graylog',
    description: 'Persoenlicher Zugriff auf Log-Suchen und relevante Meldungen.',
    baseUrlLabel: 'Graylog URL',
    baseUrlPlaceholder: 'https://graylog.example.local',
    credentials: [
      { key: 'username', label: 'Benutzername', type: 'text' },
      { key: 'password', label: 'Passwort', type: 'password' },
    ],
  },
  {
    type: 'wazuh',
    title: 'Wazuh',
    description: 'Persoenlicher SIEM-Zugriff fuer Security-Alerts und Agentenstatus.',
    baseUrlLabel: 'Wazuh URL',
    baseUrlPlaceholder: 'https://wazuh.example.local',
    credentials: [
      { key: 'username', label: 'Benutzername', type: 'text' },
      { key: 'password', label: 'Passwort', type: 'password' },
    ],
  },
  {
    type: 'o365',
    title: 'O365',
    description: 'Microsoft Graph fuer Postfach-Abruf und Mail-Analyse.',
    baseUrlLabel: 'Graph Basis-URL (optional)',
    baseUrlPlaceholder: 'Leer lassen fuer Microsoft Graph Standard',
    credentials: [
      { key: 'tenant_id', label: 'Tenant ID', type: 'text' },
      { key: 'client_id', label: 'Client ID', type: 'text' },
      { key: 'client_secret', label: 'Client Secret', type: 'password' },
      { key: 'mailbox', label: 'Postfach (UPN)', type: 'text', placeholder: 'shared-mailbox@example.com' },
    ],
  },
  {
    type: 'jira',
    title: 'Jira',
    description: 'Persoenlicher Zugriff auf Jira-Software-Tickets fuer Board und Filter.',
    baseUrlLabel: 'Jira URL',
    baseUrlPlaceholder: 'https://jira.example.local',
    credentials: [
      { key: 'token', label: 'Personal Access Token', type: 'password' },
      { key: 'project', label: 'Standardprojekt (optional)', type: 'text', placeholder: 'IMIT' },
    ],
  },
  {
    type: 'jira_sd',
    title: 'Jira ServiceDesk',
    description: 'Persoenlicher Zugriff auf ServiceDesk-Tickets fuer das Kanban-Board.',
    baseUrlLabel: 'ServiceDesk URL',
    baseUrlPlaceholder: 'https://jira.example.local',
    credentials: [
      { key: 'token', label: 'Personal Access Token', type: 'password' },
      { key: 'project', label: 'Standardprojekt / Queue (optional)', type: 'text', placeholder: 'SD' },
    ],
  },
  {
    type: 'teams',
    title: 'Teams',
    description: 'Microsoft Graph fuer Teams und Kanaele im persoenlichen Feed.',
    baseUrlLabel: 'Graph Basis-URL (optional)',
    baseUrlPlaceholder: 'Leer lassen fuer Microsoft Graph Standard',
    credentials: [
      { key: 'tenant_id', label: 'Tenant ID', type: 'text' },
      { key: 'client_id', label: 'Client ID', type: 'text' },
      { key: 'client_secret', label: 'Client Secret', type: 'password' },
    ],
  },
];

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
              <button mat-stroked-button matStepperPrevious>{{ i18n.t('common.back') }}</button>
              <button mat-flat-button color="primary" matStepperNext (click)="checkLlm()">
                {{ llmChecking() ? '…' : i18n.t('common.next') }} <mat-icon>arrow_forward</mat-icon>
              </button>
            </div>
          </div>
        </mat-step>

        <!-- Step 3: Jira JQL -->
        <mat-step label="Meine Konnektoren" [completed]="connectorsLoaded()">
          <div class="step-content">
            <mat-icon class="step-icon accent">cable</mat-icon>
            <h2>Persoenliche Konnektoren einrichten</h2>
            <p class="step-desc">
              Hinterlegen Sie hier Ihre eigenen Zugaenge fuer CheckMK, Graylog, Wazuh sowie
              O365 und Teams. Diese Konfiguration gilt nur fuer Ihr Benutzerkonto.
            </p>

            @if (connectorsLoading()) {
              <div class="check-row"><mat-spinner diameter="24"></mat-spinner> Lade Ihre Konnektoren…</div>
            } @else {
              <div class="connector-grid">
                @for (connector of connectorStates(); track connector.type) {
                  <mat-card class="connector-card">
                    <div class="connector-card-header">
                      <div>
                        <h3>{{ connector.title }}</h3>
                        <p class="hint">{{ connector.description }}</p>
                      </div>
                      <mat-chip-set>
                        <mat-chip [class.ok-chip]="connector.configured">
                          {{ connector.configured ? 'Gespeichert' : 'Nicht konfiguriert' }}
                        </mat-chip>
                      </mat-chip-set>
                    </div>

                    <div class="connector-fields">
                      <mat-form-field appearance="outline" class="full-width">
                        <mat-label>Name</mat-label>
                        <input matInput
                               [ngModel]="connector.name"
                               (ngModelChange)="updateConnectorField(connector.type, 'name', $event)"
                               [placeholder]="connector.title">
                      </mat-form-field>

                      <mat-form-field appearance="outline" class="full-width">
                        <mat-label>{{ connector.baseUrlLabel || 'Basis-URL' }}</mat-label>
                        <input matInput
                               [ngModel]="connector.base_url"
                               (ngModelChange)="updateConnectorField(connector.type, 'base_url', $event)"
                               [placeholder]="connector.baseUrlPlaceholder">
                      </mat-form-field>

                      @for (field of fieldsFor(connector.type); track field.key) {
                        <mat-form-field appearance="outline" class="full-width">
                          <mat-label>{{ field.label }}</mat-label>
                          <input matInput
                                 [type]="field.type"
                                 [ngModel]="connector.values[field.key] || ''"
                                 (ngModelChange)="updateConnectorCredential(connector.type, field.key, $event)"
                                 [placeholder]="field.placeholder || ''">
                        </mat-form-field>
                      }

                      @if (connector.type === 'o365') {
                        <mat-form-field appearance="outline" class="full-width">
                          <mat-label>Mail-Ordner</mat-label>
                          <input matInput [(ngModel)]="o365Folder" placeholder="Inbox">
                        </mat-form-field>
                      }

                      @if (connector.type === 'teams') {
                        <mat-form-field appearance="outline" class="full-width">
                          <mat-label>Teams-Kanaele (eine Zeile = team_id:channel_id)</mat-label>
                          <textarea matInput [(ngModel)]="teamsChannelsText" rows="4"
                                    placeholder="team-id:channel-id"></textarea>
                        </mat-form-field>
                      }

                      <mat-slide-toggle
                        [ngModel]="connector.enabled"
                        (ngModelChange)="updateConnectorToggle(connector.type, $event)">
                        Aktiviert
                      </mat-slide-toggle>
                    </div>

                    <div class="connector-actions">
                      <button mat-stroked-button
                              (click)="testPersonalConnector(connector.type)"
                              [disabled]="connector.testing || !connector.configured">
                        @if (connector.testing) { <mat-spinner diameter="16"></mat-spinner> }
                        Verbindung testen
                      </button>
                      <button mat-flat-button color="primary"
                              (click)="savePersonalConnector(connector.type)"
                              [disabled]="connector.saving">
                        @if (connector.saving) { <mat-spinner diameter="16"></mat-spinner> }
                        Speichern
                      </button>
                    </div>
                  </mat-card>
                }
              </div>
            }

            <div class="step-actions">
              <button mat-stroked-button matStepperPrevious>Zurueck</button>
              <button mat-flat-button color="primary" matStepperNext>
                Weiter <mat-icon>arrow_forward</mat-icon>
              </button>
            </div>
          </div>
        </mat-step>

        <!-- Step 4: Jira JQL -->
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
                <mat-icon>add</mat-icon> {{ i18n.t('common.add') }}
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
              <button mat-stroked-button matStepperPrevious>{{ i18n.t('common.back') }}</button>
              <button mat-flat-button color="primary" (click)="saveJqlAndNext(stepper)">
                Speichern & Weiter <mat-icon>arrow_forward</mat-icon>
              </button>
            </div>
          </div>
        </mat-step>

        <!-- Step 5: Computer Console Agent -->
        <mat-step label="{{ i18n.t('setup.computer_agent.title') }}" [completed]="true">
          <div class="step-content">
            <mat-icon class="step-icon accent">terminal</mat-icon>
            <h2>{{ i18n.t('setup.computer_agent.title') }}</h2>
            <p class="step-desc">{{ i18n.t('setup.computer_agent.subtitle') }}</p>
            <p class="step-desc">{{ i18n.t('console.agent_section_hint') }}</p>
            <div class="agent-choice-cards">
              <div class="agent-choice" [class.selected]="wizardAgent === 'hermes'" (click)="wizardAgent = 'hermes'">
                <mat-icon>memory</mat-icon>
                <span>{{ i18n.t('console.agent_hermes') }}</span>
                <small>{{ i18n.t('console.agent_hermes_desc') }}</small>
              </div>
              <div class="agent-choice" [class.selected]="wizardAgent === 'claude_cli'" (click)="wizardAgent = 'claude_cli'">
                <mat-icon>smart_toy</mat-icon>
                <span>{{ i18n.t('console.agent_claude') }}</span>
                <small>{{ i18n.t('console.agent_claude_desc') }}</small>
              </div>
              <div class="agent-choice" [class.selected]="wizardAgent === 'codex_cli'" (click)="wizardAgent = 'codex_cli'">
                <mat-icon>code</mat-icon>
                <span>{{ i18n.t('console.agent_codex') }}</span>
                <small>{{ i18n.t('console.agent_codex_desc') }}</small>
              </div>
            </div>
            @if (wizardAgent !== 'hermes') {
              <p class="step-desc" style="font-style:italic">
                Die OAuth-Verbindung richtest du unter <strong>Einstellungen → Konsole</strong> ein.
              </p>
            }
            <div class="step-actions">
              <button mat-stroked-button matStepperPrevious>Zurück</button>
              <button mat-flat-button color="primary" (click)="saveWizardAgent(stepper)">
                {{ i18n.t('setup.next') }} <mat-icon>arrow_forward</mat-icon>
              </button>
            </div>
          </div>
        </mat-step>

        <!-- Step 6: Done -->
        <mat-step label="Fertig">
          <div class="step-content center">
            <mat-icon class="step-icon ok big">celebration</mat-icon>
            <h2>Einrichtung abgeschlossen!</h2>
            <p class="step-desc">
              CentralStation ist bereit. Ihre persoenlichen Konnektoren und Ticket-Filter
              koennen Sie spaeter jederzeit anpassen.
            </p>
            <div class="step-actions">
              <button mat-flat-button color="primary" (click)="finish()">
                {{ i18n.t('app.nav.dashboard') }} <mat-icon>dashboard</mat-icon>
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
    .connector-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }
    .connector-card { padding: 16px; }
    .connector-card-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
    .connector-card-header h3 { margin: 0 0 4px; font-size: 16px; }
    .connector-fields { display: flex; flex-direction: column; gap: 8px; }
    .connector-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px; }
    .ok-chip { background: color-mix(in srgb, #4caf50 18%, transparent); color: #2e7d32; }
    .agent-choice-cards { display: flex; flex-direction: column; gap: 10px; }
    .agent-choice {
      display: flex; align-items: flex-start; gap: 12px;
      padding: 12px 16px; border: 1px solid var(--mat-sys-outline-variant);
      border-radius: 8px; cursor: pointer; transition: border-color 0.2s;
    }
    .agent-choice:hover { border-color: var(--mat-sys-primary); }
    .agent-choice.selected { border-color: var(--mat-sys-primary); background: color-mix(in srgb, var(--mat-sys-primary) 8%, transparent); }
    .agent-choice mat-icon { color: var(--mat-sys-primary); flex-shrink: 0; }
    .agent-choice span { font-weight: 500; display: block; }
    .agent-choice small { font-size: 0.8rem; color: var(--mat-sys-on-surface-variant); }
  `],
})
export class SetupWizardComponent implements OnInit {
  readonly i18n = inject(I18nService);
  llmOk = signal(false);
  llmChecking = signal(false);
  connectorsLoading = signal(true);
  connectorsLoaded = signal(false);
  jqlReady = signal(true);
  jqlQueries = signal<any[]>([]);
  showAiJql = signal(false);
  aiJqlLoading = signal(false);
  aiJqlDesc = '';
  connectorStates = signal<PersonalConnectorState[]>(PERSONAL_CONNECTORS.map(meta => ({
    type: meta.type,
    title: meta.title,
    description: meta.description,
    baseUrlLabel: meta.baseUrlLabel,
    baseUrlPlaceholder: meta.baseUrlPlaceholder,
    base_url: '',
    name: meta.title,
    enabled: true,
    configured: false,
    saving: false,
    testing: false,
    values: {},
  })));
  o365Folder = 'Inbox';
  teamsChannelsText = '';
  wizardAgent: 'hermes' | 'claude_cli' | 'codex_cli' = 'hermes';

  constructor(
    private connectorService: ConnectorService,
    private http: HttpClient,
    private router: Router,
    private snackBar: MatSnackBar,
  ) {}

  ngOnInit() {
    this.loadPersonalConnectors();
    this.loadPreferences();
    this.loadJqlQueries();
    this.checkLlm();
  }

  fieldsFor(type: ConnectorType): ConnectorField[] {
    return PERSONAL_CONNECTORS.find(c => c.type === type)?.credentials ?? [];
  }

  loadPersonalConnectors() {
    this.connectorsLoading.set(true);
    this.connectorService.listMine().subscribe({
      next: connectors => {
        const byType = new Map(connectors.map(connector => [connector.type, connector]));
        this.connectorStates.set(PERSONAL_CONNECTORS.map(meta => {
          const existing = byType.get(meta.type);
          const values: Record<string, string> = {};
          if (meta.type === 'o365') {
            values['mailbox'] = '';
          }
          return {
            type: meta.type,
            title: meta.title,
            description: meta.description,
            baseUrlLabel: meta.baseUrlLabel,
            baseUrlPlaceholder: meta.baseUrlPlaceholder,
            name: existing?.name ?? meta.title,
            base_url: existing?.base_url ?? '',
            enabled: existing?.enabled ?? true,
            configured: !!existing,
            saving: false,
            testing: false,
            values,
          };
        }));
        this.connectorsLoading.set(false);
        this.connectorsLoaded.set(true);
      },
      error: () => {
        this.connectorsLoading.set(false);
        this.connectorsLoaded.set(true);
      },
    });
  }

  loadPreferences() {
    this.http.get<any>(`${environment.apiUrl}/preferences`).subscribe({
      next: prefs => {
        this.o365Folder = prefs?.o365_folder || 'Inbox';
        this.teamsChannelsText = Array.isArray(prefs?.feed_teams_channels)
          ? prefs.feed_teams_channels.join('\n')
          : '';
        if (prefs?.o365_mailbox) {
          this.updateConnectorCredential('o365', 'mailbox', prefs.o365_mailbox);
        }
      },
    });
  }

  updateConnectorField(type: ConnectorType, key: 'name' | 'base_url', value: string) {
    this.connectorStates.update(states => states.map(state =>
      state.type === type ? { ...state, [key]: value } : state
    ));
  }

  updateConnectorCredential(type: ConnectorType, key: string, value: string) {
    this.connectorStates.update(states => states.map(state =>
      state.type === type
        ? { ...state, values: { ...state.values, [key]: value } }
        : state
    ));
  }

  updateConnectorToggle(type: ConnectorType, enabled: boolean) {
    this.connectorStates.update(states => states.map(state =>
      state.type === type ? { ...state, enabled } : state
    ));
  }

  async savePersonalConnector(type: ConnectorType) {
    const state = this.connectorStates().find(item => item.type === type);
    if (!state) return;

    this.setConnectorFlag(type, 'saving', true);
    const credentials = Object.fromEntries(
      Object.entries(state.values).filter(([, value]) => !!String(value || '').trim())
    );

    this.connectorService.upsertMine(type, {
      name: state.name.trim() || state.title,
      type,
      base_url: state.base_url.trim() || null,
      credentials,
      enabled: state.enabled,
    }).subscribe({
      next: async () => {
        await this.saveConnectorPreferences(type, credentials);
        this.connectorStates.update(states => states.map(item =>
          item.type === type ? { ...item, configured: true, saving: false } : item
        ));
        this.snackBar.open(`${state.title} gespeichert`, '', { duration: 2500 });
      },
      error: (err) => {
        this.setConnectorFlag(type, 'saving', false);
        this.snackBar.open(err?.error?.detail ?? `Fehler beim Speichern von ${state.title}`, '', { duration: 3500 });
      },
    });
  }

  async saveConnectorPreferences(type: ConnectorType, credentials: Record<string, string>) {
    if (type === 'o365') {
      await this.http.patch(`${environment.apiUrl}/preferences`, {
        o365_mailbox: credentials['mailbox'] || null,
        o365_folder: this.o365Folder || 'Inbox',
      }).toPromise();
    }
    if (type === 'teams') {
      const channels = this.teamsChannelsText
        .split('\n')
        .map(line => line.trim())
        .filter(Boolean);
      await this.http.patch(`${environment.apiUrl}/preferences`, {
        feed_teams_channels: channels,
      }).toPromise();
    }
  }

  testPersonalConnector(type: ConnectorType) {
    this.setConnectorFlag(type, 'testing', true);
    this.connectorService.testMine(type).subscribe({
      next: result => {
        this.setConnectorFlag(type, 'testing', false);
        this.snackBar.open(result.success ? `✓ ${result.message}` : `✗ ${result.message}`, '', { duration: 3500 });
      },
      error: (err) => {
        this.setConnectorFlag(type, 'testing', false);
        this.snackBar.open(err?.error?.detail ?? 'Verbindungstest fehlgeschlagen', '', { duration: 3500 });
      },
    });
  }

  setConnectorFlag(type: ConnectorType, key: 'saving' | 'testing', value: boolean) {
    this.connectorStates.update(states => states.map(state =>
      state.type === type ? { ...state, [key]: value } : state
    ));
  }

  checkLlm() {
    this.llmChecking.set(true);
    this.http.get<{ configured: boolean }>(`${environment.apiUrl}/settings/llm-status`).subscribe({
      next: data => {
        this.llmOk.set(!!data?.configured);
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

  saveWizardAgent(stepper: any): void {
    // Always proceed to next step; for claude_cli/codex_cli we just save the preference
    // and the user will complete the OAuth in Settings → Konsole.
    if (this.wizardAgent === 'hermes') {
      stepper.next();
      return;
    }
    this.http.patch(`${environment.apiUrl}/preferences`, { computer_agent: this.wizardAgent }).subscribe({
      next: () => stepper.next(),
      error: () => stepper.next(),
    });
  }

  finish() {
    this.http.patch(`${environment.apiUrl}/preferences`, { setup_completed: true }).subscribe({
      next: () => this.router.navigate(['/dashboard']),
      error: () => this.router.navigate(['/dashboard']),
    });
  }
}
