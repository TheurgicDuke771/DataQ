import { ArrowLeftOutlined, DownloadOutlined, SwapOutlined } from '@ant-design/icons';
import {
  Alert,
  Button,
  Card,
  Dropdown,
  Empty,
  Flex,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { type Incident, listIncidents } from '../api/incidents';
import {
  compareRunsPath,
  getRun,
  listRuns,
  triggeredByText,
  type Result,
  type ResultStatus,
  type Run,
  type RunStatus,
} from '../api/runs';
import { type Check, getSuite, listChecks } from '../api/suites';
import { AssetLink } from '../components/assets/AssetLink';
import { IncidentEvidenceDrawer } from '../components/assets/IncidentEvidenceDrawer';
import { DimensionTag, EngineTag } from '../components/checks/checkBadges';
import { AsOfCell, checkAsOf, exportSource } from '../components/results/checkAsOf';
import { CheckTrend } from '../components/checks/CheckTrend';
import { SeasonalResultSummary } from '../components/results/SeasonalResultSummary';
import { ComparisonResultDetail } from '../components/results/ComparisonResultDetail';
import {
  checkExportName,
  checkLabel,
  noResultsMessage,
} from '../components/results/runResultLabels';
import { SnoozedTag } from '../components/checks/snooze';
import {
  anomalyColdStartHint,
  formatDuration,
  formatTimestamp,
  runReportTitle,
} from '../components/results/resultsFormat';
import { Page } from '../components/layout/Page';
import { RunReport } from '../components/results/RunReport';
import { SampledRunNotice, SampledTag } from '../components/results/sampling';
import { isSampled } from '../components/results/samplingFormat';
import { ScalarValue } from '../components/results/ScalarValue';
import { boundedTextStyle } from '../components/shared/ellipsisColumn';
import { useAsyncData } from '../hooks/useAsyncData';
import { downloadCsv, downloadJson, toFilenameStem } from '../utils/download';
import { PageError } from '../components/feedback/PageError';
import { ResultStatusTag, RunStatusTag } from '../components/shared/StatusTag';

/** The four severity tiers that count as "evaluated" (ADR 0005) — skip/error don't. */
const SEVERITY_STATUSES = new Set<ResultStatus>(['pass', 'warn', 'fail', 'critical']);
/** Tiers a result must breach for an incident to exist at all (`db.models.FAILING_TIERS`) — warn
 *  included, not just fail/critical (`incident_service._sync_incidents_for_run` opens/attaches on
 *  any of the three). */
const FAILING_TIERS = new Set<ResultStatus>(['warn', 'fail', 'critical']);
/** An incident is "open" for linkage purposes while active (mirrors `INCIDENT_ACTIVE_STATUSES`). */
const ACTIVE_INCIDENT_STATUSES = new Set(['open', 'acknowledged']);

/**
 * Bound for the "Observed" cell — a structured `observed_value` payload (schema_drift baselines,
 * comparison buckets, anomaly stats) is unbounded in principle.
 */
const OBSERVED_COLUMN_WIDTH = 220;
/** How many of the suite's most recent OTHER runs the "Compare with…" menu offers (the page
 *  fetched includes the run itself, hence the +1 below). An older run is still comparable by
 *  URL — `/results/<older>/compare/<this>` — or through the API. */
const COMPARE_CANDIDATES = 20;

/**
 * Routed run-detail page (`/results/:runId`, ADR 0022) — replaces the run-detail drawer so a run
 * is deep-linkable and refreshable.
 */
export function RunDetail() {
  const navigate = useNavigate();
  const { runId } = useParams<{ runId: string }>();

  const { state } = useAsyncData(async () => {
    if (!runId) throw new Error('no run');
    const run = await getRun(runId);
    // The suite may be readable while details race; tolerate a missing name/checks
    // rather than failing the whole page.
    const [suite, checks, incidents, siblings] = await Promise.all([
      getSuite(run.suite_id).catch(() => null),
      // `known: false` when the list could not be read: a check missing from it is then not
      // known to be deleted.
      listChecks(run.suite_id).then(
        (list) => ({ list, known: true }),
        () => ({ list: [] as Check[], known: false }),
      ),
      // Incident linkage (#1634): reuses the existing `/incidents` list read — no new endpoint.
      // Null asset (targetless suite, or an older/operationally-failed run) has nothing to link;
      // likewise a run with no breaching (warn/fail/critical) result has nothing an incident tag
      // could ever attach to, so skip the read entirely rather than fetch and discard it.
      run.asset_id && run.results.some((r) => FAILING_TIERS.has(r.status))
        ? listIncidents({ asset_id: run.asset_id, suite_id: run.suite_id }).catch(
            () => [] as Incident[],
          )
        : Promise.resolve([] as Incident[]),
      // The other runs of this suite, newest first, for "Compare with…". A failed read only
      // loses the menu, never the page.
      listRuns({ suite_id: run.suite_id, limit: COMPARE_CANDIDATES + 1 }).then(
        (page) => page.items.filter((r) => r.id !== run.id).slice(0, COMPARE_CANDIDATES),
        () => [] as Run[],
      ),
    ]);
    return {
      run,
      suiteName: suite?.name ?? null,
      checks: checks.list,
      checksKnown: checks.known,
      incidents,
      siblings,
    };
  });

  const back = () => navigate('/results');

  // The tab title (and the filename a browser's Save-as-PDF dialog suggests) identifies the run
  // while it's loaded (#345 a11y ask).
  useEffect(() => {
    if (state.status !== 'ok') return;
    const previous = document.title;
    document.title = runReportTitle(state.data.suiteName, state.data.run);
    return () => {
      document.title = previous;
    };
  }, [state]);

  return (
    <>
      {/* `.no-print` (styles.css) hides the whole interactive page — including
          the app header/sider — when the "Print / Save as PDF" download-menu
          item calls `window.print()`; the parallel `RunReport` below is the
          only thing a print/PDF context renders (#345). */}
      <div className="no-print" data-testid="rd-screen">
        <Page width={1000} gap={16}>
          <div>
            <Button
              type="text"
              icon={<ArrowLeftOutlined />}
              onClick={back}
              style={{ paddingLeft: 0 }}
            >
              Results
            </Button>
          </div>

          {state.status === 'loading' && <Spin description="Loading run…" size="large" />}
          {state.status === 'error' && (
            <PageError
              error={state.error}
              kind={state.kind}
              httpStatus={state.httpStatus}
              requestId={state.requestId}
            />
          )}
          {state.status === 'ok' && (
            <RunDetailBody
              run={state.data.run}
              suiteName={state.data.suiteName}
              checks={state.data.checks}
              checksKnown={state.data.checksKnown}
              incidents={state.data.incidents}
              siblings={state.data.siblings}
            />
          )}
        </Page>
      </div>
      {state.status === 'ok' && (
        <RunReport
          run={state.data.run}
          suiteName={state.data.suiteName}
          checks={state.data.checks}
          checksKnown={state.data.checksKnown}
        />
      )}
    </>
  );
}

function RunDetailBody({
  run,
  suiteName,
  checks,
  checksKnown,
  incidents,
  siblings,
}: {
  run: Awaited<ReturnType<typeof getRun>>;
  suiteName: string | null;
  checks: Check[];
  checksKnown: boolean;
  incidents: Incident[];
  siblings: Run[];
}) {
  const checksById = useMemo(() => {
    const map = new Map<string, Check>();
    for (const c of checks) map.set(c.id, c);
    return map;
  }, [checks]);

  // The active (open|acknowledged) incident per check_id, for the fail/critical row linkage
  // (#1634) — resolved incidents aren't "open" and don't get a link.
  const activeIncidentByCheckId = useMemo(() => {
    const map = new Map<string, Incident>();
    for (const incident of incidents) {
      if (ACTIVE_INCIDENT_STATUSES.has(incident.status)) map.set(incident.check_id, incident);
    }
    return map;
  }, [incidents]);
  const [evidenceIncidentId, setEvidenceIncidentId] = useState<string | null>(null);

  // "Checks passed" counts only evaluated (severity-tier) results — skip/error didn't evaluate a
  // severity, so they're excluded from the denominator.
  const evaluated = run.results.filter((r) => SEVERITY_STATUSES.has(r.status));
  const passed = evaluated.filter((r) => r.status === 'pass').length;

  return (
    <Flex vertical gap={16}>
      <Flex justify="space-between" align="center" gap={12} wrap>
        <Flex align="center" gap={10} wrap style={{ minWidth: 0 }}>
          <Typography.Title level={3} style={{ margin: 0 }}>
            {suiteName ?? `Run ${run.suite_id.slice(0, 8)}`}
          </Typography.Title>
          {/* Links back to the asset this run executed against (#773). */}
          <AssetLink assetId={run.asset_id} />
        </Flex>
        <Flex gap={8} wrap>
          <CompareMenu run={run} siblings={siblings} />
          <DownloadMenu
            run={run}
            suiteName={suiteName}
            checks={checksById}
            checksKnown={checksKnown}
          />
        </Flex>
      </Flex>

      {/* Equal-width cards that fill the row so its right edge lines up with the
          results table below (auto-fit + 1fr stretches them to the full width). */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
          gap: 12,
        }}
      >
        <Stat label="Status">
          <RunStatusTag status={run.status} />
        </Stat>
        <Stat label="Checks passed">
          {evaluated.length === 0 ? '—' : `${passed} / ${evaluated.length}`}
        </Stat>
        <Stat label="Triggered by">{triggeredByText(run)}</Stat>
        <Stat label="Started">{formatTimestamp(run.started_at)}</Stat>
        <Stat label="Duration">{formatDuration(run.started_at, run.finished_at)}</Stat>
      </div>

      {/* Sampled-ness is a caveat on the VERDICTS below, so it sits with the
          run header rather than inside the table — a reader who scans the
          "6 / 7 passed" card must not walk away without it (#595/#1325). */}
      <SampledRunNotice results={run.results} />

      {run.status === 'failed' && run.failure_reason && (
        <Alert
          type="error"
          showIcon
          title="This run failed to execute"
          description={run.failure_reason}
        />
      )}

      <ResultsTable
        results={run.results}
        runStatus={run.status}
        checksKnown={checksKnown}
        checks={checksById}
        suiteId={run.suite_id}
        runId={run.id}
        incidentByCheckId={activeIncidentByCheckId}
        onViewIncident={setEvidenceIncidentId}
      />

      <IncidentEvidenceDrawer
        incidentId={evidenceIncidentId}
        onClose={() => setEvidenceIncidentId(null)}
      />
    </Flex>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Card size="small" style={{ height: '100%' }}>
      <Flex vertical gap={4}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {label}
        </Typography.Text>
        <span style={{ fontSize: 15 }}>{children}</span>
      </Flex>
    </Card>
  );
}

