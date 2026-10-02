import {
  Component, OnInit, OnDestroy, signal, computed,
  ViewChild, ElementRef, HostListener, inject, NgZone, SecurityContext,
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
import { extractWorkspaceFiles, workspaceRelative } from '../../shared/workspace-files';
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
  /** Anhänge, die mit dieser Nachricht gingen — im Template gerendert (Bilder als
   *  <img>, Dateien als Chip), nicht über Markdown/innerHTML. */
  attachments?: MessageAttachment[];
  /** Current tool being executed — shown as a spinner line while streaming. */
  activeTool?: string;
  /** Permanent log of all tool calls made during this message turn. */
  toolCalls?: ToolCall[];
}

/** Ein eingefügtes Bild, das an der nächsten Nachricht hängt. */
/** Eine Datei oder ein Bild, das an der nächsten Nachricht hängt.
 *
 *  Beides läuft über denselben Upload. Der Unterschied liegt nur danach: Bilder
 *  bekommen eine Vorschau und gehen an Codex/Hermes als Bild, andere Dateien öffnet
 *  der Agent selbst über den Pfad. */
interface PendingAttachment {
  kind: 'image' | 'file';
  /** Serverseitig vergebener Dateiname, zugleich die ID in der URL. */
  id: string;
  /** Anzeigename, z. B. "umsatz_q3.xlsx". */
  name: string;
  /** Kategorie für die Anzeige, z. B. "Tabelle". */
  label: string;
  /** Pfad, unter dem der Agent die Datei im Workspace sieht. */
  agentPath: string;
  /** URL zum Anzeigen bzw. Herunterladen. */
  url: string;
  /** Lokale Objekt-URL — nur bei Bildern, für die Vorschau ohne Umweg über den Server. */
  previewUrl?: string;
  size: number;
}

/** Was eine gesendete Nachricht an Anhängen trägt — für die Anzeige im Verlauf. */
interface MessageAttachment {
  kind: 'image' | 'file';
  url: string;
  agentPath: string;
  name: string;
}

/** Ticket-Kontext, der an einer Sitzung hängt und mit der nächsten Nachricht mitgeht. */
interface PendingContext {
  /** Der volle Text, der der KI vorangestellt wird. */
  text: string;
  /** Kurzbezeichnung für die Anzeige, z.B. "IMIT-1234 · Neue Aktivität". */
  label: string;
  /** Jira-Stand, der beim Absenden als übernommen gebucht wird. */
  snapshot?: TicketActivitySnapshot;
  contextHash: string | null;
  /** Kennung der Aktivität, die hier übernommen wurde — nur bei einer Meldung aus
   *  dem Aktivitätsbanner gesetzt. Solange sie hängt, verschwindet das Banner für
   *  genau diesen Stand; ein neuerer Kommentar hat eine andere Kennung und meldet
   *  sich wieder. Bei der Erstübergabe eines Tickets gibt es keine Meldung, also
   *  auch nichts zu unterdrücken. */
  activityVersion?: string;
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
        <!-- Drag-and-Drop auf die ganze Unterhaltung, nicht nur aufs Eingabefeld:
             das Feld ist klein, und eine Datei soll man einfach "in die Konsole"
             ziehen können. -->
        <div class="conversation"
             [class.drag-over]="dragOver()"
             (dragover)="onDragOver($event)"
             (dragleave)="onDragLeave($event)"
             (drop)="onDrop($event)">

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
                @if (msg.attachments?.length) {
                  <!-- Im Template gerendert, NICHT über Markdown/innerHTML: dort läuft
                       bypassSecurityTrustHtml ohne Sanitizer, und diese Fläche soll
                       nicht zusätzlich belastet werden. Bilder kommen als Blob-URL,
                       weil ein <img src> keinen Authorization-Header mitschickt. -->
                  <div class="msg-attachments">
                    @for (a of msg.attachments!; track a.url) {
                      @if (a.kind === 'image') {
                        @if (imageSrc(a.url); as src) {
                          <img class="msg-attachment-img" [src]="src" [alt]="a.name"
                               [title]="a.agentPath" (click)="openAttachment(a)">
                        } @else {
                          <div class="msg-attachment-img msg-attachment-img--loading">
                            <mat-icon>image</mat-icon>
                          </div>
                        }
                      } @else {
                        <button class="msg-attachment-file" (click)="openAttachment(a)"
                                [title]="a.agentPath + ' — herunterladen'">
                          <mat-icon>insert_drive_file</mat-icon>
                          <span>{{ a.name }}</span>
                        </button>
                      }
                    }
                  </div>
                }
                @if (msg.role === 'assistant' && workspaceFiles(msg).length) {
                  <!-- Dateien, die die Antwort im Workspace nennt (Berichte): ein Klick
                       öffnet sie gerendert in einem neuen Fenster. Im Template, nicht im
                       Markdown-HTML (dort läuft kein Sanitizer). -->
                  <div class="msg-ws-files">
                    @for (f of workspaceFiles(msg); track f) {
                      <button class="msg-ws-file" (click)="openWorkspaceFile(f)"
                              [title]="f + ' — in neuem Fenster öffnen'">
                        <mat-icon>description</mat-icon>
                        <span>{{ wsRelative(f) }}</span>
                        <mat-icon class="msg-ws-open">open_in_new</mat-icon>
                      </button>
                    }
                  </div>
                }
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
                      {{ acceptingTicketActivity() ? 'WIRD ÜBERNOMMEN …' : 'ALS KONTEXT ANHÄNGEN' }}
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

