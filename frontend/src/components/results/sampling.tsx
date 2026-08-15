import { Alert, Tag, Tooltip } from 'antd';

import type { Result, ResultSampling } from '../../api/runs';
import { sampledCoverage, samplingSummary } from './samplingFormat';

/**
 * Sampled-ness surfacing for scale-aware execution (#595/#1325). The honesty
 * rule these two components enforce — a caveat only when the read genuinely was
 * a sample — lives with the pure helpers in `samplingFormat.ts`.
 */

/**
 * The per-check badge. Renders nothing unless the check genuinely ran on a
 * sample, so an unsampled run's results table is untouched.
 */
export function SampledTag({ sampling }: { sampling: ResultSampling | null | undefined }) {
  if (!sampling?.sampled) return null;
  return (
    <Tooltip title={samplingSummary(sampling)}>
      <Tag color="gold" data-testid="sampled-tag">
        Sampled
      </Tag>
    </Tooltip>
  );
}

/**
 * The run-level caveat, shown when **any** result on the run was sampled.
 *
 * Deliberately counts rather than generalises: within one run a volume monitor
 * pushes its `COUNT(*)` down and is exact while the expectations beside it saw a
 * sample, so "this run was sampled" would be wrong about half the table. Naming
 * the number sends the reader to the badged rows.
 *
 * The denominator is **evaluated** results only — the same one the "Checks
 * passed" stat beside it uses. Counting `skip`/`error` rows here would put two
 * different denominators for one run in one header, and would let a single
 * skipped check permanently downgrade "Every check ran on a sample" to "3 of 4",
 * which is the caveat getting quieter because something unrelated didn't run.
 */
export function SampledRunNotice({ results }: { results: Pick<Result, 'sampling' | 'status'>[] }) {
  const { sampled, evaluated } = sampledCoverage(results);
  if (sampled === 0) return null;
  return (
    <Alert
      type="info"
      showIcon
      data-testid="sampled-run-notice"
      title={
        sampled === evaluated
          ? 'Every check ran on a sample of the data'
          : `${sampled} of ${evaluated} checks ran on a sample of the data`
      }
      description={
        'Their verdicts describe the rows that were read, not the whole dataset — a check ' +
        'can pass on a sample and still have failing rows outside it. The sampled checks ' +
        'are tagged below; sampling is configured on the suite’s run target.'
      }
    />
  );
}
