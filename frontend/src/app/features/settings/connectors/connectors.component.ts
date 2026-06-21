import { Component, computed, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ReactiveFormsModule } from '@angular/forms';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatTableModule } from '@angular/material/table';
import { MatDialogModule, MatDialog } from '@angular/material/dialog';
import { MatChipsModule } from '@angular/material/chips';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { ConnectorService } from '../../../core/services/connector.service';
import { Connector, ConnectorType } from '../../../core/models/connector.model';
import { ConnectorFormDialogComponent } from './connector-form-dialog.component';
import { AuthService } from '../../../core/auth/auth.service';

@Component({
  selector: 'cs-connectors',
  standalone: true,
  imports: [
    CommonModule, ReactiveFormsModule,
    MatCardModule, MatButtonModule, MatIconModule, MatTableModule,
    MatDialogModule, MatChipsModule, MatProgressSpinnerModule,
    MatSlideToggleModule, MatSnackBarModule,
  ],
  template: `
    <div class="page-container">
      <div class="page-header">
        <div>
          <h2>{{ isAdmin() ? 'Connectors' : 'Meine Konnektoren' }}</h2>
          @if (!isAdmin()) {
            <p class="subtle">Hier pflegen Sie Ihre persönlichen Zugänge für Monitoring, Jira, O365 und Teams.</p>
          }
        </div>
        <button mat-raised-button color="primary" (click)="openCreate()">
          <mat-icon>add</mat-icon> Connector hinzufügen
        </button>
      </div>

      @if (loading()) {
        <div class="spinner-center"><mat-spinner diameter="40"></mat-spinner></div>
      } @else {
        <mat-card>
          <table mat-table [dataSource]="connectors()" class="full-width">
            <ng-container matColumnDef="type">
              <th mat-header-cell *matHeaderCellDef>Typ</th>
              <td mat-cell *matCellDef="let c">
                <mat-chip [class]="'chip-' + c.type">{{ c.type }}</mat-chip>
              </td>
            </ng-container>
            <ng-container matColumnDef="name">
              <th mat-header-cell *matHeaderCellDef>Name</th>
              <td mat-cell *matCellDef="let c">{{ c.name }}</td>
            </ng-container>
            <ng-container matColumnDef="base_url">
              <th mat-header-cell *matHeaderCellDef>URL</th>
              <td mat-cell *matCellDef="let c" class="url-cell">{{ c.base_url || '—' }}</td>
            </ng-container>
            <ng-container matColumnDef="enabled">
              <th mat-header-cell *matHeaderCellDef>Aktiv</th>
              <td mat-cell *matCellDef="let c">
                <mat-slide-toggle
                  [checked]="c.enabled"
                  (change)="toggleEnabled(c, $event.checked)">
                </mat-slide-toggle>
              </td>
            </ng-container>
            <ng-container matColumnDef="actions">
              <th mat-header-cell *matHeaderCellDef>Aktionen</th>
              <td mat-cell *matCellDef="let c">
                <button mat-icon-button (click)="testConnector(c)" title="Verbindung testen"
                        [disabled]="testingId() === c.id">
                  @if (testingId() === c.id) {
                    <mat-spinner diameter="20"></mat-spinner>
                  } @else {
                    <mat-icon>wifi_tethering</mat-icon>
                  }
                </button>
                <button mat-icon-button (click)="openEdit(c)" title="Bearbeiten">
                  <mat-icon>edit</mat-icon>
                </button>
                @if (isAdmin()) {
                  <button mat-icon-button color="warn" (click)="deleteConnector(c)" title="Löschen">
                    <mat-icon>delete</mat-icon>
                  </button>
                } @else if (c.owner_user_id) {
                  <button mat-icon-button color="warn" (click)="deleteMyConnector(c)" title="Löschen">
                    <mat-icon>delete</mat-icon>
                  </button>
                }
              </td>
            </ng-container>
            <tr mat-header-row *matHeaderRowDef="columns"></tr>
            <tr mat-row *matRowDef="let row; columns: columns"></tr>
          </table>
          @if (connectors().length === 0) {
            <div class="empty-state">Keine Connectors konfiguriert.</div>
          }
        </mat-card>
      }
    </div>
  `,
  styles: [`
    .page-container { padding: 24px; max-width: 1100px; }
    .page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
    .page-header h2 { margin: 0; }
    .subtle { margin: 4px 0 0; color: var(--mat-sys-on-surface-variant); font-size: 13px; }
    .full-width { width: 100%; }
    .url-cell { font-family: monospace; font-size: 12px; max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .empty-state { padding: 24px; text-align: center; color: var(--mat-sys-on-surface-variant); }
    .spinner-center { display: flex; justify-content: center; padding: 40px; }
    mat-chip { font-size: 11px; min-height: 22px; }
  `],
})
export class ConnectorsComponent implements OnInit {
  columns = ['type', 'name', 'base_url', 'enabled', 'actions'];
  connectors = signal<Connector[]>([]);
  loading = signal(true);
  testingId = signal<string | null>(null);
  isAdmin = computed(() => this.auth.userRole() === 'admin');

  constructor(
    private svc: ConnectorService,
    private dialog: MatDialog,
    private snack: MatSnackBar,
    private auth: AuthService,
  ) {}

  ngOnInit() { this.load(); }

  load() {
    this.loading.set(true);
    const request$ = this.isAdmin() ? this.svc.list() : this.svc.listMine();
    request$.subscribe({
      next: list => { this.connectors.set(list); this.loading.set(false); },
      error: () => { this.loading.set(false); },
    });
  }

  openCreate() {
    const ref = this.dialog.open(ConnectorFormDialogComponent, {
      width: '540px',
      data: { personal: !this.isAdmin() },
    });
    ref.afterClosed().subscribe(result => { if (result) this.load(); });
  }

  openEdit(connector: Connector) {
    const ref = this.dialog.open(ConnectorFormDialogComponent, {
      width: '540px',
      data: { connector, personal: !this.isAdmin() },
    });
    ref.afterClosed().subscribe(result => { if (result) this.load(); });
  }

  toggleEnabled(connector: Connector, enabled: boolean) {
    if (this.isAdmin()) {
      this.svc.update(connector.id, { enabled }).subscribe({
        next: updated => {
          this.connectors.update(list => list.map(c => c.id === updated.id ? updated : c));
        },
      });
      return;
    }

    this.svc.updateMineById(connector.id, { enabled }).subscribe({
      next: updated => {
        this.connectors.update(list => list.map(c => c.id === updated.id ? updated : c));
      },
    });
  }

  testConnector(connector: Connector) {
    this.testingId.set(connector.id);
    const request$ = this.isAdmin() ? this.svc.test(connector.id) : this.svc.testMine(connector.type);
    request$.subscribe({
      next: result => {
        this.testingId.set(null);
        const msg = result.success ? `✓ ${result.message}` : `✗ ${result.message}`;
        this.snack.open(msg, 'OK', {
          duration: 4000,
          panelClass: result.success ? 'snack-success' : 'snack-error',
        });
      },
      error: () => {
        this.testingId.set(null);
        this.snack.open('Verbindungstest fehlgeschlagen', 'OK', { duration: 4000 });
      },
    });
  }

  deleteConnector(connector: Connector) {
    if (!confirm(`Connector "${connector.name}" wirklich löschen?`)) return;
    this.svc.delete(connector.id).subscribe({ next: () => this.load() });
  }

  deleteMyConnector(connector: Connector) {
    if (!confirm(`Connector "${connector.name}" wirklich löschen?`)) return;
    this.svc.deleteMineById(connector.id).subscribe({ next: () => this.load() });
  }
}
