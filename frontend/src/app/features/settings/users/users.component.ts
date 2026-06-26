import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';
import { MatDialogModule, MatDialog } from '@angular/material/dialog';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatDividerModule } from '@angular/material/divider';
import { environment } from '../../../../environments/environment';
import { I18nService } from '../../../core/services/i18n.service';

const ROLES = [
  { value: 'admin',              label: 'Admin',               color: '#c62828' },
  { value: 'sysadmin',           label: 'SysAdmin',            color: '#1565c0' },
  { value: 'network_technician', label: 'Network-Technician',  color: '#2e7d32' },
  { value: 'viewer',             label: 'Viewer',              color: '#757575' },
];

@Component({
  selector: 'cs-users',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatCardModule, MatButtonModule, MatIconModule,
    MatFormFieldModule, MatInputModule, MatSelectModule,
    MatSlideToggleModule, MatProgressSpinnerModule, MatChipsModule,
    MatDialogModule, MatSnackBarModule, MatTooltipModule, MatDividerModule,
  ],
  template: `
    <div class="page-container">
      <div class="page-header">
        <h2>{{ i18n.t('settings.users.title') }}</h2>
        <button mat-flat-button color="primary" (click)="openCreate()">
          <mat-icon>person_add</mat-icon> {{ i18n.t('settings.users.create') }}
        </button>
      </div>

      @if (loading()) {
        <div class="spinner-center"><mat-spinner diameter="40"></mat-spinner></div>
      } @else {
        <mat-card>
          <div class="user-list">
            @for (u of users(); track u.id) {
              <div class="user-row">
                <div class="user-avatar" [style.background]="roleColor(u.role)">
                  {{ initials(u.full_name || u.email) }}
                </div>
                <div class="user-info">
                  <span class="user-name">{{ u.full_name || '–' }}</span>
                  <span class="user-email">{{ u.email }}</span>
                </div>
                <mat-chip class="role-chip" [style.background]="roleColor(u.role) + '22'" [style.color]="roleColor(u.role)">
                  {{ roleLabel(u.role) }}
                </mat-chip>
                <mat-slide-toggle
                  [checked]="u.is_active"
                  (change)="toggleActive(u, $event.checked)"
                  matTooltip="Aktiv/Inaktiv">
                </mat-slide-toggle>
                <mat-slide-toggle
                  [checked]="u.computer_console_enabled"
                  (change)="toggleConsole(u, $event.checked)"
                  matTooltip="Computer Console (Hermes KI)"
                  color="accent">
                  <mat-icon style="font-size:14px;width:14px;height:14px;vertical-align:middle">smart_toy</mat-icon>
                </mat-slide-toggle>
                <button mat-icon-button (click)="openEdit(u)" matTooltip="Bearbeiten">
                  <mat-icon>edit</mat-icon>
                </button>
                <button mat-icon-button color="warn" (click)="deleteUser(u)" [matTooltip]="i18n.t('common.delete')">
                  <mat-icon>delete</mat-icon>
                </button>
              </div>
              <mat-divider></mat-divider>
            }
            @if (users().length === 0) {
              <div class="empty">Keine Benutzer vorhanden.</div>
            }
          </div>
        </mat-card>
      }
    </div>

    <!-- Create / Edit Dialog -->
    @if (showDialog()) {
      <div class="dialog-backdrop" (click)="closeDialog()"></div>
      <div class="dialog-panel">
        <div class="dialog-header">
          <h3>{{ editingUser() ? i18n.t('settings.users.edit') : i18n.t('settings.users.create') }}</h3>
          <button mat-icon-button (click)="closeDialog()"><mat-icon>close</mat-icon></button>
        </div>

        <div class="dialog-body">
          @if (!editingUser()) {
            <mat-form-field appearance="outline" class="full-width">
              <mat-label>E-Mail</mat-label>
              <input matInput type="email" [(ngModel)]="form.email" placeholder="name@example.com">
              <mat-icon matSuffix>email</mat-icon>
            </mat-form-field>
          }

          <mat-form-field appearance="outline" class="full-width">
            <mat-label>Anzeigename</mat-label>
            <input matInput [(ngModel)]="form.full_name" placeholder="Max Mustermann">
            <mat-icon matSuffix>badge</mat-icon>
          </mat-form-field>

          <mat-form-field appearance="outline" class="full-width">
            <mat-label>Rolle</mat-label>
            <mat-select [(ngModel)]="form.role">
              @for (r of roles; track r.value) {
                <mat-option [value]="r.value">
                  <span [style.color]="r.color">{{ r.label }}</span>
                </mat-option>
              }
            </mat-select>
          </mat-form-field>

          @if (!editingUser()) {
            <mat-form-field appearance="outline" class="full-width">
              <mat-label>Passwort</mat-label>
              <input matInput [type]="showPw ? 'text' : 'password'" [(ngModel)]="form.password">
              <button mat-icon-button matSuffix (click)="showPw = !showPw" type="button">
                <mat-icon>{{ showPw ? 'visibility_off' : 'visibility' }}</mat-icon>
              </button>
            </mat-form-field>
          }
        </div>

        <div class="dialog-actions">
          <button mat-stroked-button (click)="closeDialog()">{{ i18n.t('common.cancel') }}</button>
          <button mat-flat-button color="primary" (click)="save()" [disabled]="saving()">
            @if (saving()) { <mat-spinner diameter="18"></mat-spinner> }
            @else { <mat-icon>save</mat-icon> }
            {{ editingUser() ? i18n.t('common.save') : i18n.t('common.create') }}
          </button>
        </div>
      </div>
    }
  `,
  styles: [`
    .page-container { padding: 24px; max-width: 860px; }
    .page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
    .page-header h2 { margin: 0; }
    .spinner-center { display: flex; justify-content: center; padding: 60px; }
    .user-list { display: flex; flex-direction: column; }
    .user-row { display: flex; align-items: center; gap: 12px; padding: 12px 16px; }
    .user-avatar { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: #fff; font-weight: 700; font-size: 13px; flex-shrink: 0; }
    .user-info { flex: 1; display: flex; flex-direction: column; min-width: 0; }
    .user-name { font-weight: 500; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .user-email { font-size: 12px; color: var(--mat-sys-on-surface-variant); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .role-chip { font-size: 11px; min-height: 22px; font-weight: 500; }
    .empty { padding: 32px; text-align: center; color: var(--mat-sys-on-surface-variant); }
    /* Dialog */
    .dialog-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,.4); z-index: 99; }
    .dialog-panel { position: fixed; top: 50%; left: 50%; transform: translate(-50%,-50%); width: 440px; max-width: 95vw; background: var(--mat-sys-surface); border-radius: 12px; box-shadow: 0 8px 32px rgba(0,0,0,.3); z-index: 100; display: flex; flex-direction: column; }
    .dialog-header { display: flex; align-items: center; justify-content: space-between; padding: 16px 20px 8px; }
    .dialog-header h3 { margin: 0; font-size: 18px; }
    .dialog-body { padding: 8px 20px; display: flex; flex-direction: column; gap: 4px; }
    .dialog-actions { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 20px 16px; }
    .full-width { width: 100%; }
  `],
})
export class UsersComponent implements OnInit {
  readonly i18n = inject(I18nService);
  users = signal<any[]>([]);
  loading = signal(true);
  saving = signal(false);
  showDialog = signal(false);
  editingUser = signal<any | null>(null);
  showPw = false;

