import { ArrowLeftOutlined, DownloadOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Dropdown, Empty, Flex, Spin, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { getRun, type Result, type ResultStatus } from '../api/runs';
import { type Check, getSuite, listChecks } from '../api/suites';
import { AssetLink } from '../components/assets/AssetLink';
import { CheckTrend } from '../components/checks/CheckTrend';
import { ComparisonResultDetail } from '../components/results/ComparisonResultDetail';
import { SnoozedTag } from '../components/checks/snooze';
import {
  formatDuration,
  formatTimestamp,
  RESULT_STATUS_COLORS,
  RUN_STATUS_COLORS,
} from '../components/results/resultsFormat';
import { Page } from '../components/layout/Page';
import { ScalarValue } from '../components/results/ScalarValue';
import { useAsyncData } from '../hooks/useAsyncData';
import { downloadCsv, downloadJson, toFilenameStem } from '../utils/download';
import { PageError } from '../components/feedback/PageError';

/** The four severity tiers that count as "evaluated" (ADR 0005) — skip/error don't. */
const SEVERITY_STATUSES = new Set<ResultStatus>(['pass', 'warn', 'fail', 'critical']);

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

  return (
    <Page width={1000} gap={16}>
      <div>
        <Button type="text" icon={<ArrowLeftOutlined />} onClick={back} style={{ paddingLeft: 0 }}>
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

  return (
    <Dropdown
      menu={{
        items: [
          { key: 'csv', label: 'Download CSV', onClick: exportCsv },
          { key: 'json', label: 'Download JSON', onClick: exportJson },
        ],
      }}
      disabled={run.results.length === 0}
    >
      <Button icon={<DownloadOutlined />}>Download</Button>
    </Dropdown>
  );
}

/** Redacted failing-row sample for a check (#226). The API masks every cell
 *  value to "<redacted>"; we surface the counts and the row/column *shape* so a
 *  reviewer sees how much (and structurally what) failed without seeing PII. */
function SampleFailures({ sample }: { sample: Record<string, unknown> | null }) {
  if (!sample) return null;
  const count = typeof sample.unexpected_count === 'number' ? sample.unexpected_count : null;
  const percent = typeof sample.unexpected_percent === 'number' ? sample.unexpected_percent : null;
  const rawList = sample.partial_unexpected_list;
  // Entries are either row dicts ({col: value}) or bare scalars; normalise both
  // to row objects so a single column-derived table renders them.
  const rows: Record<string, unknown>[] = Array.isArray(rawList)
    ? rawList.map((entry) =>
        entry !== null && typeof entry === 'object'
          ? (entry as Record<string, unknown>)
          : { value: entry },
      )
    : [];
  // GX's partial_unexpected_list rows share one schema, but union the keys
  // defensively so a ragged sample still renders every column.
  const colKeys = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  const columns: ColumnsType<Record<string, unknown>> = colKeys.map((key) => ({
    title: key,
    dataIndex: key,
    render: (v: unknown) => (
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {/* Values are already masked by the API; stringify objects so a nested
            redacted cell shows as JSON rather than "[object Object]". */}
        {v === undefined ? '' : typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v)}
      </Typography.Text>
    ),
  }));

  return (
    <Flex vertical gap={8}>
      <Typography.Text strong style={{ fontSize: 13 }}>
        Failing rows{' '}
        <Typography.Text type="secondary" style={{ fontWeight: 'normal' }}>
          {count !== null && `· ${count} row${count === 1 ? '' : 's'}`}
          {percent !== null && ` · ${percent}%`} · values redacted
        </Typography.Text>
      </Typography.Text>
      {rows.length > 0 ? (
        <Table<Record<string, unknown>>
          scroll={{ x: 'max-content' }}
          rowKey={(_, i) => String(i)}
          size="small"
          columns={columns}
          dataSource={rows}
          pagination={false}
        />
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
      title: 'Observed',
      dataIndex: 'observed_value',
      render: (v: Record<string, unknown> | null) => <ScalarValue value={v} />,
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
        expandedRowRender: (record) =>
          checks.get(record.check_id)?.kind === 'comparison' ? (
            <Flex vertical gap={16}>
              <CheckTrend key={record.check_id} suiteId={suiteId} checkId={record.check_id} />
              <ComparisonResultDetail runId={runId} result={record} />
            </Flex>
          ) : (
            <Flex vertical gap={16}>
              <CheckTrend key={record.check_id} suiteId={suiteId} checkId={record.check_id} />
              <SampleFailures sample={record.sample_failures} />
            </Flex>
          ),
        // Expandable when we can show a trend (known check) or a failing sample.
        rowExpandable: (record) => checks.has(record.check_id) || record.sample_failures !== null,
      }}
    />
  );
}