          <!-- Angehängter Ticket-Kontext: wartet neben dem Feld, geht mit der
               nächsten eigenen Nachricht mit. Sichtbar benannt, damit kein
               unsichtbarer Zustand entsteht — und einzeln verwerfbar. -->
          @if (activeContext(); as ctx) {
            <div class="attached-context">
              <div class="attached-context-head">
                <mat-icon>attachment</mat-icon>
                <span class="attached-context-label">{{ ctx.label }}</span>
                <span class="attached-context-hint">geht mit deiner nächsten Nachricht mit</span>
                <button class="attached-context-btn" (click)="toggleContextExpanded()"
                        [title]="contextExpanded() ? 'Einklappen' : 'Anzeigen'">
                  <mat-icon>{{ contextExpanded() ? 'expand_less' : 'expand_more' }}</mat-icon>
                </button>
                <button class="attached-context-btn" (click)="discardActiveContext()"
                        title="Kontext verwerfen">
                  <mat-icon>close</mat-icon>
                </button>
              </div>
              @if (taskStaged()) {
                <!-- Nach der Ticket-Übergabe: die Aufgabe steht im Eingabefeld. Beide
                     Wege sind gleich richtig, deshalb stehen beide da — losschicken
                     oder erst anpassen. -->
                <div class="attached-context-actions">
                  <span class="attached-context-hint">Aufgabe steht im Eingabefeld:</span>
                  <button class="ctx-action ctx-action--go" (click)="startTask()">
                    <mat-icon>play_arrow</mat-icon> LEG LOS
                  </button>
                  <button class="ctx-action" (click)="editTask()">
                    <mat-icon>edit</mat-icon> BEARBEITEN
                  </button>
                </div>
              }
              @if (contextExpanded()) {
                <pre class="attached-context-body">{{ ctx.text }}</pre>
              }
            </div>
          }

          <!-- Eingefügte Bilder: warten neben dem Feld und gehen mit der nächsten
               Nachricht mit — dasselbe Muster wie der Ticket-Kontext darüber. -->
          @if (activeAttachments().length || uploadingCount() > 0) {
            <div class="pasted-images">
              @for (a of activeAttachments(); track a.id) {
                <div class="pasted-image" [class.pasted-image--file]="a.kind === 'file'"
                     [title]="a.agentPath">
                  @if (a.kind === 'image' && a.previewUrl) {
                    <img [src]="a.previewUrl" [alt]="a.name">
                  } @else {
                    <mat-icon class="pasted-file-icon">{{ attachmentIcon(a.label) }}</mat-icon>
                    <span class="pasted-file-name">{{ a.name }}</span>
                  }
                  <span class="pasted-image-size">{{ formatBytes(a.size) }}</span>
                  <button class="pasted-image-remove" (click)="removeAttachment(a)"
                          [title]="a.name + ' entfernen'">
                    <mat-icon>close</mat-icon>
                  </button>
                </div>
              }
              @if (uploadingCount() > 0) {
                <div class="pasted-image pasted-image--loading">
                  <mat-icon class="spinning">sync</mat-icon>
                </div>
              }
            </div>
          }

          <!-- Input -->
          <div class="input-row">
            <textarea #inputEl
                      class="lcars-input"
                      [(ngModel)]="inputText"
                      placeholder="Computer, ...  (Space = Mikrofon, Strg+V für Bilder)"
                      [disabled]="loading()"
                      (input)="resizeInput()"
                      (paste)="onPaste($event)"
                      (keydown)="onInputKeydown($event)"></textarea>
            <!-- Dateien hochladen. Das <input> bleibt unsichtbar; der Knopf öffnet es.
                 Dieselben Dateien gehen auch per Strg+V und per Drag-and-Drop. -->
            <input #fileInput type="file" multiple hidden (change)="onFilesPicked($event)">
            <button class="icon-btn" (click)="openFilePicker()" [disabled]="loading()"
                    title="Datei anhängen (auch per Strg+V oder Drag-and-Drop)">
              <mat-icon>attach_file</mat-icon>
            </button>
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
                      [disabled]="(!inputText.trim() && !activeAttachments().length) || uploadingCount() > 0">→</button>
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

  private _wsFileCache = new WeakMap<HermesMessage, string[]>();

  /** Workspace-Dateien, die eine Antwort erwähnt (gecacht wie das Markdown). */
  workspaceFiles(msg: HermesMessage): string[] {
    let files = this._wsFileCache.get(msg);
    if (!files) {
      files = extractWorkspaceFiles(msg.text);
      this._wsFileCache.set(msg, files);
    }
    return files;
  }

  wsRelative(path: string): string { return workspaceRelative(path); }

