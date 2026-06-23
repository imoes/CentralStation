import { Component, OnInit, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatDividerModule } from '@angular/material/divider';
import { MatDialogModule, MatDialog } from '@angular/material/dialog';
import { MatBadgeModule } from '@angular/material/badge';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { environment } from '../../../../environments/environment';
import { AuthService } from '../../../core/auth/auth.service';

interface Skill {
  id: string;
  name: string;
  title: string;
  description: string;
  content?: string;
  tags: string[];
  version: string;
  author: string;
  user_id: string;
  visibility: 'public' | 'private';
  updated_at: string;
}

interface SkillForm {
  name: string;
  title: string;
  description: string;
  content: string;
  tags: string;
  version: string;
  visibility: 'public' | 'private';
}

@Component({
  selector: 'cs-skills',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatCardModule, MatButtonModule, MatIconModule,
    MatFormFieldModule, MatInputModule, MatSelectModule,
    MatProgressSpinnerModule, MatChipsModule,
    MatSnackBarModule, MatTooltipModule, MatDividerModule,
    MatDialogModule, MatBadgeModule, MatExpansionModule, MatSlideToggleModule,
  ],
  template: `
    <div class="page-container">
      <div class="page-header">
        <div class="page-header-left">
          <h2>Skill-Bibliothek</h2>
          <span class="page-subtitle">Wiederverwendbare Prozeduren für Hermes</span>
        </div>
        <div class="page-header-actions">
          <mat-form-field appearance="outline" class="search-field">
            <mat-icon matPrefix>search</mat-icon>
            <input matInput placeholder="Skills durchsuchen…" [(ngModel)]="searchQuery" (ngModelChange)="filterSkills()">
          </mat-form-field>
          <button mat-flat-button color="primary" (click)="openCreateDialog()">
            <mat-icon>add</mat-icon> Skill erstellen
          </button>
        </div>
      </div>

      @if (loading()) {
        <div class="spinner-center"><mat-spinner diameter="40"></mat-spinner></div>
      } @else if (filteredSkills().length === 0) {
        <div class="empty-state">
          <mat-icon class="empty-icon">psychology</mat-icon>
          <p>Noch keine Skills vorhanden.</p>
          <p class="empty-sub">Erstelle den ersten Skill oder lass Hermes einen speichern.</p>
          <button mat-stroked-button (click)="openCreateDialog()">
            <mat-icon>add</mat-icon> Ersten Skill erstellen
          </button>
        </div>
      } @else {
        <div class="skill-grid">
          @for (skill of filteredSkills(); track skill.id) {
            <mat-card class="skill-card" [class.private-skill]="skill.visibility === 'private'">
              <mat-card-header>
                <div class="skill-header">
                  <div class="skill-title-row">
                    <span class="skill-name">{{ skill.name }}</span>
                    @if (skill.visibility === 'private') {
                      <mat-icon class="private-icon" matTooltip="Privater Skill — nur für dich sichtbar">lock</mat-icon>
                    }
                    <span class="skill-version">v{{ skill.version }}</span>
                  </div>
                  <h3 class="skill-title">{{ skill.title }}</h3>
                </div>
                <div class="skill-actions">
                  @if (canEdit(skill)) {
                    <button mat-icon-button matTooltip="Bearbeiten" (click)="openEditDialog(skill)">
                      <mat-icon>edit</mat-icon>
                    </button>
                    <button mat-icon-button matTooltip="Löschen" color="warn" (click)="deleteSkill(skill)">
                      <mat-icon>delete</mat-icon>
                    </button>
                  }
                </div>
              </mat-card-header>
              <mat-card-content>
                <p class="skill-description">{{ skill.description }}</p>
                @if (skill.tags?.length) {
                  <div class="skill-tags">
                    @for (tag of skill.tags; track tag) {
                      <span class="skill-tag">{{ tag }}</span>
                    }
                  </div>
                }
                <div class="skill-meta">
                  <span>{{ skill.author || 'unbekannt' }}</span>
                  <span>·</span>
                  <span>{{ skill.updated_at | date:'dd.MM.yy HH:mm' }}</span>
                </div>
              </mat-card-content>
              <mat-card-actions>
                <button mat-button (click)="toggleContent(skill.id)">
                  <mat-icon>{{ expandedId() === skill.id ? 'expand_less' : 'expand_more' }}</mat-icon>
                  {{ expandedId() === skill.id ? 'Anleitung verbergen' : 'Anleitung anzeigen' }}
                </button>
              </mat-card-actions>
              @if (expandedId() === skill.id) {
                <div class="skill-content">
                  @if (loadingContent()) {
                    <mat-spinner diameter="20"></mat-spinner>
                  } @else {
                    <pre class="skill-content-text">{{ selectedContent() }}</pre>
                  }
                </div>
              }
            </mat-card>
          }
        </div>
      }
    </div>

    <!-- Create/Edit Dialog -->
    @if (dialogOpen()) {
      <div class="dialog-overlay" (click)="closeDialog()"></div>
      <div class="skill-dialog">
        <div class="dialog-header">
          <h3>{{ editingSkill() ? 'Skill bearbeiten' : 'Neuen Skill erstellen' }}</h3>
          <button mat-icon-button (click)="closeDialog()"><mat-icon>close</mat-icon></button>
        </div>
        <div class="dialog-body">
          <mat-form-field appearance="outline" class="full-width">
            <mat-label>Name (Slug)</mat-label>
            <input matInput [(ngModel)]="form.name" placeholder="z.B. opensearch-reindex"
                   [disabled]="!!editingSkill()" pattern="[a-z0-9][a-z0-9\\-]{1,79}">
            <mat-hint>Kleinbuchstaben + Bindestriche, z.B. "graylog-restart-sequence"</mat-hint>
          </mat-form-field>

          <mat-form-field appearance="outline" class="full-width">
            <mat-label>Titel</mat-label>
            <input matInput [(ngModel)]="form.title" placeholder="Kurzer, beschreibender Titel">
          </mat-form-field>

          <mat-form-field appearance="outline" class="full-width">
            <mat-label>Beschreibung (1–2 Sätze)</mat-label>
            <textarea matInput [(ngModel)]="form.description" rows="2"
                      placeholder="Wann und wofür diesen Skill nutzen?"></textarea>
          </mat-form-field>

          <mat-form-field appearance="outline" class="full-width">
            <mat-label>Anleitung (Markdown)</mat-label>
            <textarea matInput [(ngModel)]="form.content" rows="10"
                      placeholder="## Schritt 1: ...&#10;## Schritt 2: ..."></textarea>
          </mat-form-field>

          <div class="dialog-row">
            <mat-form-field appearance="outline" class="flex-1">
              <mat-label>Tags (kommagetrennt)</mat-label>
              <input matInput [(ngModel)]="form.tags" placeholder="opensearch, disk, index">
            </mat-form-field>
            <mat-form-field appearance="outline" style="width: 120px">
              <mat-label>Version</mat-label>
              <input matInput [(ngModel)]="form.version" placeholder="1.0">
            </mat-form-field>
          </div>

          <mat-form-field appearance="outline" class="full-width">
            <mat-label>Sichtbarkeit</mat-label>
            <mat-select [(ngModel)]="form.visibility">
              <mat-option value="public">
                <mat-icon>public</mat-icon> Öffentlich — für alle sichtbar
              </mat-option>
              <mat-option value="private">
                <mat-icon>lock</mat-icon> Privat — nur für mich
              </mat-option>
            </mat-select>
          </mat-form-field>
        </div>
        <div class="dialog-footer">
          <button mat-button (click)="closeDialog()">Abbrechen</button>
          <button mat-flat-button color="primary" (click)="saveSkill()" [disabled]="saving()">
            @if (saving()) { <mat-spinner diameter="18"></mat-spinner> }
            @else { {{ editingSkill() ? 'Speichern' : 'Erstellen' }} }
          </button>
        </div>
      </div>
    }
  `,
  styles: [`
    .page-container { padding: 24px; max-width: 1100px; margin: 0 auto; }
    .page-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 24px; gap: 16px; flex-wrap: wrap; }
    .page-header-left h2 { margin: 0; font-size: 22px; }
    .page-subtitle { font-size: 13px; opacity: .6; }
    .page-header-actions { display: flex; gap: 12px; align-items: center; }
    .search-field { width: 260px; }
    ::ng-deep .search-field .mat-mdc-form-field-subscript-wrapper { display: none; }
    .spinner-center { display: flex; justify-content: center; padding: 60px; }

    .empty-state { text-align: center; padding: 60px 20px; opacity: .7; }
    .empty-icon { font-size: 64px; height: 64px; width: 64px; opacity: .3; }
    .empty-sub { font-size: 13px; }

    .skill-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 16px; }
    .skill-card { border-radius: 12px !important; }
    .private-skill { border: 1px solid color-mix(in srgb, var(--mat-sys-tertiary) 30%, transparent) !important; }

    .skill-header { flex: 1; }
    mat-card-header { display: flex; justify-content: space-between; align-items: flex-start; padding: 16px 16px 0; }
    .skill-actions { display: flex; }
    .skill-title-row { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
    .skill-name { font-family: monospace; font-size: 12px; background: var(--mat-sys-surface-container); padding: 2px 6px; border-radius: 4px; }
    .private-icon { font-size: 14px; height: 14px; width: 14px; color: var(--mat-sys-tertiary); }
    .skill-version { font-size: 11px; opacity: .5; margin-left: auto; }
    .skill-title { margin: 0; font-size: 15px; font-weight: 600; }

    mat-card-content { padding: 8px 16px 0 !important; }
    .skill-description { font-size: 13px; opacity: .8; margin: 0 0 10px; line-height: 1.5; }
    .skill-tags { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 8px; }
    .skill-tag { font-size: 11px; padding: 2px 8px; border-radius: 12px; background: color-mix(in srgb, var(--mat-sys-primary) 12%, transparent); color: var(--mat-sys-primary); }
    .skill-meta { font-size: 11px; opacity: .5; display: flex; gap: 6px; }

    .skill-content { padding: 0 16px 16px; }
    .skill-content-text { font-size: 12px; white-space: pre-wrap; background: var(--mat-sys-surface-container); padding: 12px; border-radius: 8px; margin: 0; max-height: 400px; overflow-y: auto; line-height: 1.6; }

    /* Dialog */
    .dialog-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: 999; }
    .skill-dialog { position: fixed; top: 50%; left: 50%; transform: translate(-50%,-50%); z-index: 1000; background: var(--mat-sys-surface); border-radius: 16px; width: min(680px, 95vw); max-height: 90vh; display: flex; flex-direction: column; box-shadow: 0 8px 40px rgba(0,0,0,.3); }
    .dialog-header { display: flex; justify-content: space-between; align-items: center; padding: 20px 24px 16px; border-bottom: 1px solid var(--mat-sys-outline-variant); }
    .dialog-header h3 { margin: 0; font-size: 18px; }
    .dialog-body { padding: 20px 24px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
    .dialog-footer { padding: 16px 24px; border-top: 1px solid var(--mat-sys-outline-variant); display: flex; justify-content: flex-end; gap: 12px; }
    .full-width { width: 100%; }
    .dialog-row { display: flex; gap: 12px; }
    .flex-1 { flex: 1; }
  `],
})
export class SkillsComponent implements OnInit {
  private readonly apiUrl = environment.apiUrl;

