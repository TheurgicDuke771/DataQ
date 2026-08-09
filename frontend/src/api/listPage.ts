/**
 * The `X-Total-Count` paging contract, shared by every list endpoint that
 * reports its unpaged population size (#925 `/assets`; #1108 `/runs`,
 * `/pipeline_runs`, `/incidents`).
 *
 * Its own module rather than part of `client.ts`: this is pure header→number
 * logic with no axios instance in it, and `client.ts` is the module every API
 * test replaces wholesale with `vi.mock`. Living here, the real helper keeps
 * running under those mocks — so a test that stubs the transport still exercises
 * the truncation arithmetic instead of a stub of it.
 */

/** One page of a list endpoint carrying `X-Total-Count`. `total` can exceed
 *  `items.length` whenever the fetch is capped below the true population — the
 *  truncation a caller must render honestly rather than looking complete. */
export interface ListPage<T> {
  items: T[];
  total: number;
}

/**
 * Build a {@link ListPage} from an axios list response body + headers.
 *
 * One implementation for every paged list endpoint, rather than a copy per API
 * module — the fallback below is a judgement call that must not be re-decided
 * (or mis-transcribed) per call site.
 *
 * axios lowercases response header keys. A missing or non-numeric header falls
 * back to the page length — never `undefined`/`NaN` — so a deploy-skew backend
 * that predates the header degrades to "no known truncation" rather than
 * breaking the page.
 */
export function toListPage<T>(
  data: T[],
  headers: { [key: string]: unknown } | undefined,
): ListPage<T> {
  const rawTotal = headers?.['x-total-count'];
  const total = rawTotal !== undefined ? Number(rawTotal) : data.length;
  return { items: data, total: Number.isFinite(total) ? total : data.length };
}