  /**
   * Datei aus dem Workspace in einem NEUEN Fenster zeigen, Markdown gerendert.
   *
   * Das Fenster wird sofort beim Klick geöffnet (sonst blockt der Browser das Popup)
   * und dann befüllt — als eigenes Fenster wie die Cockpit-Ansicht, nicht als Tab. Es teilt den Ursprung mit CentralStation — ein Bericht mit
   * eingeschleustem Skript käme sonst an den Token. Darum: HTML durch Angulars
   * Sanitizer UND eine CSP, die jedes Skript verbietet.
   */
  openWorkspaceFile(path: string): void {
    // Eigenes Fenster wie die Cockpit-Ansicht (Fenstername + Maße), kein neuer Tab. Der
    // Name je Datei holt beim zweiten Klick dasselbe Fenster nach vorn statt ein weiteres.
    const name = 'report-' + workspaceRelative(path).replace(/[^\w-]+/g, '_').slice(0, 80);
    const w = window.open('', name,
      'width=1100,height=860,menubar=no,toolbar=no,location=no,status=no');
    w?.focus();
    if (!w) {
      this.snackBar.open('Popup blockiert — bitte Popups für CentralStation erlauben', 'OK', { duration: 4000 });
      return;
    }
    w.document.title = workspaceRelative(path);
    w.document.body.textContent = 'Lade …';
    const token = this.auth.getAccessToken();
    fetch(`${this.apiBase}/workspace-file?path=${encodeURIComponent(path)}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    }).then(async r => {
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        w.document.body.textContent = `Nicht verfügbar: ${data?.detail || r.status}`;
        return;
      }
      const esc = (t: string) => t.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!));
      const body = data.kind === 'markdown'
        ? this.sanitizer.sanitize(SecurityContext.HTML, marked.parse(data.content) as string) ?? ''
        : `<pre>${esc(data.content)}</pre>`;
      const raw = URL.createObjectURL(new Blob([data.content], { type: 'text/plain;charset=utf-8' }));
      const modified = new Date(data.modified).toLocaleString('de-DE');
      w.document.open();
      w.document.write(`<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(data.name)}</title>
<style>
  :root { color-scheme: light dark; --fg:#1d1d1f; --bg:#fff; --muted:#666; --line:#ddd; --code:#f4f4f4; }
  @media (prefers-color-scheme: dark) { :root { --fg:#e8e6e3; --bg:#16130f; --muted:#a09a90; --line:#3a332a; --code:#221d17; } }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.6 -apple-system, Segoe UI, Roboto, sans-serif; }
  header { position:sticky; top:0; background:var(--bg); border-bottom:1px solid var(--line); padding:10px 24px;
           display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
  header .path { font:13px/1.4 'Fira Code', monospace; color:var(--muted); flex:1; word-break:break-all; }
  header a { color:inherit; font-size:13px; }
  main { max-width:900px; margin:0 auto; padding:16px 24px 48px; }
  pre, code { background:var(--code); border-radius:4px; font-family:'Fira Code', monospace; font-size:13px; }
  pre { padding:10px 12px; overflow:auto; white-space:pre-wrap; }
  code { padding:1px 4px; } pre code { padding:0; }
  table { border-collapse:collapse; width:100%; } th, td { border:1px solid var(--line); padding:5px 8px; text-align:left; }
  img { max-width:100%; } h1, h2, h3 { line-height:1.25; }
  @media print { header { display:none; } }
</style></head><body>
<header><span class="path">${esc(data.path)} · ${esc(modified)}</span>
<a href="${raw}" download="${esc(data.name)}">Herunterladen</a></header>
<main>${body}</main></body></html>`);
      w.document.close();
    }).catch(() => { w.document.body.textContent = 'Datei konnte nicht geladen werden.'; });
  }

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

  /** Angehängter Ticket-Kontext je Sitzung — bereitgelegt, aber noch nicht gesendet.
   *
   *  Er landet NICHT im Eingabefeld: dort stünde eine Textwand, die der Nutzer erst
   *  wegräumen müsste. Er hängt an der Sitzung und geht mit der nächsten Nachricht
   *  mit, die der Nutzer selbst schreibt. Bis dahin arbeitet die KI nicht.
   *
   *  Erst das Absenden quittiert den Jira-Stand. Solange der Kontext nur anhängt,
   *  gilt die Änderung als ungelesen und die Aktivitätsmeldung bleibt stehen —
   *  sonst verschwände etwas, das die KI nie gesehen hat. */
  /** Eingefügte Bilder je Sitzung, noch nicht abgeschickt. Gleiches Muster wie der
   *  angehängte Ticket-Kontext: an der Sitzung, nicht im Eingabefeld. */
  private pendingAttachments = signal<Record<string, PendingAttachment[]>>({});

  /** Bilder der aktiven Sitzung, für die Leiste über dem Eingabefeld. */
  readonly activeAttachments = computed<PendingAttachment[]>(() => {
    const sid = this.activeTabId();
    return sid ? this.pendingAttachments()[sid] ?? [] : [];
  });

  /** Läuft gerade ein Bild-Upload? Blockiert das Absenden, damit kein Bild verloren geht. */
  readonly uploadingCount = signal(0);

  /** Liegt gerade eine Datei über der Konsole (Drag-and-Drop)? */
  readonly dragOver = signal(false);

  private pendingContext = signal<Record<string, PendingContext>>({});

  /** Kontext der aktiven Sitzung, für die Anzeige über dem Eingabefeld. */
  readonly activeContext = computed<PendingContext | null>(() => {
    const sid = this.activeTabId();
    return sid ? this.pendingContext()[sid] ?? null : null;
  });

  /** Ist der angehängte Kontext ausgeklappt? */
  readonly contextExpanded = signal(false);

  /** Liegt eine frisch übergebene Aufgabe im Eingabefeld? Steuert die beiden Knöpfe
   *  "Leg los" / "Bearbeiten". Wird zurückgesetzt, sobald gesendet oder verworfen
   *  wird — die Knöpfe sollen nicht stehenbleiben, wenn es nichts mehr zu starten gibt. */
  readonly taskStaged = signal(false);

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
    // Hängt die Aktivität bereits als Kontext an der Eingabe, verschwindet das
    // Banner: die Kontextleiste über dem Eingabefeld zeigt dieselbe Tatsache und
    // sagt zusätzlich, was als Nächstes passiert. Zwei Anzeigen für dasselbe wären
    // nicht nur überflüssig — der Knopf im Banner ließe sich erneut drücken und
    // hinge denselben Kommentar ein zweites Mal an.
    return sid ? this.computerService.pendingActivity(sid) : null;
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

  /** Eine Kennung, eine Stelle: die Berechnung steht im ComputerService, weil der
   *  Kopfzeilen-Zähler sie ebenfalls braucht. Zwei Kopien würden auseinanderlaufen. */
  private activityVersion(activity: TicketActivity): string {
    return ComputerService.activityVersion(activity);
  }

  activityDismissed(activity: TicketActivity): boolean {
    return this.dismissedActivityVersions()[activity.session_id] === this.activityVersion(activity);
  }

  dismissActivity(activity: TicketActivity): void {
    // Sofort ausblenden — die Quittierung unten läuft asynchron.
    const dismissed = {
      ...this.dismissedActivityVersions(),
      [activity.session_id]: this.activityVersion(activity),
    };
    this.dismissedActivityVersions.set(dismissed);
    localStorage.setItem(this.dismissedActivityStorageKey, JSON.stringify(dismissed));

    // Bei neuer Aktivität heißt "Später": gesehen. Das wird jetzt QUITTIERT, nicht nur
    // lokal ausgeblendet. Die lokale Ausblendung allein hing am ganzen Ticketstand —
    // jeder spätere eigene Kommentar änderte ihn, und der längst gesehene fremde
    // Kommentar kam wieder als neu. Beim Banner "Quelle nicht erreichbar" gibt es
    // nichts zu quittieren; dort bleibt es beim Ausblenden.
    if (activity.state === 'changed' && activity.snapshot) {
      void this.acknowledgeSeen(activity.session_id, activity.snapshot, null);
    }
  }

  /** Quittiert einen gesehenen Ticketstand und meldet ein Scheitern sichtbar.
   *
   *  Vorher schlug die Quittierung still fehl (nur console.debug) — und genau deshalb
   *  fiel eine Woche lang nicht auf, dass sie nie geschah. */
  private async acknowledgeSeen(
    sid: string, snapshot: TicketActivitySnapshot, contextHash: string | null,
  ): Promise<void> {
    const ok = await this.acknowledgeTicketActivity(sid, snapshot, contextHash);
    if (!ok) {
      this.snackBar.open(
        'Der Ticketstand konnte nicht als gelesen gespeichert werden — der Kommentar kann wieder als neu erscheinen.',
        '', { duration: 6000 },
      );
      return;
    }
    await this.computerService.refreshTicketActivities(sid);
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

      // Erstübergabe: der Ticketinhalt hängt als Kontext an der Sitzung, die Aufgabe
      // kommt sichtbar ins Eingabefeld. Beides geht erst los, wenn der Nutzer
      // abschickt — vorher hat die KI den Stand nicht gesehen.
      //
      // Getrennt, weil es zwei verschiedene Dinge sind: der Inhalt ist Material und
      // gehört nicht ins Eingabefeld, die Aufgabe ist die Bitte an die KI und muss
      // lesbar und änderbar sein. Steckte sie wie zuvor im eingeklappten Kontext,
      // sähe der Nutzer nur "TICKET" und ein leeres Feld.
      this.attachContext(existing.session_id, {
        text: ticketRef.ticketContext || prompt,
        label: `${ticketRef.key} · Ticket`,
        snapshot: ticketRef.snapshot,
        contextHash,
      });
      if (ticketRef.taskPrompt) {
        this.inputText = ticketRef.taskPrompt;
        this.taskStaged.set(true);
        setTimeout(() => this.resizeInput(), 60);
      }
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

      // Der Text hängt als Kontext an der Sitzung, er geht nicht an die KI. Sie
      // arbeitet erst, wenn der Nutzer eine Nachricht schreibt; bis dahin bleibt
      // die Änderung als ungelesen vermerkt.
      this.activeTabId.set(activity.session_id);
      this.selectTab(activity.session_id);
      this.attachContext(activity.session_id, {
        text: data.prompt,
        label: `${activity.ticket_ref.key} · Neue Aktivität`,
        snapshot: data.snapshot,
        contextHash: data.context_hash,
        // Der frisch geholte Stand, nicht der beim Klick angezeigte: zwischen
        // Klick und Antwort kann ein Kommentar dazugekommen sein.
        activityVersion: this.activityVersion({ ...activity, ...data }),
      });
      this.snackBar.open(
        'Als Kontext angehängt — geht mit deiner nächsten Nachricht mit',
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
        // Oben einreihen: die Liste ist "zuletzt aktiv zuerst" (Backend sortiert nach
        // last_activity_at, und das Anlegen setzt ihn auf jetzt). Hinten angehängt
        // stand die neue Sitzung unten und sprang erst beim Senden oder Neuladen
        // nach oben — dieselbe Sitzung an zwei Plätzen, je nach Zeitpunkt.
        return [{
          ...session,
          label: displayLabel,
          msg_count: session.msg_count ?? 0,
          messages: session.messages ?? [],
          external_id: externalId,
          last_activity_at: session.last_activity_at ?? new Date().toISOString(),
        }, ...ss];
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
        .map(m => this.withAttachmentsFromMarker(m.role as 'user' | 'assistant', m.content));
      if (!messages.length) return;
      this.sessions.update(ss => ss.map(s =>
        s.session_id === sid ? { ...s, messages } : s
      ));
    } catch (err) {
      console.debug('loadSessionHistory failed:', err);
    }
  }

  /** Baut aus dem `[Bilder: …]`-Marker einer geladenen Nachricht wieder Vorschauen.
   *
   *  Die Historie kommt vom Agenten und kennt nur Text. Ohne diesen Schritt stünden
   *  nach einem Neuladen rohe Pfade im Verlauf, wo vorher Bilder waren — dieselbe
   *  Unterhaltung sähe je nach Zeitpunkt des Öffnens anders aus. */
  private withAttachmentsFromMarker(role: 'user' | 'assistant', text: string): HermesMessage {
    const attachments: MessageAttachment[] = [];
    const markers: [RegExp, 'image' | 'file'][] = [
      [ComputerComponent.IMAGE_MARKER, 'image'],
      [ComputerComponent.FILE_MARKER, 'file'],
    ];
    for (const [marker, kind] of markers) {
      const re = new RegExp(marker.source, 'gm');
      let match: RegExpExecArray | null;
      while ((match = re.exec(text)) !== null) {
        for (const path of match[1].split(/\s+/).filter(Boolean)) {
          const id = path.split('/').pop() || '';
          if (!id) continue;
          attachments.push({
            kind, agentPath: path,
            url: `${this.apiBase}/uploads/${encodeURIComponent(id)}`,
            name: ComputerComponent.displayName(id),
          });
        }
      }
    }
    return attachments.length ? { role, text, attachments } : { role, text };
  }

  /** Gespeicherter Name → Anzeigename: `<32 Hex-Zeichen>-umsatz.xlsx` → `umsatz.xlsx`.
   *  Dieselbe Regel wie `_display_name` im Backend, damit ein Anhang live und nach
   *  einem Neuladen gleich heißt. */
  static displayName(stored: string): string {
    const m = /^[0-9a-f]{32}-(.+)$/.exec(stored);
    return m ? m[1] : stored;
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

  // ── Anhänge: Strg+V, Dateiauswahl, Drag-and-Drop ──────────────────

  /** Marker für Bildpfade — Nutzlast (der Agent liest sie) und Anzeige-Anker (beim
   *  Nachladen der Historie werden daraus wieder Vorschauen). */
  static readonly IMAGE_MARKER = /^\[Bilder:\s*(.+?)\]$/gm;
  /** Marker für andere Dateien. Getrennt vom Bild-Marker, weil die Agenten beides
   *  unterschiedlich behandeln: Bilder sehen sie, Dateien öffnen sie selbst. */
  static readonly FILE_MARKER = /^\[Dateien:\s*(.+?)\]$/gm;

  @ViewChild('fileInput') private fileInput?: ElementRef<HTMLInputElement>;

  async onPaste(e: ClipboardEvent): Promise<void> {
    // Dateien aus der Zwischenablage — Screenshots ebenso wie im Dateimanager
    // kopierte Dateien. Reiner Text bleibt beim normalen Einfügen.
    const files = Array.from(e.clipboardData?.files ?? []);
    if (!files.length) return;
    e.preventDefault();
    await this.uploadFiles(files);
  }

  openFilePicker(): void {
    this.fileInput?.nativeElement.click();
  }

  async onFilesPicked(e: Event): Promise<void> {
    const input = e.target as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    input.value = '';   // dieselbe Datei muss erneut wählbar sein
    await this.uploadFiles(files);
  }

  onDragOver(e: DragEvent): void {
    if (!e.dataTransfer?.types?.includes('Files')) return;
    e.preventDefault();
    this.dragOver.set(true);
  }

  onDragLeave(e: DragEvent): void {
    // Nur wirklich verlassen zählen — beim Überfahren von Kindelementen feuert
    // dragleave ständig, und die Markierung würde flackern.
    const into = e.relatedTarget as Node | null;
    if (into && (e.currentTarget as Node).contains(into)) return;
    this.dragOver.set(false);
  }

  async onDrop(e: DragEvent): Promise<void> {
    e.preventDefault();
    this.dragOver.set(false);
    await this.uploadFiles(Array.from(e.dataTransfer?.files ?? []));
  }

  private async uploadFiles(files: File[]): Promise<void> {
    if (!files.length) return;
    let sid = this.activeTabId();
    if (!sid) {
      await this.newSession();
      sid = this.activeTabId();
      if (!sid) return;
    }
    // Nacheinander, nicht parallel: bei einem Fehler bleibt klar, welche Datei es war.
    for (const f of files) await this.uploadAttachment(sid, f);
  }

  private async uploadAttachment(sid: string, file: Blob & { name?: string }): Promise<void> {
    const isImage = (file.type || '').startsWith('image/');
    const previewUrl = isImage ? URL.createObjectURL(file) : undefined;
    this.uploadingCount.update(n => n + 1);
    try {
      const fd = new FormData();
      // Den Namen mitschicken: der Server leitet daraus Art und Anzeigenamen ab (und
      // prüft beides gegen den Inhalt). Ein eingefügter Screenshot hat keinen Namen.
      fd.append('file', file, file.name || 'eingefuegt');
      const token = this.auth.getAccessToken();
      const r = await fetch(`${this.apiBase}/uploads`, {
        method: 'POST',
        body: fd,
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!r.ok) {
        const detail = await r.json().catch(() => null);
        throw new Error(detail?.detail || `HTTP ${r.status}`);
      }
      const data = await r.json();
      const att: PendingAttachment = {
        kind: data.kind, id: data.id, name: data.name, label: data.label,
        agentPath: data.agent_path, url: data.url,
        previewUrl: data.kind === 'image' ? previewUrl : undefined,
        size: data.size,
      };
      if (att.kind !== 'image' && previewUrl) URL.revokeObjectURL(previewUrl);
      this.pendingAttachments.update(all => ({ ...all, [sid]: [...(all[sid] ?? []), att] }));
    } catch (err) {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      const what = file.name ? `„${file.name}"` : 'Die Datei';
      this.snackBar.open(
        `${what} wurde nicht angenommen: ${err instanceof Error ? err.message : 'Upload fehlgeschlagen'}`,
        '', { duration: 5000 },
      );
    } finally {
      this.uploadingCount.update(n => Math.max(0, n - 1));
    }
  }

  removeAttachment(att: PendingAttachment): void {
    const sid = this.activeTabId();
    if (!sid) return;
    if (att.previewUrl) URL.revokeObjectURL(att.previewUrl);
    this.pendingAttachments.update(all => ({
      ...all, [sid]: (all[sid] ?? []).filter(i => i.id !== att.id),
    }));
  }

  // ── Anhänge anzeigen und öffnen ───────────────────────────────────
  //
  // Die Upload-Endpunkte verlangen einen Bearer-Token. Ein <img src> und ein normaler
  // Download-Link schicken den aber nicht mit — sie bekämen 401. Deshalb werden
  // Anhänge per fetch MIT Token geholt und als Blob-URL eingesetzt. Ohne das zeigten
  // die Vorschauen im Verlauf nach einem Neuladen nur leere Rahmen.

  /** Aufgelöste Blob-URLs je Anhang-URL (Signal, damit das Template nachzieht). */
  private readonly resolvedUrls = signal<Record<string, string>>({});
  private readonly resolving = new Set<string>();

  /** Blob-URL für ein Bild, oder '' solange es lädt. Stößt das Laden beim ersten
   *  Aufruf an; die Antwort kommt asynchron über das Signal. */
  imageSrc(url: string): string {
    const known = this.resolvedUrls()[url];
    if (known) return known;
    if (!this.resolving.has(url)) {
      this.resolving.add(url);
      void this.fetchAuthed(url)
        .then(blob => {
          const obj = URL.createObjectURL(blob);
          this.resolvedUrls.update(all => ({ ...all, [url]: obj }));
        })
        .catch(err => console.debug('Anhang nicht ladbar:', url, err))
        .finally(() => this.resolving.delete(url));
    }
    return '';
  }

  private async fetchAuthed(url: string): Promise<Blob> {
    const token = this.auth.getAccessToken();
    const r = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.blob();
  }

  /** Bild in neuem Tab ansehen, Datei herunterladen — beides mit Token. */
  async openAttachment(a: MessageAttachment): Promise<void> {
    try {
      const blob = await this.fetchAuthed(a.url);
      const obj = URL.createObjectURL(blob);
      if (a.kind === 'image') {
        window.open(obj, '_blank', 'noopener');
      } else {
        const link = document.createElement('a');
        link.href = obj;
        link.download = a.name;
        link.click();
      }
      // Kurz warten, damit Tab bzw. Download die URL noch öffnen können.
      setTimeout(() => URL.revokeObjectURL(obj), 60_000);
    } catch {
      this.snackBar.open(`„${a.name}" ist nicht mehr verfügbar`, '', { duration: 4000 });
    }
  }

  /** Material-Icon je Kategorie, für die Chips. */
  attachmentIcon(label: string): string {
    switch (label) {
      case 'Tabelle': return 'table_chart';
      case 'PDF': return 'picture_as_pdf';
      case 'Dokument': return 'description';
      case 'Präsentation': return 'slideshow';
      case 'Archiv': return 'folder_zip';
      case 'Skript': return 'code';
      case 'Log': return 'receipt_long';
      default: return 'insert_drive_file';
    }
  }

  formatBytes(n: number): string {
    return n < 1024 * 1024 ? `${Math.round(n / 1024)} KB`
                           : `${(n / (1024 * 1024)).toFixed(1)} MB`;
  }

  /** Hängt Ticket-Kontext an eine Sitzung, ohne die KI zu starten.
   *
   *  Bewusst nicht ins Eingabefeld: dort stünde eine Textwand, die der Nutzer erst
   *  wegräumen müsste, bevor er seine eigene Frage schreiben kann. Der Kontext wartet
   *  neben dem Feld und geht mit der nächsten Nachricht mit. Ein zweiter Kontext
   *  für dieselbe Sitzung wird angehängt, nicht ersetzt — sonst ginge die erste
   *  Änderung verloren, obwohl sie als übernommen angezeigt wurde. */
  private attachContext(sid: string, next: PendingContext): void {
    this.pendingContext.update(all => {
      const existing = all[sid];
      const merged: PendingContext = existing
        ? {
            text: `${existing.text}\n\n---\n\n${next.text}`,
            label: `${existing.label} + ${next.label}`,
            snapshot: next.snapshot ?? existing.snapshot,
            contextHash: next.contextHash ?? existing.contextHash,
          }
        : next;
      return { ...all, [sid]: merged };
    });
    if (next.activityVersion) {
      this.computerService.markContextAttached(sid, next.activityVersion);
    }
    // Angehängt heißt: übernommen. Quittiert wird JETZT, nicht erst nach dem
    // erfolgreichen Senden. Hing es am Senden, ging es bei jeder gescheiterten Runde,
    // jedem Neuladen und jedem Neustart verloren — der Merker "angehängt" lebt nur im
    // Arbeitsspeicher, das "ungelesen" aber in der Datenbank. So blieb ein Kommentar
    // eine Woche lang "neu", obwohl er mehrfach übernommen worden war.
    if (next.snapshot) {
      void this.acknowledgeSeen(sid, next.snapshot, next.contextHash);
    }
    this.contextExpanded.set(false);
    setTimeout(() => this.inputEl?.nativeElement.focus(), 50);
  }

  /** Angehängten Kontext verwerfen. Die Aktivitätsmeldung kommt dadurch zurück,
   *  weil nichts quittiert wurde — die Änderung bleibt sichtbar ungelesen. */
  async discardContext(sid: string): Promise<void> {
    this.pendingContext.update(all => {
      const { [sid]: _dropped, ...rest } = all;
      return rest;
    });
    // Verworfen heißt: nicht übernommen. Das Banner kommt zurück, weil die Änderung
    // weiterhin ungelesen ist — sonst verschwände sie, ohne dass sie jemand sah.
    this.computerService.clearContextAttached(sid);
    this.contextExpanded.set(false);
    this.taskStaged.set(false);
    await this.computerService.refreshTicketActivities(sid);
  }

  /** "Leg los": schickt die Aufgabe ab, wie sie im Feld steht. */
  async startTask(): Promise<void> {
    this.taskStaged.set(false);
    await this.send();
  }

  /** "Bearbeiten": Fokus ins Eingabefeld, Cursor ans Ende. Die Knöpfe verschwinden,
   *  weil der Nutzer ab hier selbst entscheidet, wann er sendet. */
  editTask(): void {
    this.taskStaged.set(false);
    const el = this.inputEl?.nativeElement;
    if (el) {
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    }
  }

  toggleContextExpanded(): void {
    this.contextExpanded.update(v => !v);
  }

  /** Den Kontext der aktiven Sitzung verwerfen (Knopf in der Kontextleiste). */
  async discardActiveContext(): Promise<void> {
    const sid = this.activeTabId();
    if (sid) await this.discardContext(sid);
  }

  /** Nach erfolgreichem Absenden: den mitgegangenen Jira-Stand als übernommen buchen. */
  private async resolveSentContext(sid: string, sent: PendingContext): Promise<void> {
    // Der Ticketstand wurde bereits beim Anhängen quittiert (attachContext). Hier
    // bleibt nur der Kontext-Hash für Sitzungen ohne strukturierten Snapshot.
    if (!sent.snapshot && sent.contextHash) {
      await this.persistContextHash(sid, sent.contextHash);
    }
    await this.computerService.refreshTicketActivities(sid);
  }

  async send(): Promise<boolean> {
    const text = this.inputText.trim();
    const sid = this.activeTabId();
    const attachments = sid ? this.pendingAttachments()[sid] ?? [] : [];
    // Ein Anhang allein genügt — so verhalten sich Claude und Google auch. Solange ein
    // Upload läuft, wird nicht gesendet: sonst ginge die Datei still verloren.
    if ((!text && !attachments.length) || this.loading() || this.uploadingCount() > 0) return false;

    const context = sid ? this.pendingContext()[sid] ?? null : null;
    this.inputText = '';
    setTimeout(() => this.resizeInput(), 0);

    // Der Kontext geht der eigenen Nachricht voran; gesendet wird beides, angezeigt
    // die eigene Nachricht mit dem Kontext als aufklappbarem Anhang.
    let payload = context ? `${context.text}\n\n---\n\n${text}` : text;
    if (attachments.length) {
      // Die Marker sind Nutzlast und Anzeige zugleich: der Agent liest die Pfade, und
      // beim Nachladen der Historie werden daraus wieder Vorschauen und Chips. Bilder
      // und Dateien getrennt, weil die Agenten sie unterschiedlich behandeln.
      const markers: string[] = [];
      const imgs = attachments.filter(a => a.kind === 'image');
      const docs = attachments.filter(a => a.kind === 'file');
      if (imgs.length) markers.push(`[Bilder: ${imgs.map(a => a.agentPath).join(' ')}]`);
      if (docs.length) markers.push(`[Dateien: ${docs.map(a => a.agentPath).join(' ')}]`);
      const block = markers.join('\n');
      payload = payload ? `${payload}\n\n${block}` : block;
      this.pendingAttachments.update(all => {
        const { [sid!]: _used, ...rest } = all;
        return rest;
      });
    }
    // Der angehängte Kontext verschwindet, sobald der Server die Nachricht ANGENOMMEN
    // hat — nicht erst, wenn die Antwort erfolgreich zu Ende ist. Vorher blieb die
    // Leiste während der ganzen Antwort stehen (mit einer Tiefensuche bis zu fünf
    // Minuten) und nach einer gescheiterten Runde für immer, obwohl der Kontext längst
    // in der Nachricht stand. Er geht auch nicht verloren: er ist Teil der Nachricht im
    // Verlauf und im Gedächtnis des Agenten, ein "Probiere es nochmal" hat ihn also.
    let accepted = false;
    const sent = await this.sendContent(payload, undefined, attachments, () => {
      accepted = true;
      if (sid && context) {
        this.pendingContext.update(all => {
          const { [sid]: _used, ...rest } = all;
          return rest;
        });
        this.computerService.clearContextAttached(sid);
      }
      this.taskStaged.set(false);
    });
    if (accepted && sid && context) {
      await this.resolveSentContext(sid, context);
    }
    return sent;
  }

  private async sendContent(
    text: string, targetSid?: string, attachments: PendingAttachment[] = [],
    onAccepted?: () => void,
  ): Promise<boolean> {
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
    // Angezeigt wird genau das, was gesendet wurde: der Ticket-Kontext als ganz
    // normale Nachricht im Verlauf, gefolgt von der eigenen Zeile. Kein Aufklapper
    // und keine gekürzte Fassung — nach einem Neuladen liefert der Agent dieselbe
    // eine Nachricht zurück, und beide Ansichten müssen übereinstimmen.
    this._addMessage(sid, 'user', text,
                     attachments.map(a => ({ kind: a.kind, url: a.url,
                                             agentPath: a.agentPath, name: a.name })));
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
        body: JSON.stringify({
          content: text,
          // Nur Bilder: Codex bekommt sie als -i, Hermes als Bildblock. Dateien stehen
          // im Text-Marker, den Rest erledigt der Agent selbst.
          ...(attachments.some(a => a.kind === 'image')
            ? { image_paths: attachments.filter(a => a.kind === 'image').map(a => a.agentPath) }
            : {}),
        }),
        signal: this._abortController.signal,
      });

      if (!resp.ok) {
        this._appendToLast(sid, `[Fehler: HTTP ${resp.status}]`);
        streamFailed = true;
        return false;
      }
      // Ab hier liegt die Nachricht beim Agenten — auch wenn die Antwort noch Minuten
      // braucht oder später scheitert. Was an ihr hing, gilt damit als übergeben.
      onAccepted?.();

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
              if (data.type === 'notice') {
                // Fehlversuch beim LLM-Anbieter (Hermes): sichtbar statt stummem "verarbeitet".
                this._setActiveTool(sid, `⚠ ${data.text}`);
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

  private _addMessage(
    sid: string, role: 'user' | 'assistant', text: string,
    attachments: MessageAttachment[] = [],
  ): void {
    const msg: HermesMessage = attachments.length ? { role, text, attachments } : { role, text };
    this.sessions.update(ss => ss.map(s =>
      s.session_id === sid ? { ...s, messages: [...s.messages, msg] } : s
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
