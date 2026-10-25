import { triggeredByText, type RunDetail as RunDetailType } from '../../api/runs';
import { checkLabel, noResultsMessage } from './runResultLabels';
import type { Check } from '../../api/suites';
import { DIMENSION_LABEL, type DqDimension } from '../checks/expectationCatalog';
import { engineShortLabel } from '../checks/checkBadges';
import { isSnoozed } from '../checks/snooze';
import { checkAsOf, currentSuffix } from './checkAsOf';
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
  checksKnown,
}: {
  run: RunDetailType;
  suiteName: string | null;
  checks: Check[];
  /** False when the suite's check list could not be loaded. */
  checksKnown: boolean;
}) {
  const checksById = new Map(checks.map((c) => [c.id, c]));
  const checkName = (id: string) => checkLabel(checksById.get(id)?.name, id, checksKnown);
  // Expectation, engine and dimension as the check WAS at this result (#1613) — the live check
  // only when the payload carries no snapshot, and then suffixed "(current)" (footnote below).
  const asOf = (r: RunDetailType['results'][number]) => checkAsOf(r, checksById.get(r.check_id));
  const expectationOrKind = (r: RunDetailType['results'][number]) => {
    const check = asOf(r);
    return (check?.expectation_type || check?.kind || '—') + currentSuffix(check?.source);
  };
  // The evaluator (#1551, see checkBadges.tsx's EngineTag docstring for why it matters).
  const engineLabel = (r: RunDetailType['results'][number]) => {
    const check = asOf(r);
    return check ? engineShortLabel(check.engine) + currentSuffix(check.source) : '—';
  };
  // ADR 0038: unclassified is a coverage gap and prints as one, never as a blank.
  const dimensionLabel = (r: RunDetailType['results'][number]) => {
    const check = asOf(r);
    if (!check) return '—';
    const label = check.dimension
      ? (DIMENSION_LABEL[check.dimension as DqDimension] ?? check.dimension)
      : 'Unclassified';
    return label + currentSuffix(check.source);
  };
  const marked = new Set(
    run.results.map((r) => asOf(r)?.source).filter((s) => s && s !== 'version'),
  );
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
            <td>{triggeredByText(run)}</td>
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
        <p>{noResultsMessage(run.status)}</p>
      ) : (
        <table className="rd-report-table" aria-label="Per-check results">
          <thead>
            <tr>
              <th>Check</th>
              <th>Expectation / kind</th>
              <th>Engine</th>
              <th>Dimension</th>
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
                <td>{expectationOrKind(r)}</td>
                <td>{engineLabel(r)}</td>
                <td>{dimensionLabel(r)}</td>
                <td>{r.status}</td>
                <td>{r.metric_value ?? '—'}</td>
                <td>{formatScalar(r.observed_value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* No sample failing rows in this report — see the docstring above. Kept
          state-neutral (#1122 review): redaction has several possible states
          (#417/#1115/#1873), and a printed artifact is read out of context, so
          this must not claim a specific redaction outcome. */}
      {marked.has('live') && (
        <p className="rd-report-footnote" data-testid="report-current-footnote">
          (current) marks a check shown from its present state: no snapshot of it as it was at this
          result is available, so its expectation, engine or dimension may have changed since the
          run.
        </p>
      )}
      {marked.has('later_version') && (
        <p className="rd-report-footnote" data-testid="report-later-footnote">
          (later) marks a check shown from its earliest recorded version, which postdates this
          result: its state at the run was not recorded and may have differed.
        </p>
      )}
      <p className="rd-report-footnote">
        Sample failing rows are not included in this report; review them in-app on each check's
        expanded row (redaction policy applies).
      </p>
    </div>
  );
}
