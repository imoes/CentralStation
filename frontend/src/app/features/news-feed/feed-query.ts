/**
 * Feed-Suche wie in Graylog 5.2 — Feldwert per Rechtsklick in die Suche übernehmen oder ausschließen.
 *
 * Vorbild (Graylog2/graylog2-server, Zweig 5.2, graylog2-web-interface/src/views/logic/):
 *   queries/QueryHelper.ts            escape(), addToQuery()
 *   valueactions/AddToQueryHandler.ts  „Add to query“        → `feld:wert`,    fehlend → `NOT _exists_:feld`
 *   valueactions/ExcludeFromQueryHandler.ts „Exclude from results“ → `NOT feld:wert`, fehlend → `_exists_:feld`
 * Die Sprache ist dieselbe wie in Graylog: Lucene (`query_string` in OpenSearch, feed_index.suche_seite).
 * Rein — ohne Angular, damit es ohne Browser prüfbar ist.
 */

/** Ein Feldwert eines Feed-Eintrags; `wert === null` heißt: das Feld fehlt (Graylogs MISSING_BUCKET). */
export interface Feldwert {
  feld: string;
  wert: string | number | boolean | null;
}

export const isPhrase = (s: unknown): boolean => String(s).indexOf(' ') !== -1;

/** Graylogs QueryHelper.escape: Zeilenumbrüche zu Leerzeichen; Phrase → in Anführungszeichen,
 *  sonst jedes Lucene-Sonderzeichen escapen. */
export function escapeWert(v: unknown): string {
  let s = String(v);
  s = s.replace(/\r\n/g, ' ').replace(/\n/g, ' ').replace(/<br>/g, ' ');
  // leerer Wert ≠ fehlendes Feld: `feld:""` trifft genau die leeren (Graylog escapte '' zu `feld:`)
  if (s === '') return '""';
  if (isPhrase(s)) {
    return `"${s.replace(/(["\\])/g, '\\$&')}"`;
  }
  return s.replace(/(&&|\|\||[:\\/+\-!(){}[\]^"~*?$])/g, '\\$&');
}

/** Graylogs QueryHelper.addToQuery: leer oder `*` → nur der neue Term, sonst mit AND anhängen. */
export function zurSuche(alt: string, term: string, operator = 'AND'): string {
  const a = (alt || '').trim();
  const t = (term || '').trim();
  if (a === '*' || a === '') return t;
  if (t === '*' || t === '') return a;
  return `${a} ${operator} ${t}`;
}

/** `.keyword` hält im dynamischen Mapping höchstens 256 Zeichen (ignore_above) */
const KEYWORD_MAX = 256;

/** Feld, gegen das gesucht wird: Texte unter `metadata.*` exakt über ihr `.keyword` (wie Graylogs
 *  keyword-Felder) — sonst träfe `cs-backend-1` auch `cs-backend-2` (Analyse in Wörter). */
export function suchFeld(f: Feldwert): string {
  if (f.feld.startsWith('metadata.') && !f.feld.endsWith('.keyword') && typeof f.wert === 'string'
      && f.wert.length <= KEYWORD_MAX) {
    return `${f.feld}.keyword`;
  }
  return f.feld;
}

/** Der Suchterm für „Zur Suche hinzufügen“ bzw. „Ausschließen“ — genau wie Graylog. */
export function praedikat(f: Feldwert, ausschliessen: boolean): string {
  if (f.wert === null || f.wert === undefined) {
    return ausschliessen ? `_exists_:${suchFeld(f)}` : `NOT _exists_:${suchFeld(f)}`;
  }
  return `${ausschliessen ? 'NOT ' : ''}${suchFeld(f)}:${escapeWert(f.wert)}`;
}

/** Feldnamen, die Lucene ohne Escaping liest */
const FELDNAME = /^[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)*$/;

/** Alle suchbaren Feldwerte eines Eintrags (Graylogs Nachrichtenfelder): feste Felder, `tags` je
 *  Wert, `metadata.*` eine Ebene tief, Listen je Element. Lange Texte bleiben drin (Phrase). */
export function felderVon(item: {
  source?: string; severity?: string; type?: string; status?: string;
  location_name?: string | null; location_city?: string | null; tags?: string[] | null;
  metadata?: Record<string, any> | null;
}): Feldwert[] {
  const out: Feldwert[] = [];
  const add = (feld: string, wert: unknown) => {
    if (wert === null || wert === undefined || wert === '') return;
    if (typeof wert === 'string' || typeof wert === 'number' || typeof wert === 'boolean') {
      out.push({ feld, wert });
    }
  };
  for (const k of ['source', 'severity', 'type', 'status', 'location_name', 'location_city'] as const) {
    add(k, (item as any)[k]);
  }
  for (const t of item.tags ?? []) add('tags', t);
  const m = item.metadata ?? {};
  for (const k of Object.keys(m).sort()) {
    const feld = `metadata.${k}`;
    if (!FELDNAME.test(feld)) continue;
    const v = m[k];
    if (Array.isArray(v)) v.forEach(x => add(feld, x));
    else if (v && typeof v === 'object') {
      for (const k2 of Object.keys(v).sort()) {
        if (FELDNAME.test(`${feld}.${k2}`)) add(`${feld}.${k2}`, v[k2]);
      }
    } else add(feld, v);
  }
  return out;
}

/** Der Host als Feldwert (nicht das zusammengesetzte Etikett der Karte): Wazuh trägt ihn in
 *  `metadata.agent`, die übrigen Quellen in `metadata.host`. */
export function hostFeld(item: { source?: string; metadata?: Record<string, any> | null }): Feldwert | null {
  const m = item.metadata ?? {};
  if (item.source === 'wazuh' && m['agent']) return { feld: 'metadata.agent', wert: String(m['agent']) };
  if (m['host']) return { feld: 'metadata.host', wert: String(m['host']) };
  return null;
}

/** Kleinster Zeitraum aus `bereiche`, der ein `alterSek` altes Ereignis noch enthält (sonst der größte) —
 *  eine Suche aus einer Karte heraus findet die Karte selbst wieder. */
export function bereichFuer(alterSek: number, bereiche: number[]): number {
  const s = [...bereiche].sort((a, b) => a - b);
  return s.find(b => b >= alterSek) ?? s[s.length - 1];
}
