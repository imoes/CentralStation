import { Component, OnInit, AfterViewInit, ElementRef, ViewChild, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { ProjectsService, ProposedStep } from '../../core/services/projects.service';
import { I18nService } from '../../core/services/i18n.service';

interface ChatMsg { role: 'user' | 'assistant'; content: string; }

@Component({
  selector: 'cs-project-planner',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatButtonModule, MatIconModule, MatInputModule,
    MatProgressSpinnerModule, MatSnackBarModule,
  ],
  template: `
    <div class="planner-container">
      <div class="planner-header lcars-header">
        <div class="header-elbow"></div>
        <div class="header-title">{{ i18n.t('projects.planner_title') }}</div>
        <div class="header-actions">
          <button mat-button (click)="back()">
            <mat-icon>arrow_back</mat-icon> {{ i18n.t('projects.back') }}
          </button>
        </div>
      </div>

      <div class="planner-body">
        <!-- Left: Chat -->
        <div class="chat-panel">
          <div class="chat-messages" #chatScroll>
            @if (messages().length === 0) {
              <div class="chat-welcome">
                <mat-icon>auto_awesome</mat-icon>
                <p>{{ i18n.t('projects.planner_welcome') }}</p>
              </div>
            }
            @for (msg of messages(); track $index) {
              <div class="chat-msg" [class.user]="msg.role === 'user'" [class.assistant]="msg.role === 'assistant'">
                <div class="msg-bubble">{{ msg.content }}</div>
              </div>
            }
            @if (thinking()) {
              <div class="chat-msg assistant">
                <div class="msg-bubble thinking">
                  <mat-spinner diameter="16"></mat-spinner>
                  <span>{{ i18n.t('projects.planner_thinking') }}</span>
                </div>
              </div>
            }
          </div>

          <div class="chat-input-area">
            <textarea
              #inputArea
              [(ngModel)]="inputText"
              (keydown.enter)="onEnter($event)"
              [placeholder]="i18n.t('projects.planner_placeholder')"
              rows="3"
              class="chat-textarea"
            ></textarea>
            <button mat-raised-button (click)="send()" [disabled]="thinking() || !inputText.trim()">
              <mat-icon>send</mat-icon>
            </button>
          </div>
        </div>

        <!-- Right: Step preview -->
        <div class="preview-panel">
          <div class="preview-header">
            <span class="preview-label">{{ i18n.t('projects.plan_preview') }}</span>
            @if (proposedSteps().length > 0) {
              <button mat-raised-button (click)="openSaveDialog()">
                <mat-icon>save</mat-icon> {{ i18n.t('projects.save_as_project') }}
              </button>
            }
          </div>

          @if (proposedSteps().length === 0) {
            <div class="preview-empty">{{ i18n.t('projects.plan_preview_empty') }}</div>
          } @else {
            <div class="step-tree">
              @for (s of rootSteps(); track s.temp_id) {
                <div class="step-node">
                  <div class="step-icon" [style.background]="issueTypeColor(s.jira_issue_type)">
                    {{ issueTypeIcon(s.jira_issue_type) }}
                  </div>
                  <div class="step-info">
                    <div class="step-title">{{ s.title }}</div>
                    <div class="step-meta">
                      <span class="issue-type">{{ s.jira_issue_type }}</span>
                      <span class="duration">{{ s.duration_days }}d</span>
                      @if (s.depends_on.length > 0) {
                        <span class="deps">→ {{ s.depends_on.length }} Abhängigkeit(en)</span>
                      }
                    </div>
                  </div>
                </div>
                @for (child of childSteps(s.temp_id); track child.temp_id) {
                  <div class="step-node child">
                    <div class="step-icon" [style.background]="issueTypeColor(child.jira_issue_type)">
                      {{ issueTypeIcon(child.jira_issue_type) }}
                    </div>
                    <div class="step-info">
                      <div class="step-title">{{ child.title }}</div>
                      <div class="step-meta">
                        <span class="issue-type">{{ child.jira_issue_type }}</span>
                        <span class="duration">{{ child.duration_days }}d</span>
                      </div>
                    </div>
                  </div>
                }
              }
            </div>
          }
        </div>
      </div>
    </div>

    <!-- Save dialog overlay -->
    @if (showSaveDialog()) {
      <div class="dialog-overlay" (click)="showSaveDialog.set(false)">
        <div class="save-dialog" (click)="$event.stopPropagation()">
          <h3>{{ i18n.t('projects.save_dialog_title') }}</h3>
          <input class="dialog-input" [(ngModel)]="saveName" [placeholder]="i18n.t('projects.name_placeholder')" />
          <textarea class="dialog-input" [(ngModel)]="saveDescription" [placeholder]="i18n.t('projects.desc_placeholder')" rows="3"></textarea>
          <div class="dialog-actions">
            <button mat-button (click)="showSaveDialog.set(false)">{{ i18n.t('dialog.cancel') }}</button>
            <button mat-raised-button (click)="saveProject()" [disabled]="!saveName.trim() || saving()">
              @if (saving()) { <mat-spinner diameter="16"></mat-spinner> }
              {{ i18n.t('projects.save') }}
            </button>
          </div>
        </div>
      </div>
    }
  `,
  styles: [`
    .planner-container { display: flex; flex-direction: column; height: 100%; background: var(--cs-bg); }

    .lcars-header { display: flex; align-items: center; gap: 0; padding: 0; }
    .header-elbow {
      width: 32px; height: 56px;
      border-top-left-radius: 24px;
      background: var(--cs-accent, #FFCC99);
      flex-shrink: 0;
    }
    .header-title { padding: 0 24px; font-size: 1.4rem; font-weight: 700; letter-spacing: 0.05em; color: var(--cs-accent, #FFCC99); flex: 1; }
    .header-actions { padding-right: 16px; }

    .planner-body { display: flex; flex: 1; overflow: hidden; }

    .chat-panel { display: flex; flex-direction: column; width: 50%; border-right: 1px solid var(--cs-border, #333); }
    .chat-messages { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
    .chat-welcome { display: flex; flex-direction: column; align-items: center; gap: 12px; color: var(--cs-text-muted); padding: 40px 0; }
    .chat-welcome mat-icon { font-size: 40px; width: 40px; height: 40px; }

    .chat-msg { display: flex; }
    .chat-msg.user { justify-content: flex-end; }
    .msg-bubble {
      max-width: 80%; padding: 10px 14px; border-radius: 12px;
      font-size: 0.9rem; line-height: 1.5; white-space: pre-wrap;
    }
    .user .msg-bubble { background: var(--cs-accent, #FFCC99); color: #000; border-bottom-right-radius: 2px; }
    .assistant .msg-bubble { background: var(--cs-surface, #1a1a2e); border: 1px solid var(--cs-border, #333); border-bottom-left-radius: 2px; }
    .thinking { display: flex; align-items: center; gap: 8px; color: var(--cs-text-muted); }

    .chat-input-area { display: flex; align-items: flex-end; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--cs-border, #333); }
    .chat-textarea {
      flex: 1; background: var(--cs-surface, #1a1a2e); border: 1px solid var(--cs-border, #333);
      border-radius: 4px; padding: 8px 12px; color: var(--cs-text); font-size: 0.95rem; resize: none; outline: none;
    }
    .chat-textarea:focus { border-color: var(--cs-accent, #FFCC99); }

    .preview-panel { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
    .preview-header { display: flex; align-items: center; justify-content: space-between; padding: 16px 20px; border-bottom: 1px solid var(--cs-border, #333); }
    .preview-label { font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--cs-text-muted); }
    .preview-empty { padding: 40px; color: var(--cs-text-muted); text-align: center; }

    .step-tree { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 6px; }
    .step-node { display: flex; align-items: flex-start; gap: 10px; padding: 8px; border-radius: 4px; }
    .step-node:hover { background: var(--cs-surface, #1a1a2e); }
    .step-node.child { padding-left: 36px; }
    .step-icon {
      width: 28px; height: 28px; border-radius: 4px; flex-shrink: 0;
      display: flex; align-items: center; justify-content: center;
      font-size: 12px; font-weight: 700; color: #000;
    }
    .step-info { flex: 1; }
    .step-title { font-size: 0.9rem; color: var(--cs-text); margin-bottom: 2px; }
    .step-meta { display: flex; gap: 8px; font-size: 0.75rem; color: var(--cs-text-muted); }
    .issue-type { text-transform: uppercase; font-weight: 600; }
    .duration { }
    .deps { color: #FFCC66; }

    .dialog-overlay {
      position: fixed; inset: 0; background: rgba(0,0,0,0.6);
      display: flex; align-items: center; justify-content: center; z-index: 1000;
    }
    .save-dialog {
      background: var(--cs-surface, #1a1a2e); border: 1px solid var(--cs-border, #333);
      border-radius: 8px; padding: 24px; width: 420px; display: flex; flex-direction: column; gap: 16px;
    }
    .save-dialog h3 { margin: 0; color: var(--cs-accent, #FFCC99); }
    .dialog-input {
      background: var(--cs-bg); border: 1px solid var(--cs-border, #333); border-radius: 4px;
      padding: 8px 12px; color: var(--cs-text); font-size: 0.95rem; outline: none; resize: vertical;
    }
    .dialog-input:focus { border-color: var(--cs-accent, #FFCC99); }
    .dialog-actions { display: flex; justify-content: flex-end; gap: 8px; }
  `],
})
export class ProjectPlannerComponent implements AfterViewInit {
  @ViewChild('chatScroll') private chatScroll!: ElementRef<HTMLDivElement>;
  @ViewChild('inputArea') private inputArea!: ElementRef<HTMLTextAreaElement>;

