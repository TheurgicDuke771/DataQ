import { ArrowLeftOutlined, DownloadOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Dropdown, Empty, Flex, Spin, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { getRun, type Result, type ResultStatus } from '../api/runs';
import { type Check, getSuite, listChecks } from '../api/suites';
import { CheckTrend } from '../components/checks/CheckTrend';
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

/** The four severity tiers that count as "evaluated" (ADR 0005) — skip/error don't. */
const SEVERITY_STATUSES = new Set<ResultStatus>(['pass', 'warn', 'fail', 'critical']);

/**
 * Routed run-detail page (`/results/:runId`, ADR 0022) — replaces the run-detail
 * drawer so a run is deep-linkable and refreshable. Loads the run + its results
 * by id, plus the suite name and per-check names for display.
 *
 * Sample failing rows stay withheld by the API (PII, #226 / ADR 0018) — this
 * page never requests or renders them.
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

      {state.status === 'loading' && <Spin tip="Loading run…" size="large" />}
      {state.status === 'error' && (
        <Alert type="error" showIcon message="Failed to load run" description={state.error} />
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
        <Typography.Title level={3} style={{ margin: 0 }}>
          {suiteName ?? `Run ${run.suite_id.slice(0, 8)}`}
        </Typography.Title>
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

      <ResultsTable results={run.results} checks={checksById} suiteId={run.suite_id} />
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
    // Sample failing rows are never in the payload (PII, withheld by the API).
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

function ResultsTable({
  results,
  checks,
  suiteId,
}: {
  results: Result[];
  checks: Map<string, Check>;
  suiteId: string;
}) {
  if (results.length === 0) {
    return <Empty description="No check results — the run did not complete." />;
  }
  const columns: ColumnsType<Result> = [
    {
      title: 'Check',
      dataIndex: 'check_id',
      render: (id: string) =>
        checks.get(id)?.name ?? <Typography.Text code>{id.slice(0, 8)}</Typography.Text>,
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
      rowKey="id"
      size="small"
      columns={columns}
      dataSource={results}
      pagination={false}
      expandable={{
        // Lazily fetch a check's metric trend only when its row is expanded —
        // keyed by check_id so each row's chart fetches its own history.
        expandedRowRender: (record) => (
          <CheckTrend key={record.check_id} suiteId={suiteId} checkId={record.check_id} />
        ),
        rowExpandable: (record) => checks.has(record.check_id),
      }}
    />
  );
}
