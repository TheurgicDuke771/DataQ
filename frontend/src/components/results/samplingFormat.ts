import type { Result, ResultSampling } from '../../api/runs';

/**
 * Pure helpers behind the sampled-ness surfacing (#595/#1325) — framework-free so the honesty rule
 * below can be unit-tested as a value, without rendering antd.
 */

/** Whether this result's verdict describes less than the whole dataset. */
export function isSampled(result: Pick<Result, 'sampling'>): boolean {
  return result.sampling?.sampled === true;
}

/** `100,000 of 5,000,000 rows`, or `100,000 rows` when the population is unknown. */
function coverage(sampling: ResultSampling): string {
  const rows = (sampling.rows ?? 0).toLocaleString();
  const total = typeof sampling.total_rows === 'number' ? sampling.total_rows : null;
  return total === null ? `${rows} rows` : `${rows} of ${total.toLocaleString()} rows`;
}

/**
 * The one-line explanation behind the badge: which strategy drew the rows, how many were seen
 * against how many exist.
 */
export function samplingSummary(sampling: ResultSampling): string {
  const requested =
    typeof sampling.requested_rows === 'number' && sampling.requested_rows !== sampling.rows
      ? ` · asked for ${sampling.requested_rows.toLocaleString()}`
      : '';
  const seed = typeof sampling.seed === 'number' ? ` · seed ${sampling.seed}` : '';
  return (
    `Evaluated on a ${sampling.strategy} sample — ${coverage(sampling)}${requested}${seed}. ` +
    'The verdict describes the sample, not the whole dataset.'
  );
}

/** The severity-tier statuses — the results that actually **evaluated** something. */
const EVALUATED: ReadonlySet<string> = new Set(['pass', 'warn', 'fail', 'critical']);

/**
 * The run-level headline: how many of a run's **evaluated** results carry a sample caveat, out of
 * how many evaluated at all.
 */
export function sampledCoverage(results: Pick<Result, 'sampling' | 'status'>[]): {
  sampled: number;
  evaluated: number;
} {
  const evaluated = results.filter((r) => EVALUATED.has(r.status));
  return { sampled: evaluated.filter(isSampled).length, evaluated: evaluated.length };
}