  skills = signal<Skill[]>([]);
  filteredSkills = signal<Skill[]>([]);
  loading = signal(true);
  saving = signal(false);
  dialogOpen = signal(false);
  editingSkill = signal<Skill | null>(null);
  expandedId = signal<string | null>(null);
  loadingContent = signal(false);
  selectedContent = signal('');
  searchQuery = '';

  form: SkillForm = {
    name: '', title: '', description: '', content: '',
    tags: '', version: '1.0', visibility: 'public',
  };

  constructor(
    private http: HttpClient,
    private snack: MatSnackBar,
    private auth: AuthService,
  ) {}

  ngOnInit() {
    this.loadSkills();
  }

  loadSkills() {
    this.loading.set(true);
    this.http.get<Skill[]>(`${this.apiUrl}/skills`).subscribe({
      next: skills => {
        this.skills.set(skills);
        this.filteredSkills.set(skills);
        this.loading.set(false);
      },
      error: () => {
        this.snack.open('Skills konnten nicht geladen werden', 'OK', { duration: 3000 });
        this.loading.set(false);
      },
    });
  }

  filterSkills() {
    const q = this.searchQuery.toLowerCase();
    if (!q) {
      this.filteredSkills.set(this.skills());
      return;
    }
    this.filteredSkills.set(this.skills().filter(s =>
      s.name.includes(q) || s.title.toLowerCase().includes(q) ||
      s.description.toLowerCase().includes(q) ||
      s.tags?.some(t => t.includes(q))
    ));
  }