// ─────────────────────────── export (CSV / JSON) ────────────────────

type RunWithResults = Awaited<ReturnType<typeof getRun>>;

/** Compact, stable string for a JSONB scalar in a flat export cell. */
function exportScalar(value: Record<string, unknown> | null): string {
  return value === null ? '' : JSON.stringify(value);
}

function DownloadMenu({
  run,
  suiteName,
  checks,
  checksKnown,
}: {
  run: RunWithResults;
  suiteName: string | null;
  checks: Map<string, Check>;
  checksKnown: boolean;
}) {
  const stem = `${toFilenameStem(suiteName ?? 'run')}_run_${run.id.slice(0, 8)}`;
  const checkName = (id: string) => checkExportName(checks.get(id)?.name, id, checksKnown);
  // As of the result (#1613), the live check only as the fallback — and the export says which,
  // since a downloaded row is read without the in-app "(current)" marker.
  const asOf = (r: Result) => checkAsOf(r, checks.get(r.check_id));
  const expectation = (r: Result) => asOf(r)?.expectation_type ?? '';

  const exportCsv = () => {
    // `sampled` rides along (#1325 review F2): a downloaded row that says `pass` without saying it
    // was measured on 100k of 5M is the same overclaim the in-app badge exists to prevent.
    downloadCsv(
      `${stem}.csv`,
      [
        'check',
        'expectation',
        'engine',
        'dimension',
        'check_as_of',
        'status',
        'metric_value',
        'observed',
        'sampled',
      ],
      run.results.map((r) => [
        checkName(r.check_id),
        expectation(r),
        asOf(r)?.engine ?? '',
        asOf(r)?.dimension ?? '',
        exportSource(asOf(r)?.source),
        r.status,
        r.metric_value,
        exportScalar(r.observed_value),
        isSampled(r) ? 'yes' : 'no',
      ]),
    );
  };

  const exportJson = () => {
    // Export stays metric/observed-focused; the (redacted) failing-row sample is
    // surfaced in-app on each check's expanded row, not in the download.
    downloadJson(`${stem}.json`, {
      run: {
        id: run.id,
        suite_id: run.suite_id,
        suite_name: suiteName,
        status: run.status,
        triggered_by: run.triggered_by,
        triggered_by_label: run.triggered_by_label ?? null,
        started_at: run.started_at,
        finished_at: run.finished_at,
      },
      checks: run.results.map((r) => ({
        check: checkName(r.check_id),
        expectation_type: expectation(r) || null,
        engine: asOf(r)?.engine ?? null,
        dimension: asOf(r)?.dimension ?? null,
        // `version` | `later_version` | `current` — how far the three fields above are history.
        check_as_of: exportSource(asOf(r)?.source) || null,
        status: r.status,
        metric_value: r.metric_value,
        observed_value: r.observed_value,
        expected_value: r.expected_value,
        // The whole record here, not just a flag: JSON is the machine-readable artifact most likely
        // to feed downstream reporting, and the strategy.
        sampling: r.sampling ?? null,
      })),
    });
  };

  // The PDF "export" is the browser's own print-to-PDF: `RunReport` (rendered once, always, in
  // `RunDetail`) is a chrome-free print-only twin of this page.
  const exportPdf = () => window.print();

  return (
    <Dropdown
      menu={{
        items: [
          { key: 'csv', label: 'Download CSV', onClick: exportCsv },
          { key: 'json', label: 'Download JSON', onClick: exportJson },
          { key: 'pdf', label: 'Print / Save as PDF', onClick: exportPdf },
        ],
      }}
      disabled={run.results.length === 0}
    >
      <Button icon={<DownloadOutlined />}>Download</Button>
    </Dropdown>
  );
}