  private svc = inject(ProjectsService);
  private router = inject(Router);
  private snack = inject(MatSnackBar);
  i18n = inject(I18nService);

  messages = signal<ChatMsg[]>([]);
  proposedSteps = signal<ProposedStep[]>([]);
  thinking = signal(false);
  showSaveDialog = signal(false);
  saving = signal(false);
  inputText = '';
  saveName = '';
  saveDescription = '';

  ngAfterViewInit() {
    this.inputArea?.nativeElement.focus();
  }

  onEnter(ev: Event) {
    const ke = ev as KeyboardEvent;
    if (!ke.shiftKey) { ke.preventDefault(); this.send(); }
  }

  send() {
    const content = this.inputText.trim();
    if (!content || this.thinking()) return;
    this.inputText = '';

    const userMsg: ChatMsg = { role: 'user', content };
    this.messages.update(ms => [...ms, userMsg]);
    this.thinking.set(true);
    this.scrollToBottom();

    const allMsgs = this.messages().map(m => ({ role: m.role, content: m.content }));
    this.svc.runPlanner(allMsgs).subscribe({
      next: resp => {
        this.thinking.set(false);
        this.messages.update(ms => [...ms, { role: 'assistant', content: resp.reply }]);
        if (resp.steps.length > 0) this.proposedSteps.set(resp.steps);
        if (!this.saveName) this.saveName = this.guessName(content);
        this.scrollToBottom();
      },
      error: () => {
        this.thinking.set(false);
        this.messages.update(ms => [...ms, { role: 'assistant', content: 'Fehler beim KI-Aufruf. Bitte erneut versuchen.' }]);
        this.scrollToBottom();
      },
    });
  }

