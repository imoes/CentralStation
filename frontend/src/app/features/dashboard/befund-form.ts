/**
 * Befund lesbar machen (Nutzer 07.11.: „mit diesem Output kann ich nichts anfangen“).
 *
 * Seit 07.11. liefert die KI kern (ein Satz), fakten (kurze Punkte) und folge_von (frühere Befunde)
 * getrennt — backend log_watch/befund.py prüft die Form. Ältere Befunde sind ein Absatz; sie werden hier
 * so gut es geht geteilt: Bezüge (ki:…) heraus, erster Satz als Kern, der Rest unter „Details“.
 * Rein — ohne Angular prüfbar.
 */
export interface BefundForm { kern: string; fakten: string[]; folge: string[]; rest: string }

const BEZUG = /\bki:[0-9a-f]{10}\b/g;
const BEZUG_PHRASE =
  /\(?\s*(?:Folge-?[Bb]efund(?:e)?\s+(?:zu|von)\s+)?ki:[0-9a-f]{10}(?:\s*(?:,|und)\s*ki:[0-9a-f]{10})*\s*\)?\s*:?\s*/g;

/** Dieselbe Regel wie befund.ohne_bezuege: Befund-IDs aus dem Text, ohne Groß-/Kleinschreibung zu ändern. */
export function ohneBezuege(text: string): { text: string; ids: string[] } {
  const ids = [...new Set((text || '').match(BEZUG) ?? [])];
  if (!ids.length) return { text: (text || '').trim(), ids };
  const t = text.replace(BEZUG_PHRASE, ' ').replace(/\s{2,}/g, ' ').replace(/\s+([,.;:])/g, '$1')
    .trim().replace(/^[ ,;:]+|[ ,;:]+$/g, '');
  return { text: t, ids };
}

/** Ende des ersten Satzes: Punkt/!/? gefolgt von Leerzeichen und Großbuchstabe oder „ — nicht nach
 *  Abkürzungen wie „z. B.“, „u. a.“, „ca.“, „Nr.“ und nicht in Zahlen (100.00). */
export function ersterSatz(text: string): [string, string] {
  const re = /([.!?])\s+(?=[A-ZÄÖÜ„"])/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    const vor = text.slice(Math.max(0, m.index - 4), m.index + 1);
    // ein einzelner Buchstabe mit Punkt („z.“ in „z. B.“) ist eine Abkürzung, kein Satzende
    if (/((^|[\s(])[a-zA-Z]\.|\bca\.|\bNr\.|\bvgl\.|\bbzw\.|\busw\.)$/.test(vor)) continue;
    return [text.slice(0, m.index + 1).trim(), text.slice(m.index + 1).trim()];
  }
  return [text.trim(), ''];
}

export function befundForm(b: { kern?: string; fakten?: string[]; folge_von?: string[]; befund?: string }): BefundForm {
  if (b.kern) {
    return { kern: b.kern, fakten: b.fakten ?? [], folge: b.folge_von ?? [], rest: '' };
  }
  const { text, ids } = ohneBezuege(b.befund ?? '');
  const [kern, rest] = ersterSatz(text);
  return { kern, fakten: [], folge: ids, rest };
}
