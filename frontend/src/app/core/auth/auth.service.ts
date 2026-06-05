import { Injectable, signal, computed } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { tap, catchError, EMPTY, Observable, of, map, switchMap } from 'rxjs';
import { environment } from '../../../environments/environment';
import { User, TokenResponse } from '../models/user.model';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private _accessToken = signal<string | null>(null);
  private _user = signal<User | null>(null);

  readonly user = this._user.asReadonly();
  readonly isLoggedIn = computed(() => !!this._accessToken());
  readonly userRole = computed(() => this._user()?.role ?? null);

  constructor(private http: HttpClient, private router: Router) {}

  login(email: string, password: string) {
    return this.http.post<TokenResponse>(`${environment.apiUrl}/auth/login`,
      { email, password }, { withCredentials: true }
    ).pipe(
      tap(res => {
        this._accessToken.set(res.access_token);
        this.fetchMe();
      })
    );
  }

  fetchMe() {
    this.http.get<User>(`${environment.apiUrl}/auth/me`).subscribe({
      next: user => this._user.set(user),
      error: () => this.logout(),
    });
  }

  refresh() {
    return this.http.post<TokenResponse>(`${environment.apiUrl}/auth/refresh`, {},
      { withCredentials: true }
    ).pipe(
      tap(res => this._accessToken.set(res.access_token)),
      catchError(() => {
        this.logout();
        return EMPTY;
      })
    );
  }

  logout() {
    this.http.post(`${environment.apiUrl}/auth/logout`, {}, { withCredentials: true })
      .subscribe();
    this._accessToken.set(null);
    this._user.set(null);
    this.router.navigate(['/login']);
  }

  getAccessToken(): string | null {
    return this._accessToken();
  }

  /**
   * Ensure the session is authenticated, attempting a silent refresh via the
   * HttpOnly refresh cookie when no in-memory access token exists (e.g. a fresh
   * browser window opened via window.open, or a full page reload).
   * Resolves to true if authenticated, false if the refresh failed.
   */
  ensureAuthenticated(): Observable<boolean> {
    if (this.isLoggedIn()) return of(true);
    return this.http.post<TokenResponse>(`${environment.apiUrl}/auth/refresh`, {},
      { withCredentials: true }
    ).pipe(
      switchMap(res => {
        this._accessToken.set(res.access_token);
        return this.http.get<User>(`${environment.apiUrl}/auth/me`);
      }),
      map(user => { this._user.set(user); return true; }),
      catchError(() => of(false)),
    );
  }
}
