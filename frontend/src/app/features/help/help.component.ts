import { Component, OnInit, AfterViewInit, ViewChild, ElementRef, signal, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { marked } from 'marked';
import { environment } from '../../../environments/environment';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

@Component({
  selector: 'cs-help',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatIconModule,
    MatInputModule,
    MatFormFieldModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
  ],
  template: `
    <div class="help-shell">

      <!-- Left: Documentation -->
      <div class="doc-panel">
        <div class="doc-header">
          <mat-icon>menu_book</mat-icon>
          <span>Dokumentation</span>
        </div>
        @if (docLoading()) {
          <div class="doc-loading"><mat-spinner diameter="32"></mat-spinner></div>
        } @else {
          <div class="doc-content" #docContent [innerHTML]="docHtml()" (click)="onDocClick($event)"></div>
        }
      </div>

      <!-- Right: Chatbot -->
      <div class="chat-panel">
        <div class="chat-header">
          <mat-icon>psychology</mat-icon>
          <span>Hilfe-Assistent</span>
          <span class="chat-hint">Stell Fragen zur Dokumentation</span>
        </div>

        <div class="chat-messages" #chatMessages>
          @if (messages().length === 0) {
            <div class="chat-empty">
              <mat-icon>live_help</mat-icon>
              <p>Stelle eine Frage zur CentralStation-Dokumentation.</p>
              <div class="suggestions">
                @for (s of suggestions; track s) {
                  <button class="suggestion-chip" (click)="askSuggestion(s)">{{ s }}</button>
                }
              </div>
            </div>
          }
          @for (msg of messages(); track $index) {
            <div class="msg" [class.user]="msg.role === 'user'" [class.assistant]="msg.role === 'assistant'">
              <div class="msg-avatar">
                <mat-icon>{{ msg.role === 'user' ? 'person' : 'psychology' }}</mat-icon>
              </div>
              <div class="msg-bubble">{{ msg.content }}</div>
            </div>
          }
          @if (asking()) {
            <div class="msg assistant">
              <div class="msg-avatar"><mat-icon>psychology</mat-icon></div>
              <div class="msg-bubble thinking">
                <span class="dot"></span><span class="dot"></span><span class="dot"></span>
              </div>
            </div>
          }
        </div>

        <div class="chat-input-row">
          <mat-form-field appearance="outline" class="chat-field">
            <textarea matInput rows="2" [(ngModel)]="question"
              placeholder="Frage eingeben… (Enter = Senden, Shift+Enter = Zeilenumbruch)"
              (keydown)="onKey($event)"></textarea>
          </mat-form-field>
          <button mat-flat-button color="primary" class="send-btn"
            [disabled]="!question.trim() || asking()"
            (click)="ask()">
            <mat-icon>send</mat-icon>
          </button>
        </div>
      </div>

    </div>
  `,
  styles: [`
    .help-shell {
      display: grid;
      grid-template-columns: 1fr 380px;
      height: calc(100vh - 0px);
      overflow: hidden;
      background: var(--mat-sys-surface);
    }

    /* ── Documentation panel ──────────────────────────── */
    .doc-panel {
      display: flex;
      flex-direction: column;
      overflow: hidden;
      border-right: 1px solid var(--mat-sys-outline-variant);
    }
    .doc-header {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 16px 24px;
      font-size: 15px;
      font-weight: 700;
      border-bottom: 1px solid var(--mat-sys-outline-variant);
      flex-shrink: 0;
      background: var(--mat-sys-surface-container);
    }
    .doc-header mat-icon { color: var(--mat-sys-primary); }
    .doc-loading { flex: 1; display: flex; align-items: center; justify-content: center; }
    .doc-content {
      flex: 1;
      overflow-y: auto;
      padding: 24px 40px;
      max-width: 900px;
      line-height: 1.7;
      font-size: 14px;
    }

    /* markdown styles applied to rendered HTML */
    .doc-content ::ng-deep {
      h1 { font-size: 26px; font-weight: 900; margin: 0 0 16px; letter-spacing: -.03em; color: var(--mat-sys-on-surface); }
      h2 { font-size: 20px; font-weight: 700; margin: 32px 0 10px; padding-bottom: 6px; border-bottom: 1px solid var(--mat-sys-outline-variant); color: var(--mat-sys-on-surface); }
      h3 { font-size: 16px; font-weight: 700; margin: 20px 0 8px; color: var(--mat-sys-on-surface); }
      h4 { font-size: 14px; font-weight: 700; margin: 14px 0 6px; }
      p { margin: 0 0 12px; color: var(--mat-sys-on-surface-variant); }
      a { color: var(--mat-sys-primary); text-decoration: none; }
      a:hover { text-decoration: underline; }
      strong { color: var(--mat-sys-on-surface); font-weight: 700; }
      code {
        background: var(--mat-sys-surface-container);
        padding: 2px 6px;
        border-radius: 4px;
        font-family: 'Fira Code', monospace;
        font-size: 12px;
        color: var(--mat-sys-primary);
      }
      pre {
        background: var(--mat-sys-surface-container);
        border-radius: 8px;
        padding: 14px 18px;
        overflow-x: auto;
        margin: 12px 0;
        border-left: 3px solid var(--mat-sys-primary);
      }
      pre code { background: none; padding: 0; color: var(--mat-sys-on-surface); font-size: 12px; }
      table {
        width: 100%;
        border-collapse: collapse;
        margin: 12px 0;
        font-size: 13px;
      }
      th {
        background: var(--mat-sys-surface-container);
        text-align: left;
        padding: 8px 12px;
        font-weight: 700;
        border: 1px solid var(--mat-sys-outline-variant);
        color: var(--mat-sys-on-surface);
      }
      td {
        padding: 7px 12px;
        border: 1px solid var(--mat-sys-outline-variant);
        color: var(--mat-sys-on-surface-variant);
        vertical-align: top;
      }
      tr:nth-child(even) td { background: color-mix(in srgb, var(--mat-sys-surface-container) 40%, transparent); }
      ul, ol { padding-left: 22px; margin: 0 0 12px; color: var(--mat-sys-on-surface-variant); }
      li { margin-bottom: 4px; }
      hr { border: none; border-top: 1px solid var(--mat-sys-outline-variant); margin: 24px 0; }
      blockquote { border-left: 3px solid var(--mat-sys-primary); padding: 4px 16px; margin: 12px 0; color: var(--mat-sys-on-surface-variant); background: color-mix(in srgb, var(--mat-sys-primary) 6%, transparent); border-radius: 0 6px 6px 0; }
    }

    /* ── Chat panel ───────────────────────────────────── */
    .chat-panel {
      display: flex;
      flex-direction: column;
      overflow: hidden;
      background: var(--mat-sys-surface-container-low);
    }
    .chat-header {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 16px 18px;
      font-size: 15px;
      font-weight: 700;
      border-bottom: 1px solid var(--mat-sys-outline-variant);
      flex-shrink: 0;
      background: var(--mat-sys-surface-container);
    }
    .chat-header mat-icon { color: var(--mat-sys-primary); }
    .chat-hint { font-size: 11px; font-weight: 400; color: var(--mat-sys-on-surface-variant); margin-left: auto; }

    .chat-messages {
      flex: 1;
      overflow-y: auto;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .chat-empty {
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 12px;
      color: var(--mat-sys-on-surface-variant);
      text-align: center;
      padding: 24px;
    }
    .chat-empty mat-icon { font-size: 40px; height: 40px; width: 40px; opacity: .5; }
    .chat-empty p { margin: 0; font-size: 13px; }
    .suggestions { display: flex; flex-direction: column; gap: 6px; width: 100%; }
    .suggestion-chip {
      background: var(--mat-sys-surface-container);
      border: 1px solid var(--mat-sys-outline-variant);
      border-radius: 8px;
      padding: 8px 12px;
      font-size: 12px;
      text-align: left;
      cursor: pointer;
      color: var(--mat-sys-on-surface);
      transition: background .15s;
    }
    .suggestion-chip:hover { background: color-mix(in srgb, var(--mat-sys-primary) 10%, var(--mat-sys-surface-container)); }

    .msg { display: flex; gap: 8px; align-items: flex-start; }
    .msg.user { flex-direction: row-reverse; }
    .msg-avatar {
      width: 30px; height: 30px; border-radius: 50%;
      background: var(--mat-sys-surface-container);
      display: flex; align-items: center; justify-content: center;
      flex-shrink: 0;
    }
    .msg-avatar mat-icon { font-size: 16px; height: 16px; width: 16px; color: var(--mat-sys-primary); }
    .msg-bubble {
      max-width: 85%;
      padding: 10px 14px;
      border-radius: 12px;
      font-size: 13px;
      line-height: 1.5;
      white-space: pre-wrap;
    }
    .msg.user .msg-bubble { background: var(--mat-sys-primary); color: var(--mat-sys-on-primary); border-radius: 12px 4px 12px 12px; }
    .msg.assistant .msg-bubble { background: var(--mat-sys-surface-container); color: var(--mat-sys-on-surface); border-radius: 4px 12px 12px 12px; }

    .thinking { display: flex; gap: 4px; align-items: center; padding: 14px 18px; }
    .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--mat-sys-primary); animation: blink 1.2s infinite; }
    .dot:nth-child(2) { animation-delay: .2s; }
    .dot:nth-child(3) { animation-delay: .4s; }
    @keyframes blink { 0%,80%,100% { opacity:.2; transform: scale(.8); } 40% { opacity:1; transform: scale(1); } }

    .chat-input-row {
      display: flex;
      gap: 8px;
      align-items: flex-end;
      padding: 12px 16px;
      border-top: 1px solid var(--mat-sys-outline-variant);
      background: var(--mat-sys-surface-container);
      flex-shrink: 0;
    }
    .chat-field { flex: 1; }
    .send-btn { height: 42px; flex-shrink: 0; }

    @media (max-width: 900px) {
      .help-shell { grid-template-columns: 1fr; grid-template-rows: 60% 40%; }
      .doc-panel { border-right: none; border-bottom: 1px solid var(--mat-sys-outline-variant); }
    }
  `],
})
export class HelpComponent implements OnInit, AfterViewInit {
  @ViewChild('chatMessages') private chatEl!: ElementRef<HTMLElement>;
  @ViewChild('docContent') private docContentEl!: ElementRef<HTMLElement>;

