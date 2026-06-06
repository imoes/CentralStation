import { Injectable } from '@angular/core';
import { Subject } from 'rxjs';

export interface ComputerHandoff {
  /** First message Hermes should receive — pre-filled with incident context */
  prompt: string;
  /** Optional label for the session tab */
  label?: string;
}

/**
 * Singleton bridge between the News-Feed (or any component) and the
 * always-present ComputerComponent.  Emit a ComputerHandoff to open the
 * Computer panel with a new session pre-loaded with that context.
 */
@Injectable({ providedIn: 'root' })
export class ComputerService {
  readonly handoff$ = new Subject<ComputerHandoff>();

  openWithContext(prompt: string, label?: string): void {
    this.handoff$.next({ prompt, label });
  }
}
