import { ArrowLeftOutlined, DownloadOutlined } from '@ant-design/icons';
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
import { useEffect, useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { getRun, type Result, type ResultStatus } from '../api/runs';
import { type Check, getSuite, listChecks } from '../api/suites';
import { AssetLink } from '../components/assets/AssetLink';
import { CheckTrend } from '../components/checks/CheckTrend';
import { ComparisonResultDetail } from '../components/results/ComparisonResultDetail';
import { SnoozedTag } from '../components/checks/snooze';
import {
  anomalyColdStartHint,
  formatDuration,
  formatTimestamp,
  RESULT_STATUS_COLORS,
  RUN_STATUS_COLORS,
  runReportTitle,
} from '../components/results/resultsFormat';
import { Page } from '../components/layout/Page';
import { RunReport } from '../components/results/RunReport';
import { ScalarValue } from '../components/results/ScalarValue';
import { boundedTextStyle } from '../components/shared/ellipsisColumn';
import { useAsyncData } from '../hooks/useAsyncData';
import { downloadCsv, downloadJson, toFilenameStem } from '../utils/download';
import { PageError } from '../components/feedback/PageError';

/** The four severity tiers that count as "evaluated" (ADR 0005) — skip/error don't. */
const SEVERITY_STATUSES = new Set<ResultStatus>(['pass', 'warn', 'fail', 'critical']);

/**
 * Bound for the "Observed" cell — a structured `observed_value` payload
 * (schema_drift baselines, comparison buckets, anomaly stats) is unbounded in
 * principle. Deliberately NOT exported: importing this module from a Playwright
 * spec would drag the whole React/antd page into the test process, so
 * `e2e/results.spec.ts` restates the number and points back here. Keep the two
 * in step.
 */
const OBSERVED_COLUMN_WIDTH = 220;

/**
 * Routed run-detail page (`/results/:runId`, ADR 0022) — replaces the run-detail
 * drawer so a run is deep-linkable and refreshable. Loads the run + its results
 * by id, plus the suite name and per-check names for display.
 *
 * Sample failing rows are surfaced in each check's expanded row, redacted at the
 * API boundary (#226): the counts are shown; the raw cell values are masked.
 */
export function RunDetail() {
  const navigate = useNavigate();
  const { runId } = useParams<{ runId: string }>();

  const { state } = useAsyncData(async () => {
    if (!runId) throw new Error('no run');
    const run = await getRun(runId);
    // The suite may be readable while details race; tolerate a missing name/checks
    // rather than failing the whole page.
    const [suite, checks] = await Promise.all([
      getSuite(run.suite_id).catch(() => null),
      listChecks(run.suite_id).catch(() => [] as Check[]),
    ]);
    return { run, suiteName: suite?.name ?? null, checks };
  });

  const back = () => navigate('/results');

  // The tab title (and the filename a browser's Save-as-PDF dialog suggests)
  // identifies the run while it's loaded (#345 a11y ask), restored on
  // navigating away rather than left stuck on a stale run's title.
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
            />
          )}
        </Page>
      </div>
      {state.status === 'ok' && (
        <RunReport
          run={state.data.run}
          suiteName={state.data.suiteName}
          checks={state.data.checks}
        />
      )}
    </>
  );
}

