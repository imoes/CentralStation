import { Component, Inject, OnInit, OnDestroy, signal, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, FormGroup, Validators, ReactiveFormsModule } from '@angular/forms';
import { MatDialogModule, MatDialogRef, MAT_DIALOG_DATA } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatButtonModule } from '@angular/material/button';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatIconModule } from '@angular/material/icon';
import { HttpClient } from '@angular/common/http';
import { ConnectorService } from '../../../core/services/connector.service';
import { Connector, ConnectorType } from '../../../core/models/connector.model';
import { environment } from '../../../../environments/environment';

interface CredField { key: string; label: string; type: 'text' | 'password' | 'textarea'; hint?: string }
const PERSONAL_CONNECTOR_TYPES: ConnectorType[] = ['jira', 'jira_sd', 'o365', 'teams'];

const CONNECTOR_TYPES: { value: ConnectorType; label: string }[] = [
  { value: 'checkmk',      label: 'CheckMK' },
  { value: 'graylog',      label: 'Graylog' },
  { value: 'wazuh',        label: 'Wazuh' },
  { value: 'jira',         label: 'Jira' },
  { value: 'jira_sd',      label: 'Jira ServiceDesk' },
  { value: 'o365',         label: 'O365 / Microsoft Graph' },
  { value: 'teams',        label: 'Microsoft Teams / Graph' },
  { value: 'prometheus',   label: 'Prometheus' },
  { value: 'netbox',       label: 'NetBox' },
  { value: 'id_generator', label: 'ID-Generator' },
  { value: 'coroot',       label: 'Coroot (Monitoring)' },
  { value: 'aikb',         label: 'IT-AIKB (RAG / Knowledge Base)' },
];

const CRED_FIELDS: Record<ConnectorType, CredField[]> = {
  checkmk:      [
    { key: 'username', label: 'Username', type: 'text' },
    { key: 'password', label: 'Password', type: 'password' },
    { key: 'site', label: 'Site name (optional, e.g. im)', type: 'text' },
  ],
  graylog:      [{ key: 'username', label: 'Username', type: 'text' }, { key: 'password', label: 'Password', type: 'password' }],
  wazuh:        [
    { key: 'username',           label: 'Manager Username',                      type: 'text' },
    { key: 'password',           label: 'Manager Password',                      type: 'password' },
    { key: 'indexer_url',        label: 'Indexer URL (e.g. http://wazuh-indexer:9200)', type: 'text' },
    { key: 'indexer_username',   label: 'Indexer Username (default: admin)',     type: 'text' },
    { key: 'indexer_password',   label: 'Indexer Password',                      type: 'password' },
    { key: 'excluded_rule_ids',  label: 'Excluded Rule IDs (one per line)',       type: 'textarea',
      hint: 'Default: 503 504 533 591 5402 5501 5502 5715 — leave empty for defaults' },
    { key: 'excluded_fim_paths', label: 'Excluded FIM Paths (one per line)',      type: 'textarea',
      hint: 'Default: /etc/cmk-update-agent.state /etc/patchmon/config.yml' },
  ],
  jira:         [
    { key: 'token', label: 'Personal Access Token', type: 'password' },
    { key: 'project', label: 'Default project (optional)', type: 'text' },
  ],
  jira_sd:      [
    { key: 'token', label: 'Personal Access Token', type: 'password' },
    { key: 'project', label: 'Default project / queue (optional)', type: 'text' },
  ],
  o365:         [
    { key: 'tenant_id',     label: 'Tenant ID (from Azure App Registration)',     type: 'text' },
    { key: 'client_id',     label: 'Client ID (Application ID)',                  type: 'text' },
    { key: 'client_secret', label: 'Client Secret (optional, for confidential app)', type: 'password' },
  ],
  teams:        [
    { key: 'tenant_id',     label: 'Tenant ID (from Azure App Registration)',     type: 'text' },
    { key: 'client_id',     label: 'Client ID (Application ID)',                  type: 'text' },
    { key: 'client_secret', label: 'Client Secret (optional, for confidential app)', type: 'password' },
  ],
  prometheus:   [
    { key: 'username', label: 'Username (optional)', type: 'text' },
    { key: 'password', label: 'Password (optional)', type: 'password' },
    { key: 'token',    label: 'Bearer Token (optional)', type: 'password' },
  ],
  netbox:       [{ key: 'token', label: 'API Token', type: 'password' }],
  id_generator: [
    { key: 'username', label: 'Username', type: 'text' },
    { key: 'password', label: 'Password', type: 'password' },
  ],
  coroot:       [
    { key: 'username', label: 'Username', type: 'text' },
    { key: 'password', label: 'Password', type: 'password' },
  ],
  aikb:         [
    { key: 'api_token', label: 'API Token (Bearer — preferred)', type: 'password' },
    { key: 'username',  label: 'Username (fallback if no token)', type: 'text' },
    { key: 'password',  label: 'Password (fallback if no token)', type: 'password' },
  ],
};