/**
 * "Compare with…" — the suite's other recent runs, newest first. Picking one opens the comparison
 * with the EARLIER run as the base, so the deltas read as what changed since.
 */
function CompareMenu({ run, siblings }: { run: Run; siblings: Run[] }) {
  const navigate = useNavigate();
  const outcome = (r: Run) =>
    r.checks_total === 0 ? '' : ` · ${r.checks_passed}/${r.checks_total} passed`;
  const items = siblings.map((r) => ({
    key: r.id,
    label: `${formatTimestamp(r.started_at ?? r.created_at)} · ${r.status}${outcome(r)}`,
    onClick: () => navigate(compareRunsPath(run, r)),
  }));
  if (items.length === 0) {
    return (
      <Tooltip title="No other runs of this suite to compare with yet.">
        <Button icon={<SwapOutlined />} disabled>
          Compare with…
        </Button>
      </Tooltip>
    );
  }
  return (
    <Dropdown menu={{ items }} trigger={['click']}>
      <Button icon={<SwapOutlined />} aria-label="Compare with another run of this suite">
        Compare with…
      </Button>
    </Dropdown>
  );
}

/** Failing-row sample for a check (#226). */
function SampleFailures({
  sample,
  redaction,
  redactedColumns,
}: {
  sample: Record<string, unknown> | null;
  redaction: 'full' | 'partial' | 'none' | 'zero_sample' | null;
  redactedColumns: string[];
}) {
  // #1873: a null sample under `zero_sample` was SUPPRESSED, not absent — say so
  // rather than rendering nothing, which would read identically to "no sample".
  if (!sample) {
    return redaction === 'zero_sample' ? (
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Failing rows suppressed — this deployment's zero-sample privacy mode never persisted a
        sample for this result.
      </Typography.Text>
    ) : null;
  }
  const count = typeof sample.unexpected_count === 'number' ? sample.unexpected_count : null;
  const percent = typeof sample.unexpected_percent === 'number' ? sample.unexpected_percent : null;
  // #1183: prefer `unexpected_index_list` when it's present and dict-shaped — those rows already
  // carry the suite's configured identifier column(s) alongside the failing value(s).
  const indexList = sample.unexpected_index_list;
  const isIdentifierRows = (list: unknown): list is Record<string, unknown>[] =>
    Array.isArray(list) &&
    list.length > 0 &&
    list.every((entry) => entry !== null && typeof entry === 'object' && !Array.isArray(entry));
  const rawList = isIdentifierRows(indexList) ? indexList : sample.partial_unexpected_list;
  // Entries are either row dicts ({col: value}) or bare scalars; normalise both
  // to row objects so a single column-derived table renders them.
  const rows: Record<string, unknown>[] = Array.isArray(rawList)
    ? rawList.map((entry) =>
        entry !== null && typeof entry === 'object'
          ? (entry as Record<string, unknown>)
          : { value: entry },
      )
    : [];
  // #1190 review: GX caps `partial_unexpected_list` at ~20 rows (`partial_unexpected_count`) on
  // every engine.
  const MAX_SAMPLE_ROWS = 20;
  const displayRows = rows.slice(0, MAX_SAMPLE_ROWS);
  const hiddenRowCount = Math.max((count ?? rows.length) - displayRows.length, 0);
  // GX's partial_unexpected_list rows share one schema, but union the keys
  // defensively so a ragged sample still renders every column.
  const colKeys = [...new Set(displayRows.flatMap((r) => Object.keys(r)))];
  const columns: ColumnsType<Record<string, unknown>> = colKeys.map((key) => ({
    title: key,
    dataIndex: key,
    render: (v: unknown) => (
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {/* Values are already masked by the API; stringify objects so a nested
            redacted cell shows as JSON rather than "[object Object]", and
            em-dash an explicit null (e.g. a not-null check's own failing
            value) rather than the literal string "null". A missing key (a
            ragged row that lacks this column) stays blank, not em-dashed. */}
        {v === undefined
          ? ''
          : v === null
            ? '—'
            : typeof v === 'object'
              ? JSON.stringify(v)
              : String(v)}
      </Typography.Text>
    ),
  }));

  // #424: the redaction claim must match reality — "values redacted" only when the whole sample was
  // masked; a partial mix names how many columns were.
  const redactionLabel =
    redaction === 'full'
      ? 'values redacted'
      : redaction === 'none'
        ? 'values shown'
        : redaction === 'partial'
          ? redactedColumns.length > 0
            ? `${redactedColumns.length} column${redactedColumns.length === 1 ? '' : 's'} redacted`
            : 'partially redacted'
          : null;

  return (
    <Flex vertical gap={8}>
      <Typography.Text strong style={{ fontSize: 13 }}>
        Failing rows{' '}
        <Typography.Text type="secondary" style={{ fontWeight: 'normal' }}>
          {count !== null && `· ${count} row${count === 1 ? '' : 's'}`}
          {percent !== null && ` · ${percent}%`}
          {redactionLabel !== null && ` · ${redactionLabel}`}
        </Typography.Text>
      </Typography.Text>
      {displayRows.length > 0 ? (
        <>
          <Table<Record<string, unknown>>
            scroll={{ x: 'max-content' }}
            rowKey={(_, i) => String(i)}
            size="small"
            columns={columns}
            dataSource={displayRows}
            pagination={false}
          />
          {hiddenRowCount > 0 && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              +{hiddenRowCount} more row{hiddenRowCount === 1 ? '' : 's'} not shown
            </Typography.Text>
          )}
        </>
      ) : (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          No sample rows captured.
        </Typography.Text>
      )}
    </Flex>
  );
}

