import { Injectable, signal, computed } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { tap, catchError, EMPTY, Observable, of, map, switchMap } from 'rxjs';
import { environment } from '../../../environments/environment';
import { User, TokenResponse } from '../models/user.model';

const TOKEN_KEY = 'cs_access_token';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private _accessToken = signal<string | null>(localStorage.getItem(TOKEN_KEY));
  private _user = signal<User | null>(null);

  readonly user = this._user.asReadonly();
  readonly isLoggedIn = computed(() => !!this._accessToken());
  readonly userRole = computed(() => this._user()?.role ?? null);

  constructor(private http: HttpClient, private router: Router) {
    // If we have a stored token on startup, fetch the user profile immediately.
    if (this._accessToken()) {
      this.fetchMe();
    }
  }

  login(email: string, password: string) {
    return this.http.post<TokenResponse>(`${environment.apiUrl}/auth/login`,
      { email, password }, { withCredentials: true }
    ).pipe(
      tap(res => {
        this._setToken(res.access_token);
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
      tap(res => this._setToken(res.access_token)),
      catchError(() => {
        this.logout();
        return EMPTY;
      })
    );
  }

  logout() {
    this.http.post(`${environment.apiUrl}/auth/logout`, {}, { withCredentials: true })
      .subscribe();
    this._setToken(null);
    this._user.set(null);
    this.router.navigate(['/login']);
  }

  getAccessToken(): string | null {
    return this._accessToken();
  }

  /**
   * Ensure the session is authenticated. On reload the token is already in
   * localStorage so isLoggedIn() is true immediately — no extra round-trip needed.
   * Falls back to cookie-based silent refresh for new windows (cockpit, etc.)
   * where localStorage may hold a valid token but the user object is not yet loaded.
   */
  ensureAuthenticated(): Observable<boolean> {
    if (this.isLoggedIn()) {
      // Token present but user may not be loaded yet (fresh cockpit window) —
      // fetch the profile if missing, then confirm authenticated.
      if (!this._user()) {
        return this.http.get<User>(`${environment.apiUrl}/auth/me`).pipe(
          map(user => { this._user.set(user); return true; }),
          catchError(() => {
            // Token stored but invalid/expired — clear it and try cookie refresh.
            this._setToken(null);
            return this._silentRefresh();
          }),
        );
      }
      return of(true);
    }
    return this._silentRefresh();
  }

  private _silentRefresh(): Observable<boolean> {
    return this.http.post<TokenResponse>(`${environment.apiUrl}/auth/refresh`, {},
      { withCredentials: true }
    ).pipe(
      switchMap(res => {
        this._setToken(res.access_token);
        return this.http.get<User>(`${environment.apiUrl}/auth/me`);
      }),
      map(user => { this._user.set(user); return true; }),
      catchError(() => of(false)),
    );
  }

  private _setToken(token: string | null): void {
    this._accessToken.set(token);
    if (token) {
      localStorage.setItem(TOKEN_KEY, token);
    } else {
      localStorage.removeItem(TOKEN_KEY);
    }
  }
}