@Component({
  selector: 'cs-connector-form-dialog',
  standalone: true,
  imports: [
    CommonModule, ReactiveFormsModule,
    MatDialogModule, MatFormFieldModule, MatInputModule,
    MatSelectModule, MatButtonModule, MatSlideToggleModule,
    MatProgressSpinnerModule, MatIconModule,
  ],
  template: `
    <h2 mat-dialog-title>{{ isEdit ? 'Connector bearbeiten' : 'Neuer Connector' }}</h2>
    <mat-dialog-content>
      <form [formGroup]="form" class="form-grid">
        <mat-form-field appearance="outline" class="full-width">
          <mat-label>Typ</mat-label>
          <mat-select formControlName="type" [disabled]="isEdit">
            @for (t of connectorTypes; track t.value) {
              <mat-option [value]="t.value">{{ t.label }}</mat-option>
            }
          </mat-select>
        </mat-form-field>

        <mat-form-field appearance="outline" class="full-width">
          <mat-label>Name</mat-label>
          <input matInput formControlName="name" placeholder="z.B. CheckMK Produktion">
        </mat-form-field>

        <mat-form-field appearance="outline" class="full-width">
          <mat-label>Basis-URL</mat-label>
          <input matInput formControlName="base_url" placeholder="https://...">
        </mat-form-field>

        @for (field of credFields(); track field.key) {
          <mat-form-field appearance="outline" class="full-width">
            <mat-label>{{ field.label }}</mat-label>
            @if (field.type === 'textarea') {
              <textarea matInput rows="3"
                        [formControlName]="'cred_' + field.key"
                        [placeholder]="field.hint ?? ''"></textarea>
            } @else {
              <input matInput [type]="field.type"
                     [formControlName]="'cred_' + field.key"
                     [placeholder]="isEdit ? '(unverändert lassen = leer)' : ''">
            }
            @if (field.hint && field.type !== 'textarea') {
              <mat-hint>{{ field.hint }}</mat-hint>
            }
          </mat-form-field>
        }

        <mat-slide-toggle formControlName="enabled">Aktiviert</mat-slide-toggle>

        <!-- ── Microsoft Delegated Auth (O365 / Teams) ───────────── -->
        @if (isMicrosoftType()) {
          <div class="ms-auth-section">
            <div class="ms-auth-title">
              <mat-icon>account_circle</mat-icon>
              <span>Microsoft-Konto verknüpfen (Delegated Permissions)</span>
            </div>

            @if (msAuthStatus() === 'idle') {
              <p class="ms-auth-hint">
                Gib Tenant ID und Client ID ein und klicke auf „Mit Microsoft anmelden".
                Connector wird automatisch gespeichert und der Anmelde-Code erscheint.
              </p>
              <button type="button" mat-stroked-button color="primary"
                      [disabled]="msAuthLoading()"
                      (click)="startDeviceCode()">
                @if (msAuthLoading()) { <mat-spinner diameter="16"></mat-spinner> }
                @else { <mat-icon>login</mat-icon> }
                Mit Microsoft anmelden
              </button>
              @if (msAuthStatus() === 'idle' && isAuthorized()) {
                <span class="ms-authorized-badge"><mat-icon>check_circle</mat-icon> Bereits autorisiert</span>
              }
            }

            @if (msAuthStatus() === 'waiting') {
              <div class="ms-device-code-box">
                <p>Öffne <strong>{{ msVerificationUrl() }}</strong> in einem Browser und gib diesen Code ein:</p>
                <div class="ms-user-code">{{ msUserCode() }}</div>
                <p class="ms-poll-hint">Warte auf Bestätigung…</p>
                <mat-spinner diameter="20"></mat-spinner>
              </div>
            }

            @if (msAuthStatus() === 'authorized') {
              <div class="ms-success">
                <mat-icon>check_circle</mat-icon>
                <span>Erfolgreich verbunden! Refresh-Token wurde gespeichert.</span>
              </div>
            }

            @if (msAuthStatus() === 'error') {
              <div class="ms-error">
                <mat-icon>error</mat-icon>
                <span>{{ msAuthError() }}</span>
                <button mat-button (click)="resetMsAuth()">Erneut versuchen</button>
              </div>
            }
          </div>
        }
      </form>
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button mat-dialog-close>Abbrechen</button>
      <button mat-raised-button color="primary" [disabled]="form.invalid || saving()" (click)="save()">
        @if (saving()) { <mat-spinner diameter="18"></mat-spinner> }
        @else { Speichern }
      </button>
    </mat-dialog-actions>
  `,
  styles: [`
    .form-grid { display: flex; flex-direction: column; gap: 4px; min-width: 460px; padding-top: 8px; }
    .full-width { width: 100%; }
    mat-spinner { display: inline-block; }

    .ms-auth-section {
      border: 1px solid var(--mat-sys-outline-variant);
      border-radius: 8px;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      background: color-mix(in srgb, var(--mat-sys-primary) 4%, transparent);
    }
    .ms-auth-title { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 14px; }
    .ms-auth-title mat-icon { color: var(--mat-sys-primary); font-size: 20px; }
    .ms-auth-hint { margin: 0; font-size: 12px; color: var(--mat-sys-on-surface-variant); }
    .ms-authorized-badge { display: flex; align-items: center; gap: 4px; color: #2e7d32; font-size: 13px; }
    .ms-device-code-box { display: flex; flex-direction: column; gap: 8px; }
    .ms-user-code {
      font-size: 28px; font-weight: 900; letter-spacing: 6px;
      text-align: center; padding: 12px;
      background: var(--mat-sys-surface-container);
      border-radius: 8px;
      font-family: monospace;
      color: var(--mat-sys-primary);
    }
    .ms-poll-hint { margin: 0; font-size: 12px; color: var(--mat-sys-on-surface-variant); }
    .ms-success { display: flex; align-items: center; gap: 8px; color: #2e7d32; font-size: 13px; }
    .ms-error { display: flex; align-items: center; gap: 8px; color: var(--mat-sys-error); font-size: 13px; }
  `],
})
export class ConnectorFormDialogComponent implements OnInit, OnDestroy {
  connectorTypes = CONNECTOR_TYPES;
  isEdit: boolean;
  form!: FormGroup;
  saving = signal(false);
  credFields = signal<CredField[]>([]);

