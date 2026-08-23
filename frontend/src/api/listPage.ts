/**
 * The `X-Total-Count` paging contract, shared by every list endpoint that reports its unpaged
 * population size (#925 `/assets`; #1108 `/runs`, `/pipeline_runs`, `/incidents`).
 */

/** One page of a list endpoint carrying `X-Total-Count`. */
export interface ListPage<T> {
  items: T[];
  total: number;
}

/** Build a {@link ListPage} from an axios list response body + headers. */
export function toListPage<T>(
  data: T[],
  headers: { [key: string]: unknown } | undefined,
): ListPage<T> {
  const rawTotal = headers?.['x-total-count'];
  const total = rawTotal !== undefined ? Number(rawTotal) : data.length;
  return { items: data, total: Number.isFinite(total) ? total : data.length };
}
