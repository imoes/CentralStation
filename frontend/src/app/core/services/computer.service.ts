import { Injectable } from '@angular/core';
import { Subject } from 'rxjs';

export interface ComputerHandoff {
  prompt: string;
  label?: string;
  /** When set, reuses an existing session for this host instead of always creating a new one. */
  hostKey?: string;
  /** Alert external_id — enables the "Problem gelöst" button that saves a learning comment. */
  externalId?: string;
}

@Injectable({ providedIn: 'root' })
export class ComputerService {
  readonly handoff$ = new Subject<ComputerHandoff>();
  /** Resume an existing persisted session by its session_id. */
  readonly resume$ = new Subject<string>();

  openWithContext(prompt: string, label?: string, hostKey?: string, externalId?: string): void {
    this.handoff$.next({ prompt, label, hostKey, externalId });
  }

  resumeSession(sessionId: string): void {
    this.resume$.next(sessionId);
  }
}
