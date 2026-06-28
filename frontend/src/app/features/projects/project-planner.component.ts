import { Component, AfterViewInit, ElementRef, ViewChild, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { ProjectsService, ProposedStep, ToolActivity } from '../../core/services/projects.service';
import { I18nService } from '../../core/services/i18n.service';
import { ThemeService } from '../../core/services/theme.service';

interface ChatMsg {
  role: 'user' | 'assistant';
  content: string;
  activity?: ToolActivity[];
  sources?: string[];
  openPoints?: string[];
}

const ISSUE_COLORS: Record<string, string> = {
  epic: '#9B59B6', story: '#2ECC71', task: '#3498DB', subtask: '#1ABC9C', bug: '#E74C3C',
};

@Component({
  selector: 'cs-project-planner',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatButtonModule, MatIconModule, MatProgressSpinnerModule, MatTooltipModule, MatSnackBarModule,
  ],
  template: `
    <div class="pv" [class.t-lcars]="theme()==='lcars'" [class.t-holo]="theme()==='holo'" [class.t-classic]="theme()==='classic'">

      <!-- ══ Top sweep ══ -->
      <div class="topbar">
        <div class="cap cap-tl"></div>
        <div class="bar-seg seg-a">{{ i18n.t('projects.planner_title') }}</div>
        <div class="topbar-fill"></div>
        <button class="sweep-action" (click)="back()">
          <mat-icon>arrow_back</mat-icon> {{ i18n.t('projects.back') }}
        </button>
        <div class="cap cap-tr"></div>
      </div>

      <div class="body">
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
                @if (msg.activity?.length) {
                  <div class="activity">
                    <div class="activity-label">{{ i18n.t('projects.research') }}</div>
                    @for (a of msg.activity!; track $index) {
                      <div class="activity-row" [class.failed]="!a.ok">
                        <mat-icon>{{ a.tool === 'web_fetch' ? 'description' : 'travel_explore' }}</mat-icon>
                        <span class="activity-detail">{{ a.detail }}</span>
                      </div>
                    }
                  </div>
                }
                <div class="msg-bubble">{{ msg.content }}</div>
                @if (msg.openPoints?.length) {
                  <div class="annot open-points">
                    <div class="annot-label"><mat-icon>flag</mat-icon> {{ i18n.t('projects.open_points') }}</div>
                    <ul>@for (op of msg.openPoints!; track $index) { <li>{{ op }}</li> }</ul>
                  </div>
                }
                @if (msg.sources?.length) {
                  <div class="annot sources">
                    <div class="annot-label"><mat-icon>link</mat-icon> {{ i18n.t('projects.sources') }}</div>
                    @for (s of msg.sources!; track $index) {
                      <a [href]="s" target="_blank" class="source-link">{{ s }}</a>
                    }
                  </div>
                }
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
            <textarea #inputArea [(ngModel)]="inputText" (keydown.enter)="onEnter($event)"
                      [placeholder]="i18n.t('projects.planner_placeholder')" rows="3" class="chat-textarea"></textarea>
            <button class="send-btn" (click)="send()" [disabled]="thinking() || !inputText.trim()">
              <mat-icon>send</mat-icon>
            </button>
          </div>
        </div>

        <!-- Right: editable step preview -->
        <div class="preview-panel">
          <div class="preview-header">
            <span class="preview-label">{{ i18n.t('projects.plan_preview') }}</span>
            @if (proposedSteps().length > 0) {
              <button class="sweep-action small" (click)="openSaveDialog()">
                <mat-icon>save</mat-icon> {{ i18n.t('projects.save_as_project') }}
              </button>
            }
          </div>

          @if (proposedSteps().length === 0) {
            <div class="preview-empty">{{ i18n.t('projects.plan_preview_empty') }}</div>
          } @else {
            <div class="step-tree">
              @for (s of rootSteps(); track s.temp_id) {
                <div class="step-node" (click)="editStep(s)">
                  <div class="step-icon" [style.background]="issueColor(s.jira_issue_type)">{{ issueIcon(s.jira_issue_type) }}</div>
                  <div class="step-info">
                    <div class="step-title">{{ s.title }}</div>
                    <div class="step-meta">
                      <span class="issue-type">{{ s.jira_issue_type }}</span>
                      <span>{{ s.duration_days }}d</span>
                      @if (s.depends_on.length > 0) { <span class="deps">→ {{ s.depends_on.length }}</span> }
                    </div>
                  </div>
                  <mat-icon class="edit-hint">edit</mat-icon>
                </div>
                @for (child of childSteps(s.temp_id); track child.temp_id) {
                  <div class="step-node child" (click)="editStep(child)">
                    <div class="step-icon" [style.background]="issueColor(child.jira_issue_type)">{{ issueIcon(child.jira_issue_type) }}</div>
                    <div class="step-info">
                      <div class="step-title">{{ child.title }}</div>
                      <div class="step-meta">
                        <span class="issue-type">{{ child.jira_issue_type }}</span>
                        <span>{{ child.duration_days }}d</span>
                      </div>
                    </div>
                    <mat-icon class="edit-hint">edit</mat-icon>
                  </div>
                }
              }
            </div>
          }
        </div>
      </div>
    </div>

    <!-- Edit proposed step overlay -->
    @if (editing()) {
      <div class="dialog-overlay" (click)="editing.set(null)">
        <div class="dialog" (click)="$event.stopPropagation()">
          <h3>{{ i18n.t('projects.edit_proposed') }}</h3>
          <label>{{ i18n.t('projects.step') }}</label>
          <input class="dialog-input" [(ngModel)]="edTitle" />
          <label>Beschreibung</label>
          <textarea class="dialog-input" [(ngModel)]="edDescription" rows="3"></textarea>
          <div class="dialog-row">
            <div class="dialog-col">
              <label>Typ</label>
              <select class="dialog-input" [(ngModel)]="edType">
                <option value="epic">Epic</option><option value="story">Story</option>
                <option value="task">Task</option><option value="subtask">Subtask</option><option value="bug">Bug</option>
              </select>
            </div>
            <div class="dialog-col">
              <label>Dauer (Tage)</label>
              <input class="dialog-input" type="number" [(ngModel)]="edDuration" min="1" />
            </div>
          </div>
          @if (otherSteps().length > 0) {
            <label>{{ i18n.t('projects.deps_label') }}</label>
            <div class="dep-list">
              @for (o of otherSteps(); track o.temp_id) {
                <label class="dep-item">
                  <input type="checkbox" [checked]="edDeps.includes(o.temp_id)" (change)="toggleDep(o.temp_id)" />
                  <span>{{ o.title }}</span>
                </label>
              }
            </div>
          }
          <div class="dialog-actions">
            <button class="btn-text danger" (click)="deleteStep()">{{ i18n.t('projects.delete') }}</button>
            <span class="spacer"></span>
            <button class="btn-text" (click)="editing.set(null)">{{ i18n.t('dialog.cancel') }}</button>
            <button class="btn-solid" (click)="saveStepEdit()">{{ i18n.t('projects.save') }}</button>
          </div>
        </div>
      </div>
    }

    <!-- Save dialog overlay -->
    @if (showSaveDialog()) {
      <div class="dialog-overlay" (click)="showSaveDialog.set(false)">
        <div class="dialog" (click)="$event.stopPropagation()">
          <h3>{{ i18n.t('projects.save_dialog_title') }}</h3>
          <input class="dialog-input" [(ngModel)]="saveName" [placeholder]="i18n.t('projects.name_placeholder')" />
          <textarea class="dialog-input" [(ngModel)]="saveDescription" [placeholder]="i18n.t('projects.desc_placeholder')" rows="3"></textarea>
          <div class="dialog-actions">
            <span class="spacer"></span>
            <button class="btn-text" (click)="showSaveDialog.set(false)">{{ i18n.t('dialog.cancel') }}</button>
            <button class="btn-solid" (click)="saveProject()" [disabled]="!saveName.trim() || saving()">
              @if (saving()) { <mat-spinner diameter="16"></mat-spinner> }
              {{ i18n.t('projects.save') }}
            </button>
          </div>
        </div>
      </div>
    }
  `,
  styles: [`
    .pv { display:flex; flex-direction:column; height:100%; min-height:0; font-family:Roboto,'Helvetica Neue',sans-serif; }

    /* structural */
    .topbar { display:flex; align-items:center; gap:6px; flex-shrink:0; height:46px; padding:6px 6px 0; }
    .cap { width:60px; height:100%; flex-shrink:0; }
    .bar-seg { height:100%; display:flex; align-items:center; padding:0 18px; font-weight:800; letter-spacing:.14em;
               font-size:13px; text-transform:uppercase; font-family:'Antonio','Eurostile',sans-serif; }
    .topbar-fill { flex:1; height:100%; }
    .sweep-action { border:none; cursor:pointer; font-family:'Antonio','Eurostile',sans-serif; font-weight:800;
                    letter-spacing:.1em; font-size:13px; text-transform:uppercase; height:100%; padding:0 20px;
                    display:flex; align-items:center; gap:8px; flex-shrink:0; }
    .sweep-action.small, .sweep-action.solo { height:34px; border-radius:8px; }
    .sweep-action mat-icon { font-size:18px; width:18px; height:18px; }

    .body { display:flex; flex:1; min-height:0; gap:6px; padding:6px; }
    .chat-panel { display:flex; flex-direction:column; width:50%; min-width:0; border-radius:8px; overflow:hidden; }
    .chat-messages { flex:1; overflow-y:auto; padding:16px; display:flex; flex-direction:column; gap:14px; }
    .chat-welcome { display:flex; flex-direction:column; align-items:center; gap:12px; padding:40px 0; opacity:.7; }
    .chat-welcome mat-icon { font-size:40px; width:40px; height:40px; }

    .chat-msg { display:flex; flex-direction:column; gap:6px; }
    .chat-msg.user { align-items:flex-end; }
    .msg-bubble { max-width:88%; padding:10px 14px; border-radius:12px; font-size:0.92rem; line-height:1.5; white-space:pre-wrap; }
    .thinking { display:flex; align-items:center; gap:8px; }

    /* research activity */
    .activity { max-width:88%; display:flex; flex-direction:column; gap:3px; padding:8px 10px; border-radius:10px; }
    .activity-label { font-size:.65rem; font-weight:800; letter-spacing:.14em; text-transform:uppercase; opacity:.7; margin-bottom:2px; }
    .activity-row { display:flex; align-items:center; gap:7px; font-size:.78rem; }
    .activity-row mat-icon { font-size:15px; width:15px; height:15px; }
    .activity-row.failed { opacity:.45; text-decoration:line-through; }
    .activity-detail { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }

    .annot { max-width:88%; padding:8px 12px; border-radius:10px; font-size:.82rem; }
    .annot-label { display:flex; align-items:center; gap:6px; font-size:.68rem; font-weight:800; letter-spacing:.1em; text-transform:uppercase; margin-bottom:4px; }
    .annot-label mat-icon { font-size:14px; width:14px; height:14px; }
    .annot ul { margin:0; padding-left:18px; line-height:1.5; }
    .source-link { display:block; font-size:.78rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }

    .chat-input-area { display:flex; align-items:flex-end; gap:8px; padding:12px; }
    .chat-textarea { flex:1; border-radius:8px; padding:10px 12px; font-size:0.95rem; resize:none; outline:none; font-family:Roboto,sans-serif; }
    .send-btn { border:none; cursor:pointer; width:44px; height:44px; border-radius:8px; display:flex; align-items:center; justify-content:center; }

    .preview-panel { flex:1; display:flex; flex-direction:column; min-width:0; border-radius:8px; overflow:hidden; }
    .preview-header { display:flex; align-items:center; justify-content:space-between; padding:12px 16px; flex-shrink:0; }
    .preview-label { font-size:0.8rem; text-transform:uppercase; letter-spacing:.12em; font-weight:800; font-family:'Antonio','Eurostile',sans-serif; }
    .preview-empty { padding:40px; text-align:center; opacity:.6; }

    .step-tree { flex:1; overflow-y:auto; padding:12px; display:flex; flex-direction:column; gap:6px; }
    .step-node { display:flex; align-items:center; gap:10px; padding:9px 10px; border-radius:0 8px 8px 0; cursor:pointer; transition:filter .12s; }
    .step-node:hover { filter:brightness(1.12); }
    .step-node:hover .edit-hint { opacity:.8; }
    .step-node.child { margin-left:34px; }
    .step-icon { width:28px; height:28px; border-radius:5px; flex-shrink:0; display:flex; align-items:center; justify-content:center; font-size:12px; font-weight:800; color:#000; }
    .step-info { flex:1; min-width:0; }
    .step-title { font-size:0.9rem; margin-bottom:2px; }
    .step-meta { display:flex; gap:8px; font-size:0.74rem; opacity:.75; }
    .issue-type { text-transform:uppercase; font-weight:700; }
    .edit-hint { opacity:0; font-size:16px; width:16px; height:16px; transition:opacity .12s; }

    /* dialogs */
    .dialog-overlay { position:fixed; inset:0; background:rgba(0,0,0,.6); display:flex; align-items:center; justify-content:center; z-index:1500; }
    .dialog { border-radius:10px; padding:22px; width:460px; max-width:92vw; max-height:88vh; overflow-y:auto; display:flex; flex-direction:column; gap:9px; }
    .dialog h3 { margin:0 0 4px; font-family:'Antonio','Eurostile',sans-serif; text-transform:uppercase; letter-spacing:.06em; }
    .dialog label { font-size:.74rem; text-transform:uppercase; letter-spacing:.06em; opacity:.7; }
    .dialog-input { border-radius:6px; padding:8px 11px; font-size:0.92rem; outline:none; resize:vertical; width:100%; box-sizing:border-box; font-family:Roboto,sans-serif; }
    .dialog-row { display:flex; gap:10px; }
    .dialog-col { flex:1; display:flex; flex-direction:column; gap:4px; }
    .dep-list { display:flex; flex-direction:column; gap:4px; max-height:140px; overflow-y:auto; padding:4px 0; }
    .dep-item { display:flex; align-items:center; gap:8px; font-size:.85rem; text-transform:none; letter-spacing:0; opacity:1; cursor:pointer; }
    .dialog-actions { display:flex; align-items:center; gap:8px; margin-top:8px; }
    .spacer { flex:1; }
    .btn-text, .btn-solid { border:none; cursor:pointer; padding:8px 16px; border-radius:8px; font-weight:700; font-size:.85rem; font-family:'Antonio','Eurostile',sans-serif; text-transform:uppercase; letter-spacing:.06em; }
    .btn-text { background:transparent; }
    .btn-text.danger { color:#e74c3c; }

    /* ════ CLASSIC ════ */
    .t-classic { background:#f4f6f9; color:#1f2933; }
    .t-classic .cap { display:none; }
    .t-classic .topbar { padding:8px 12px; }
    .t-classic .seg-a { background:#1565c0; color:#fff; border-radius:14px; }
    .t-classic .sweep-action { background:#1565c0; color:#fff; border-radius:14px; }
    .t-classic .chat-panel, .t-classic .preview-panel { background:#fff; border:1px solid #dde6ef; }
    .t-classic .user .msg-bubble { background:#1565c0; color:#fff; }
    .t-classic .assistant .msg-bubble { background:#eef2f7; }
    .t-classic .activity { background:#eef4fb; color:#3a5a78; }
    .t-classic .annot.open-points { background:#fff7ed; border:1px solid #fbbf77; } .t-classic .open-points .annot-label { color:#b8860b; }
    .t-classic .annot.sources { background:#eef4fb; } .t-classic .source-link { color:#1565c0; }
    .t-classic .chat-textarea, .t-classic .dialog-input { background:#fff; border:1px solid #d7e0ea; color:#1f2933; }
    .t-classic .send-btn { background:#1565c0; color:#fff; }
    .t-classic .step-node { background:#f1f5fa; border-left:4px solid #90a4b8; }
    .t-classic .dialog { background:#fff; color:#1f2933; }
    .t-classic .btn-solid { background:#1565c0; color:#fff; }
    .t-classic .preview-label, .t-classic .dialog h3 { color:#1565c0; }

    /* ════ HOLO ════ */
    .t-holo { color:#cfeeff; background:linear-gradient(160deg,#02060f,#050d1a 60%,#02060f); }
    .t-holo .cap { display:none; }
    .t-holo .seg-a { background:rgba(79,214,255,.14); color:#9fe8ff; border:1px solid rgba(79,214,255,.35); border-radius:8px; }
    .t-holo .sweep-action { background:rgba(79,214,255,.15); color:#9fe8ff; border:1px solid #4fd6ff; border-radius:8px; }
    .t-holo .chat-panel, .t-holo .preview-panel { background:rgba(10,28,46,.5); border:1px solid rgba(79,214,255,.2); }
    .t-holo .user .msg-bubble { background:rgba(79,214,255,.2); color:#cff6ff; }
    .t-holo .assistant .msg-bubble { background:rgba(10,28,46,.8); border:1px solid rgba(79,214,255,.2); }
    .t-holo .activity { background:rgba(79,214,255,.08); color:#8fd8f0; }
    .t-holo .annot.open-points { background:rgba(255,216,74,.08); border:1px solid rgba(255,216,74,.4); } .t-holo .open-points .annot-label { color:#ffe27a; }
    .t-holo .annot.sources { background:rgba(79,214,255,.08); } .t-holo .source-link { color:#7fdfff; }
    .t-holo .chat-textarea, .t-holo .dialog-input { background:rgba(2,6,15,.6); border:1px solid rgba(79,214,255,.3); color:#cfeeff; }
    .t-holo .send-btn { background:rgba(79,214,255,.2); color:#9fe8ff; border:1px solid #4fd6ff; }
    .t-holo .step-node { background:rgba(10,28,46,.7); border-left:4px solid #4fd6ff; }
    .t-holo .dialog { background:#050d1a; color:#cfeeff; border:1px solid rgba(79,214,255,.35); }
    .t-holo .btn-solid { background:rgba(79,214,255,.2); color:#9fe8ff; border:1px solid #4fd6ff; }
    .t-holo .preview-label, .t-holo .dialog h3 { color:#9fe8ff; }

    /* ════ LCARS ════ */
    .t-lcars { background:#000; color:#FF9933; }
    .t-lcars .cap { background:#FF9933; }
    .t-lcars .cap-tl { border-radius:46px 0 0 0; }
    .t-lcars .cap-tr { border-radius:0 46px 0 0; width:34px; }
    .t-lcars .seg-a { background:#ffcc66; color:#000; min-width:200px; }
    .t-lcars .topbar-fill { background:#FF9933; }
    .t-lcars .sweep-action { background:#99CCFF; color:#000; }
    .t-lcars .chat-panel, .t-lcars .preview-panel { background:#0a0804; }
    .t-lcars .user .msg-bubble { background:#ffcc66; color:#000; }
    .t-lcars .assistant .msg-bubble { background:#15120c; color:#ffcc99; }
    .t-lcars .activity { background:#15120c; color:#99CCFF; }
    .t-lcars .annot.open-points { background:#1a1206; border-left:4px solid #ffcc00; } .t-lcars .open-points .annot-label { color:#ffcc00; }
    .t-lcars .annot.sources { background:#15120c; } .t-lcars .source-link { color:#99CCFF; }
    .t-lcars .chat-textarea, .t-lcars .dialog-input { background:#15120c; border:1px solid #3a2810; color:#ffe8a0; }
    .t-lcars .send-btn { background:#FF9933; color:#000; }
    .t-lcars .step-node { background:#15120c; border-left:5px solid #FF9933; }
    .t-lcars .step-node:nth-child(3n) { border-left-color:#99CCFF; }
    .t-lcars .step-title { color:#ffcc99; }
    .t-lcars .dialog { background:#15120c; color:#ffe8a0; border-left:18px solid #FF9933; border-radius:0 10px 10px 0; }
    .t-lcars .btn-solid { background:#FF9933; color:#000; }
    .t-lcars .preview-label, .t-lcars .dialog h3 { color:#FF9933; }
    .t-lcars .preview-empty, .t-lcars .chat-welcome { color:#e8a060; }
  `],
})
export class ProjectPlannerComponent implements AfterViewInit {
  @ViewChild('chatScroll') private chatScroll!: ElementRef<HTMLDivElement>;
  @ViewChild('inputArea') private inputArea!: ElementRef<HTMLTextAreaElement>;

