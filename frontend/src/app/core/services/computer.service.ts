import { computed, inject, Injectable, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom, Subject } from 'rxjs';
import { environment } from '../../../environments/environment';

export interface TicketActivitySnapshot {
  version: number;
  issue_id: string;
  issue_key: string;
  issue_updated_at: string;
  fields: Record<string, string>;
  comments: Record<string, {
    created: string;
    updated: string;
    body_hash: string;
    author_ids?: string[];
  }>;
}

export interface TicketActivityComment {
  id: string;
  author: string;
  author_ids?: string[];
  body: string;
  created: string;
  updated: string;
}

export interface TicketFieldChange {
  field: string;
  label: string;
  before: string | null;
  after: string | null;
}

export interface TicketActivity {
  session_id: string;
  ticket_ref: { connector_id: string; issue_id: string; key: string };
  state: 'current' | 'changed' | 'unavailable';
  checked_at: string;
  issue_updated_at?: string;
  comment_change_count: number;
  new_comments: TicketActivityComment[];
  edited_comments: TicketActivityComment[];
  deleted_comment_ids: string[];
  field_changes: TicketFieldChange[];
  ticket_changed: boolean;
  snapshot?: TicketActivitySnapshot;
  error?: string;
  source_unavailable?: boolean;
}

export interface TicketReference {
  connectorId: string;
  issueId: string;
  key: string;
  snapshot?: TicketActivitySnapshot;
  contextHash?: string;
}

export interface ComputerHandoff {
  prompt: string;
  label?: string;
  /** When set, reuses an existing session for this host instead of always creating a new one. */
  hostKey?: string;
  /** Alert external_id — enables the "Problem gelöst" button that saves a learning comment. */
  externalId?: string;
  /** Stable Jira identity; the visible key alone is not unique across instances. */
  ticketRef?: TicketReference;
}