  form: any = { email: '', full_name: '', role: 'sysadmin', password: '' };
  readonly roles = ROLES;

  constructor(private http: HttpClient, private snackBar: MatSnackBar) {}

  ngOnInit() { this.load(); }

  load() {
    this.loading.set(true);
    this.http.get<any[]>(`${environment.apiUrl}/users/`).subscribe({
      next: data => { this.users.set(data); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }

  openCreate() {
    this.editingUser.set(null);
    this.form = { email: '', full_name: '', role: 'sysadmin', password: '' };
    this.showPw = false;
    this.showDialog.set(true);
  }

  openEdit(u: any) {
    this.editingUser.set(u);
    this.form = { full_name: u.full_name ?? '', role: u.role };
    this.showDialog.set(true);
  }

  closeDialog() { this.showDialog.set(false); }

  save() {
    this.saving.set(true);
    const req = this.editingUser()
      ? this.http.patch(`${environment.apiUrl}/users/${this.editingUser().id}`, { full_name: this.form.full_name, role: this.form.role })
      : this.http.post(`${environment.apiUrl}/users/`, this.form);

    req.subscribe({
      next: () => {
        this.snackBar.open(this.editingUser() ? 'Gespeichert' : 'Benutzer angelegt', '', { duration: 2500 });
        this.saving.set(false);
        this.closeDialog();
        this.load();
      },
      error: (err) => {
        this.snackBar.open(err?.error?.detail ?? 'Fehler', '', { duration: 3000 });
        this.saving.set(false);
      },
    });
  }

  toggleActive(u: any, is_active: boolean) {
    this.http.patch(`${environment.apiUrl}/users/${u.id}`, { is_active }).subscribe({
      next: () => {
        this.users.update(list => list.map(x => x.id === u.id ? { ...x, is_active } : x));
        this.snackBar.open(is_active ? 'Aktiviert' : 'Deaktiviert', '', { duration: 2000 });
      },
    });
  }

  toggleConsole(u: any, computer_console_enabled: boolean) {
    this.http.patch(`${environment.apiUrl}/users/${u.id}`, { computer_console_enabled }).subscribe({
      next: () => {
        this.users.update(list => list.map(x => x.id === u.id ? { ...x, computer_console_enabled } : x));
        this.snackBar.open(
          computer_console_enabled ? 'Computer Console aktiviert' : 'Computer Console deaktiviert',
          '', { duration: 2500 }
        );
      },
      error: (err) => this.snackBar.open(err?.error?.detail ?? 'Fehler', '', { duration: 3000 }),
    });
  }

  deleteUser(u: any) {
    if (!confirm(`Benutzer "${u.email}" wirklich löschen?`)) return;
    this.http.delete(`${environment.apiUrl}/users/${u.id}`).subscribe({
      next: () => {
        this.users.update(list => list.filter(x => x.id !== u.id));
        this.snackBar.open('Gelöscht', '', { duration: 2000 });
      },
      error: (err) => this.snackBar.open(err?.error?.detail ?? 'Fehler', '', { duration: 3000 }),
    });
  }

  roleLabel(role: string) { return ROLES.find(r => r.value === role)?.label ?? role; }
  roleColor(role: string) { return ROLES.find(r => r.value === role)?.color ?? '#757575'; }
  initials(name: string) { return name.split(/[\s@]/)[0].slice(0, 2).toUpperCase(); }
}
