import {
  Component, OnInit, OnDestroy, signal, computed,
  ViewChild, ElementRef, HostListener, inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { marked } from 'marked';
import { AuthService } from '../../core/auth/auth.service';
import { ComputerService } from '../../core/services/computer.service';
import { environment } from '../../../environments/environment';

// Configure marked: no wrapping <p> for simple one-liners, GFM tables + breaks
marked.setOptions({ gfm: true, breaks: true });

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

/**
 * Parse [FEED:key=val&key2=val2] markers from an assistant response.
 * Returns the cleaned text (markers stripped) and the first set of params
 * found (used to auto-navigate the feed).
 */
function parseFeedMarker(text: string): { cleanText: string; params: Record<string, string> | null } {
  const match = /\[FEED:([^\]]+)\]/.exec(text);
  if (!match) return { cleanText: text, params: null };

  const params: Record<string, string> = {};
  match[1].split('&').forEach(part => {
    const [k, ...rest] = part.split('=');
    if (k) params[k.trim()] = rest.join('=').trim();
  });

  const cleanText = text.replace(/\[FEED:[^\]]+\]/g, '').trimEnd();
  return { cleanText, params: Object.keys(params).length > 0 ? params : null };
}

@Component({
  selector: 'app-computer',
  standalone: true,
  imports: [CommonModule, FormsModule, MatIconModule, MatButtonModule],
  template: `
    @if (isOpen()) {
      <div class="computer-backdrop" (click)="close()"></div>
    }
    <div class="computer-panel t-lcars" [class.open]="isOpen()">

      <!-- LCARS Topbar -->
      <div class="panel-top">
        <div class="cap-tl"></div>
        <span class="panel-title">COMPUTER</span>

        <!-- Stop button (only while streaming) -->
        @if (loading()) {
          <button class="stop-btn" (click)="stopGeneration()" title="Antwort abbrechen">
            <mat-icon>stop</mat-icon>
          </button>
        }

        <!-- TTS mute toggle -->
        <button class="tts-btn" [class.muted]="muted()" (click)="toggleMute()"
                [title]="muted() ? 'Sprachausgabe aktivieren' : 'Sprachausgabe stummschalten'">
          <mat-icon>{{ muted() ? 'volume_off' : 'volume_up' }}</mat-icon>
        </button>

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
              <div class="empty-hint">⌨ Strg+K öffnen/schließen · Leertaste = Mikrofon</div>
            </div>
          }

          <div class="messages" #msgContainer>
            @for (msg of activeMessages(); track $index) {
              <div class="msg" [class.user]="msg.role === 'user'"
                               [class.agent]="msg.role === 'assistant'">
                <span class="msg-label">
                  {{ msg.role === 'user' ? '▶ NUTZER' : '◎ COMPUTER' }}
                </span>
                <div class="msg-text"
                     [innerHTML]="renderMarkdown(msg.text)"></div>
              </div>
            }
            @if (loading()) {
              <div class="thinking">
                VERARBEITE<span class="cursor">_</span>
              </div>
            }
          </div>

          <!-- Voice error banner -->
          @if (voiceError()) {
            <div class="voice-error" (click)="voiceError.set(null)">
              ⚠ {{ voiceError() }}
            </div>
          }

          <!-- Input -->
          <div class="input-row">
            <input #inputEl
                   class="lcars-input"
                   [(ngModel)]="inputText"
                   placeholder="Computer, ...  (Leertaste = Mikrofon)"
                   [disabled]="loading()"
                   (keydown.enter)="send()"
                   (keydown.escape)="close()" />
            <button class="icon-btn"
                    [class.active]="listening()"
                    (click)="toggleVoice()"
                    title="Spracheingabe (Leertaste)">
              <mat-icon>{{ listening() ? 'mic' : 'mic_none' }}</mat-icon>
            </button>
            @if (loading()) {
              <button class="stop-inline-btn" (click)="stopGeneration()" title="Abbrechen">
                <mat-icon>stop_circle</mat-icon>
              </button>
            } @else {
              <button class="send-btn"
                      (click)="send()"
                      [disabled]="!inputText.trim()">→</button>
            }
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
        @if (loading()) {
          <span class="num-cell loading-cell">■ AKTIV</span>
        }
        @if (!muted()) {
          <span class="num-cell tts-cell">♪ TTS</span>
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

  private auth = inject(AuthService);
  private router = inject(Router);
  private sanitizer = inject(DomSanitizer);
  private computerService = inject(ComputerService);
  private apiBase = `${environment.apiUrl}/computer`;

  // Maps a host key (e.g. hostname) → session_id so that repeated "Computer, prüfe das"
  // clicks for the same host reuse the existing session instead of always creating a new one.
  private hostSessions = new Map<string, string>();

  renderMarkdown(text: string): SafeHtml {
    const html = marked.parse(text) as string;
    return this.sanitizer.bypassSecurityTrustHtml(html);
  }

  isOpen = signal(false);
  sessions = signal<HermesSession[]>([]);
  activeTabId = signal<string | null>(null);
  inputText = '';
  loading = signal(false);
  listening = signal(false);
  muted = signal(localStorage.getItem('cs_computer_muted') === '1');
  voiceError = signal<string | null>(null);

  private mediaRecorder?: MediaRecorder;
  private audioChunks: Blob[] = [];
  private _ttsAudio?: HTMLAudioElement;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private _recognition?: any;
  private _abortController?: AbortController;
  private _handoffSub?: Subscription;

  activeMessages = computed<HermesMessage[]>(() => {
    const sid = this.activeTabId();
    return this.sessions().find(s => s.session_id === sid)?.messages ?? [];
  });

  totalMessages = computed(() =>
    this.sessions().reduce((sum, s) => sum + s.msg_count, 0)
  );

  // ── Keyboard shortcuts ────────────────────────────────────────────

  @HostListener('document:keydown', ['$event'])
  onKeydown(e: KeyboardEvent): void {
    if (e.ctrlKey && e.key === 'k') {
      e.preventDefault();
      this.toggle();
      return;
    }
    if (e.key === ' ' && this.isOpen()) {
      const active = document.activeElement;
      const isTyping = active instanceof HTMLInputElement
                    || active instanceof HTMLTextAreaElement
                    || (active instanceof HTMLElement && active.isContentEditable);
      if (!isTyping) {
        e.preventDefault();
        this.toggleVoice();
      }
    }
  }

  ngOnInit(): void {
    this._handoffSub = this.computerService.handoff$.subscribe(({ prompt, label, hostKey }) => {
      this._handleHandoff(prompt, label, hostKey);
    });
  }

  ngOnDestroy(): void {
    this._recognition?.abort();
    this.mediaRecorder?.stop();
    this._ttsAudio?.pause();
    this._abortController?.abort();
    this._handoffSub?.unsubscribe();
  }

  // ── Panel controls ────────────────────────────────────────────────

  toggle(): void { this.isOpen.update(v => !v); }
  open(): void   { this.isOpen.set(true); }
  close(): void  { this.isOpen.set(false); this._ttsAudio?.pause(); }

  focusInput(): void {
    setTimeout(() => this.inputEl?.nativeElement.focus(), 50);
  }

  // ── TTS controls ──────────────────────────────────────────────────

  toggleMute(): void {
    this.muted.update(v => {
      const next = !v;
      localStorage.setItem('cs_computer_muted', next ? '1' : '0');
      if (next) { this._ttsAudio?.pause(); this._ttsAudio = undefined; }
      return next;
    });
  }

  /** Extract the concluding paragraph (Fazit) for TTS.
   *  Finds a section starting with Fazit/Zusammenfassung/Empfehlung/Ergebnis/Schluss,
   *  or falls back to the last substantive paragraph. Capped at 300 chars. */
  private _extractFazit(text: string): string {
    let t = text.replace(/\[FEED:[^\]]+\]/g, '').replace(/```[\s\S]*?```/g, '').replace(/`[^`]*`/g, '');
    t = t.replace(/\*\*([^*]+)\*\*/g, '$1').replace(/\*([^*]+)\*/g, '$1');
    t = t.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/^#{1,6}\s+/gm, '');
    // Merge heading line with its following content so "## Fazit\nText" becomes one paragraph
    t = t.replace(/(Fazit|Zusammenfassung|Empfehlung|Ergebnis|Schluss)[:\s]*\n+/gi, '$1: ');
    const paragraphs = t.split(/\n{2,}/)
      .map(p => p.replace(/^\s*[-*>|]+\s*/gm, '').replace(/\s+/g, ' ').trim())
      .filter(p => p.length > 8);
    if (paragraphs.length === 0) return '';
    const fazit = paragraphs.find(p => /^(Fazit|Zusammenfassung|Empfehlung|Ergebnis|Schluss)/i.test(p));
    const chosen = fazit ?? paragraphs[paragraphs.length - 1];
    const clean = chosen.replace(/^(Fazit|Zusammenfassung|Empfehlung|Ergebnis|Schluss)[:\s]*/i, '');
    return clean.length > 300 ? clean.slice(0, 297) + ' …' : clean;
  }

  private speak(text: string): void {
    const fazit = this._extractFazit(text);
    if (this.muted()) return;
    if (!fazit.trim()) {
      console.debug('[TTS] kein Fazit gefunden — keine Wiedergabe');
      return;
    }
    console.debug('[TTS] spreche Fazit (%d Zeichen): %s', fazit.length, fazit.slice(0, 80));
    const token = this.auth.getAccessToken();
    this._ttsAudio?.pause();
    this._ttsAudio = undefined;
    fetch(`${this.apiBase}/tts`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ text: fazit }),
    }).then(r => {
      if (!r.ok) throw new Error(`TTS HTTP ${r.status}`);
      return r.blob();
    }).then(blob => {
      if (this.muted()) return;
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      this._ttsAudio = audio;
      audio.onended = () => URL.revokeObjectURL(url);
      audio.play().catch(err => console.warn('[TTS] Wiedergabe fehlgeschlagen:', err));
    }).catch(err => console.warn('[TTS] Anfrage fehlgeschlagen:', err));
  }

  // ── Incident handoff ──────────────────────────────────────────────

  private async _handleHandoff(prompt: string, label?: string, hostKey?: string): Promise<void> {
    this.open();

    // Reuse an existing session for this host if one exists
    if (hostKey) {
      const existingSid = this.hostSessions.get(hostKey);
      if (existingSid && this.sessions().some(s => s.session_id === existingSid)) {
        this.activeTabId.set(existingSid);
        this.scrollToBottom(true);
        this.inputText = prompt;
        await this.send();
        return;
      }
      // Session was deleted or never existed — fall through to create a new one
      this.hostSessions.delete(hostKey);
    }

    await this.newSession(label);
    const sid = this.activeTabId();
    if (!sid) return;

    if (hostKey) {
      this.hostSessions.set(hostKey, sid);
    }

    this.inputText = prompt;
    await this.send();
  }

  // ── Session management ────────────────────────────────────────────

  async newSession(label?: string): Promise<void> {
    const token = this.auth.getAccessToken();
    try {
      const r = await fetch(`${this.apiBase}/sessions`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({}),
      });
      if (!r.ok) { console.error('Session creation failed:', r.status); return; }
      const session: { session_id: string; label: string } = await r.json();
      const displayLabel = label ?? session.label;
      this.sessions.update(ss => [...ss, { ...session, label: displayLabel, msg_count: 0, messages: [] }]);
      this.activeTabId.set(session.session_id);
      this.open();
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
    const token = this.auth.getAccessToken();
    try {
      await fetch(`${this.apiBase}/sessions/${sid}`, {
        method: 'DELETE',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
    } catch { /* ignore */ }

    // Remove host → session mapping so the next handoff creates a fresh session
    for (const [key, id] of this.hostSessions) {
      if (id === sid) { this.hostSessions.delete(key); break; }
    }

    this.sessions.update(ss => ss.filter(s => s.session_id !== sid));
    const remaining = this.sessions();
    this.activeTabId.set(remaining.length > 0 ? remaining[remaining.length - 1].session_id : null);
  }

  // ── Stop generation ───────────────────────────────────────────────

  stopGeneration(): void {
    this._abortController?.abort();
    this.loading.set(false);
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
    this.scrollToBottom(true); // force: immer zur neuen Nachricht scrollen

    const token = this.auth.getAccessToken();
    let fullAssistantText = '';
    let wasAborted = false;
    this._abortController = new AbortController();

    try {
      const resp = await fetch(`${this.apiBase}/sessions/${sid}/message`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ content: text }),
        signal: this._abortController.signal,
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
            if (data.type === 'delta') {
              fullAssistantText += data.text;
              this._appendToLast(sid, data.text);
            }
            if (data.type === 'error') {
              this._appendToLast(sid, `\n[Fehler: ${data.text}]`);
            }
          } catch { /* skip malformed */ }
        }
        this.scrollToBottom();
      }

      // The SSE stream can close before the final \n\n reaches the buffer,
      // leaving the last event (often the "done" event) unparsed.
      // Process whatever remains so no content or markers are dropped.
      if (buf.startsWith('data:')) {
        const raw = buf.slice(5).trim();
        if (raw && raw !== '[DONE]') {
          try {
            const data = JSON.parse(raw);
            if (data.type === 'delta') {
              fullAssistantText += data.text;
              this._appendToLast(sid, data.text);
            }
          } catch { /* ignore */ }
        }
      }

    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        wasAborted = true;
        this._appendToLast(sid, ' [gestoppt]');
      } else {
        this._appendToLast(sid, `[Verbindungsfehler: ${err}]`);
      }
    } finally {
      // Always strip [FEED:...] markers and attach action buttons, regardless
      // of how the stream ended (normal close, abort, or error).
      if (fullAssistantText) {
        this._finishAssistantMessage(sid, fullAssistantText);
      }
      // Read the Fazit aloud — but not if the user explicitly stopped generation.
      if (!wasAborted && fullAssistantText) {
        this.speak(fullAssistantText);
      }
      this.loading.set(false);
      this.scrollToBottom();
    }
  }

  // ── Voice input ───────────────────────────────────────────────────

  toggleVoice(): void {
    if (this.listening()) {
      this._recognition?.stop();
      this.mediaRecorder?.stop();
      this.listening.set(false);
      return;
    }

    this.voiceError.set(null);

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const SR = (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition;
    if (SR) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      this._recognition = new SR() as any;
      this._recognition.lang = 'de-DE';
      this._recognition.continuous = false;
      this._recognition.interimResults = false;

      this._recognition.onstart = () => this.listening.set(true);
      this._recognition.onend   = () => this.listening.set(false);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      this._recognition.onerror = (e: any) => {
        this.listening.set(false);
        if (e.error === 'not-allowed') {
          this.voiceError.set('Mikrofon-Zugriff verweigert — Browsereinstellungen prüfen');
        } else if (e.error === 'network') {
          this.voiceError.set('Spracherkennung benötigt Internetverbindung');
        } else if (e.error !== 'no-speech') {
          this.voiceError.set(`Fehler: ${e.error}`);
        }
      };
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      this._recognition.onresult = (e: any) => {
        const text = e.results[0][0].transcript.trim();
        if (text) {
          this.inputText = text;
          this.send();
        }
      };

      try {
        this._recognition.start();
        return;
      } catch (err) {
        console.warn('SpeechRecognition start failed, trying Whisper fallback:', err);
      }
    }

    if (!navigator.mediaDevices?.getUserMedia) {
      this.voiceError.set('Mikrofon nicht verfügbar – HTTPS oder localhost erforderlich');
      return;
    }

    navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
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
        fd.append('file', blob, mimeType.includes('webm') ? 'audio.webm' : 'audio.ogg');

        const token = this.auth.getAccessToken();
        try {
          const r = await fetch(`${this.apiBase}/transcribe`, {
            method: 'POST',
            body: fd,
            headers: token ? { Authorization: `Bearer ${token}` } : {},
          });
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          const { text } = await r.json();
          if (text?.trim()) {
            this.inputText = text.trim();
            this.send();
          }
        } catch (err) {
          this.voiceError.set(`Transkription fehlgeschlagen: ${err}`);
        }
      };

      this.listening.set(true);
      this.mediaRecorder.start();
      setTimeout(() => { if (this.listening()) this.mediaRecorder?.stop(); }, 10_000);

    }).catch(err => {
      const msg = err instanceof DOMException && err.name === 'NotAllowedError'
        ? 'Mikrofon-Zugriff verweigert — Berechtigung im Browser prüfen'
        : `Mikrofon-Fehler: ${err}`;
      this.voiceError.set(msg);
    });
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

  /**
   * Called once streaming is complete.
   * Strips [FEED:...] markers from the displayed text and, if any were found,
   * automatically navigates to the feed with the matching query params.
   * The Computer panel stays open on top.
   */
  private _finishAssistantMessage(sid: string, fullText: string): void {
    const { cleanText, params } = parseFeedMarker(fullText);
    this.sessions.update(ss => ss.map(s => {
      if (s.session_id !== sid) return s;
      const msgs = [...s.messages];
      const last = msgs[msgs.length - 1];
      if (last?.role === 'assistant') {
        msgs[msgs.length - 1] = { ...last, text: cleanText };
      }
      return { ...s, messages: msgs };
    }));
    if (params) {
      this.router.navigate(['/feed'], { queryParams: params });
    }
  }

  private _updateMsgCount(sid: string): void {
    this.sessions.update(ss => ss.map(s =>
      s.session_id === sid ? { ...s, msg_count: s.msg_count + 1 } : s
    ));
  }

  /** Scroll to bottom only if the user is already near the end (within 120px).
   *  Pass force=true when the user sends a new message (always scroll). */
  private scrollToBottom(force = false): void {
    setTimeout(() => {
      const el = this.msgContainer?.nativeElement;
      if (!el) return;
      const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
      if (force || nearBottom) {
        el.scrollTop = el.scrollHeight;
      }
    }, 10);
  }
}
