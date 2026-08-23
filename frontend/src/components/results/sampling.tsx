import { Alert, Tag, Tooltip } from 'antd';

import type { Result, ResultSampling } from '../../api/runs';
import { sampledCoverage, samplingSummary } from './samplingFormat';

/** Sampled-ness surfacing for scale-aware execution (#595/#1325). */

/** The per-check badge. */
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

/** The run-level caveat, shown when **any** result on the run was sampled. */
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
