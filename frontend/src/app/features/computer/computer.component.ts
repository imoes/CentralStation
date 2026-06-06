import {
  Component, OnInit, OnDestroy, signal, computed,
  ViewChild, ElementRef, HostListener, inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { HttpClient } from '@angular/common/http';
import { AuthService } from '../../core/auth/auth.service';
import { environment } from '../../../environments/environment';

interface HermesMessage {
  role: 'user' | 'assistant';
  text: string;
}

interface HermesSession {
  session_id: string;
  label: string;
  msg_count: number;
  messages: HermesMessage[];
}

@Component({
  selector: 'app-computer',
  standalone: true,
  imports: [CommonModule, FormsModule, MatIconModule, MatButtonModule],
  template: `
    <div class="computer-panel t-lcars" [class.open]="isOpen()">

      <!-- LCARS Topbar -->
      <div class="panel-top">
        <div class="cap-tl"></div>
        <span class="panel-title">COMPUTER</span>
        <button class="close-btn" (click)="close()" title="Schließen (Esc)">✕</button>
        <div class="cap-tr"></div>
      </div>

      <div class="panel-body">

        <!-- Session Rail (LCARS pills) -->
        <div class="session-rail">
          <div class="rail-head">SESSIONS</div>
          @for (s of sessions(); track s.session_id) {
            <button class="rail-pill"
                    [class.active]="s.session_id === activeTabId()"
                    (click)="selectTab(s.session_id)"
                    [title]="s.label">
              {{ s.label }}
              @if (s.msg_count > 0) {
                <span class="msg-badge">{{ s.msg_count }}</span>
              }
            </button>
          }
          <button class="rail-pill new-pill" (click)="newSession()" title="Neue Session">
            + NEU
          </button>
          @if (activeTabId()) {
            <button class="rail-pill del-pill" (click)="deleteSession()" title="Session beenden">
              ✕ ENDE
            </button>
          }
        </div>

        <!-- Conversation area -->
        <div class="conversation">

          @if (!activeTabId()) {
            <div class="empty-state">
              <div class="empty-icon">◉</div>
              <div class="empty-text">BEREIT</div>
              <div class="empty-sub">Neue Session starten oder Befehl eingeben</div>
            </div>
          }

          <div class="messages" #msgContainer>
            @for (msg of activeMessages(); track $index) {
              <div class="msg" [class.user]="msg.role === 'user'"
                               [class.agent]="msg.role === 'assistant'">
                <span class="msg-label">
                  {{ msg.role === 'user' ? '▶ NUTZER' : '◎ COMPUTER' }}
                </span>
                <div class="msg-text">{{ msg.text }}</div>
              </div>
            }
            @if (loading()) {
              <div class="thinking">
                VERARBEITE<span class="cursor">_</span>
              </div>
            }
          </div>

          <!-- Input -->
          <div class="input-row">
            <input #inputEl
                   class="lcars-input"
                   [(ngModel)]="inputText"
                   placeholder="Computer, ..."
                   [disabled]="loading()"
                   (keydown.enter)="send()"
                   (keydown.escape)="close()" />
            <button class="icon-btn"
                    [class.active]="listening()"
                    (click)="toggleVoice()"
                    title="Spracheingabe">
              <mat-icon>mic</mat-icon>
            </button>
            <button class="send-btn"
                    (click)="send()"
                    [disabled]="loading() || !inputText.trim()">→</button>
          </div>
        </div>

      </div>

      <!-- LCARS Bottom Bar -->
      <div class="panel-bottom">
        <div class="cap-bl"></div>
        <span class="num-cell">{{ sessions().length }} SESSION{{ sessions().length !== 1 ? 'S' : '' }}</span>
        <span class="num-cell">{{ totalMessages() }} MSG</span>
        @if (listening()) {
          <span class="num-cell listening-cell">● REC</span>
        }
        <div class="cap-br"></div>
      </div>

    </div>
  `,
  styleUrl: './computer.component.scss',
})
export class ComputerComponent implements OnInit, OnDestroy {
  @ViewChild('msgContainer') private msgContainer?: ElementRef<HTMLDivElement>;
  @ViewChild('inputEl') private inputEl?: ElementRef<HTMLInputElement>;

  private http = inject(HttpClient);
  private auth = inject(AuthService);
  private apiBase = `${environment.apiUrl}/computer`;

  isOpen = signal(false);
  sessions = signal<HermesSession[]>([]);
  activeTabId = signal<string | null>(null);
  inputText = '';
  loading = signal(false);
  listening = signal(false);

  private mediaRecorder?: MediaRecorder;
  private audioChunks: Blob[] = [];

  activeMessages = computed<HermesMessage[]>(() => {
    const sid = this.activeTabId();
    return this.sessions().find(s => s.session_id === sid)?.messages ?? [];
  });

  totalMessages = computed(() =>
    this.sessions().reduce((sum, s) => sum + s.msg_count, 0)
  );

  // ── Keyboard shortcut ─────────────────────────────────────────────

  @HostListener('document:keydown', ['$event'])
  onKeydown(e: KeyboardEvent): void {
    if (e.ctrlKey && e.key === 'k') {
      e.preventDefault();
      this.toggle();
    }
  }

  ngOnInit(): void {}
  ngOnDestroy(): void {
    this.mediaRecorder?.stop();
  }

  // ── Panel controls ────────────────────────────────────────────────

  toggle(): void { this.isOpen.update(v => !v); this.focusInput(); }
  open(): void   { this.isOpen.set(true);  this.focusInput(); }
  close(): void  { this.isOpen.set(false); }

  private focusInput(): void {
    setTimeout(() => this.inputEl?.nativeElement.focus(), 50);
  }

  // ── Session management ────────────────────────────────────────────

  async newSession(): Promise<void> {
    try {
      const session = await this.http
        .post<{ session_id: string; label: string }>(`${this.apiBase}/sessions`, {})
        .toPromise();
      if (!session) return;
      this.sessions.update(ss => [...ss, { ...session, msg_count: 0, messages: [] }]);
      this.activeTabId.set(session.session_id);
      this.open();
      this.focusInput();
    } catch (err) {
      console.error('Failed to create session:', err);
    }
  }

  selectTab(sid: string): void {
    this.activeTabId.set(sid);
    this.scrollToBottom();
  }

  async deleteSession(): Promise<void> {
    const sid = this.activeTabId();
    if (!sid) return;
    try {
      await this.http.delete(`${this.apiBase}/sessions/${sid}`).toPromise();
    } catch { /* ignore */ }
    this.sessions.update(ss => ss.filter(s => s.session_id !== sid));
    const remaining = this.sessions();
    this.activeTabId.set(remaining.length > 0 ? remaining[remaining.length - 1].session_id : null);
  }

  // ── Send message → SSE stream ─────────────────────────────────────

  async send(): Promise<void> {
    const text = this.inputText.trim();
    if (!text || this.loading()) return;

    let sid = this.activeTabId();
    if (!sid) {
      await this.newSession();
      sid = this.activeTabId();
      if (!sid) return;
    }

    this.inputText = '';
    this.loading.set(true);
    this._addMessage(sid, 'user', text);
    this._addMessage(sid, 'assistant', '');
    this._updateMsgCount(sid);
    this.scrollToBottom();

    const token = this.auth.getAccessToken();
    try {
      const resp = await fetch(`${this.apiBase}/sessions/${sid}/message`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ content: text }),
      });

      if (!resp.ok) {
        this._appendToLast(sid, `[Fehler: HTTP ${resp.status}]`);
        this.loading.set(false);
        return;
      }

      const reader = resp.body!.getReader();
      const dec = new TextDecoder();
      let buf = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split('\n\n');
        buf = parts.pop() ?? '';
        for (const part of parts) {
          if (!part.startsWith('data:')) continue;
          const raw = part.slice(5).trim();
          if (!raw || raw === '[DONE]') continue;
          try {
            const data = JSON.parse(raw);
            if (data.type === 'delta') this._appendToLast(sid, data.text);
            if (data.type === 'done')  this.loading.set(false);
            if (data.type === 'error') {
              this._appendToLast(sid, `\n[Fehler: ${data.text}]`);
              this.loading.set(false);
            }
          } catch { /* skip malformed */ }
        }
        this.scrollToBottom();
      }
    } catch (err) {
      this._appendToLast(sid, `[Verbindungsfehler: ${err}]`);
    } finally {
      this.loading.set(false);
      this.scrollToBottom();
    }
  }

  // ── Voice input ───────────────────────────────────────────────────

  async toggleVoice(): Promise<void> {
    if (this.listening()) {
      this.mediaRecorder?.stop();
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this.audioChunks = [];
      const mimeType = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : 'audio/ogg';
      this.mediaRecorder = new MediaRecorder(stream, { mimeType });

      this.mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) this.audioChunks.push(e.data);
      };

      this.mediaRecorder.onstop = async () => {
        this.listening.set(false);
        stream.getTracks().forEach(t => t.stop());

        const blob = new Blob(this.audioChunks, { type: mimeType });
        const fd = new FormData();
        fd.append('file', blob, `audio.${mimeType.includes('webm') ? 'webm' : 'ogg'}`);

        const token = this.auth.getAccessToken();
        try {
          const r = await fetch(`${this.apiBase}/transcribe`, {
            method: 'POST',
            body: fd,
            headers: token ? { Authorization: `Bearer ${token}` } : {},
          });
          const { text } = await r.json();
          if (text?.trim()) {
            this.inputText = text.trim();
            this.focusInput();
          }
        } catch (err) {
          console.error('Transcription failed:', err);
        }
      };

      this.listening.set(true);
      this.mediaRecorder.start();
      // Auto-stop after 10 seconds
      setTimeout(() => { if (this.listening()) this.mediaRecorder?.stop(); }, 10_000);

    } catch (err) {
      console.error('Microphone access denied:', err);
    }
  }

  // ── Helpers ───────────────────────────────────────────────────────

  private _addMessage(sid: string, role: 'user' | 'assistant', text: string): void {
    this.sessions.update(ss => ss.map(s =>
      s.session_id === sid
        ? { ...s, messages: [...s.messages, { role, text }] }
        : s
    ));
  }

  private _appendToLast(sid: string, text: string): void {
    this.sessions.update(ss => ss.map(s => {
      if (s.session_id !== sid) return s;
      const msgs = [...s.messages];
      if (msgs.length > 0 && msgs[msgs.length - 1].role === 'assistant') {
        msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], text: msgs[msgs.length - 1].text + text };
      }
      return { ...s, messages: msgs };
    }));
  }

  private _updateMsgCount(sid: string): void {
    this.sessions.update(ss => ss.map(s =>
      s.session_id === sid ? { ...s, msg_count: s.msg_count + 1 } : s
    ));
  }

  private scrollToBottom(): void {
    setTimeout(() => {
      const el = this.msgContainer?.nativeElement;
      if (el) el.scrollTop = el.scrollHeight;
    }, 10);
  }
}