  // Microsoft Device Code flow state
  private http = inject(HttpClient);
  savedConnectorId = signal<string | null>(null);
  isAuthorized = signal(false);
  msAuthStatus = signal<'idle' | 'waiting' | 'authorized' | 'error'>('idle');
  msAuthLoading = signal(false);
  msUserCode = signal('');
  msVerificationUrl = signal('https://microsoft.com/devicelogin');
  msAuthError = signal('');
  private msDeviceCode = '';
  private msPollInterval: ReturnType<typeof setInterval> | null = null;

  constructor(
    private fb: FormBuilder,
    private svc: ConnectorService,
    private ref: MatDialogRef<ConnectorFormDialogComponent>,
    @Inject(MAT_DIALOG_DATA) public data: { connector?: Connector; personal?: boolean } | null,
  ) {
    this.isEdit = !!data?.connector;
    if (data?.personal) {
      this.connectorTypes = CONNECTOR_TYPES.filter(type => PERSONAL_CONNECTOR_TYPES.includes(type.value));
    }
    if (data?.connector?.id) {
      this.savedConnectorId.set(data.connector.id);
    }
  }

  isMicrosoftType(): boolean {
    return ['o365', 'teams'].includes(this.form?.get('type')?.value ?? '');
  }

  ngOnDestroy() {
    this._stopPolling();
  }

