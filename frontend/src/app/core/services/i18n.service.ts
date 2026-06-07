import { Injectable, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';

export type AppLanguage = 'en' | 'de';

const STORAGE_KEY = 'cs_language';
const ALL_LANGUAGES: AppLanguage[] = ['en', 'de'];

const TRANSLATIONS: Record<AppLanguage, Record<string, string>> = {
  en: {
    'app.nav.dashboard': 'Dashboard',
    'app.nav.bridge': 'Bridge',
    'app.nav.feed': 'News Feed',
    'app.nav.alerts': 'Alerts',
    'app.nav.myTickets': 'My Tickets',
    'app.nav.kanban': 'Kanban',
    'app.nav.aiInsights': 'AI Insights',
    'app.nav.settings': 'Settings',
    'app.nav.help': 'Help',
    'app.nav.logout': 'Sign out',
    'app.nav.toggle': 'Collapse or expand navigation',

    'login.email': 'Email',
    'login.password': 'Password',
    'login.submit': 'Sign in',
    'login.invalid': 'Invalid credentials',

    'settings.tabs.connectors': 'Connectors',
    'settings.tabs.myConnectors': 'My connectors',
    'settings.tabs.mySettings': 'My settings',
    'settings.tabs.users': 'Users',
    'settings.tabs.ai': 'AI configuration',
    'settings.tabs.audit': 'Audit log',
    'settings.tabs.feed': 'Feed',

    'settings.my.title': 'My settings',
    'settings.my.save': 'Save',
    'settings.my.appearance.title': 'Appearance',
    'settings.my.appearance.subtitle': 'Theme and language for your workspace. Saved per user.',
    'settings.my.language': 'Language',
    'settings.my.theme.title': 'Theme',
    'settings.my.filter.title': 'AI agent - CheckMK filters',
    'settings.my.filter.subtitle': 'Controls which CheckMK alerts are shown by the AI agent and the news feed. Nothing selected means all values are included.',
    'settings.my.minAge': 'Minimum age for CheckMK messages (minutes)',
    'settings.my.minAgeHint': 'Messages newer than this value are hidden (default: 5)',
    'settings.my.location': 'Location (folder)',
    'settings.my.locationHint': 'Based on the host\'s CheckMK folder path',
    'settings.my.ve': 'Business unit / company',
    'settings.my.criticality': 'Criticality',
    'settings.my.os': 'Operating system',
    'settings.my.hostgroup': 'Host group',
    'settings.my.hostgroupHint': 'CheckMK host group filter for AI agent and alerts',
    'settings.my.activeFilters': 'Active filters:',
    'settings.my.noFilters': 'No filter active - the AI agent analyzes all CheckMK locations.',
    'settings.my.password.title': 'Change password',
    'settings.my.password.current': 'Current password',
    'settings.my.password.new': 'New password',
    'settings.my.password.confirm': 'Confirm new password',
    'settings.my.password.mismatch': 'Passwords do not match',
    'settings.my.password.submit': 'Change password',
    'settings.my.password.minHint': 'At least 8 characters',
    'settings.my.password.changed': 'Password changed successfully',
    'settings.my.password.currentWrong': 'Current password is incorrect',
    'settings.my.password.error': 'Error while changing the password',
    'settings.my.saved': 'Settings saved',
    'settings.my.saveError': 'Error while saving settings',

    'theme.classic.label': 'Classic',
    'theme.classic.desc': 'Light, tidy, blue veil',
    'theme.holo.label': 'Holo HUD',
    'theme.holo.desc': 'Dark blue, cyan glow',
    'theme.lcars.label': 'LCARS',
    'theme.lcars.desc': 'Black/orange, Star Trek',

    'language.en': 'English',
    'language.de': 'German',

    'help.docs': 'Documentation',
    'help.assistant': 'Help assistant',
    'help.hint': 'Ask questions about the documentation',
    'help.empty': 'Ask a question about the CentralStation documentation.',
    'help.placeholder': 'Type a question... (Enter = send, Shift+Enter = newline)',
    'help.error': 'Error fetching the answer. Please check whether the LLM is configured.',
    'help.suggestion.1': 'How do I configure a time-series widget?',
    'help.suggestion.2': 'What are saved searches and how do I use them?',
    'help.suggestion.3': 'How does AI enrichment work?',
    'help.suggestion.4': 'Which user roles are available?',
    'help.suggestion.5': 'How does CentralStation synchronize Jira tickets?',
  },
  de: {
    'app.nav.dashboard': 'Dashboard',
    'app.nav.bridge': 'Brücke',
    'app.nav.feed': 'News Feed',
    'app.nav.alerts': 'Alerts',
    'app.nav.myTickets': 'Meine Tickets',
    'app.nav.kanban': 'Kanban',
    'app.nav.aiInsights': 'KI-Insights',
    'app.nav.settings': 'Einstellungen',
    'app.nav.help': 'Hilfe',
    'app.nav.logout': 'Abmelden',
    'app.nav.toggle': 'Navigation ein- oder ausklappen',

    'login.email': 'E-Mail',
    'login.password': 'Passwort',
    'login.submit': 'Anmelden',
    'login.invalid': 'Ungültige Anmeldedaten',

    'settings.tabs.connectors': 'Connectors',
    'settings.tabs.myConnectors': 'Meine Konnektoren',
    'settings.tabs.mySettings': 'Meine Einstellungen',
    'settings.tabs.users': 'Benutzer',
    'settings.tabs.ai': 'KI-Konfiguration',
    'settings.tabs.audit': 'Audit-Log',
    'settings.tabs.feed': 'Feed',

    'settings.my.title': 'Meine Einstellungen',
    'settings.my.save': 'Speichern',
    'settings.my.appearance.title': 'Darstellung',
    'settings.my.appearance.subtitle': 'Design und Sprache der gesamten Anwendung. Wird pro Benutzer gespeichert.',
    'settings.my.language': 'Sprache',
    'settings.my.theme.title': 'Design',
    'settings.my.filter.title': 'KI-Agent - CheckMK Filter',
    'settings.my.filter.subtitle': 'Bestimmt welche CheckMK-Alerts der KI-Agent und der News Feed anzeigen. Nichts ausgewählt = alle Werte werden berücksichtigt.',
    'settings.my.minAge': 'Mindestalter CheckMK-Meldungen (Minuten)',
    'settings.my.minAgeHint': 'Meldungen die jünger als dieser Wert sind werden ausgeblendet (Standard: 5)',
    'settings.my.location': 'Standort (Ordner)',
    'settings.my.locationHint': 'Basiert auf dem CheckMK-Ordner-Pfad des Hosts',
    'settings.my.ve': 'VE / Unternehmen',
    'settings.my.criticality': 'Kritikalität',
    'settings.my.os': 'Betriebssystem',
    'settings.my.hostgroup': 'Hostgruppe',
    'settings.my.hostgroupHint': 'CheckMK Hostgruppen-Filter für KI-Agent und Alerts',
    'settings.my.activeFilters': 'Aktive Filter:',
    'settings.my.noFilters': 'Kein Filter aktiv - der KI-Agent analysiert alle CheckMK-Standorte.',
    'settings.my.password.title': 'Passwort ändern',
    'settings.my.password.current': 'Aktuelles Passwort',
    'settings.my.password.new': 'Neues Passwort',
    'settings.my.password.confirm': 'Neues Passwort bestätigen',
    'settings.my.password.mismatch': 'Passwörter stimmen nicht überein',
    'settings.my.password.submit': 'Passwort ändern',
    'settings.my.password.minHint': 'Mindestens 8 Zeichen',
    'settings.my.password.changed': 'Passwort erfolgreich geändert',
    'settings.my.password.currentWrong': 'Aktuelles Passwort falsch',
    'settings.my.password.error': 'Fehler beim Ändern des Passworts',
    'settings.my.saved': 'Einstellungen gespeichert',
    'settings.my.saveError': 'Fehler beim Speichern',

    'theme.classic.label': 'Klassisch',
    'theme.classic.desc': 'Hell, aufgeräumt, blauer Schleier',
    'theme.holo.label': 'Holo-HUD',
    'theme.holo.desc': 'Dunkelblau, Cyan-Glow',
    'theme.lcars.label': 'LCARS',
    'theme.lcars.desc': 'Schwarz/Orange, Star Trek',

    'language.en': 'Englisch',
    'language.de': 'Deutsch',

    'help.docs': 'Dokumentation',
    'help.assistant': 'Hilfe-Assistent',
    'help.hint': 'Stell Fragen zur Dokumentation',
    'help.empty': 'Stelle eine Frage zur CentralStation-Dokumentation.',
    'help.placeholder': 'Frage eingeben... (Enter = Senden, Shift+Enter = Zeilenumbruch)',
    'help.error': 'Fehler beim Abrufen der Antwort. Bitte prüfe ob der LLM konfiguriert ist.',
    'help.suggestion.1': 'Wie konfiguriere ich ein Zeitreihen-Widget?',
    'help.suggestion.2': 'Was sind gespeicherte Suchen und wie nutze ich sie?',
    'help.suggestion.3': 'Wie funktioniert die KI-Anreicherung?',
    'help.suggestion.4': 'Welche Benutzerrollen gibt es?',
    'help.suggestion.5': 'Wie synchronisiert CentralStation Jira-Tickets?',
  },
};

@Injectable({ providedIn: 'root' })
export class I18nService {
  private readonly http = inject(HttpClient);
  readonly language = signal<AppLanguage>('en');

  initFromStorage(): void {
    const stored = localStorage.getItem(STORAGE_KEY) as AppLanguage | null;
    this.apply(stored && ALL_LANGUAGES.includes(stored) ? stored : 'en', false);
  }

  loadFromPreference(): void {
    this.http.get<{ ui_language?: string }>(`${environment.apiUrl}/preferences`).subscribe({
      next: prefs => {
        const lang = (prefs.ui_language as AppLanguage) || 'en';
        if (ALL_LANGUAGES.includes(lang)) this.apply(lang, false);
      },
      error: () => {},
    });
  }

  setLanguage(lang: AppLanguage): void {
    this.apply(lang, true);
  }

  t(key: string): string {
    return TRANSLATIONS[this.language()][key] ?? TRANSLATIONS.en[key] ?? key;
  }

  private apply(lang: AppLanguage, persist: boolean): void {
    this.language.set(lang);
    document.documentElement.lang = lang;
    if (persist) {
      localStorage.setItem(STORAGE_KEY, lang);
      this.http.patch(`${environment.apiUrl}/preferences`, { ui_language: lang }).subscribe({ error: () => {} });
    }
  }
}
