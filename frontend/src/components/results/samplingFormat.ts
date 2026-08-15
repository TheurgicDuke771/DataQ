import type { Result, ResultSampling } from '../../api/runs';

/**
 * Pure helpers behind the sampled-ness surfacing (#595/#1325) — framework-free
 * so the honesty rule below can be unit-tested as a value, without rendering
 * antd. The components that use them live in `sampling.tsx`.
 *
 * **One rule governs everything here: a caveat is claimed only when the read
 * genuinely was a sample** — `sampling.sampled === true`, never a derived
 * `rows < total_rows`. `total_rows` is legitimately null for a head sample that
 * stopped reading early rather than pay for a count, and a "sample" that covered
 * the whole dataset is a complete read; badging that would put a caveat on every
 * small target and teach the reader to skip past the one that matters. This is
 * the same honesty rule the redaction label follows (#424/#1115): say exactly
 * what happened, or say nothing.
 */

/** Whether this result's verdict describes less than the whole dataset. */
export function isSampled(result: Pick<Result, 'sampling'>): boolean {
  return result.sampling?.sampled === true;
}

/** `100,000 of 5,000,000 rows`, or `100,000 rows` when the population is unknown.
 *
 * `total_rows` is legitimately null — a head sample stops reading at the cap
 * rather than pay for a count — so the two-branch shape is real. `rows` is not:
 * `sampling_record` always writes it, and the only way to reach this function is
 * `sampled === true`, which the same writer sets. An unreachable third branch
 * would be two user-facing strings no test could ever produce, so it is gone; if
 * the record shape ever loosens, `rows` becomes optional in `ResultSampling` and
 * TypeScript says so here.
 */
function coverage(sampling: ResultSampling): string {
  const rows = (sampling.rows ?? 0).toLocaleString();
  const total = typeof sampling.total_rows === 'number' ? sampling.total_rows : null;
  return total === null ? `${rows} rows` : `${rows} of ${total.toLocaleString()} rows`;
}

/**
 * The one-line explanation behind the badge: which strategy drew the rows, how
 * many were seen against how many exist, the cap that was asked for **when it
 * differs from what arrived** (a head sample that hit EOF early got fewer rows
 * than requested, and that gap is what tells a reader the cap was not the
 * binding constraint), and the seed when the draw was made reproducible.
 *
 * A value, not JSX, so its wording is directly assertable — antd renders a
 * Tooltip's title into a portal only on hover, and reaching for it through the
 * DOM instead invites the assertion that silently reads `undefined` and passes.
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

/** The severity-tier statuses — the results that actually **evaluated** something.
 *  `skip`/`error` did not, so they are not part of any "N of M checks" claim; the
 *  run header's "Checks passed" stat has always used this denominator (ADR 0005),
 *  and two different denominators in one header is a contradiction the reader has
 *  to resolve. */
const EVALUATED: ReadonlySet<string> = new Set(['pass', 'warn', 'fail', 'critical']);

/**
 * The run-level headline: how many of a run's **evaluated** results carry a
 * sample caveat, out of how many evaluated at all.
 *
 * Counting over every row instead would also let a single skipped check
 * permanently suppress the stronger "Every check ran on a sample" wording — the
 * caveat getting quieter because something unrelated didn't run.
 */
export function sampledCoverage(results: Pick<Result, 'sampling' | 'status'>[]): {
  sampled: number;
  evaluated: number;
} {
  const evaluated = results.filter((r) => EVALUATED.has(r.status));
  return { sampled: evaluated.filter(isSampled).length, evaluated: evaluated.length };
}