function ResultsTable({
  results,
  runStatus,
  checksKnown,
  checks,
  suiteId,
  runId,
  incidentByCheckId,
  onViewIncident,
}: {
  results: Result[];
  runStatus: RunStatus;
  checksKnown: boolean;
  checks: Map<string, Check>;
  suiteId: string;
  runId: string;
  /** Active incident per check_id (#1634) — empty when the run has no resolved asset. */
  incidentByCheckId: Map<string, Incident>;
  onViewIncident: (incidentId: string) => void;
}) {
  if (results.length === 0) {
    return <Empty description={noResultsMessage(runStatus)} />;
  }
  const columns: ColumnsType<Result> = [
    {
      title: 'Check',
      dataIndex: 'check_id',
      render: (id: string, record: Result) => {
        const check = checks.get(id);
        const incident = incidentByCheckId.get(id);
        // One wrapper, whether or not the check still exists: the label differs, the annotations do
        // not.
        return (
          <Flex gap={8} align="center" wrap>
            {check ? (
              check.name
            ) : (
              <Typography.Text type="secondary">
                {checkLabel(undefined, id, checksKnown)}
              </Typography.Text>
            )}
            {/* Failure triage happens here — a muted check must say so, or the
                operator wastes time asking why no alert arrived (#653). */}
            {check && <SnoozedTag check={check} />}
            <SampledTag sampling={record.sampling} />
            {/* An open incident on this (asset, check) pair (#1634) — only for a breaching row;
                a `pass` result whose check has an incident from a PRIOR run gets no tag here (the
                incident isn't about this row). */}
            {incident && FAILING_TIERS.has(record.status) && (
              <Tag
                color="volcano"
                style={{ cursor: 'pointer' }}
                onClick={() => onViewIncident(incident.id)}
              >
                Incident
              </Tag>
            )}
          </Flex>
        );
      },
    },
    {
      // Expectation, engine and dimension are the check's values AS OF this result (#1613) —
      // `result.check`, resolved server-side from `check_versions` — not the suite's current
      // check, which the live map only backfills when the payload has no snapshot; an
      // `AsOfCell` then marks the cell "(current)" rather than passing today's value off as
      // history.
      title: 'Expectation',
      dataIndex: 'check_id',
      render: (id: string, record: Result) => {
        const asOf = checkAsOf(record, checks.get(id));
        return (
          <AsOfCell source={asOf?.source}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {asOf?.expectation_type ?? '—'}
            </Typography.Text>
          </AsOfCell>
        );
      },
    },
    {
      // The evaluator (#1551, see EngineTag's docstring for why it matters), as of this result.
      title: 'Engine',
      dataIndex: 'check_id',
      width: 80,
      render: (id: string, record: Result) => {
        const asOf = checkAsOf(record, checks.get(id));
        return asOf ? (
          <AsOfCell source={asOf.source}>
            <EngineTag engine={asOf.engine} />
          </AsOfCell>
        ) : (
          '—'
        );
      },
    },
    {
      // DQ dimension (ADR 0038) as of this result; unclassified renders as a coverage gap.
      title: 'Dimension',
      dataIndex: 'check_id',
      width: 120,
      render: (id: string, record: Result) => {
        const asOf = checkAsOf(record, checks.get(id));
        return asOf ? (
          <AsOfCell source={asOf.source}>
            <DimensionTag dimension={asOf.dimension} />
          </AsOfCell>
        ) : (
          '—'
        );
      },
    },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 100,
      render: (s: ResultStatus) => <ResultStatusTag status={s} />,
    },
    {
      title: 'Metric',
      dataIndex: 'metric_value',
      width: 90,
      render: (v: number | null) => (v === null ? '—' : v),
    },
    {
      // Bounded width + ellipsis (#1207 — #1184's "verified-benign" scope-out didn't hold for
      // every monitor kind: schema_drift's added/removed column lists and comparison's per-column
      // buckets both scale with column count, and ScalarValue's formatScalar JSON.stringifies
      // whatever shape observed_value is, unbounded.
      title: 'Observed',
      dataIndex: 'observed_value',
      width: OBSERVED_COLUMN_WIDTH,
      ellipsis: { showTitle: false },
      render: (v: Record<string, unknown> | null) =>
        v === null || v === undefined ? (
          <ScalarValue value={v} />
        ) : (
          <Tooltip title={<ScalarValue value={v} />}>
            <span style={boundedTextStyle(OBSERVED_COLUMN_WIDTH)}>
              <ScalarValue value={v} />
            </span>
          </Tooltip>
        ),
    },
  ];
  return (
    <Table
      scroll={{ x: 'max-content' }}
      rowKey="id"
      size="small"
      columns={columns}
      dataSource={results}
      pagination={false}
      expandable={{
        // Lazily fetch a check's metric trend only when its row is expanded — keyed by check_id so
        // each row's chart fetches its own history.
        expandedRowRender: (record) => {
          const check = checks.get(record.check_id);
          if (!check) {
            // No known check (e.g. a deleted one) — nothing to trend against,
            // fall back to just the failing-row sample.
            return (
              <SampleFailures
                sample={record.sample_failures}
                redaction={record.redaction}
                redactedColumns={record.redacted_columns}
              />
            );
          }
          // Anomaly's cold-start skip (#593): honest "collecting history" beats
          // making the author decode the raw observed_value JSON.
          const coldStart =
            check.kind === 'anomaly' ? anomalyColdStartHint(record.observed_value) : null;
          return (
            <Flex vertical gap={16}>
              <CheckTrend key={record.check_id} suiteId={suiteId} check={check} />
              {coldStart && <Alert type="info" showIcon title={coldStart} />}
              {check.kind === 'anomaly' && (
                <SeasonalResultSummary observed={record.observed_value} />
              )}
              {check.kind === 'comparison' ? (
                <ComparisonResultDetail runId={runId} result={record} />
              ) : (
                <SampleFailures
                  sample={record.sample_failures}
                  redaction={record.redaction}
                  redactedColumns={record.redacted_columns}
                />
              )}
            </Flex>
          );
        },
        // Expandable when we can show a trend (known check), a failing sample, or the
        // #1873 zero-sample suppression notice (also a null `sample_failures`).
        rowExpandable: (record) =>
          checks.has(record.check_id) ||
          record.sample_failures !== null ||
          record.redaction === 'zero_sample',
      }}
    />
  );
}
