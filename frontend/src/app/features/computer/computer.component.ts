import {
  Component, OnInit, OnDestroy, signal, computed,
  ViewChild, ElementRef, HostListener, inject, NgZone,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { MatSnackBar } from '@angular/material/snack-bar';
import { FormsModule } from '@angular/forms';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { marked } from 'marked';
import { AuthService } from '../../core/auth/auth.service';
import {
  ComputerService,
  TicketActivity,
  TicketActivitySnapshot,
  TicketReference,
} from '../../core/services/computer.service';
import { I18nService } from '../../core/services/i18n.service';
import { environment } from '../../../environments/environment';
import { TicketCreateDialogComponent } from '../../shared/ticket-dialog/ticket-create-dialog.component';

// Configure marked: no wrapping <p> for simple one-liners, GFM tables + breaks
marked.setOptions({ gfm: true, breaks: true });

interface ToolCall {
  tool: string;
  done: boolean;
}

interface HermesMessage {
  role: 'user' | 'assistant';
  text: string;
  /** Current tool being executed — shown as a spinner line while streaming. */
  activeTool?: string;
  /** Permanent log of all tool calls made during this message turn. */
  toolCalls?: ToolCall[];
}

interface HermesSession {
  session_id: string;
  label: string;
  msg_count: number;
  messages: HermesMessage[];
  /** Which agent handled this session: hermes | claude_cli | codex_cli */
  agent_type?: string;
  /** Alert external_id — present only for alert-triggered sessions. */
  external_id?: string;
  /** True after the user clicked "✓ GELÖST" and the learning comment was saved. */
  resolved?: boolean;
  ticket_ref?: { connector_id: string; issue_id: string; key: string } | null;
  context_hash?: string | null;
  has_activity_snapshot?: boolean;
  context_synced_at?: string | null;
  last_activity_at?: string;
  reused?: boolean;
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
  imports: [CommonModule, FormsModule, MatIconModule, MatButtonModule, MatDialogModule],
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
          <button class="stop-btn" (click)="stopGeneration()" title="Cancel response">
            <mat-icon>stop</mat-icon>
          </button>
        }

        <button class="sync-btn" (click)="manualSync()"
                [disabled]="computerService.ticketActivityRefreshing()"
                title="Ticket-Neuigkeiten jetzt synchronisieren"
                aria-label="Ticket-Neuigkeiten jetzt synchronisieren">
          <mat-icon [class.spinning]="computerService.ticketActivityRefreshing()">sync</mat-icon>
        </button>

        <!-- TTS mute toggle -->
        <button class="tts-btn" [class.muted]="muted()" (click)="toggleMute()"
                [title]="muted() ? 'Enable voice output' : 'Mute voice output'">
          <mat-icon>{{ muted() ? 'volume_off' : 'volume_up' }}</mat-icon>
        </button>

        <button class="close-btn" (click)="close()" title="Close (Esc)">✕</button>
        <div class="cap-tr"></div>
      </div>

      <div class="panel-body">

        <!-- Session Rail (LCARS pills) -->
        <div class="session-rail">
          <div class="rail-head">SESSIONS</div>

          <!-- Scrollable session list -->
          <div class="rail-sessions">
            @for (s of sessions(); track s.session_id) {
              @if (editingSid() === s.session_id) {
                <input class="rail-pill rail-edit"
                       [value]="s.label"
                       (keydown.enter)="renameSession(s.session_id, $any($event.target).value)"
                       (keydown.escape)="editingSid.set(null)"
                       (blur)="renameSession(s.session_id, $any($event.target).value)"
                       autofocus>
              } @else {
                <button class="rail-pill"
                        [class.active]="s.session_id === activeTabId()"
                        (click)="selectTab(s.session_id)"
                        (dblclick)="editingSid.set(s.session_id)"
                        [title]="s.label + ' (Doppelklick zum Umbenennen)'">
                  {{ s.label }}
                  @if (s.agent_type === 'claude_cli') {
                    <span class="agent-badge agent-claude" title="Claude CLI">CL</span>
                  } @else if (s.agent_type === 'codex_cli') {
                    <span class="agent-badge agent-codex" title="Codex CLI">CO</span>
                  }
                  @if (s.msg_count > 0) {
                    <span class="msg-badge">{{ s.msg_count }}</span>
                  }
                  @if (sessionActivity(s.session_id); as activity) {
                    @if (activity.state === 'changed') {
                      <span class="activity-badge"
                            [attr.aria-label]="activity.comment_change_count + ' Kommentaränderungen'">
                        {{ activity.comment_change_count > 9 ? '9+' : (activity.comment_change_count || '!') }}
                      </span>
                    } @else if (activity.state === 'unavailable') {
                      <span class="activity-warning" aria-label="Jira-Quelle nicht erreichbar">!</span>
                    }
                  }
                </button>
              }
            }
          </div>

          <!-- Fixed action buttons -->
          <div class="rail-actions">
            <button class="rail-pill new-pill" (click)="newSession()" title="New session">
              + NEW
            </button>
            @if (activeTabId()) {
              <button class="rail-pill del-pill" (click)="deleteSession()" title="End session">
                ✕ END
              </button>
            }
            @if (activeSession()?.external_id && !activeSession()?.resolved) {
              <button class="rail-pill resolve-pill" (click)="resolveSession()"
                      title="Mark as resolved — saves learning comment on the alert">
                ✓ RESOLVED
              </button>
            }
            @if (activeSession()?.resolved) {
              <span class="rail-pill resolved-pill">✓ RESOLVED</span>
            }
            @if (activeMessages().length > 0) {
              <button class="rail-pill ticket-pill" (click)="createTicket()"
                      title="Create Jira ticket from this conversation">
                🎫 TICKET
              </button>
            }
            <!-- Write approval: the in-container guard blocks system-modifying
                 commands and cannot see the chat, so consent must be granted here. -->
            @if (writeApprovalUntil()) {
              <button class="rail-pill write-active-pill" (click)="revokeWrite()"
                      title="Schreibzugriff ist freigegeben — klicken zum sofortigen Sperren">
                🔓 SCHREIBEN {{ writeRemaining() }}
              </button>
            } @else {
              <button class="rail-pill write-pill" (click)="grantWrite()"
                      title="Erlaubt dem Agenten für 15 Minuten Schreib-/Systembefehle (auch via SSH). Eine Zustimmung im Chat allein genügt nicht.">
                🔒 SCHREIBEN FREIGEBEN
              </button>
            }
            @if (activeTabId()) {
              <button class="rail-pill workbench-pill" (click)="sendToWorkbench()"
                      title="Transfer session to workbench">
                ⬡ WORKBENCH
              </button>
            }
          </div>
        </div>

        <!-- Conversation area -->
        <div class="conversation">

          @if (!activeTabId()) {
            <div class="empty-state">
              <div class="empty-icon">◉</div>
              <div class="empty-text">BEREIT</div>
              <div class="empty-sub">Start a new session or enter a command</div>
              <div class="empty-hint">⌨ Ctrl+K open/close · Space = microphone</div>
            </div>
          }

          <div class="messages" #msgContainer (scroll)="onMessagesScroll()">
            @for (msg of completedMessages(); track msg) {
              <div class="msg" [class.user]="msg.role === 'user'"
                               [class.agent]="msg.role === 'assistant'">
                <div class="msg-header">
                  <span class="msg-label">
                    {{ msg.role === 'user' ? '▶ NUTZER' : '◎ COMPUTER' }}
                  </span>
                  @if (msg.role === 'assistant' && msg.text.trim()) {
                    <button class="tts-msg-btn"
                            (click)="speakMessage(msg.text)"
                            [title]="muted() ? 'Muted (TTS disabled)' : 'Read summary aloud'">
                      <mat-icon>{{ muted() ? 'volume_off' : 'volume_up' }}</mat-icon>
                    </button>
                  }
                </div>
                <div class="msg-text"
                     [innerHTML]="renderMarkdown(msg)"></div>
                <!-- Tool calls of PAST turns are intentionally not rendered; only the
                     live streaming turn below shows tool activity. -->
              </div>
            }
            @if (activeTicketActivity(); as activity) {
              @if (!activityDismissed(activity)) {
                @if (activity.state === 'unavailable') {
                  <section class="ticket-activity ticket-activity--warning" aria-live="polite">
                    <mat-icon>cloud_off</mat-icon>
                    <div class="ticket-activity-content">
                      <strong>{{ activity.ticket_ref.key }} · Quelle nicht erreichbar</strong>
                      <span>Der letzte bekannte Stand bleibt erhalten. Es wurde nichts als gelesen markiert.</span>
                    </div>
                    <button class="activity-later" (click)="dismissActivity(activity)" title="Hinweis einklappen">Später</button>
                  </section>
                } @else if (activity.state === 'changed') {
                  <section class="ticket-activity" aria-live="polite">
                    <div class="ticket-activity-heading">
                      <div>
                        <strong>{{ activity.ticket_ref.key }} · Neue Aktivität</strong>
                        <span>
                          {{ activity.comment_change_count }} Kommentaränderung{{ activity.comment_change_count === 1 ? '' : 'en' }}
                          @if (activity.ticket_changed) { · Ticketdaten geändert }
                          @if (activity.source_unavailable) { · Quelle derzeit nicht erreichbar }
                        </span>
                      </div>
                      <button class="activity-later" (click)="dismissActivity(activity)">Später</button>
                    </div>

                    <div class="activity-details">
                      @for (comment of activity.new_comments; track comment.id) {
                        <article class="activity-comment">
                          <span class="activity-kind">NEU</span>
                          <strong>{{ comment.author || '?' }}</strong>
                          <time>{{ formatActivityTime(comment.created) }}</time>
                          <p>{{ comment.body }}</p>
                        </article>
                      }
                      @for (comment of activity.edited_comments; track comment.id) {
                        <article class="activity-comment">
                          <span class="activity-kind activity-kind--edited">BEARBEITET</span>
                          <strong>{{ comment.author || '?' }}</strong>
                          <time>{{ formatActivityTime(comment.updated || comment.created) }}</time>
                          <p>{{ comment.body }}</p>
                        </article>
                      }
                      @for (change of activity.field_changes; track change.field) {
                        <div class="activity-field">
                          <strong>{{ change.label }}</strong>
                          @if (change.field === 'description_hash') {
                            <span>wurde geändert</span>
                          } @else {
                            <span>{{ change.before || '(leer)' }} → {{ change.after || '(leer)' }}</span>
                          }
                        </div>
                      }
                      @if (activity.deleted_comment_ids.length > 0) {
                        <div class="activity-field">
                          {{ activity.deleted_comment_ids.length }} Kommentar(e) wurde(n) entfernt.
                        </div>
                      }
                      @if (activity.ticket_changed && activity.field_changes.length === 0) {
                        <div class="activity-field">Weitere Ticketdaten wurden geändert.</div>
                      }
                    </div>

                    <button class="activity-accept" (click)="acceptTicketActivity(activity)"
                            [disabled]="loading() || acceptingTicketActivity() || activity.source_unavailable"
                            [title]="activity.source_unavailable ? 'Erst nach erfolgreicher Jira-Prüfung verfügbar' : ''">
                      <mat-icon>add_comment</mat-icon>
                      {{ acceptingTicketActivity() ? 'WIRD ÜBERNOMMEN …' : 'IN EINGABE ÜBERNEHMEN' }}
                    </button>
                  </section>
                }
              }
            }
            @if (streamingMsg(); as sm) {
              <div class="msg agent">
                <div class="msg-header">
                  <span class="msg-label">◎ COMPUTER</span>
                </div>
                <div class="msg-text streaming">{{ sm.text }}</div>
                @if (sm.toolCalls?.length) {
                  <div class="tool-log">
                    @for (tc of sm.toolCalls!; track tc) {
                      <div class="tool-log-entry" [class.running]="!tc.done">
                        <span class="tool-icon">{{ tc.done ? '✓' : '⟳' }}</span>
                        <span class="tool-name">{{ tc.tool }}</span>
                      </div>
                    }
                  </div>
                }
              </div>
            }
            @if (loading()) {
              <div class="thinking">
                VERARBEITE<span class="cursor">_</span>
              </div>
            }
          </div>

          <!-- Voice error banner -->
          <!-- Jump back to the newest message. Sits outside the scrolling container,
               otherwise it would scroll away with the content it is meant to reach. -->
          @if (showScrollDown()) {
            <button class="scroll-down-btn" (click)="scrollDown()"
                    title="Zur neuesten Nachricht springen">
              <mat-icon>keyboard_double_arrow_down</mat-icon>
            </button>
          }

          @if (voiceError()) {
            <div class="voice-error" (click)="voiceError.set(null)">
              ⚠ {{ voiceError() }}
            </div>
          }

          <!-- Input -->
          <div class="input-row">
            <textarea #inputEl
                      class="lcars-input"
                      [(ngModel)]="inputText"
                      placeholder="Computer, ...  (Space = microphone)"
                      [disabled]="loading()"
                      (input)="resizeInput()"
                      (keydown)="onInputKeydown($event)"></textarea>
            <button class="icon-btn"
                    [class.active]="listening()"
                    (click)="toggleVoice()"
                    title="Spracheingabe (Leertaste)">
              <mat-icon>{{ listening() ? 'mic' : 'mic_none' }}</mat-icon>
            </button>
            @if (loading()) {
              <button class="stop-inline-btn" (click)="stopGeneration()" title="Cancel">
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
  @ViewChild('inputEl') private inputEl?: ElementRef<HTMLTextAreaElement>;

  private auth = inject(AuthService);
  private router = inject(Router);
  private sanitizer = inject(DomSanitizer);
  readonly computerService = inject(ComputerService);
  private http = inject(HttpClient);
  private snackBar = inject(MatSnackBar);
  private dialog = inject(MatDialog);
  private ngZone = inject(NgZone);
  readonly i18n = inject(I18nService);
  private apiBase = `${environment.apiUrl}/computer`;

  // Maps a host key (e.g. hostname) → session_id so that repeated "Computer, prüfe das"
  // clicks for the same host reuse the existing session instead of always creating a new one.
  private hostSessions = new Map<string, string>();

  // Cache rendered markdown per message object. Without this, the [innerHTML]
  // binding calls bypassSecurityTrustHtml() on every change-detection cycle and
  // returns a NEW SafeHtml each time, so Angular rewrites the DOM on every CD —
  // including the mouse events fired while selecting text, which wipes the
  // selection. Messages are replaced immutably on each update (new object key),
  // so a WeakMap auto-invalidates on text change and stays bounded to live messages.
  private _mdCache = new WeakMap<HermesMessage, SafeHtml>();

  renderMarkdown(msg: HermesMessage): SafeHtml {
    const cached = this._mdCache.get(msg);
    if (cached) return cached;
    const html = marked.parse(msg.text) as string;
    const safe = this.sanitizer.bypassSecurityTrustHtml(html);
    this._mdCache.set(msg, safe);
    return safe;
  }

  isOpen = signal(false);
  sessions = signal<HermesSession[]>([]);
  activeTabId = signal<string | null>(null);
  editingSid = signal<string | null>(null);
  inputText = '';

  /** Ticket-Kontext, der im Eingabefeld liegt und noch nicht abgeschickt wurde.
   *  Erst das Absenden markiert die Jira-Änderungen als übernommen — solange der
   *  Text nur im Feld steht, gilt er als ungelesen und die Aktivitätsmeldung
   *  bleibt stehen. Sonst verschwände eine Änderung, die nie bei der KI ankam. */
  private pendingTicketAck = new Map<
    string,
    { snapshot?: TicketActivitySnapshot; contextHash: string | null }
  >();
  loading = signal(false);
  listening = signal(false);
  muted = signal(localStorage.getItem('cs_computer_muted') === '1');
  voiceError = signal<string | null>(null);
  acceptingTicketActivity = signal(false);
  private readonly dismissedActivityStorageKey = 'cs_computer_dismissed_activity';
  private dismissedActivityVersions = signal<Record<string, string>>(this.loadDismissedActivities());

  private mediaRecorder?: MediaRecorder;
  private audioChunks: Blob[] = [];
  private _ttsAudio?: HTMLAudioElement;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private _recognition?: any;
  private _abortController?: AbortController;
  private _handoffSub?: Subscription;
  private _resumeSub?: Subscription;
  private _resolvingSession = false;
  private _sessionCreating = false;

  activeMessages = computed<HermesMessage[]>(() => {
    const sid = this.activeTabId();
    return this.sessions().find(s => s.session_id === sid)?.messages ?? [];
  });

  // The last assistant message while loading — rendered as plain text to avoid
  // innerHTML replacement (which destroys DOM nodes and clears text selections).
  streamingMsg = computed<HermesMessage | null>(() => {
    if (!this.loading()) return null;
    const last = this.activeMessages().at(-1);
    return last?.role === 'assistant' ? last : null;
  });

  // All messages except the one currently streaming, rendered with markdown.
  // Tracked by object identity so Angular skips stable messages entirely.
  completedMessages = computed<HermesMessage[]>(() => {
    const msgs = this.activeMessages();
    return this.streamingMsg() ? msgs.slice(0, -1) : msgs;
  });

  activeSession = computed<HermesSession | null>(() => {
    const sid = this.activeTabId();
    return this.sessions().find(s => s.session_id === sid) ?? null;
  });

  activeTicketActivity = computed<TicketActivity | null>(() => {
    const sid = this.activeTabId();
    return sid ? this.computerService.ticketActivities()[sid] ?? null : null;
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
    // Push-to-talk: start recording on Space press (ignore auto-repeat)
    if (e.key === ' ' && !e.repeat && this.isOpen()) {
      const active = document.activeElement;
      const isTyping = active instanceof HTMLInputElement
                    || active instanceof HTMLTextAreaElement
                    || (active instanceof HTMLElement && active.isContentEditable);
      if (!isTyping) {
        e.preventDefault();
        if (!this.listening()) this.toggleVoice();
      }
    }
  }

  @HostListener('document:keyup', ['$event'])
  onKeyup(e: KeyboardEvent): void {
    // Push-to-talk: stop recording on Space release
    if (e.key === ' ' && this.isOpen() && this.listening()) {
      const active = document.activeElement;
      const isTyping = active instanceof HTMLInputElement
                    || active instanceof HTMLTextAreaElement
                    || (active instanceof HTMLElement && active.isContentEditable);
      if (!isTyping) {
        e.preventDefault();
        this._recognition?.stop();
        this.mediaRecorder?.stop();
        this.listening.set(false);
      }
    }
  }

  ngOnInit(): void {
    this.loadWriteApproval();
    this._handoffSub = this.computerService.handoff$.subscribe(({ prompt, label, hostKey, externalId, ticketRef }) => {
      this._handleHandoff(prompt, label, hostKey, externalId, ticketRef);
    });
    this._resumeSub = this.computerService.resume$.subscribe(async (sid) => {
      this.isOpen.set(true);
      await this.loadSessions();
      this.selectTab(sid);
    });
    this.loadSessions();
  }

  /** Load persisted sessions from the backend DB.
   *
   * Sessions are stored in PostgreSQL so they survive page reloads.
   * For sessions already in memory (current page), in-memory messages are preserved.
   * For restored sessions, messages start empty — the conversation history lives
   * in Hermes state.db and is replayed automatically on the next message.
   */
  async loadSessions(): Promise<void> {
    const token = this.auth.getAccessToken();
    try {
      const r = await fetch(`${this.apiBase}/sessions`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!r.ok) return;
      const list: Array<{
        session_id: string; label: string; msg_count: number;
        agent_type?: string; external_id?: string | null; resolved?: boolean;
        ticket_ref?: { connector_id: string; issue_id: string; key: string } | null;
        context_hash?: string | null;
        has_activity_snapshot?: boolean;
        context_synced_at?: string | null;
        last_activity_at?: string;
      }> = await r.json();
      if (!list.length) return;

      this.sessions.update(current => {
        const inMemory = new Map(current.map(s => [s.session_id, s]));
        return list.map(s => inMemory.get(s.session_id) ?? {
          session_id: s.session_id,
          label: s.label,
          msg_count: s.msg_count,
          messages: [],
          agent_type: s.agent_type ?? 'hermes',
          // Restore external_id + resolved so the "✓ GELÖST" button reappears
          // for alert-handoff sessions after a reload.
          external_id: s.external_id ?? undefined,
          resolved: s.resolved ?? false,
          ticket_ref: s.ticket_ref ?? null,
          context_hash: s.context_hash ?? null,
          has_activity_snapshot: s.has_activity_snapshot ?? false,
          context_synced_at: s.context_synced_at ?? null,
          last_activity_at: s.last_activity_at,
        });
      });

      // Restore the last explicitly selected session when it still belongs to the
      // configured agent. Otherwise pick the most recently active matching session,
      // because the backend returns the list newest-first. This keeps an older ticket
      // that was just continued visible and active after a reload.
      // Switching providers still starts a matching session instead of silently
      // sending to a session owned by another agent backend.
      // If no session matches, start a fresh one for the current agent.
      const ids = list.map(s => s.session_id);
      if (!this.activeTabId() || !ids.includes(this.activeTabId()!)) {
        const currentAgent = await this.fetchCurrentAgent();
        const storedSid = localStorage.getItem('cs_computer_active_session');
        const stored = list.find(s =>
          s.session_id === storedSid && (s.agent_type ?? 'hermes') === currentAgent
        );
        const matching = stored ?? list.find(s => (s.agent_type ?? 'hermes') === currentAgent);
        if (matching) {
          this.activeTabId.set(matching.session_id);
          localStorage.setItem('cs_computer_active_session', matching.session_id);
        } else {
          await this.newSession();
          return;
        }
      }

      // Load message history for all sessions so they're available after a reload.
      await Promise.all(ids.map(sid => this.loadSessionHistory(sid)));
    } catch (err) {
      console.debug('loadSessions failed:', err);
    }
  }

  /** Current Console agent from user preferences ('hermes' if unset/unreachable). */
  private async fetchCurrentAgent(): Promise<string> {
    const token = this.auth.getAccessToken();
    try {
      const r = await fetch(`${environment.apiUrl}/preferences`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!r.ok) return 'hermes';
      const prefs = await r.json();
      return prefs?.computer_agent ?? 'hermes';
    } catch {
      return 'hermes';
    }
  }

  ngOnDestroy(): void {
    this._recognition?.abort();
    this.mediaRecorder?.stop();
    this._ttsAudio?.pause();
    this._abortController?.abort();
    this._handoffSub?.unsubscribe();
    this._resumeSub?.unsubscribe();
  }

  // ── Panel controls ────────────────────────────────────────────────

  toggle(): void {
    this.isOpen.update(v => !v);
    if (this.isOpen()) void this.refreshActiveTicketActivity();
  }
  open(): void {
    this.isOpen.set(true);
    void this.refreshActiveTicketActivity();
  }
  close(): void  { this.isOpen.set(false); this._ttsAudio?.pause(); }

  sessionActivity(sid: string): TicketActivity | undefined {
    return this.computerService.activityFor(sid);
  }

  private activityVersion(activity: TicketActivity): string {
    const snapshot = activity.snapshot;
    if (!snapshot) return `${activity.state}:${activity.checked_at}`;
    return [
      activity.state,
      activity.source_unavailable ? 'offline' : 'online',
      snapshot.issue_updated_at,
      JSON.stringify(snapshot.fields),
      JSON.stringify(snapshot.comments),
    ].join(':');
  }

  activityDismissed(activity: TicketActivity): boolean {
    return this.dismissedActivityVersions()[activity.session_id] === this.activityVersion(activity);
  }

  dismissActivity(activity: TicketActivity): void {
    const dismissed = {
      ...this.dismissedActivityVersions(),
      [activity.session_id]: this.activityVersion(activity),
    };
    this.dismissedActivityVersions.set(dismissed);
    localStorage.setItem(this.dismissedActivityStorageKey, JSON.stringify(dismissed));
  }

  private loadDismissedActivities(): Record<string, string> {
    try {
      const stored = JSON.parse(localStorage.getItem(this.dismissedActivityStorageKey) || '{}');
      return stored && typeof stored === 'object' && !Array.isArray(stored) ? stored : {};
    } catch {
      return {};
    }
  }

  formatActivityTime(value: string): string {
    if (!value) return '';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value.slice(0, 16) : parsed.toLocaleString('de-DE', {
      dateStyle: 'short',
      timeStyle: 'short',
    });
  }

  private async refreshActiveTicketActivity(): Promise<boolean> {
    const session = this.activeSession();
    if (!session?.ticket_ref) return this.computerService.refreshTicketActivities();
    return this.computerService.refreshTicketActivities(session.session_id);
  }

  async manualSync(): Promise<void> {
    const ok = await this.refreshActiveTicketActivity();
    this.snackBar.open(
      ok ? 'Prüfung der Ticket-Aktivität abgeschlossen' : 'Jira-Aktivität konnte nicht geprüft werden',
      '',
      { duration: 3000 },
    );
  }

  focusInput(): void {
    setTimeout(() => this.inputEl?.nativeElement.focus(), 50);
  }

  resizeInput(): void {
    const el = this.inputEl?.nativeElement;
    if (!el) return;
    // '0' statt 'auto': scrollHeight misst so den reinen Inhalt ohne rows-Attribut-Offset
    el.style.height = '0';
    el.style.height = Math.min(el.scrollHeight, 180) + 'px';
  }

  onInputKeydown(e: KeyboardEvent): void {
    if (e.key === 'Escape') { this.close(); return; }
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.send(); }
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

  /** Called by the per-message TTS button — always plays, ignores muted state. */
  speakMessage(text: string): void { this._playTTS(text); }

  /** Mark the active session as resolved: calls the backend to save a
   *  LLM-generated lesson-learned comment on the originating alert. */
  async resolveSession(): Promise<void> {
    const session = this.activeSession();
    if (!session?.external_id || session.resolved || this._resolvingSession) return;
    this._resolvingSession = true;
    const token = this.auth.getAccessToken();
    const messages = session.messages.map(m => ({ role: m.role, text: m.text }));
    try {
      const eid = encodeURIComponent(session.external_id);
      const r = await fetch(`${environment.apiUrl}/feed/computer-resolve?external_id=${eid}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ messages }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      this.sessions.update(ss => ss.map(s =>
        s.session_id === session.session_id ? { ...s, resolved: true } : s
      ));
      console.info('[Computer] Lernkommentar gespeichert für', session.external_id);
    } catch (err) {
      console.error('[Computer] Resolve fehlgeschlagen:', err);
    } finally {
      this._resolvingSession = false;
    }
  }

  /** Open the shared ticket dialog, formulating a ticket from this conversation. */
  // ── Write approval ────────────────────────────────────────────────────────
  // The PreToolUse guard in the container denies system-modifying commands and has
  // no view of this conversation — deliberately, since the agent drives the chat and
  // could otherwise argue itself into permission. This is the only consent channel.
  writeApprovalUntil = signal<number | null>(null);
  writeRemaining = signal('');
  private writeTimer: any = null;

  private trackWrite(expiresAtSec: number | null): void {
    this.writeApprovalUntil.set(expiresAtSec);
    if (this.writeTimer) { clearInterval(this.writeTimer); this.writeTimer = null; }
    if (!expiresAtSec) { this.writeRemaining.set(''); return; }
    const tick = () => {
      const left = Math.round(expiresAtSec - Date.now() / 1000);
      if (left <= 0) { this.trackWrite(null); return; }
      const m = Math.floor(left / 60), s = left % 60;
      this.writeRemaining.set(`${m}:${String(s).padStart(2, '0')}`);
    };
    tick();
    this.writeTimer = setInterval(tick, 1000);
  }

  loadWriteApproval(): void {
    this.http.get<any>(`${environment.apiUrl}/computer/write-approval`).subscribe({
      next: r => this.trackWrite(r?.approval?.expires_at ?? null),
      error: () => this.trackWrite(null),
    });
  }

  grantWrite(): void {
    if (!confirm(
      'Schreibzugriff für 15 Minuten freigeben?\n\n' +
      'Der Agent darf dann System- und Schreibbefehle ausführen — auch per SSH auf ' +
      'entfernten Produktionssystemen. Die Freigabe endet automatisch und kann ' +
      'jederzeit sofort widerrufen werden.'
    )) return;
    this.http.post<any>(`${environment.apiUrl}/computer/write-approval`, { minutes: 15 })
      .subscribe({
        next: r => this.trackWrite(r?.approval?.expires_at ?? null),
        error: e => this.snackBar.open(e?.error?.detail ?? 'Freigabe fehlgeschlagen', '', { duration: 3000 }),
      });
  }

  revokeWrite(): void {
    this.http.delete(`${environment.apiUrl}/computer/write-approval`).subscribe({
      next: () => this.trackWrite(null),
      error: () => this.trackWrite(null),
    });
  }

  createTicket(): void {
    const msgs = this.activeMessages();
    if (!msgs.length) return;
    const transcript = msgs
      .map(m => `${m.role === 'user' ? 'Operator' : 'Assistant'}: ${m.text.trim()}`)
      .join('\n\n');
    const session = this.activeSession();
    this.dialog.open(TicketCreateDialogComponent, {
      data: {
        mode: 'computer',
        transcript,
        host: session?.external_id || session?.label || '',
      },
      panelClass: 'tkt-panel',
      autoFocus: false,
      maxWidth: '92vw',
    });
  }

  /** Extract the concluding paragraph (Fazit) for TTS.
   *  Finds a section starting with Fazit/Zusammenfassung/Empfehlung/Ergebnis/Schluss,
   *  or falls back to the last substantive paragraph. Capped at 280 chars. */
  private _extractFazit(text: string): string {
    let t = text.replace(/\[FEED:[^\]]+\]/g, '').replace(/```[\s\S]*?```/g, '').replace(/`[^`]*`/g, '');
    t = t.replace(/\*\*([^*]+)\*\*/g, '$1').replace(/\*([^*]+)\*/g, '$1');
    t = t.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/^#{1,6}\s+/gm, '');
    // Merge "## Fazit\nText" into one paragraph before splitting
    t = t.replace(/(Fazit|Zusammenfassung|Empfehlung|Ergebnis|Schluss)[:\s]*\n+/gi, '$1: ');
    const paragraphs = t.split(/\n{2,}/)
      .map(p => p.replace(/^\s*[-*>|]+\s*/gm, '').replace(/\s+/g, ' ').trim())
      .filter(p => p.length > 8 && !/^[|\-\s]+$/.test(p));
    // Fallback: use last 280 chars of clean text if no paragraph was found
    if (paragraphs.length === 0) {
      const flat = t.replace(/\s+/g, ' ').trim();
      return flat.slice(-280).trim();
    }
    const fazit = paragraphs.find(p => /^(Fazit|Zusammenfassung|Empfehlung|Ergebnis|Schluss)/i.test(p));
    const chosen = fazit ?? paragraphs[paragraphs.length - 1];
    const clean = chosen.replace(/^(Fazit|Zusammenfassung|Empfehlung|Ergebnis|Schluss)[:\s]*/i, '');
    return clean.length > 280 ? clean.slice(0, 277) + ' …' : clean;
  }

  /** Auto-trigger after stream end — respects muted. */
  private speak(text: string): void {
    if (this.muted()) { console.debug('[TTS] stumm — überspringe'); return; }
    this._playTTS(text);
  }

  /** Core TTS: extract Fazit, fetch audio, play. Always executes regardless of muted. */
  private _playTTS(text: string): void {
    const fazit = this._extractFazit(text);
    if (!fazit.trim()) { console.debug('[TTS] kein Text extrahiert'); return; }
    console.debug('[TTS] spreche (%d Zeichen): %s', fazit.length, fazit.slice(0, 80));
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
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      this._ttsAudio = audio;
      audio.onended = () => URL.revokeObjectURL(url);
      audio.play().catch(err => console.warn('[TTS] Wiedergabe fehlgeschlagen:', err));
    }).catch(err => console.warn('[TTS] Anfrage fehlgeschlagen:', err));
  }

  // ── Incident handoff ──────────────────────────────────────────────

  private async _handleHandoff(
    prompt: string,
    label?: string,
    hostKey?: string,
    externalId?: string,
    ticketRef?: TicketReference,
  ): Promise<void> {
    this.open();

    if (ticketRef) {
      await this.loadSessions();
      const contextHash = ticketRef.contextHash || await this.hashContext(prompt);
      let existing: HermesSession | null | undefined = this.sessions().find(s =>
        s.ticket_ref?.connector_id === ticketRef.connectorId
        && s.ticket_ref?.issue_id === ticketRef.issueId
      );
      if (!existing) {
        existing = await this.newSession(label, undefined, ticketRef);
      }
      if (!existing) return;
      this.activeTabId.set(existing.session_id);
      this.selectTab(existing.session_id);
      // An established ticket session only resumes here. Its live Jira changes are
      // shown by the activity banner and require the explicit confirmation button.
      if (existing.context_hash) {
        // Legacy sessions predate structured snapshots. An identical full-context
        // hash proves that the current Jira state was already handed to the agent,
        // so adopting that snapshot does not discard unseen content.
        if (
          !existing.has_activity_snapshot
          && existing.context_hash === contextHash
          && ticketRef.snapshot
        ) {
          await this.acknowledgeTicketActivity(existing.session_id, ticketRef.snapshot, contextHash);
        }
        await this.computerService.refreshTicketActivities(existing.session_id);
        return;
      }

      // Erstübergabe: das Ticket landet im Eingabefeld, nicht beim Agenten. Die
      // Jira-Grundlinie wird erst gesetzt, wenn der Nutzer die Nachricht abschickt
      // (resolvePendingTicketAck) — vorher hat die KI den Stand nicht gesehen.
      this.pendingTicketAck.set(existing.session_id, {
        snapshot: ticketRef.snapshot,
        contextHash,
      });
      this.stageInInput(prompt);
      return;
    }

    // Reuse an existing session for this host if one exists
    if (hostKey) {
      const existingSid = this.hostSessions.get(hostKey);
      if (existingSid && this.sessions().some(s => s.session_id === existingSid)) {
        this.activeTabId.set(existingSid);
        // Re-point the reused session at the new alert — in memory AND persisted,
        // so the "✓ GELÖST" button shows for the new alert and survives reload.
        if (externalId) {
          this.sessions.update(ss => ss.map(s =>
            s.session_id === existingSid ? { ...s, external_id: externalId, resolved: false } : s
          ));
          this.persistSessionAlert(existingSid, externalId);
        }
        this.inputText = prompt;
        await this.send();
        return;
      }
      this.hostSessions.delete(hostKey);
    }

    await this.newSession(label, externalId);
    const sid = this.activeTabId();
    if (!sid) return;

    if (hostKey) this.hostSessions.set(hostKey, sid);

    this.inputText = prompt;
    await this.send();
  }

  // ── Session management ────────────────────────────────────────────

  /** Persist a (possibly changed) alert external_id on a reused session. */
  private async persistSessionAlert(sid: string, externalId: string): Promise<void> {
    const token = this.auth.getAccessToken();
    try {
      await fetch(`${this.apiBase}/sessions/${sid}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ external_id: externalId }),
      });
    } catch (err) {
      console.debug('persistSessionAlert failed:', err);
    }
  }

  private async hashContext(value: string): Promise<string> {
    const bytes = new TextEncoder().encode(value);
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
  }

  private async persistContextHash(sid: string, contextHash: string): Promise<void> {
    const token = this.auth.getAccessToken();
    try {
      const r = await fetch(`${this.apiBase}/sessions/${sid}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ context_hash: contextHash }),
      });
      if (!r.ok) return;
      this.sessions.update(ss => ss.map(s =>
        s.session_id === sid ? { ...s, context_hash: contextHash } : s
      ));
    } catch (err) {
      console.debug('persistContextHash failed:', err);
    }
  }

  private async acknowledgeTicketActivity(
    sid: string,
    snapshot: TicketActivitySnapshot,
    contextHash: string | null,
  ): Promise<boolean> {
    const token = this.auth.getAccessToken();
    try {
      const response = await fetch(`${this.apiBase}/sessions/${sid}/ticket-activity/ack`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ snapshot, context_hash: contextHash }),
      });
      if (!response.ok) return false;
      const data = await response.json();
      this.sessions.update(sessions => sessions.map(session =>
        session.session_id === sid ? {
          ...session,
          context_hash: data.context_hash ?? contextHash,
          context_synced_at: data.context_synced_at,
          has_activity_snapshot: true,
        } : session
      ));
      this.computerService.clearTicketActivity(sid);
      return true;
    } catch (err) {
      console.debug('acknowledgeTicketActivity failed:', err);
      return false;
    }
  }

  async acceptTicketActivity(activity: TicketActivity): Promise<void> {
    if (this.loading() || this.acceptingTicketActivity()) return;
    this.acceptingTicketActivity.set(true);
    const token = this.auth.getAccessToken();
    try {
      const response = await fetch(
        `${this.apiBase}/sessions/${activity.session_id}/ticket-activity/context`,
        {
          method: 'POST',
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        },
      );
      if (!response.ok) {
        const error = await response.json().catch(() => null);
        throw new Error(error?.detail || 'Ticket-Aktivität konnte nicht geladen werden');
      }
      const data: TicketActivity & {
        prompt: string;
        context_hash: string | null;
        snapshot?: TicketActivitySnapshot;
      } = await response.json();
      if (data.state !== 'changed' || !data.prompt || !data.snapshot) {
        await this.computerService.refreshTicketActivities(activity.session_id);
        this.snackBar.open('Keine neuen Ticketänderungen vorhanden', '', { duration: 2500 });
        return;
      }

      // Der Text geht ins Eingabefeld, nicht an die KI. Sie arbeitet erst, wenn der
      // Nutzer abschickt; bis dahin bleibt die Änderung als ungelesen vermerkt.
      this.activeTabId.set(activity.session_id);
      this.selectTab(activity.session_id);
      this.pendingTicketAck.set(activity.session_id, {
        snapshot: data.snapshot,
        contextHash: data.context_hash,
      });
      this.stageInInput(data.prompt);
      this.snackBar.open(
        'In das Eingabefeld übernommen — zum Bearbeiten absenden',
        '',
        { duration: 3500 },
      );
    } catch (err) {
      this.snackBar.open(
        err instanceof Error ? err.message : 'Jira-Quelle nicht erreichbar',
        '',
        { duration: 4000 },
      );
    } finally {
      this.acceptingTicketActivity.set(false);
    }
  }

  async newSession(
    label?: string,
    externalId?: string,
    ticketRef?: TicketReference,
  ): Promise<HermesSession | null> {
    // Guard against concurrent calls (e.g. double-click or race between _handleHandoff + send())
    if (this._sessionCreating) return null;
    this._sessionCreating = true;
    const token = this.auth.getAccessToken();
    try {
      // Persist label (handoff host name) and external_id (alert id, drives the
      // "✓ GELÖST" button) so both survive reloads — otherwise the backend
      // generates a generic "Session N" and the resolve button is lost.
      const createBody: {
        label?: string;
        external_id?: string;
        ticket_connector_id?: string;
        ticket_issue_id?: string;
        ticket_key?: string;
      } = {};
      if (label) createBody.label = label;
      if (externalId) createBody.external_id = externalId;
      if (ticketRef) {
        createBody.ticket_connector_id = ticketRef.connectorId;
        createBody.ticket_issue_id = ticketRef.issueId;
        createBody.ticket_key = ticketRef.key;
      }
      const r = await fetch(`${this.apiBase}/sessions`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(createBody),
      });
      if (!r.ok) { console.error('Session creation failed:', r.status); return null; }
      const session: HermesSession = await r.json();
      const displayLabel = label ?? session.label;
      this.sessions.update(ss => {
        // Prevent duplicate entries if the session was somehow already added
        if (ss.some(s => s.session_id === session.session_id)) return ss;
        return [...ss, {
          ...session,
          label: displayLabel,
          msg_count: session.msg_count ?? 0,
          messages: session.messages ?? [],
          external_id: externalId,
        }];
      });
      this.activeTabId.set(session.session_id);
      this.open();
      return this.sessions().find(s => s.session_id === session.session_id) ?? session;
    } catch (err) {
      console.error('Failed to create session:', err);
      return null;
    } finally {
      this._sessionCreating = false;
    }
  }

  selectTab(sid: string): void {
    this.activeTabId.set(sid);
    localStorage.setItem('cs_computer_active_session', sid);
    const session = this.sessions().find(s => s.session_id === sid);
    // Lazy-load history for sessions restored from DB (no messages in memory yet).
    // Always try — msg_count in PostgreSQL can be 0 even when state.db has messages.
    if (session && session.messages.length === 0) {
      this.loadSessionHistory(sid);
    }
    if (session?.ticket_ref) void this.computerService.refreshTicketActivities(sid);
  }

  async loadSessionHistory(sid: string): Promise<void> {
    const token = this.auth.getAccessToken();
    try {
      const r = await fetch(`${this.apiBase}/sessions/${sid}/history`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!r.ok) return;
      const raw: Array<{ role: string; content: string }> = await r.json();
      // SessionDB returns OpenAI format {role, content}; map to HermesMessage {role, text}
      const messages: HermesMessage[] = raw
        .filter(m => m.role === 'user' || m.role === 'assistant')
        .filter(m => typeof m.content === 'string' && m.content.trim())
        .map(m => ({ role: m.role as 'user' | 'assistant', text: m.content }));
      if (!messages.length) return;
      this.sessions.update(ss => ss.map(s =>
        s.session_id === sid ? { ...s, messages } : s
      ));
    } catch (err) {
      console.debug('loadSessionHistory failed:', err);
    }
  }

  async renameSession(sid: string, label: string): Promise<void> {
    this.editingSid.set(null);
    const v = (label || '').trim().slice(0, 120);
    if (!v) return;
    const token = this.auth.getAccessToken();
    await fetch(`${this.apiBase}/sessions/${sid}`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ label: v }),
    });
    this.sessions.update(ss => ss.map(s => s.session_id === sid ? { ...s, label: v } : s));
  }

  async sendToWorkbench(): Promise<void> {
    const session = this.activeSession();
    if (!session) return;
    const token = this.auth.getAccessToken();
    const r = await fetch(`${environment.apiUrl}/ide/open-chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        session_id: session.session_id,
        extension_type: 'none',
        session_label: session.label,
        messages: session.messages.map(m => ({ role: m.role, text: m.text })),
      }),
    });
    if (r.ok) {
      const data = await r.json();
      this.close();
      this.router.navigate(['/workbench'], { state: { ideUrl: data.ide_url } });
    }
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
    if (localStorage.getItem('cs_computer_active_session') === sid) {
      localStorage.removeItem('cs_computer_active_session');
    }
    const remaining = this.sessions();
    this.activeTabId.set(remaining.length > 0 ? remaining[remaining.length - 1].session_id : null);
  }

  // ── Stop generation ───────────────────────────────────────────────

  stopGeneration(): void {
    this._abortController?.abort();
    this.loading.set(false);
  }

  // ── Send message → SSE stream ─────────────────────────────────────

  /** Legt Text ins Eingabefeld, statt ihn abzuschicken.
   *
   *  Ticket-Kontext startet die KI bewusst NICHT von selbst: der Mensch sieht
   *  zuerst, was übernommen wurde, kann es ergänzen oder streichen, und erst
   *  sein Absenden lässt die KI arbeiten. */
  private stageInInput(text: string): void {
    const current = this.inputText.trim();
    this.inputText = current ? `${current}\n\n${text}` : text;
    setTimeout(() => {
      this.resizeInput();
      this.inputEl?.nativeElement.focus();
    }, 0);
  }

  /** Nach erfolgreichem Absenden: den vorgemerkten Jira-Stand als übernommen buchen. */
  private async resolvePendingTicketAck(sid: string): Promise<void> {
    const pending = this.pendingTicketAck.get(sid);
    if (!pending) return;
    this.pendingTicketAck.delete(sid);
    if (pending.snapshot) {
      await this.acknowledgeTicketActivity(sid, pending.snapshot, pending.contextHash);
    } else if (pending.contextHash) {
      await this.persistContextHash(sid, pending.contextHash);
    }
    await this.computerService.refreshTicketActivities(sid);
  }

  async send(): Promise<boolean> {
    const text = this.inputText.trim();
    if (!text || this.loading()) return false;

    const sid = this.activeTabId();
    this.inputText = '';
    setTimeout(() => this.resizeInput(), 0);
    const sent = await this.sendContent(text);
    if (sent && sid) await this.resolvePendingTicketAck(sid);
    return sent;
  }

  private async sendContent(text: string, targetSid?: string): Promise<boolean> {
    if (!text.trim() || this.loading()) return false;

    let sid = targetSid ?? this.activeTabId();
    if (!sid) {
      await this.newSession();
      sid = this.activeTabId();
      if (!sid) return false;
    }

    this.loading.set(true);
    const activityAt = new Date().toISOString();
    this.sessions.update(sessions => {
      const active = sessions.find(session => session.session_id === sid);
      if (!active) return sessions;
      return [
        { ...active, last_activity_at: activityAt },
        ...sessions.filter(session => session.session_id !== sid),
      ];
    });
    localStorage.setItem('cs_computer_active_session', sid);
    this._addMessage(sid, 'user', text);
    this._addMessage(sid, 'assistant', '');
    this._updateMsgCount(sid);
    this.scrollToBottom();

    const token = this.auth.getAccessToken();
    let fullAssistantText = '';
    let wasAborted = false;
    let streamFailed = false;
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
        streamFailed = true;
        return false;
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
            this.ngZone.run(() => {
              if (data.type === 'delta') {
                fullAssistantText += data.text;
                this._appendToLast(sid, data.text);
                this._clearActiveTool(sid);
              }
              if (data.type === 'tool_start') {
                this._setActiveTool(sid, data.tool);
              }
              if (data.type === 'tool_done') {
                this._markToolDone(sid);
              }
              if (data.type === 'reasoning') {
                this._clearActiveTool(sid);
              }
              if (data.type === 'error') {
                streamFailed = true;
                this._appendToLast(sid, `\n[Fehler: ${data.text}]`);
              }
            });
          } catch { /* skip malformed */ }
        }
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
            if (data.type === 'error') {
              streamFailed = true;
              this._appendToLast(sid, `\n[Fehler: ${data.text}]`);
            }
          } catch { /* ignore */ }
        }
      }

    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        wasAborted = true;
        this._appendToLast(sid, ' [gestoppt]');
      } else {
        streamFailed = true;
        this._appendToLast(sid, `[Verbindungsfehler: ${err}]`);
      }
    } finally {
      // All signal writes must run inside NgZone — the stream and its finally-block
      // execute outside Angular's zone (ReadableStream is not zone-patched), so
      // without ngZone.run() the view would not update after stream end.
      this.ngZone.run(() => {
        if (fullAssistantText) {
          this._finishAssistantMessage(sid, fullAssistantText);
        }
        if (!wasAborted && fullAssistantText) {
          this.speak(fullAssistantText);
        }
        this._finalizeTools(sid);
        this.loading.set(false);
        this.scrollToBottomIfFollowing();
      });
    }
    return !wasAborted && !streamFailed && fullAssistantText.trim().length > 0;
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
          this.voiceError.set('Microphone access denied — check browser settings');
        } else if (e.error === 'network') {
          this.voiceError.set('Speech recognition requires internet connection');
        } else if (e.error !== 'no-speech') {
          this.voiceError.set(`Error: ${e.error}`);
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
      this.voiceError.set('Microphone not available — HTTPS or localhost required');
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
          this.voiceError.set(`Transcription failed: ${err}`);
        }
      };

      this.listening.set(true);
      this.mediaRecorder.start();
      setTimeout(() => { if (this.listening()) this.mediaRecorder?.stop(); }, 10_000);

    }).catch(err => {
      let msg: string;
      if (err instanceof DOMException && err.name === 'NotAllowedError') {
        msg = 'Microphone access denied — check browser permissions';
      } else if (err instanceof DOMException && (err.name === 'NotFoundError' || err.name === 'DevicesNotFoundError')) {
        msg = 'No microphone found — please connect a microphone';
      } else {
        msg = `Microphone error: ${err instanceof Error ? err.message : err}`;
      }
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

  /** Start a new tool call: show spinner + add permanent log entry. */
  private _setActiveTool(sid: string, tool: string): void {
    this.sessions.update(ss => ss.map(s => {
      if (s.session_id !== sid) return s;
      const msgs = [...s.messages];
      if (msgs.length === 0 || msgs[msgs.length - 1].role !== 'assistant') return s;
      const prev = msgs[msgs.length - 1];
      msgs[msgs.length - 1] = {
        ...prev,
        activeTool: tool,
        toolCalls: [...(prev.toolCalls ?? []), { tool, done: false }],
      };
      return { ...s, messages: msgs };
    }));
  }

  /** Mark the last running tool call as done and clear spinner. */
  private _markToolDone(sid: string): void {
    this.sessions.update(ss => ss.map(s => {
      if (s.session_id !== sid) return s;
      const msgs = [...s.messages];
      if (msgs.length === 0 || msgs[msgs.length - 1].role !== 'assistant') return s;
      const prev = msgs[msgs.length - 1];
      const calls = [...(prev.toolCalls ?? [])];
      let lastRunning = -1;
      for (let i = calls.length - 1; i >= 0; i--) {
        if (!calls[i].done) { lastRunning = i; break; }
      }
      if (lastRunning >= 0) calls[lastRunning] = { ...calls[lastRunning], done: true };
      msgs[msgs.length - 1] = { ...prev, activeTool: undefined, toolCalls: calls };
      return { ...s, messages: msgs };
    }));
  }

  /** Clear the active-tool spinner without touching the permanent log. */
  private _clearActiveTool(sid: string): void {
    this.sessions.update(ss => ss.map(s => {
      if (s.session_id !== sid) return s;
      const msgs = [...s.messages];
      if (msgs.length === 0 || msgs[msgs.length - 1].role !== 'assistant') return s;
      msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], activeTool: undefined };
      return { ...s, messages: msgs };
    }));
  }

  /** At stream end: clear spinner AND mark all still-pending tool calls as done. */
  private _finalizeTools(sid: string): void {
    this.sessions.update(ss => ss.map(s => {
      if (s.session_id !== sid) return s;
      const msgs = [...s.messages];
      if (msgs.length === 0 || msgs[msgs.length - 1].role !== 'assistant') return s;
      const prev = msgs[msgs.length - 1];
      const calls = (prev.toolCalls ?? []).map(tc => tc.done ? tc : { ...tc, done: true });
      msgs[msgs.length - 1] = { ...prev, activeTool: undefined, toolCalls: calls };
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

  /** True while the view is scrolled away from the newest message. */
  showScrollDown = signal(false);
  /** Within this many px of the bottom still counts as "at the bottom": a couple of
   *  pixels of rounding should not make the button flicker in and out. */
  private static readonly BOTTOM_EPS = 48;

  private atBottom(): boolean {
    const el = this.msgContainer?.nativeElement;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight <= ComputerComponent.BOTTOM_EPS;
  }

  onMessagesScroll(): void {
    this.showScrollDown.set(!this.atBottom());
  }

  /** Jump to the newest message (the floating button). */
  scrollDown(): void {
    const el = this.msgContainer?.nativeElement;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    this.showScrollDown.set(false);
  }

  private scrollToBottom(): void {
    setTimeout(() => {
      const el = this.msgContainer?.nativeElement;
      if (el) el.scrollTop = el.scrollHeight;
      this.showScrollDown.set(false);
    }, 10);
  }

  /** Follow new output only when the reader is already at the bottom.
   *  Scrolling up is a deliberate act — yanking the view back down at the end of a
   *  stream is what made reading earlier output impossible. */
  private scrollToBottomIfFollowing(): void {
    if (this.atBottom()) {
      this.scrollToBottom();
    } else {
      this.showScrollDown.set(true);
    }
  }
}