  private scrollToBottom() {
    setTimeout(() => {
      const el = this.chatScroll?.nativeElement;
      if (el) el.scrollTop = el.scrollHeight;
    }, 50);
  }

  private guessName(text: string): string {
    const words = text.split(/\s+/).slice(0, 5).join(' ');
    return words.length > 30 ? words.slice(0, 30) + '…' : words;
  }

  rootSteps(): ProposedStep[] {
    return this.proposedSteps().filter(s => !s.parent_temp_id);
  }

  childSteps(parentId: string): ProposedStep[] {
    return this.proposedSteps().filter(s => s.parent_temp_id === parentId);
  }

  issueTypeColor(type: string): string {
    return { epic: '#9B59B6', story: '#2ECC71', task: '#3498DB', subtask: '#1ABC9C', bug: '#E74C3C' }[type] ?? '#888';
  }

  issueTypeIcon(type: string): string {
    return { epic: 'E', story: 'S', task: 'T', subtask: '↳', bug: 'B' }[type] ?? '?';
  }

  openSaveDialog() { this.showSaveDialog.set(true); }

  saveProject() {
    if (!this.saveName.trim()) return;
    this.saving.set(true);
    this.svc.savePlan(this.saveName.trim(), this.saveDescription.trim() || null, this.proposedSteps()).subscribe({
      next: project => {
        this.saving.set(false);
        this.showSaveDialog.set(false);
        this.snack.open(`Projekt "${project.name}" gespeichert`, 'OK', { duration: 3000 });
        this.router.navigate(['/projects', project.id]);
      },
      error: () => {
        this.saving.set(false);
        this.snack.open('Fehler beim Speichern', 'OK', { duration: 3000 });
      },
    });
  }

  back() { this.router.navigate(['/projects']); }
}
