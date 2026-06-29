import { PlayCircleOutlined } from '@ant-design/icons';
import { Alert, Button, Empty, Flex, Select, Spin, Table, Tabs, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import {
  listConnections,
  CONNECTION_ENVS,
  type ConnectionEnv,
  DATASOURCE_CATEGORIES,
  DATASOURCE_CATEGORY,
  DATASOURCE_CATEGORY_LABELS,
  type DatasourceCategory,
  envLabel,
} from '../api/connections';
import {
  listPipelineRuns,
  listRuns,
  type PipelineRun,
  type Run,
  type RunStatus,
  RUN_STATUSES,
} from '../api/runs';
import { listSuites } from '../api/suites';
import { Page } from '../components/layout/Page';
import { RunNowPanel } from '../components/runs/RunNowPanel';
import { useAsyncData } from '../hooks/useAsyncData';
import {
  formatDuration,
  formatTimestamp,
  isWithinWindowDays,
  pipelineRunMarker,
  pipelineStatusColor,
  RESULT_STATUS_COLORS,
  RUN_STATUS_COLORS,
} from '../components/results/resultsFormat';

const LIST_LIMIT = 200;

/** Date-window presets for the Results date filter (no true range picker → no
 *  dayjs dependency; mirrors the dashboard's 24h/7d/30d window control). */
const DATE_WINDOWS = [
  { value: 'all', label: 'All time' },
  { value: '1', label: 'Last 24h' },
  { value: '7', label: 'Last 7 days' },
  { value: '30', label: 'Last 30 days' },
] as const;
type DateWindow = (typeof DATE_WINDOWS)[number]['value'];

/** A labelled filter control — one `secondary` caption above each Select so the
 *  growing filter bar stays scannable and wraps cleanly on narrow viewports. */
function Filter({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Flex vertical gap={4}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {label}
      </Typography.Text>
      {children}
    </Flex>
  );
}

export function Results() {
  const [runNowOpen, setRunNowOpen] = useState(false);
  return (
    <Page>
      <Flex justify="space-between" align="center" gap={12}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          Results
        </Typography.Title>
        <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => setRunNowOpen(true)}>
          Run now
        </Button>
      </Flex>
      <RunNowPanel open={runNowOpen} onClose={() => setRunNowOpen(false)} />
      <Tabs
        defaultActiveKey="runs"
        items={[
          { key: 'runs', label: 'Runs', children: <RunsTab /> },
          { key: 'pipelines', label: 'Pipeline runs', children: <PipelineRunsTab /> },
        ]}
      />
    </Page>
  );
}

// ───────────────────────────── Runs tab ─────────────────────────────

/** Per-suite facts the run filters need: display name + the env / datasource
 *  category of the suite's connection (a run only carries `suite_id`). */
interface SuiteMeta {
  name: string;
  env: ConnectionEnv | null;
  category: DatasourceCategory | null;
}