  canEdit(skill: Skill): boolean {
    const role = this.auth.userRole();
    const userId = this.auth.user()?.id;
    return role === 'admin' || role === 'sysadmin' || skill.user_id === String(userId);
  }

  openCreateDialog() {
    this.editingSkill.set(null);
    this.form = { name: '', title: '', description: '', content: '', tags: '', version: '1.0', visibility: 'public' };
    this.dialogOpen.set(true);
  }

  openEditDialog(skill: Skill) {
    this.editingSkill.set(skill);
    // Load full content first
    this.http.get<Skill>(`${this.apiUrl}/skills/${skill.name}`).subscribe(full => {
      this.form = {
        name: full.name,
        title: full.title,
        description: full.description,
        content: full.content || '',
        tags: (full.tags || []).join(', '),
        version: full.version || '1.0',
        visibility: full.visibility || 'public',
      };
      this.dialogOpen.set(true);
    });
  }

  closeDialog() {
    this.dialogOpen.set(false);
    this.editingSkill.set(null);
  }

  saveSkill() {
    if (!this.form.name || !this.form.title || !this.form.content) {
      this.snack.open('Name, Titel und Anleitung sind Pflichtfelder', 'OK', { duration: 3000 });
      return;
    }
    this.saving.set(true);
    const payload = {
      name: this.form.name,
      title: this.form.title,
      description: this.form.description,
      content: this.form.content,
      tags: this.form.tags.split(',').map(t => t.trim()).filter(Boolean),
      version: this.form.version || '1.0',
      visibility: this.form.visibility,
    };

    const editing = this.editingSkill();
    const req = editing
      ? this.http.put(`${this.apiUrl}/skills/${editing.name}`, payload)
      : this.http.post(`${this.apiUrl}/skills`, payload, { observe: 'response' });

    req.subscribe({
      next: () => {
        this.snack.open(editing ? 'Skill aktualisiert' : 'Skill erstellt', 'OK', { duration: 2500 });
        this.saving.set(false);
        this.closeDialog();
        this.loadSkills();
      },
      error: (err) => {
        this.snack.open(err?.error?.detail || 'Fehler beim Speichern', 'OK', { duration: 4000 });
        this.saving.set(false);
      },
    });
  }

  deleteSkill(skill: Skill) {
    if (!confirm(`Skill "${skill.name}" wirklich löschen?`)) return;
    this.http.delete(`${this.apiUrl}/skills/${skill.name}`).subscribe({
      next: () => {
        this.snack.open('Skill gelöscht', 'OK', { duration: 2500 });
        this.loadSkills();
      },
      error: (err) => {
        this.snack.open(err?.error?.detail || 'Löschen fehlgeschlagen', 'OK', { duration: 4000 });
      },
    });
  }

  toggleContent(id: string) {
    if (this.expandedId() === id) {
      this.expandedId.set(null);
      return;
    }
    this.expandedId.set(id);
    const skill = this.skills().find(s => s.id === id);
    if (!skill) return;
    this.loadingContent.set(true);
    this.http.get<Skill>(`${this.apiUrl}/skills/${skill.name}`).subscribe({
      next: full => {
        this.selectedContent.set(full.content || '(kein Inhalt)');
        this.loadingContent.set(false);
      },
      error: () => {
        this.selectedContent.set('Fehler beim Laden');
        this.loadingContent.set(false);
      },
    });
  }
}
