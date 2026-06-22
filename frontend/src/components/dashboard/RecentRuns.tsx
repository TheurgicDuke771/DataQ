import { Alert, Card, Empty, Flex, Spin, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMemo } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { listRuns, type Run, type RunStatus } from '../../api/runs';
import { listSuites } from '../../api/suites';
import { useAsyncData } from '../../hooks/useAsyncData';
import { formatDuration, formatTimestamp, RUN_STATUS_COLORS } from '../results/resultsFormat';

/**
 * Recent Runs (prototype `RecentRuns`) — a cross-suite feed of the latest runs
 * on the dashboard, each row deep-linking to the routed run-detail page. Fetches
 * its own slice (the summary endpoint carries aggregates, not run rows); the
 * runs are already suite-scoped by the backend. "View all" → the Results page.
 *
 * The prototype's "Anomalies" column is dropped — there's no anomaly count on a
 * run (KPI honesty, ADR 0022).
 */
const RECENT_LIMIT = 8;

export function RecentRuns() {
  const navigate = useNavigate();
  const { state } = useAsyncData(() => listRuns({ limit: RECENT_LIMIT }));
  const { state: suitesState } = useAsyncData(listSuites);

  const suiteNames = useMemo(() => {
    const map = new Map<string, string>();
    if (suitesState.status === 'ok') {
      for (const s of suitesState.data) map.set(s.id, s.name);
    }
    return map;
  }, [suitesState]);

  const columns: ColumnsType<Run> = [
    {
      title: 'Suite',
      dataIndex: 'suite_id',
      render: (suiteId: string) =>
        suiteNames.get(suiteId) ?? <Typography.Text code>{suiteId.slice(0, 8)}</Typography.Text>,
    },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 120,
      render: (s: RunStatus) => <Tag color={RUN_STATUS_COLORS[s]}>{s}</Tag>,
    },
    { title: 'Triggered by', dataIndex: 'triggered_by', render: (t: string | null) => t ?? '—' },
    { title: 'Started', dataIndex: 'started_at', render: (t: string | null) => formatTimestamp(t) },
    {
      title: 'Duration',
      width: 110,
      render: (_: unknown, run: Run) => formatDuration(run.started_at, run.finished_at),
    },
  ];

  return (
    <Card size="small">
      <Flex justify="space-between" align="center" style={{ marginBottom: 12 }}>
        <Typography.Text strong style={{ fontSize: 16 }}>
          Recent Runs
        </Typography.Text>
        <Link to="/results">View all</Link>
      </Flex>

      {state.status === 'loading' && <Spin tip="Loading runs…" />}
      {state.status === 'error' && (
        <Alert type="error" showIcon message="Failed to load runs" description={state.error} />
      )}
      {state.status === 'ok' && (
        <Table<Run>
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={state.data}
          pagination={false}
          locale={{ emptyText: <Empty description="No runs yet." /> }}
          onRow={(run) => ({
            onClick: () => navigate(`/results/${run.id}`),
            style: { cursor: 'pointer' },
          })}
        />
      )}
    </Card>
  );
}
