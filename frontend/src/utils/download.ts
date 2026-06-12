/**
 * Trigger a browser download of `data` serialised as pretty-printed JSON.
 *
 * Builds an object-URL blob and clicks a transient anchor — the standard
 * no-backend "save this JSON" path (used by the suite export). The URL is
 * revoked immediately after the click so the blob doesn't leak.
 */
export function downloadJson(filename: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
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