  ngOnInit() {
    const c = this.data?.connector;
    const defaultType: ConnectorType = this.data?.personal ? 'jira' : 'checkmk';
    this.form = this.fb.group({
      type:     [c?.type ?? defaultType, Validators.required],
      name:     [c?.name ?? '', Validators.required],
      base_url: [c?.base_url ?? ''],
      enabled:  [c?.enabled ?? true],
    });

    this.form.get('type')!.valueChanges.subscribe(type => this.updateCredFields(type as ConnectorType));
    this.updateCredFields((c?.type ?? defaultType) as ConnectorType);
  }

  updateCredFields(type: ConnectorType) {
    const fields = CRED_FIELDS[type] ?? [];
    this.credFields.set(fields);

    // Remove old credential controls
    Object.keys(this.form.controls)
      .filter(k => k.startsWith('cred_'))
      .forEach(k => this.form.removeControl(k));

    // Add new ones
    for (const field of fields) {
      this.form.addControl(`cred_${field.key}`, this.fb.control(''));
    }
  }

  save() {
    if (this.form.invalid) return;
    this.saving.set(true);

    const v = this.form.value;
    const credentials = this._buildCredentials();

    if (this.isEdit && this.data?.connector) {
      if (this.data.personal) {
        this.svc.upsertMine(this.data.connector.type, {
          name: v.name,
          type: this.data.connector.type,
          base_url: v.base_url || null,
          credentials,
          enabled: v.enabled,
        }).subscribe({
          next: () => { this.saving.set(false); this.ref.close(true); },
          error: () => this.saving.set(false),
        });
        return;
      }

      const update: Record<string, unknown> = {
        name: v.name,
        base_url: v.base_url || null,
        enabled: v.enabled,
      };
      if (Object.keys(credentials).length > 0) {
        update['credentials'] = credentials;
      }
      this.svc.update(this.data.connector.id, update).subscribe({
        next: () => { this.saving.set(false); this.ref.close(true); },
        error: () => this.saving.set(false),
      });
    } else {
      if (this.data?.personal) {
        this.svc.upsertMine(v.type, {
          name: v.name,
          type: v.type,
          base_url: v.base_url || null,
          credentials,
          enabled: v.enabled,
        }).subscribe({
          next: () => { this.saving.set(false); this.ref.close(true); },
          error: () => this.saving.set(false),
        });
      } else {
        this.svc.create({
          name: v.name,
          type: v.type,
          base_url: v.base_url || null,
          credentials,
          enabled: v.enabled,
        }).subscribe({
          next: (created: any) => {
            this.saving.set(false);
            if (['o365', 'teams'].includes(v.type) && created?.id) {
              // Stay open for Device Code flow
              this._afterSave(created.id);
              this.isEdit = true;
            } else {
              this.ref.close(true);
            }
          },
          error: () => this.saving.set(false),
        });
      }
    }
  }

  // After successful save of a new connector, keep its ID for the Device Code button
  private _afterSave(id: string) {
    this.savedConnectorId.set(id);
  }

