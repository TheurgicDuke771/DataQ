/**
 * Trigger a browser download of `content` as a file. Builds an object-URL blob
 * and clicks a transient anchor — the standard no-backend "save this" path. The
 * URL is revoked immediately after the click so the blob doesn't leak.
 */
export function downloadText(filename: string, content: string, mime: string): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** Trigger a download of `data` serialised as pretty-printed JSON. */
export function downloadJson(filename: string, data: unknown): void {
  downloadText(filename, JSON.stringify(data, null, 2), 'application/json');
}

/**
 * Quote a CSV cell per RFC 4180: a field containing a comma, double-quote, or
 * newline is wrapped in double-quotes with inner quotes doubled. `null`/
 * `undefined` become an empty field. Everything is coerced via `String()` — an
 * object should be JSON-stringified by the caller before it gets here.
 */
function csvCell(value: unknown): string {
  if (value === null || value === undefined) return '';
  const s = String(value);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Build an RFC-4180 CSV string from a header row + data rows (CRLF line ends). */
export function toCsv(headers: string[], rows: unknown[][]): string {
  return [headers, ...rows].map((row) => row.map(csvCell).join(',')).join('\r\n');
}

/** Trigger a download of a CSV built from `headers` + `rows`. */
export function downloadCsv(filename: string, headers: string[], rows: unknown[][]): void {
  downloadText(filename, toCsv(headers, rows), 'text/csv;charset=utf-8');
}

/**
 * Turn an arbitrary label into a safe, lowercase filename stem: non-word
 * characters collapse to underscores, runs trim to one, leading/trailing
 * underscores drop. Falls back to `fallback` when nothing usable remains (e.g.
 * a name of only punctuation).
 */
export function toFilenameStem(label: string, fallback = 'suite'): string {
  const stem = label
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
  return stem || fallback;
}
