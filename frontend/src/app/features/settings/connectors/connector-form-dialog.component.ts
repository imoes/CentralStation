import { Component, Inject, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, FormGroup, Validators, ReactiveFormsModule } from '@angular/forms';
import { MatDialogModule, MatDialogRef, MAT_DIALOG_DATA } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatButtonModule } from '@angular/material/button';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { ConnectorService } from '../../../core/services/connector.service';
import { Connector, ConnectorType } from '../../../core/models/connector.model';

interface CredField { key: string; label: string; type: 'text' | 'password' }

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
  { value: 'it_aikb',      label: 'it-aikb RAG' },
];

const CRED_FIELDS: Record<ConnectorType, CredField[]> = {
  checkmk:      [{ key: 'username', label: 'Benutzername', type: 'text' }, { key: 'password', label: 'Passwort', type: 'password' }],
  graylog:      [{ key: 'username', label: 'Benutzername', type: 'text' }, { key: 'password', label: 'Passwort', type: 'password' }],
  wazuh:        [{ key: 'username', label: 'Benutzername', type: 'text' }, { key: 'password', label: 'Passwort', type: 'password' }],
  jira:         [
    { key: 'token', label: 'Personal Access Token', type: 'password' },
    { key: 'project', label: 'Standardprojekt (optional)', type: 'text' },
  ],
  jira_sd:      [
    { key: 'token', label: 'Personal Access Token', type: 'password' },
    { key: 'project', label: 'Standardprojekt / Queue (optional)', type: 'text' },
  ],
  o365:         [
    { key: 'tenant_id',     label: 'Tenant ID',     type: 'text' },
    { key: 'client_id',     label: 'Client ID',     type: 'text' },
    { key: 'client_secret', label: 'Client Secret', type: 'password' },
    { key: 'mailbox',       label: 'Postfach (UPN)', type: 'text' },
  ],
  teams:        [
    { key: 'tenant_id',     label: 'Tenant ID',     type: 'text' },
    { key: 'client_id',     label: 'Client ID',     type: 'text' },
    { key: 'client_secret', label: 'Client Secret', type: 'password' },
  ],
  prometheus:   [
    { key: 'username', label: 'Benutzername (optional)', type: 'text' },
    { key: 'password', label: 'Passwort (optional)', type: 'password' },
    { key: 'token',    label: 'Bearer Token (optional)', type: 'password' },
  ],
  netbox:       [{ key: 'token', label: 'API Token', type: 'password' }],
  id_generator: [
    { key: 'username', label: 'Benutzername', type: 'text' },
    { key: 'password', label: 'Passwort', type: 'password' },
  ],
  it_aikb:      [{ key: 'token', label: 'Bearer Token (aikb_xxx)', type: 'password' }],
};

@Component({
  selector: 'cs-connector-form-dialog',
  standalone: true,
  imports: [
    CommonModule, ReactiveFormsModule,
    MatDialogModule, MatFormFieldModule, MatInputModule,
    MatSelectModule, MatButtonModule, MatSlideToggleModule,
    MatProgressSpinnerModule,
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
            <input matInput [type]="field.type"
                   [formControlName]="'cred_' + field.key"
                   [placeholder]="isEdit ? '(unverändert lassen = leer)' : ''">
          </mat-form-field>
        }

        <mat-slide-toggle formControlName="enabled">Aktiviert</mat-slide-toggle>
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
  `],
})
export class ConnectorFormDialogComponent implements OnInit {
  connectorTypes = CONNECTOR_TYPES;
  isEdit: boolean;
  form!: FormGroup;
  saving = signal(false);
  credFields = signal<CredField[]>([]);

  constructor(
    private fb: FormBuilder,
    private svc: ConnectorService,
    private ref: MatDialogRef<ConnectorFormDialogComponent>,
    @Inject(MAT_DIALOG_DATA) public data: { connector?: Connector } | null,
  ) {
    this.isEdit = !!data?.connector;
  }

  ngOnInit() {
    const c = this.data?.connector;
    this.form = this.fb.group({
      type:     [c?.type ?? 'checkmk', Validators.required],
      name:     [c?.name ?? '', Validators.required],
      base_url: [c?.base_url ?? ''],
      enabled:  [c?.enabled ?? true],
    });

    this.form.get('type')!.valueChanges.subscribe(type => this.updateCredFields(type as ConnectorType));
    this.updateCredFields((c?.type ?? 'checkmk') as ConnectorType);
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
    const credentials: Record<string, string> = {};
    for (const field of this.credFields()) {
      const val = v[`cred_${field.key}`];
      if (val) credentials[field.key] = val;
    }

    if (this.isEdit && this.data?.connector) {
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
      this.svc.create({
        name: v.name,
        type: v.type,
        base_url: v.base_url || null,
        credentials,
        enabled: v.enabled,
      }).subscribe({
        next: () => { this.saving.set(false); this.ref.close(true); },
        error: () => this.saving.set(false),
      });
    }
  }
}
