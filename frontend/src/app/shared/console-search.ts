/**
 * Livesuche über die Konsolen-Sitzungen — reine Funktionen, ohne Angular.
 *
 * Durchsucht wird genau das, was die Konsole auch anzeigt: Sitzungsname und die
 * geladenen Nachrichten. Ein Treffer ist eine Nachricht, die ALLE Suchwörter
 * enthält (oder ein Name, der sie alle enthält) — Wörter, die nur verstreut über
 * eine lange Sitzung vorkommen, sind kein Zusammenhang und kein Treffer.
 */

/** Unter dieser Länge wird nicht gesucht: ein einzelnes Zeichen trifft fast alles. */
export const MIN_QUERY_LENGTH = 2;

export interface SearchableSession<M extends { text: string } = { text: string }> {
  session_id: string;
  label: string;
  msg_count: number;
  messages: ReadonlyArray<M>;
}

/** Ein Stück Text des Ausschnitts; `hit` = Suchwort, wird hervorgehoben. */
export interface SnippetPart {
  text: string;
  hit: boolean;
}

export interface SessionHit {
  session_id: string;
  labelHit: boolean;
  /** Indizes der Nachrichten, die alle Suchwörter enthalten. */
  messageHits: number[];
  /** Ausschnitt um das erste Vorkommen in der ersten Treffer-Nachricht. */
  snippet: SnippetPart[];
}

export interface SearchResult {
  terms: string[];
  hits: SessionHit[];
  /** Sitzungen mit Nachrichten, deren Verlauf nicht geladen ist — nur ihr Name
   *  wurde durchsucht. Benannt statt still übergangen. */
  unloaded: number;
}

/** Suchwörter: klein geschrieben, an Leerraum getrennt, ohne Dubletten. */
export function searchTerms(query: string): string[] {
  if (query.trim().length < MIN_QUERY_LENGTH) return [];
  return [...new Set(query.toLowerCase().split(/\s+/).filter(Boolean))];
}

function containsAll(haystack: string, terms: string[]): boolean {
  return terms.every(t => haystack.includes(t));
}

/** Ausschnitt von etwa `radius` Zeichen um das erste Vorkommen, Suchwörter markiert. */
export function snippet(text: string, terms: string[], radius = 40): SnippetPart[] {
  // Markdown-Auszeichnung weg: im Ausschnitt stünde sonst `**Zipline**` statt Zipline.
  const plain = text.replace(/\*\*|`+/g, '').replace(/^\s{0,3}(#{1,6}|>)\s+/gm, '');
  const flat = plain.replace(/\s+/g, ' ').trim();
  const lower = flat.toLowerCase();
  const first = Math.min(...terms.map(t => lower.indexOf(t)).filter(i => i >= 0));
  if (!Number.isFinite(first)) return [{ text: flat.slice(0, radius * 2), hit: false }];

  const start = Math.max(0, first - radius);
  const end = Math.min(flat.length, first + radius * 2);
  const part = flat.slice(start, end);
  const partLower = lower.slice(start, end);

  // Markierte Bereiche sammeln und überlappende zusammenfassen.
  const ranges: [number, number][] = [];
  for (const t of terms) {
    for (let i = partLower.indexOf(t); i >= 0; i = partLower.indexOf(t, i + t.length)) {
      ranges.push([i, i + t.length]);
    }
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [];
  for (const r of ranges) {
    const last = merged.at(-1);
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
    else merged.push([r[0], r[1]]);
  }

  const parts: SnippetPart[] = [];
  let pos = 0;
  for (const [a, b] of merged) {
    if (a > pos) parts.push({ text: part.slice(pos, a), hit: false });
    parts.push({ text: part.slice(a, b), hit: true });
    pos = b;
  }
  if (pos < part.length) parts.push({ text: part.slice(pos), hit: false });
  if (start > 0) parts.unshift({ text: '…', hit: false });
  if (end < flat.length) parts.push({ text: '…', hit: false });
  return parts;
}

/**
 * Sucht in allen Sitzungen. `lower` liefert den klein geschriebenen Text einer
 * Nachricht — der Aufrufer kann ihn je Nachrichtenobjekt zwischenspeichern, damit
 * nicht jeder Tastendruck (und jedes Streaming-Stück) alle Verläufe neu umwandelt.
 */
export function searchSessions<M extends { text: string }>(
  sessions: ReadonlyArray<SearchableSession<M>>,
  terms: string[],
  lower: (m: M) => string = m => m.text.toLowerCase(),
): SearchResult {
  if (!terms.length) return { terms, hits: [], unloaded: 0 };
  const hits: SessionHit[] = [];
  let unloaded = 0;
  for (const s of sessions) {
    if (s.msg_count > 0 && s.messages.length === 0) unloaded++;
    const labelHit = containsAll(s.label.toLowerCase(), terms);
    const messageHits = s.messages.flatMap((m, i) => (containsAll(lower(m), terms) ? [i] : []));
    if (!labelHit && !messageHits.length) continue;
    hits.push({
      session_id: s.session_id,
      labelHit,
      messageHits,
      snippet: messageHits.length ? snippet(s.messages[messageHits[0]].text, terms) : [],
    });
  }
  return { terms, hits, unloaded };
}
