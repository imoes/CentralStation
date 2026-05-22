import { Injectable, OnDestroy } from '@angular/core';
import { Subject, Observable, timer, EMPTY } from 'rxjs';
import { webSocket, WebSocketSubject } from 'rxjs/webSocket';
import { catchError, retryWhen, delay, tap } from 'rxjs/operators';
import { environment } from '../../../environments/environment';
import { AuthService } from '../auth/auth.service';

export interface WsMessage {
  type: string;
  [key: string]: unknown;
}

@Injectable({ providedIn: 'root' })
export class WebsocketService implements OnDestroy {
  private socket$: WebSocketSubject<WsMessage> | null = null;
  private messages$ = new Subject<WsMessage>();
  private pingInterval: ReturnType<typeof setInterval> | null = null;

  constructor(private auth: AuthService) {}

  connect(): void {
    const token = this.auth.getAccessToken();
    if (!token || this.socket$) return;

    const url = `${environment.wsUrl}?token=${token}`;
    this.socket$ = webSocket<WsMessage>(url);

    this.socket$.pipe(
      retryWhen(errors => errors.pipe(
        tap(() => console.warn('WS disconnected, retrying in 5s')),
        delay(5000)
      )),
      catchError(() => EMPTY)
    ).subscribe({
      next: msg => this.messages$.next(msg),
      error: err => console.error('WS error', err),
    });

    // Keep-alive ping every 30s
    this.pingInterval = setInterval(() => {
      this.socket$?.next({ type: 'ping' } as unknown as WsMessage);
    }, 30000);
  }

  disconnect(): void {
    if (this.pingInterval) clearInterval(this.pingInterval);
    this.socket$?.complete();
    this.socket$ = null;
  }

  messages(): Observable<WsMessage> {
    return this.messages$.asObservable();
  }

  ngOnDestroy(): void {
    this.disconnect();
  }
}