function RunsTab() {
  // Fetch a page of runs + the accessible suites + connections (for id→name and
  // the env / datasource of each suite), then filter client-side — cheap at this
  // volume and avoids a refetch per filter change.
  const navigate = useNavigate();
  const { state } = useAsyncData(() => listRuns({ limit: LIST_LIMIT }));
  const { state: suitesState } = useAsyncData(listSuites);
  const { state: connectionsState } = useAsyncData(() => listConnections());

  const [status, setStatus] = useState<RunStatus | 'all'>('all');
  const [suiteId, setSuiteId] = useState<string | 'all'>('all');
  const [env, setEnv] = useState<ConnectionEnv | 'all'>('all');
  const [category, setCategory] = useState<DatasourceCategory | 'all'>('all');
  const [dateWindow, setDateWindow] = useState<DateWindow>('all');

  // suite_id → { name, env, datasource category }, joining suites to their
  // connection. Missing connection (still loading / inaccessible) → null facts.
  const suiteMeta = useMemo(() => {
    const map = new Map<string, SuiteMeta>();
    if (suitesState.status !== 'ok') return map;
    const conns = connectionsState.status === 'ok' ? connectionsState.data : [];
    const connById = new Map(conns.map((c) => [c.id, c]));
    for (const s of suitesState.data) {
      const conn = connById.get(s.connection_id);
      map.set(s.id, {
        name: s.name,
        env: conn?.env ?? null,
        category: conn ? DATASOURCE_CATEGORY[conn.type] : null,
      });
    }
    return map;
  }, [suitesState, connectionsState]);

  // Suite options sorted by name — the filter offers every accessible suite, not
  // only those with runs in the current page.
  const suiteOptions = useMemo(
    () =>
      [...suiteMeta.entries()]
        .map(([id, meta]) => ({ value: id, label: meta.name }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    [suiteMeta],
  );

  if (state.status === 'loading') return <Spin tip="Loading runs…" size="large" />;
  if (state.status === 'error') {
    return <Alert type="error" showIcon message="Failed to load runs" description={state.error} />;
  }

  const windowDays = dateWindow === 'all' ? null : Number(dateWindow);
  const runs = state.data.filter((r) => {
    if (status !== 'all' && r.status !== status) return false;
    if (suiteId !== 'all' && r.suite_id !== suiteId) return false;
    // Keep runs with unknown env/datasource visible under any filter — a
    // shared-suite viewer may lack access to the underlying connection (meta is
    // null), and listRuns is already suite-scoped. Only exclude when the run's
    // metadata is known and actually differs. (#348)
    const meta = suiteMeta.get(r.suite_id);
    if (env !== 'all' && meta?.env != null && meta.env !== env) return false;
    if (category !== 'all' && meta?.category != null && meta.category !== category) return false;
    if (windowDays !== null && !isWithinWindowDays(r.started_at ?? r.created_at, windowDays))
      return false;
    return true;
  });

  const columns: ColumnsType<Run> = [
    {
      title: 'Suite',
      dataIndex: 'suite_id',
      render: (id: string) =>
        suiteMeta.get(id)?.name ?? <Typography.Text code>{id.slice(0, 8)}</Typography.Text>,
    },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 120,
      render: (s: RunStatus) => <Tag color={RUN_STATUS_COLORS[s]}>{s}</Tag>,
    },
    {
      // Data-quality outcome (passed/total), coloured by worst severity — distinct
      // from the execution Status, so a `succeeded` run with failing checks reads
      // amber/red here instead of looking all-green (#423).
      title: 'Checks',
      width: 100,
      render: (_: unknown, run: Run) =>
        run.checks_total === 0 ? (
          <Typography.Text type="secondary">—</Typography.Text>
        ) : (
          <Tag color={RESULT_STATUS_COLORS[run.worst_severity ?? 'pass']}>
            {run.checks_passed}/{run.checks_total}
          </Tag>
        ),
    },
    { title: 'Triggered by', dataIndex: 'triggered_by', render: (t: string | null) => t ?? '—' },
    {
      title: 'Started',
      dataIndex: 'started_at',
      render: (t: string | null) => formatTimestamp(t),
    },
    {
      title: 'Duration',
      width: 110,
      render: (_: unknown, run: Run) => formatDuration(run.started_at, run.finished_at),
    },
  ];

  // Env / datasource are derived from the suite→connection join (suiteMeta), so
  // both fetches must succeed before those two filters can compute anything.
  // Gate the selects on that combined readiness — otherwise a suites/connections
  // load failure leaves the selects enabled but silently inert (a non-'all'
  // choice no-ops because every meta is null). (#348)
  const metaReady = suitesState.status === 'ok' && connectionsState.status === 'ok';
  const metaFailed = suitesState.status === 'error' || connectionsState.status === 'error';

  return (
    <Flex vertical gap={16}>
      {metaFailed && (
        <Alert
          type="warning"
          showIcon
          message="Environment / datasource filters unavailable"
          description="Couldn't load suites or connections, so runs can't be filtered by environment or datasource. All runs are still shown."
        />
      )}
      <Flex gap={16} align="flex-end" wrap="wrap">
        <Filter label="Status">
          <Select<RunStatus | 'all'>
            value={status}
            onChange={setStatus}
            style={{ width: 150 }}
            options={[
              { value: 'all', label: 'All' },
              ...RUN_STATUSES.map((s) => ({ value: s, label: s })),
            ]}
          />
        </Filter>
        <Filter label="Suite">
          <Select<string | 'all'>
            value={suiteId}
            onChange={setSuiteId}
            style={{ width: 220 }}
            showSearch
            optionFilterProp="label"
            options={[{ value: 'all', label: 'All suites' }, ...suiteOptions]}
          />
        </Filter>
        <Filter label="Environment">
          <Select<ConnectionEnv | 'all'>
            value={env}
            onChange={setEnv}
            disabled={!metaReady}
            loading={!metaReady && !metaFailed}
            style={{ width: 130 }}
            options={[
              { value: 'all', label: 'All' },
              ...CONNECTION_ENVS.map((e) => ({ value: e, label: envLabel(e) })),
            ]}
          />
        </Filter>
        <Filter label="Datasource">
          <Select<DatasourceCategory | 'all'>
            value={category}
            onChange={setCategory}
            disabled={!metaReady}
            loading={!metaReady && !metaFailed}
            style={{ width: 160 }}
            options={[
              { value: 'all', label: 'All' },
              ...DATASOURCE_CATEGORIES.map((c) => ({
                value: c,
                label: DATASOURCE_CATEGORY_LABELS[c],
              })),
            ]}
          />
        </Filter>
        <Filter label="Date">
          <Select<DateWindow>
            value={dateWindow}
            onChange={setDateWindow}
            style={{ width: 150 }}
            options={DATE_WINDOWS.map((w) => ({ value: w.value, label: w.label }))}
          />
        </Filter>
      </Flex>
      <Table<Run>
        rowKey="id"
        columns={columns}
        dataSource={runs}
        pagination={false}
        locale={{ emptyText: <Empty description="No runs match these filters." /> }}
        onRow={(run) => ({
          onClick: () => navigate(`/results/${run.id}`),
          style: { cursor: 'pointer' },
        })}
      />
    </Flex>
  );
}

// ─────────────────────────── Pipeline runs tab ──────────────────────

/** Pipeline-runs auto-poll cadence — orchestrator runs move on the minute scale,
 *  so 30s keeps the panel near-live without hammering the API. */
const PIPELINE_POLL_MS = 30_000;

function PipelineRunsTab({ pollMs = PIPELINE_POLL_MS }: { pollMs?: number }) {
  const navigate = useNavigate();
  // Pipeline runs + the DQ runs they triggered, both auto-refreshed so a newly
  // triggered run shows up against its pipeline run without a manual reload.
  const { state, reload } = useAsyncData(() => listPipelineRuns({ limit: LIST_LIMIT }));
  const { state: runsState, reload: reloadRuns } = useAsyncData(() =>
    listRuns({ limit: LIST_LIMIT }),
  );
  const [provider, setProvider] = useState<'all' | 'adf' | 'airflow'>('all');
  const [dateWindow, setDateWindow] = useState<DateWindow>('all');

  // Refresh both sources on the poll cadence; `reload` keeps the current rows
  // visible across the refetch (no flash back to the spinner).
  useEffect(() => {
    const id = setInterval(() => {
      reload();
      reloadRuns();
    }, pollMs);
    return () => clearInterval(id);
  }, [reload, reloadRuns, pollMs]);

  // triggered_by marker → the DQ runs it spawned (one pipeline run can trigger
  // several, one per binding).
  const runsByMarker = useMemo(() => {
    const map = new Map<string, Run[]>();
    if (runsState.status !== 'ok') return map;
    for (const r of runsState.data) {
      if (!r.triggered_by) continue;
      const list = map.get(r.triggered_by);
      if (list) list.push(r);
      else map.set(r.triggered_by, [r]);
    }
    return map;
  }, [runsState]);

  if (state.status === 'loading') return <Spin tip="Loading pipeline runs…" size="large" />;
  if (state.status === 'error') {
    return (
      <Alert
        type="error"
        showIcon
        message="Failed to load pipeline runs"
        description={state.error}
      />
    );
  }

  const windowDays = dateWindow === 'all' ? null : Number(dateWindow);
  const rows = state.data.filter((p) => {
    if (provider !== 'all' && p.provider !== provider) return false;
    if (windowDays !== null && !isWithinWindowDays(p.started_at ?? p.created_at, windowDays))
      return false;
    return true;
  });

  const columns: ColumnsType<PipelineRun> = [
    { title: 'Provider', dataIndex: 'provider', width: 110, render: (p: string) => <Tag>{p}</Tag> },
    { title: 'Pipeline / DAG', dataIndex: 'pipeline_or_dag_id' },
    {
      // The provider's own run id — the handle for cross-referencing this run in
      // ADF / Airflow when debugging. Copyable for exactly that. Distinct from the
      // "DQ run" column, which links the DataQ run this pipeline triggered.
      title: 'Provider run',
      dataIndex: 'provider_run_id',
      width: 200,
      render: (v: string) => (
        <Typography.Text code copyable={{ text: v }} style={{ fontSize: 12 }} ellipsis>
          {v}
        </Typography.Text>
      ),
    },
    { title: 'Env', dataIndex: 'env', width: 80, render: (e: string) => e.toUpperCase() },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 110,
      render: (s: string) => <Tag color={pipelineStatusColor(s)}>{s}</Tag>,
    },
    {
      title: 'DQ run',
      width: 160,
      render: (_: unknown, p: PipelineRun) => {
        const triggered = runsByMarker.get(pipelineRunMarker(p)) ?? [];
        if (triggered.length === 0) return <Typography.Text type="secondary">—</Typography.Text>;
        return (
          <Flex gap={6} wrap="wrap">
            {triggered.map((r) => (
              <Tag
                key={r.id}
                color={RUN_STATUS_COLORS[r.status]}
                style={{ cursor: 'pointer', marginInlineEnd: 0 }}
                onClick={() => navigate(`/results/${r.id}`)}
              >
                {r.status}
              </Tag>
            ))}
          </Flex>
        );
      },
    },
    {
      title: 'Started',
      dataIndex: 'started_at',
      render: (t: string | null) => formatTimestamp(t),
    },
    {
      title: 'Failure reason',
      dataIndex: 'failure_reason',
      render: (r: string | null) => r ?? '—',
    },
  ];

  // The DQ-run column joins against listRuns; if that fetch failed, every row
  // would show '—' — a confidently-wrong "no triggered runs". Flag it instead. (#348)
  const runsJoinFailed = runsState.status === 'error';

  return (
    <Flex vertical gap={16}>
      {runsJoinFailed && (
        <Alert
          type="warning"
          showIcon
          message="Triggered DQ runs unavailable"
          description="Couldn't load DataQ runs, so the “DQ run” column can't show which runs each pipeline triggered. Pipeline runs below are still accurate."
        />
      )}
      <Flex gap={12} align="flex-end" wrap>
        <Filter label="Provider">
          <Select<'all' | 'adf' | 'airflow'>
            value={provider}
            onChange={setProvider}
            style={{ width: 160 }}
            options={[
              { value: 'all', label: 'All' },
              { value: 'adf', label: 'ADF' },
              { value: 'airflow', label: 'Airflow' },
            ]}
          />
        </Filter>
        <Filter label="Date">
          <Select<DateWindow>
            value={dateWindow}
            onChange={setDateWindow}
            style={{ width: 150 }}
            options={DATE_WINDOWS.map((w) => ({ value: w.value, label: w.label }))}
          />
        </Filter>
      </Flex>
      <Table<PipelineRun>
        rowKey="id"
        columns={columns}
        dataSource={rows}
        pagination={false}
        locale={{ emptyText: <Empty description="No pipeline runs monitored yet." /> }}
      />
    </Flex>
  );
}