@Injectable({ providedIn: 'root' })
export class ComputerService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = `${environment.apiUrl}/computer`;
  private readonly _ticketActivities = signal<Record<string, TicketActivity>>({});
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private refreshPromise: Promise<boolean> | null = null;
  private refreshScope: string | null | undefined;

  readonly handoff$ = new Subject<ComputerHandoff>();
  /** Resume an existing persisted session by its session_id. */
  readonly resume$ = new Subject<string>();
  readonly ticketActivities = this._ticketActivities.asReadonly();

  /** Sitzungen, deren Ticket-Aktivität bereits als Kontext an der Eingabe hängt.
   *
   *  Liegt hier, nicht in der Konsolen-Komponente, weil zwei Stellen dieselbe
   *  Tatsache anzeigen: das Aktivitätsbanner in der Konsole und der Zähler im
   *  Kopfbereich. Stünde der Zustand nur in der Komponente, meldete der Zähler
   *  weiter "neu", nachdem der Kommentar längst übernommen wurde. */
  private readonly _contextAttached = signal<Record<string, string>>({});
  readonly contextAttached = this._contextAttached.asReadonly();

  /** Kennzeichnet EINEN Stand einer Aktivität. Trifft ein neuerer Kommentar ein,
   *  ändert sich die Kennung — und die Aktivität gilt wieder als ungelesen, auch
   *  wenn der ältere Stand schon als Kontext hängt. Sonst würde ein Kommentar,
   *  der nach dem Anhängen eintrifft, stillschweigend unterdrückt. */
  static activityVersion(activity: TicketActivity): string {
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

  markContextAttached(sessionId: string, version: string): void {
    this._contextAttached.update(all => ({ ...all, [sessionId]: version }));
  }

  clearContextAttached(sessionId: string): void {
    this._contextAttached.update(all => {
      const { [sessionId]: _dropped, ...rest } = all;
      return rest;
    });
  }

  private isAttached(sessionId: string, activity: TicketActivity): boolean {
    const attached = this._contextAttached()[sessionId];
    return !!attached && attached === ComputerService.activityVersion(activity);
  }

  /** Aktivität einer Sitzung, sofern dieser Stand nicht schon als Kontext hängt. */
  pendingActivity(sessionId: string): TicketActivity | null {
    const activity = this._ticketActivities()[sessionId] ?? null;
    if (!activity) return null;
    return this.isAttached(sessionId, activity) ? null : activity;
  }

  readonly ticketActivitySessionCount = computed(() =>
    Object.entries(this._ticketActivities())
      .filter(([sid, activity]) => activity.state === 'changed' && !this.isAttached(sid, activity))
      .length
  );
  readonly ticketActivityUnavailableCount = computed(() =>
    Object.values(this._ticketActivities()).filter(activity => activity.state === 'unavailable').length
  );
  readonly ticketActivityRefreshing = signal(false);

  openWithContext(
    prompt: string,
    label?: string,
    hostKey?: string,
    externalId?: string,
    ticketRef?: TicketReference,
  ): void {
    this.handoff$.next({ prompt, label, hostKey, externalId, ticketRef });
  }

  resumeSession(sessionId: string): void {
    this.resume$.next(sessionId);
  }

  startTicketActivityPolling(): void {
    if (this.pollTimer) return;
    void this.refreshTicketActivities();
    this.pollTimer = setInterval(() => void this.refreshTicketActivities(), 60 * 60 * 1000);
  }

  stopTicketActivityPolling(): void {
    if (this.pollTimer) clearInterval(this.pollTimer);
    this.pollTimer = null;
    this.refreshPromise = null;
    this.refreshScope = undefined;
    this.ticketActivityRefreshing.set(false);
    this._ticketActivities.set({});
  }

  activityFor(sessionId: string): TicketActivity | undefined {
    return this._ticketActivities()[sessionId];
  }

  async refreshTicketActivities(sessionId?: string): Promise<boolean> {
    // The global refresh already includes every selected session. Reuse it instead
    // of creating overlapping Jira requests from startup, timer and manual sync.
    if (this.refreshPromise) {
      const runningWasGlobal = this.refreshScope === null;
      const result = await this.refreshPromise;
      // A global timer/startup request must not be swallowed by a concurrent
      // single-session refresh.
      if (!sessionId && !runningWasGlobal) return this.refreshTicketActivities();
      return result;
    }
    this.ticketActivityRefreshing.set(true);
    this.refreshScope = sessionId ?? null;
    this.refreshPromise = (async () => {
      try {
        const params = sessionId ? { session_id: sessionId } : undefined;
        const activities = await firstValueFrom(
          this.http.get<TicketActivity[]>(`${this.apiBase}/ticket-activity`, { params })
        );
        if (sessionId) {
          this._ticketActivities.update(current => {
            const next = { ...current };
            const activity = activities[0];
            if (activity) next[sessionId] = this.mergeActivity(current[sessionId], activity);
            else delete next[sessionId];
            return next;
          });
        } else {
          this._ticketActivities.update(current => Object.fromEntries(
            activities.map(activity => [
              activity.session_id,
              this.mergeActivity(current[activity.session_id], activity),
            ])
          ));
        }
        return activities.length === 0 || activities.some(activity => activity.state !== 'unavailable');
      } catch {
        // Keep known unread activity visible. A failed refresh is not evidence that
        // the remote state is current.
        return false;
      } finally {
        this.ticketActivityRefreshing.set(false);
        this.refreshPromise = null;
        this.refreshScope = undefined;
      }
    })();
    return this.refreshPromise;
  }

  clearTicketActivity(sessionId: string): void {
    this._ticketActivities.update(current => {
      const next = { ...current };
      delete next[sessionId];
      return next;
    });
  }

  private mergeActivity(previous: TicketActivity | undefined, incoming: TicketActivity): TicketActivity {
    if (incoming.state === 'unavailable' && previous?.state === 'changed') {
      return {
        ...previous,
        checked_at: incoming.checked_at,
        error: incoming.error,
        source_unavailable: true,
      };
    }
    return {
      ...incoming,
      source_unavailable: incoming.state === 'unavailable',
    };
  }
}
