import type { RunDetail as RunDetailType } from '../../api/runs';
import type { Check } from '../../api/suites';
import { engineShortLabel } from '../checks/checkBadges';
import { isSnoozed } from '../checks/snooze';
import { isSampled, sampledCoverage } from './samplingFormat';
import { formatDuration, formatScalar, formatTimestamp } from './resultsFormat';

/**
 * PDF report export (#345) — a print-only, chrome-free rendering of a run, parallel to the
 * interactive `RunDetail` page.
 */
export function RunReport({
  run,
  suiteName,
  checks,
}: {
  run: RunDetailType;
  suiteName: string | null;
  checks: Check[];
}) {
  const checksById = new Map(checks.map((c) => [c.id, c]));
  const checkName = (id: string) => checksById.get(id)?.name ?? id;
  const expectationOrKind = (id: string) => {
    const check = checksById.get(id);
    return check?.expectation_type || check?.kind || '—';
  };
  // The evaluator (#1551) — a DMF failure skews warehouse/permission issues, a GX one
  // skews batch-resolution issues, a materially different debugging context.
  const engineLabel = (id: string) => {
    const check = checksById.get(id);
    return check ? engineShortLabel(check.engine) : '—';
  };
  // Print-friendly parity with the interactive table's <SnoozedTag> (#653): a muted check must say
  // so here too.
  const snoozedSuffix = (id: string) => {
    const check = checksById.get(id);
    return check && isSnoozed(check) ? ' (snoozed)' : '';
  };
  // The same parity rule, applied to sampled-ness (#595/#1325).
  const { sampled, evaluated } = sampledCoverage(run.results);

  return (
    <div className="print-only rd-report" data-testid="run-report">
      <h1>{suiteName ?? `Run ${run.suite_id.slice(0, 8)}`}</h1>
      <p className="rd-report-subtitle">
        Run {run.id} · generated {new Date().toLocaleString()}
      </p>

      <table className="rd-report-meta" aria-label="Run summary">
        <tbody>
          <tr>
            <th scope="row">Status</th>
            <td>{run.status}</td>
          </tr>
          <tr>
            <th scope="row">Triggered by</th>
            <td>{run.triggered_by ?? '—'}</td>
          </tr>
          <tr>
            <th scope="row">Started</th>
            <td>{formatTimestamp(run.started_at)}</td>
          </tr>
          <tr>
            <th scope="row">Duration</th>
            <td>{formatDuration(run.started_at, run.finished_at)}</td>
          </tr>
          {sampled > 0 && (
            <tr>
              <th scope="row">Coverage</th>
              <td data-testid="report-sampled-notice">
                {sampled === evaluated
                  ? 'Every check ran on a SAMPLE of the data'
                  : `${sampled} of ${evaluated} checks ran on a SAMPLE of the data`}{' '}
                — those verdicts describe the rows that were read, not the whole dataset.
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {run.status === 'failed' && run.failure_reason && (
        <p className="rd-report-failure">
          <strong>Failure reason:</strong> {run.failure_reason}
        </p>
      )}

      {run.results.length === 0 ? (
        <p>No check results — the run did not complete.</p>
      ) : (
        <table className="rd-report-table" aria-label="Per-check results">
          <thead>
            <tr>
              <th>Check</th>
              <th>Expectation / kind</th>
              <th>Engine</th>
              <th>Status</th>
              <th>Metric</th>
              <th>Observed</th>
            </tr>
          </thead>
          <tbody>
            {run.results.map((r) => (
              <tr key={r.id}>
                <td>
                  {checkName(r.check_id)}
                  {snoozedSuffix(r.check_id)}
                  {isSampled(r) ? ' (sampled)' : ''}
                </td>
                <td>{expectationOrKind(r.check_id)}</td>
                <td>{engineLabel(r.check_id)}</td>
                <td>{r.status}</td>
                <td>{r.metric_value ?? '—'}</td>
                <td>{formatScalar(r.observed_value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* No sample failing rows in this report — see the docstring above. Kept
          state-neutral (#1122 review): redaction is per-column with four
          possible states since #417/#1115, and a printed artifact is read out
          of context, so this must not claim a specific redaction outcome. */}
      <p className="rd-report-footnote">
        Sample failing rows are not included in this report; review them in-app on each check's
        expanded row (redaction policy applies).
      </p>
    </div>
  );
}