function RunDetailBody({
  run,
  suiteName,
  checks,
}: {
  run: Awaited<ReturnType<typeof getRun>>;
  suiteName: string | null;
  checks: Check[];
}) {
  const checksById = useMemo(() => {
    const map = new Map<string, Check>();
    for (const c of checks) map.set(c.id, c);
    return map;
  }, [checks]);

  // "Checks passed" counts only evaluated (severity-tier) results — skip/error
  // didn't evaluate a severity, so they're excluded from the denominator, same
  // as the ADR-0005 health score (a run with skipped checks shouldn't read worse
  // than its health).
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
        <DownloadMenu run={run} suiteName={suiteName} checks={checksById} />
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
          <Tag color={RUN_STATUS_COLORS[run.status]}>{run.status}</Tag>
        </Stat>
        <Stat label="Checks passed">
          {evaluated.length === 0 ? '—' : `${passed} / ${evaluated.length}`}
        </Stat>
        <Stat label="Triggered by">{run.triggered_by ?? '—'}</Stat>
        <Stat label="Started">{formatTimestamp(run.started_at)}</Stat>
        <Stat label="Duration">{formatDuration(run.started_at, run.finished_at)}</Stat>
      </div>

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
        checks={checksById}
        suiteId={run.suite_id}
        runId={run.id}
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
}: {
  run: RunWithResults;
  suiteName: string | null;
  checks: Map<string, Check>;
}) {
  const stem = `${toFilenameStem(suiteName ?? 'run')}_run_${run.id.slice(0, 8)}`;
  const checkName = (id: string) => checks.get(id)?.name ?? id;
  const expectation = (id: string) => checks.get(id)?.expectation_type ?? '';

  const exportCsv = () => {
    downloadCsv(
      `${stem}.csv`,
      ['check', 'expectation', 'status', 'metric_value', 'observed'],
      run.results.map((r) => [
        checkName(r.check_id),
        expectation(r.check_id),
        r.status,
        r.metric_value,
        exportScalar(r.observed_value),
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
        started_at: run.started_at,
        finished_at: run.finished_at,
      },
      checks: run.results.map((r) => ({
        check: checkName(r.check_id),
        expectation_type: expectation(r.check_id) || null,
        status: r.status,
        metric_value: r.metric_value,
        observed_value: r.observed_value,
        expected_value: r.expected_value,
      })),
    });
  };

  // The PDF "export" is the browser's own print-to-PDF: `RunReport` (rendered
  // once, always, in `RunDetail`) is a chrome-free print-only twin of this
  // page, hidden on screen and shown only in a print context (`.print-only` /
  // `.no-print` in styles.css) — so triggering it is just `window.print()`,
  // zero new dependency (#345).
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

/** Failing-row sample for a check (#226). The API masks PII/unclassified cell
 *  values to "<redacted>" per column (#415) — some columns can surface
 *  genuinely (e.g. a non-PII tested column like `line_total`), so the header
 *  reports the actual `redaction` state (#424) rather than always claiming
 *  "values redacted": full masking, a partial mix, all-shown, or (when the
 *  sample had nothing data-bearing to redact either way) no claim at all. */
function SampleFailures({
  sample,
  redaction,
  redactedColumns,
}: {
  sample: Record<string, unknown> | null;
  redaction: 'full' | 'partial' | 'none' | null;
  redactedColumns: string[];
}) {
  if (!sample) return null;
  const count = typeof sample.unexpected_count === 'number' ? sample.unexpected_count : null;
  const percent = typeof sample.unexpected_percent === 'number' ? sample.unexpected_percent : null;
  // #1183: prefer `unexpected_index_list` when it's present and dict-shaped —
  // those rows already carry the suite's configured identifier column(s)
  // alongside the failing value(s), and are already API-redacted per column
  // (the backend strips a non-dict `unexpected_index_list` before it ever
  // reaches here — `gx_runner._is_identifier_index_list` — so dict shape is
  // trustable). `partial_unexpected_list` (bare scalars → a single `value`
  // column, no identifier) is the fallback for checks/engines that don't
  // populate the index list.
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
  // #1190 review: GX caps `partial_unexpected_list` at ~20 rows
  // (`partial_unexpected_count`) on every engine, but under `result_format:
  // COMPLETE` (always used by `gx_runner`) the pandas engine — flat-file/ADLS/S3
  // and Iceberg — returns `unexpected_index_list` FULL and UNTRUNCATED (only the
  // SQLAlchemy-backed engines, Snowflake/UC, stay capped there too). Since this
  // component now prefers that list, cap what's actually rendered ourselves so a
  // check with thousands of failing rows on a pandas-backed datasource can't
  // dump thousands of DOM rows into a `pagination={false}` table.
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

  // #424: the redaction claim must match reality — "values redacted" only when
  // the whole sample was masked; a partial mix names how many columns were, and
  // an all-shown or no-data-bearing-content sample makes no redaction claim.
  // A partial state can carry an EMPTY redactedColumns list (#1115 review): the
  // backend tracker also reports "partial" when an anonymous mask (a scalar
  // partial_unexpected_list with no tested_column — nothing nameable) coincides
  // with some other column being shown, so "0 columns redacted" would be
  // false-adjacent — fall back to the unquantified phrasing instead.
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
  checks,
  suiteId,
  runId,
}: {
  results: Result[];
  checks: Map<string, Check>;
  suiteId: string;
  runId: string;
}) {
  if (results.length === 0) {
    return <Empty description="No check results — the run did not complete." />;
  }
  const columns: ColumnsType<Result> = [
    {
      title: 'Check',
      dataIndex: 'check_id',
      render: (id: string) => {
        const check = checks.get(id);
        if (!check) return <Typography.Text code>{id.slice(0, 8)}</Typography.Text>;
        return (
          <Flex gap={8} align="center" wrap>
            {check.name}
            {/* Failure triage happens here — a muted check must say so, or the
                operator wastes time asking why no alert arrived (#653). */}
            <SnoozedTag check={check} />
          </Flex>
        );
      },
    },
    {
      title: 'Expectation',
      dataIndex: 'check_id',
      render: (id: string) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {checks.get(id)?.expectation_type ?? '—'}
        </Typography.Text>
      ),
    },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 100,
      render: (s: ResultStatus) => <Tag color={RESULT_STATUS_COLORS[s]}>{s}</Tag>,
    },
    {
      title: 'Metric',
      dataIndex: 'metric_value',
      width: 90,
      render: (v: number | null) => (v === null ? '—' : v),
    },
    {
      // Bounded width + ellipsis (#1207 — #1184's "verified-benign" scope-out
      // didn't hold for every monitor kind: schema_drift's added/removed
      // column lists and comparison's per-column buckets both scale with
      // column count, and ScalarValue's formatScalar JSON.stringifies
      // whatever shape observed_value is, unbounded. `ellipsis: { showTitle:
      // false }` suppresses antd's own native-title hover so the Tooltip
      // below is the only one — same pattern as `ellipsisColumn` (#1184),
      // applied manually here since this column has a custom ScalarValue
      // render rather than a plain string field. ScalarValue's own
      // formatting (monospace, em-dash for null) is reused unchanged in
      // both the cell and the tooltip.
      //
      // The bound that actually binds is `boundedTextStyle` on the span: the
      // column `width` alone is inert under this table's `scroll.x =
      // 'max-content'` (#1282 — see that helper for the why).
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
        // Lazily fetch a check's metric trend only when its row is expanded —
        // keyed by check_id so each row's chart fetches its own history. The
        // redacted failing-row sample (if any) sits below the trend.
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
              {coldStart && <Alert type="info" showIcon message={coldStart} />}
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
        // Expandable when we can show a trend (known check) or a failing sample.
        rowExpandable: (record) => checks.has(record.check_id) || record.sample_failures !== null,
      }}
    />
  );
}
