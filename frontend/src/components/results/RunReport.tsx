import type { RunDetail as RunDetailType } from '../../api/runs';
import type { Check } from '../../api/suites';
import { isSnoozed } from '../checks/snooze';
import { isSampled, sampledCoverage } from './samplingFormat';
import { formatDuration, formatScalar, formatTimestamp } from './resultsFormat';

/**
 * PDF report export (#345) — a print-only, chrome-free rendering of a run,
 * parallel to the interactive `RunDetail` page. It is never shown on screen
 * (`.print-only` in `styles.css` keeps it `display: none` outside a print
 * context); the "Print / Save as PDF" download-menu item just calls
 * `window.print()`, and the browser's own print-to-PDF produces the artifact —
 * zero new dependency, per the issue's own cost note.
 *
 * Redaction parity (#226): the run/results payload this renders is the SAME
 * one the page already fetched via the authz-scoped `GET /runs/{id}` — no
 * second, unredacted fetch. Sample failing rows are omitted entirely, matching
 * the CSV/JSON export precedent (see `DownloadMenu.exportJson` in
 * `RunDetail.tsx`): the counts/shape are an in-app triage affordance on the
 * expanded row, not an export artifact, so there is nothing here that could
 * regress the redaction the API already applied.
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
  // Print-friendly parity with the interactive table's <SnoozedTag> (#653): a
  // muted check must say so here too, or a printed artifact shows a bare fail
  // with no suppression indicator and the reader wastes time asking why no
  // alert fired. Same `isSnoozed` predicate, plain text since a Tag/Tooltip
  // doesn't survive print.
  const snoozedSuffix = (id: string) => {
    const check = checksById.get(id);
    return check && isSnoozed(check) ? ' (snoozed)' : '';
  };
  // The same parity rule, applied to sampled-ness (#595/#1325). This is the
  // artifact people circulate, so a fully-sampled all-pass run printing as an
  // unqualified clean bill is the exact overclaim the feature exists to prevent —
  // and it is worse here than on screen, because a PDF outlives the context that
  // would have explained it. Plain text, for the same reason as above.
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
