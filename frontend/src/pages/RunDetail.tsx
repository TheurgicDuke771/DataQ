import { ArrowLeftOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Empty, Flex, Spin, Table, Tag, Typography } from 'antd';
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
import { ScalarValue } from '../components/results/ScalarValue';
import { useAsyncData } from '../hooks/useAsyncData';

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
    <Flex vertical gap={16} style={{ maxWidth: 1000 }}>
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
    </Flex>
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

  const passed = run.results.filter((r) => r.status === 'pass').length;

  return (
    <Flex vertical gap={16}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        {suiteName ?? `Run ${run.suite_id.slice(0, 8)}`}
      </Typography.Title>

      <Flex gap={12} wrap>
        <Stat label="Status">
          <Tag color={RUN_STATUS_COLORS[run.status]}>{run.status}</Tag>
        </Stat>
        <Stat label="Checks passed">
          {run.results.length === 0 ? '—' : `${passed} / ${run.results.length}`}
        </Stat>
        <Stat label="Triggered by">{run.triggered_by ?? '—'}</Stat>
        <Stat label="Started">{formatTimestamp(run.started_at)}</Stat>
        <Stat label="Duration">{formatDuration(run.started_at, run.finished_at)}</Stat>
      </Flex>

      <ResultsTable results={run.results} checks={checksById} suiteId={run.suite_id} />
    </Flex>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Card size="small" style={{ minWidth: 150 }}>
      <Flex vertical gap={4}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {label}
        </Typography.Text>
        <span style={{ fontSize: 15 }}>{children}</span>
      </Flex>
    </Card>
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