  startDeviceCode() {
    const existingId = this.savedConnectorId();
    if (existingId) {
      this._doStartDeviceCode(existingId);
      return;
    }
    // Auto-save first, then start device code flow
    if (this.form.invalid) return;
    this.msAuthLoading.set(true);
    const credentials = this._buildCredentials();
    const v = this.form.value;

    const afterSave = (id: string) => {
      this.savedConnectorId.set(id);
      this.isEdit = true;
      this._doStartDeviceCode(id);
    };
    const onError = () => {
      this.msAuthLoading.set(false);
      this.msAuthError.set('Speichern fehlgeschlagen');
      this.msAuthStatus.set('error');
    };

    if (this.data?.personal) {
      this.svc.upsertMine(v.type, {
        name: v.name, type: v.type, base_url: v.base_url || null, credentials, enabled: v.enabled,
      }).subscribe({ next: (s: any) => afterSave(s.id), error: onError });
    } else if (this.isEdit && this.data?.connector) {
      const upd: Record<string, unknown> = { name: v.name, base_url: v.base_url || null, enabled: v.enabled };
      if (Object.keys(credentials).length) upd['credentials'] = credentials;
      this.svc.update(this.data.connector.id, upd).subscribe({ next: (s: any) => afterSave(s.id), error: onError });
    } else {
      this.svc.create({ name: v.name, type: v.type, base_url: v.base_url || null, credentials, enabled: v.enabled })
        .subscribe({ next: (s: any) => afterSave(s.id), error: onError });
    }
  }

  private _buildCredentials(): Record<string, string | string[]> {
    const v = this.form.value;
    const credentials: Record<string, string | string[]> = {};
    for (const field of this.credFields()) {
      const val = v[`cred_${field.key}`];
      if (!val) continue;
      if (field.type === 'textarea') {
        const lines = (val as string).split('\n').map((s: string) => s.trim()).filter(Boolean);
        if (lines.length) credentials[field.key] = lines;
      } else {
        credentials[field.key] = val;
      }
    }
    return credentials;
  }

  private _doStartDeviceCode(id: string) {
    this.msAuthLoading.set(true);
    this.http.post<any>(`${environment.apiUrl}/connectors/${id}/ms-device-code`, {}).subscribe({
      next: res => {
        this.msAuthLoading.set(false);
        this.msUserCode.set(res.user_code);
        this.msVerificationUrl.set(res.verification_url);
        this.msDeviceCode = res.device_code;
        this.msAuthStatus.set('waiting');
        this._startPolling(id, res.interval ?? 5);
      },
      error: err => {
        this.msAuthLoading.set(false);
        this.msAuthError.set(err?.error?.detail ?? 'Fehler beim Starten des Device Code Flows');
        this.msAuthStatus.set('error');
      },
    });
  }

  private _startPolling(connectorId: string, intervalSec: number) {
    this._stopPolling();
    this.msPollInterval = setInterval(() => {
      this.http.post<any>(
        `${environment.apiUrl}/connectors/${connectorId}/ms-device-code/complete`,
        { device_code: this.msDeviceCode },
      ).subscribe({
        next: res => {
          if (res.status === 'authorized') {
            this._stopPolling();
            this.msAuthStatus.set('authorized');
            this.isAuthorized.set(true);
          } else if (res.status === 'error') {
            this._stopPolling();
            this.msAuthError.set(res.message ?? 'Unbekannter Fehler');
            this.msAuthStatus.set('error');
          }
          // 'pending' → keep polling
        },
        error: () => { /* network error, keep polling */ },
      });
    }, intervalSec * 1000);
  }

  private _stopPolling() {
    if (this.msPollInterval) {
      clearInterval(this.msPollInterval);
      this.msPollInterval = null;
    }
  }

  resetMsAuth() {
    this._stopPolling();
    this.msAuthStatus.set('idle');
    this.msDeviceCode = '';
  }
}