  private http = inject(HttpClient);
  private sanitizer = inject(DomSanitizer);

  docHtml = signal<SafeHtml>('');
  docLoading = signal(true);
  messages = signal<ChatMessage[]>([]);
  asking = signal(false);
  question = '';

  suggestions = [
    'Wie konfiguriere ich ein Zeitreihen-Widget?',
    'Was sind gespeicherte Suchen und wie nutze ich sie?',
    'Wie funktioniert die KI-Anreicherung?',
    'Welche Benutzerrollen gibt es?',
    'Wie synchronisiert CentralStation Jira-Tickets?',
  ];

  ngOnInit() {
    this.http.get<{ content: string }>(`${environment.apiUrl}/help/content`).subscribe({
      next: res => {
        Promise.resolve(marked.parse(res.content)).then(html => {
          this.docHtml.set(this.sanitizer.bypassSecurityTrustHtml(html as string));
          this.docLoading.set(false);
        }).catch(() => this.docLoading.set(false));
      },
      error: () => this.docLoading.set(false),
    });
  }

  ngAfterViewInit() {}

  onDocClick(event: MouseEvent) {
    const anchor = (event.target as HTMLElement).closest('a') as HTMLAnchorElement | null;
    if (!anchor) return;
    const href = anchor.getAttribute('href');
    if (href?.startsWith('#')) {
      event.preventDefault();
      event.stopPropagation();
      const id = href.slice(1);
      const target = document.getElementById(id);
      if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  onKey(event: KeyboardEvent) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.ask();
    }
  }

  askSuggestion(s: string) {
    this.question = s;
    this.ask();
  }

  ask() {
    const q = this.question.trim();
    if (!q || this.asking()) return;
    this.question = '';
    this.messages.update(m => [...m, { role: 'user', content: q }]);
    this.asking.set(true);
    this.scrollToBottom();

    this.http.post<{ answer: string }>(`${environment.apiUrl}/help/ask`, { question: q }).subscribe({
      next: res => {
        this.messages.update(m => [...m, { role: 'assistant', content: res.answer }]);
        this.asking.set(false);
        this.scrollToBottom();
      },
      error: () => {
        this.messages.update(m => [...m, { role: 'assistant', content: 'Fehler beim Abrufen der Antwort. Bitte prüfe ob der LLM konfiguriert ist.' }]);
        this.asking.set(false);
        this.scrollToBottom();
      },
    });
  }

  private scrollToBottom() {
    setTimeout(() => {
      if (this.chatEl) this.chatEl.nativeElement.scrollTop = this.chatEl.nativeElement.scrollHeight;
    }, 50);
  }
}
