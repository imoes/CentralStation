import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatSelectModule } from '@angular/material/select';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDividerModule } from '@angular/material/divider';
import { ConnectorService } from '../../../core/services/connector.service';
import { SettingItem } from '../../../core/models/connector.model';

const SETTING_GROUPS: { title: string; keys: string[] }[] = [
  {
    title: 'LLM Konfiguration',
    keys: ['llm.base_url', 'llm.model', 'llm.api_key', 'llm.timeout_seconds'],
  },
  {
    title: 'Vision Modell',
    keys: ['llm.vision_base_url', 'llm.vision_model', 'llm.vision_api_key'],
  },
  {
    title: 'SearXNG Web-Suche',
    keys: ['searxng.base_url', 'searxng.enabled', 'searxng.results_count'],
  },
  {
    title: 'Agent Einstellungen',
    keys: ['agent.interval_minutes', 'agent.auto_jira', 'agent.jira_severity_threshold'],
  },
];

const BOOLEAN_KEYS = new Set(['searxng.enabled', 'agent.auto_jira']);
const SELECT_KEYS: Record<string, string[]> = {
  'agent.jira_severity_threshold': ['critical', 'high', 'medium'],
};
const SECRET_MASK = '••••••••';

@Component({
  selector: 'cs-ai-settings',
  standalone: true,
  imports: [
    CommonModule, ReactiveFormsModule,
    MatCardModule, MatFormFieldModule, MatInputModule,
    MatButtonModule, MatIconModule, MatSlideToggleModule,
    MatSelectModule, MatProgressSpinnerModule, MatSnackBarModule,
    MatDividerModule,
  ],
  template: `
    <div class="page-container">
      <div class="page-header">
        <h2>Globale Einstellungen</h2>
        <button mat-raised-button color="primary" [disabled]="saving()" (click)="saveAll()">
          @if (saving()) {
            <mat-spinner diameter="18"></mat-spinner>
          } @else {
            <ng-container><mat-icon>save</mat-icon> Speichern</ng-container>
          }
        </button>
      </div>

      @if (loading()) {
        <div class="spinner-center"><mat-spinner diameter="40"></mat-spinner></div>
      } @else if (form) {
        <form [formGroup]="form">
          @for (group of groups; track group.title) {
            <mat-card class="settings-card">
              <mat-card-header>
                <mat-card-title>{{ group.title }}</mat-card-title>
              </mat-card-header>
              <mat-card-content>
                @for (key of group.keys; track key) {
                  @if (isBooleanKey(key)) {
                    <div class="toggle-row">
                      <span class="key-label">{{ keyLabel(key) }}</span>
                      <mat-slide-toggle [formControlName]="key"></mat-slide-toggle>
                    </div>
                  } @else if (isSelectKey(key)) {
                    <mat-form-field appearance="outline" class="setting-field">
                      <mat-label>{{ keyLabel(key) }}</mat-label>
                      <mat-select [formControlName]="key">
                        @for (opt of selectOptions(key); track opt) {
                          <mat-option [value]="opt">{{ opt }}</mat-option>
                        }
                      </mat-select>
                    </mat-form-field>
                  } @else {
                    <mat-form-field appearance="outline" class="setting-field">
                      <mat-label>{{ keyLabel(key) }}</mat-label>
                      <input matInput [formControlName]="key"
                             [type]="isSecret(key) ? 'password' : 'text'"
                             [placeholder]="isSecret(key) ? 'Leer lassen = unverändert' : ''">
                    </mat-form-field>
                  }
                }
              </mat-card-content>
            </mat-card>
          }
        </form>
      }
    </div>
  `,
  styles: [`
    .page-container { padding: 24px; max-width: 800px; }
    .page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
    .page-header h2 { margin: 0; }
    .settings-card { margin-bottom: 16px; }
    .settings-card mat-card-content { padding-top: 16px; }
    .setting-field { width: 100%; margin-bottom: 4px; }
    .toggle-row { display: flex; align-items: center; justify-content: space-between; padding: 8px 0; }
    .key-label { font-size: 14px; }
    .spinner-center { display: flex; justify-content: center; padding: 40px; }
    mat-spinner { display: inline-block; }
  `],
})
export class AiSettingsComponent implements OnInit {
  groups = SETTING_GROUPS;
  loading = signal(true);
  saving = signal(false);
  form: FormGroup | null = null;
  private settingsMap = new Map<string, SettingItem>();

  constructor(
    private fb: FormBuilder,
    private svc: ConnectorService,
    private snack: MatSnackBar,
  ) {}

  ngOnInit() {
    this.svc.getSettings().subscribe({
      next: res => {
        this.settingsMap.clear();
        res.settings.forEach(s => this.settingsMap.set(s.key, s));
        this.buildForm();
        this.loading.set(false);
      },
    });
  }

  buildForm() {
    const controls: Record<string, unknown> = {};
    for (const group of SETTING_GROUPS) {
      for (const key of group.keys) {
        const item = this.settingsMap.get(key);
        let val: string | boolean = item?.value ?? '';
        if (item?.is_secret && item.value === SECRET_MASK) val = '';
        if (BOOLEAN_KEYS.has(key)) val = val === 'true' || (typeof val !== 'string' && !!val);
        controls[key] = [val];
      }
    }
    this.form = this.fb.group(controls);
  }

  saveAll() {
    if (!this.form) return;
    this.saving.set(true);
    const v = this.form.value;
    const allKeys = SETTING_GROUPS.flatMap(g => g.keys);

    const updates = allKeys.map(key => {
      let val = v[key];
      if (BOOLEAN_KEYS.has(key)) val = val ? 'true' : 'false';
      const item = this.settingsMap.get(key);
      // Skip empty secret fields (keep existing)
      if (item?.is_secret && !val) return Promise.resolve();
      return this.svc.updateSetting(key, val === '' ? null : String(val)).toPromise();
    });

    Promise.all(updates).then(() => {
      this.saving.set(false);
      this.snack.open('Einstellungen gespeichert', 'OK', { duration: 3000 });
    }).catch(() => {
      this.saving.set(false);
      this.snack.open('Fehler beim Speichern', 'OK', { duration: 4000 });
    });
  }

  isBooleanKey(key: string): boolean { return BOOLEAN_KEYS.has(key); }
  isSelectKey(key: string): boolean { return key in SELECT_KEYS; }
  isSecret(key: string): boolean { return !!this.settingsMap.get(key)?.is_secret; }
  selectOptions(key: string): string[] { return SELECT_KEYS[key] ?? []; }

  keyLabel(key: string): string {
    const labels: Record<string, string> = {
      'llm.base_url':                   'LLM Basis-URL',
      'llm.model':                       'LLM Modell',
      'llm.api_key':                     'API Key',
      'llm.timeout_seconds':             'Timeout (Sekunden)',
      'llm.vision_base_url':             'Vision LLM URL',
      'llm.vision_model':                'Vision Modell',
      'llm.vision_api_key':              'Vision API Key',
      'searxng.base_url':                'SearXNG URL',
      'searxng.enabled':                 'SearXNG aktiviert',
      'searxng.results_count':           'Anzahl Suchergebnisse',
      'agent.interval_minutes':          'Intervall (Minuten)',
      'agent.auto_jira':                 'Automatisch Jira-Tickets erstellen',
      'agent.jira_severity_threshold':   'Mindest-Severity für Jira',
    };
    return labels[key] ?? key;
  }
}
