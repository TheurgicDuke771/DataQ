/**
 * Recorded API responses for the read-only demo (#2419). Dependency-free: the Playwright capture
 * lane (e2e-demo/) imports this under Node to write the same keys the app later looks up.
 */

export interface RecordedResponse {
  status: number;
  data: unknown;
  /** The `x-*` response headers the app reads (the `X-Total-Count` paging contract). */
  headers?: Record<string, string>;
}

export interface FixtureBundle {
  /** ISO instant the responses were recorded at. */
  capturedAt: string;
  /** The app routes that were visited to make the recording. */
  routes: string[];
  /** Keyed by `fixtureKey`. */
  entries: Record<string, RecordedResponse>;
}

export interface Lookup {
  response: RecordedResponse;
  /** True when the recorded response is for the same path with other query parameters. */
  approximate: boolean;
}

const API_PREFIX = '/api/v1';
const ISO_DATETIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const DAY_MS = 86_400_000;

const HOUR_MS = 3_600_000;

/**
 * A timestamp in a query string is "now minus a window": the instant never repeats between
 * recording and replay, but its distance from now does. Keying on that distance keeps a 24-hour
 * window and a 7-day one apart.
 */
function normalizeValue(v: string, now: number): string {
  if (!ISO_DATETIME.test(v)) return v;
  const at = Date.parse(v);
  return Number.isNaN(at) ? v : `<now${Math.round((at - now) / HOUR_MS)}h>`;
}

function split(url: string, now: number): { path: string; params: [string, string][] } {
  const parsed = new URL(url, 'http://demo.invalid');
  const path = parsed.pathname.startsWith(API_PREFIX)
    ? parsed.pathname.slice(API_PREFIX.length)
    : parsed.pathname;
  const params = [...parsed.searchParams.entries()]
    .map(([k, v]): [string, string] => [k, normalizeValue(v, now)])
    .sort(([ak, av], [bk, bv]) => (ak === bk ? av.localeCompare(bv) : ak.localeCompare(bk)));
  return { path: path.replace(/\/+$/, '') || '/', params };
}

/** `GET /suites?limit=50` — the method, the path under /api/v1, and the sorted query. */
export function fixtureKey(method: string, url: string, now: number = Date.now()): string {
  const { path, params } = split(url, now);
  const query = params.map(([k, v]) => `${k}=${v}`).join('&');
  return `${method.toUpperCase()} ${path}${query ? `?${query}` : ''}`;
}

/** The exact recording, else the same path's recording that shares the most query parameters. */
export function lookup(
  bundle: FixtureBundle,
  method: string,
  url: string,
  now: number = Date.now(),
): Lookup | null {
  const key = fixtureKey(method, url, now);
  const exact = bundle.entries[key];
  if (exact) return { response: exact, approximate: false };

  const { path, params } = split(url, now);
  const wanted = new Set(params.map(([k, v]) => `${k}=${v}`));
  const prefix = `${method.toUpperCase()} ${path}`;
  let best: { response: RecordedResponse; shared: number; extra: number } | null = null;
  for (const [candidate, response] of Object.entries(bundle.entries)) {
    if (candidate !== prefix && !candidate.startsWith(`${prefix}?`)) continue;
    const have =
      candidate.length > prefix.length ? candidate.slice(prefix.length + 1).split('&') : [];
    const shared = have.filter((p) => wanted.has(p)).length;
    const extra = have.length - shared;
    if (!best || shared > best.shared || (shared === best.shared && extra < best.extra)) {
      best = { response, shared, extra };
    }
  }
  return best ? { response: best.response, approximate: true } : null;
}

function shiftDate(isoDate: string, days: number): string {
  const moved = new Date(Date.parse(`${isoDate}T00:00:00Z`) + days * DAY_MS);
  return moved.toISOString().slice(0, 10);
}

/** How far from the recording a date may lie and still be an event of the demo workspace. */
const SHIFT_WINDOW = { before: 120 * DAY_MS, after: 400 * DAY_MS };

/**
 * Move the workspace's own dates (runs, schedules, incidents, trend buckets) forward by whole
 * days, so data recorded last week still reads as recent. Whole days keep date-only and
 * timestamp fields consistent with each other. A date far from the recording is somebody's
 * data or a configured threshold, not an event, and is left as recorded.
 */
export function shiftDates<T>(value: T, days: number, capturedAt: number): T {
  if (days === 0) return value;
  if (typeof value === 'string') {
    const isDate = ISO_DATE.test(value);
    if (!isDate && !ISO_DATETIME.test(value)) return value;
    const offset = Date.parse(isDate ? `${value}T00:00:00Z` : value) - capturedAt;
    if (Number.isNaN(offset) || offset < -SHIFT_WINDOW.before || offset > SHIFT_WINDOW.after) {
      return value;
    }
    return (shiftDate(value.slice(0, 10), days) + value.slice(10)) as T;
  }
  if (Array.isArray(value)) return value.map((v) => shiftDates(v, days, capturedAt)) as T;
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => [k, shiftDates(v, days, capturedAt)]),
    ) as T;
  }
  return value;
}

/** Whole days between the recording and `now`; never negative. */
export function daysSince(capturedAt: string, now: number = Date.now()): number {
  const captured = Date.parse(capturedAt);
  if (Number.isNaN(captured)) return 0;
  return Math.max(0, Math.floor((now - captured) / DAY_MS));
}
