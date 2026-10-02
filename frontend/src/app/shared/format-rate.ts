/**
 * Häufigkeit als Rate: "10 msg/s", "4,2 msg/min", "3 msg/h".
 *
 * Die Einheit wechselt, damit kleine Raten lesbar bleiben ("0,4 msg/min" statt
 * "0,007 msg/s"). Dieselben Stufen nutzt der Mail-Betreff im Backend
 * (error_clusters/notify.py: fmt_rate) — eine Regel, überall gleich.
 */
export function formatRate(count: number, seconds: number): string {
  if (!count || !seconds || seconds <= 0) return '0 msg/s';
  const perSec = count / seconds;
  const fmt = (v: number) =>
    v.toLocaleString('de-DE', { maximumFractionDigits: v >= 10 ? 0 : 1 });
  if (perSec >= 1) return `${fmt(perSec)} msg/s`;
  if (perSec * 60 >= 1) return `${fmt(perSec * 60)} msg/min`;
  return `${fmt(perSec * 3600)} msg/h`;
}
