import { Injectable, signal, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';

export type AppTheme = 'classic' | 'holo' | 'lcars';

const STORAGE_KEY = 'cs_theme';
const ALL: AppTheme[] = ['classic', 'holo', 'lcars'];

/**
 * App-wide theme switching. Applies a `cs-theme-{name}` class to <html>;
 * the actual colours come from CSS variable overrides in styles.scss.
 * Persisted in localStorage (instant) and in user preferences (cross-device).
 */
@Injectable({ providedIn: 'root' })
export class ThemeService {
  private http = inject(HttpClient);
  readonly theme = signal<AppTheme>('classic');

  /** Apply the locally-stored theme immediately (call at app start, pre-login). */
  initFromStorage(): void {
    const stored = localStorage.getItem(STORAGE_KEY) as AppTheme | null;
    this.apply(stored && ALL.includes(stored) ? stored : 'classic', false);
  }

  /** Load the theme from the server preference (call after login). */
  loadFromPreference(): void {
    this.http.get<{ ui_theme?: string }>(`${environment.apiUrl}/preferences`).subscribe({
      next: p => {
        const t = (p.ui_theme as AppTheme) || 'classic';
        if (ALL.includes(t)) this.apply(t, false);
      },
      error: () => {},
    });
  }

  setTheme(t: AppTheme): void {
    this.apply(t, true);
  }

  private apply(t: AppTheme, persist: boolean): void {
    this.theme.set(t);
    const root = document.documentElement;
    ALL.forEach(name => root.classList.remove(`cs-theme-${name}`));
    root.classList.add(`cs-theme-${t}`);
    if (persist) {
      localStorage.setItem(STORAGE_KEY, t);
      this.http.patch(`${environment.apiUrl}/preferences`, { ui_theme: t }).subscribe({ error: () => {} });
    }
  }
}
