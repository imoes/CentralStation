/**
 * Dateien im Workspace, die eine Agenten-Antwort erwähnt — damit die Konsole sie
 * verlinken kann (Berichte unter /home/yolo/workspaces/reports/…).
 *
 * Erkannt werden absolute Workspace-Pfade und relative Pfade mit mindestens einem
 * Verzeichnis ("reports/x.md"); ein bloßer Dateiname ohne Verzeichnis wäre zu oft
 * nur ein Wort im Text. Nur Typen, die das Backend auch anzeigt
 * (computer_proxy.WORKSPACE_VIEW_TYPES).
 */
export const WORKSPACE_ROOT = '/home/yolo/workspaces';

const EXT = '(?:md|markdown|txt|log|csv|tsv|json|ya?ml|xml|html|sh|py|sql|ini|conf|toml)';
// Ende der Endung: kein Wortzeichen und kein ".x" danach — ein Satzpunkt ist erlaubt.
const END = '(?![\\w-])(?!\\.\\w)';
const ABS = new RegExp(`${WORKSPACE_ROOT.replace(/\//g, '\\/')}\\/[^\\s\`'"()<>\\[\\]{}|*]+?\\.${EXT}${END}`, 'gi');
const REL = new RegExp(`(?<![\\w/.~-])(?:[\\w.-]+\\/)+[\\w.-]+\\.${EXT}${END}`, 'gi');

export function extractWorkspaceFiles(text: string): string[] {
  if (!text) return [];
  const found = new Set<string>();
  for (const m of text.matchAll(ABS)) found.add(m[0]);
  // Relative Pfade nur, wenn sie nicht Teil einer URL oder eines absoluten Pfads sind.
  const withoutAbs = text.replace(ABS, ' ').replace(/https?:\/\/\S+/gi, ' ');
  for (const m of withoutAbs.matchAll(REL)) {
    const rel = m[0].replace(/^\.\//, '');
    if (!rel.startsWith('..')) found.add(`${WORKSPACE_ROOT}/${rel}`);
  }
  return [...found];
}

/** Anzeigename: Pfad relativ zum Workspace. */
export function workspaceRelative(path: string): string {
  return path.startsWith(WORKSPACE_ROOT + '/') ? path.slice(WORKSPACE_ROOT.length + 1) : path;
}