  private svc = inject(ProjectsService);
  private router = inject(Router);
  private snack = inject(MatSnackBar);
  i18n = inject(I18nService);
  private themeSvc = inject(ThemeService);
  theme = this.themeSvc.theme;

  messages = signal<ChatMsg[]>([]);
  proposedSteps = signal<ProposedStep[]>([]);
  thinking = signal(false);
  showSaveDialog = signal(false);
  saving = signal(false);
  editing = signal<ProposedStep | null>(null);
  inputText = '';
  saveName = '';
  saveDescription = '';

  // edit fields
  edTitle = ''; edDescription = ''; edType = 'task'; edDuration = 1; edDeps: string[] = [];

  ngAfterViewInit() { this.inputArea?.nativeElement.focus(); }

  onEnter(ev: Event) {
    const ke = ev as KeyboardEvent;
    if (!ke.shiftKey) { ke.preventDefault(); this.send(); }
  }

  send() {
    const content = this.inputText.trim();
    if (!content || this.thinking()) return;
    this.inputText = '';
    this.messages.update(ms => [...ms, { role: 'user', content }]);
    this.thinking.set(true);
    this.scrollToBottom();

    const allMsgs = this.messages().map(m => ({ role: m.role, content: m.content }));
    this.svc.runPlanner(allMsgs).subscribe({
      next: resp => {
        this.thinking.set(false);
        this.messages.update(ms => [...ms, {
          role: 'assistant', content: resp.reply,
          activity: resp.tool_activity, sources: resp.sources, openPoints: resp.open_points,
        }]);
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
    setTimeout(() => { const el = this.chatScroll?.nativeElement; if (el) el.scrollTop = el.scrollHeight; }, 50);
  }

  private guessName(text: string): string {
    const words = text.split(/\s+/).slice(0, 5).join(' ');
    return words.length > 30 ? words.slice(0, 30) + '…' : words;
  }

  rootSteps(): ProposedStep[] { return this.proposedSteps().filter(s => !s.parent_temp_id); }
  childSteps(parentId: string): ProposedStep[] { return this.proposedSteps().filter(s => s.parent_temp_id === parentId); }
  otherSteps(): ProposedStep[] { const e = this.editing(); return this.proposedSteps().filter(s => s.temp_id !== e?.temp_id); }

  issueColor(type: string): string { return ISSUE_COLORS[type] ?? '#888'; }
  issueIcon(type: string): string { return { epic: 'E', story: 'S', task: 'T', subtask: '↳', bug: 'B' }[type] ?? '?'; }

  // ── editing proposed steps ──
  editStep(s: ProposedStep) {
    this.editing.set(s);
    this.edTitle = s.title;
    this.edDescription = s.description;
    this.edType = s.jira_issue_type;
    this.edDuration = s.duration_days;
    this.edDeps = [...s.depends_on];
  }

  toggleDep(tempId: string) {
    this.edDeps = this.edDeps.includes(tempId)
      ? this.edDeps.filter(d => d !== tempId)
      : [...this.edDeps, tempId];
  }

  saveStepEdit() {
    const e = this.editing();
    if (!e) return;
    this.proposedSteps.update(steps => steps.map(s => s.temp_id === e.temp_id ? {
      ...s, title: this.edTitle, description: this.edDescription,
      jira_issue_type: this.edType, duration_days: Number(this.edDuration) || 1, depends_on: this.edDeps,
    } : s));
    this.editing.set(null);
  }

  deleteStep() {
    const e = this.editing();
    if (!e) return;
    this.proposedSteps.update(steps =>
      steps.filter(s => s.temp_id !== e.temp_id)
           .map(s => ({ ...s, depends_on: s.depends_on.filter(d => d !== e.temp_id),
                        parent_temp_id: s.parent_temp_id === e.temp_id ? null : s.parent_temp_id })));
    this.editing.set(null);
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
      error: () => { this.saving.set(false); this.snack.open('Fehler beim Speichern', 'OK', { duration: 3000 }); },
    });
  }

  back() { this.router.navigate(['/projects']); }
}
