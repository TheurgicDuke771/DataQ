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
  type RunListPage,
  type RunStatus,
  RUN_STATUSES,
} from '../api/runs';
import { listSuites } from '../api/suites';
import {
  ORCHESTRATION_PROVIDERS,
  type OrchestrationProvider,
  PROVIDER_LABELS,
} from '../api/triggerBindings';
import { Page } from '../components/layout/Page';
import { RunNowPanel } from '../components/runs/RunNowPanel';
import { ellipsisColumn } from '../components/shared/ellipsisColumn';
import { useAsyncData, type AsyncState } from '../hooks/useAsyncData';
import {
  formatDuration,
  formatTimestamp,
  isWithinWindowDays,
  pipelineRunMarker,
  pipelineStatusColor,
  RESULT_STATUS_COLORS,
  RUN_STATUS_COLORS,
} from '../components/results/resultsFormat';
import { PageError } from '../components/feedback/PageError';
import { WINDOW_PRESETS } from '../components/shared/windowPresets';

const LIST_LIMIT = 200;

// Client-side pagination for the runs / pipeline-runs tables. The API already
// caps the fetch at LIST_LIMIT; this just keeps the on-screen table to one page
// at a time. hideOnSinglePage keeps it invisible until there's a second page.
const ROWS_PER_PAGE = 20;
const tablePagination = (noun: string) => ({
  pageSize: ROWS_PER_PAGE,
  showSizeChanger: false,
  hideOnSinglePage: true,
  showTotal: (total: number) => `${total} ${noun}`,
});

/** Date-window presets for the Results date filter (no true range picker → no
 *  dayjs dependency): Results' own 'all' option prepended to the presets
 *  shared with the Dashboard range selector (`WINDOW_PRESETS`). */
const DATE_WINDOWS = [{ value: 'all', label: 'All time' }, ...WINDOW_PRESETS] as const;
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
  // Runs fetch lifted from the tabs (#349): RunsTab and PipelineRunsTab both
  // need the same `listRuns` page — the latter only to correlate triggered DQ
  // runs — so fetching once here and passing it down avoids two independent
  // `listRuns` calls (antd Tabs lazy-mounts panes, so switching tabs used to
  // mean a second, fresh fetch). This does mean the fetch now starts on page
  // mount rather than on first visit to a given tab — the intended change.
  const { state: runsState, reload: reloadRuns } = useAsyncData(() =>
    listRuns({ limit: LIST_LIMIT }),
  );
  // Last-good runs snapshot (#1114 review). PipelineRunsTab's 30s poll (armed
  // once that tab has been visited — antd keeps panes mounted, #349) refetches
  // this SAME shared state; before the fetch was lifted, a transient poll
  // failure only broke the Pipeline tab's own request and was cosmetic. Now it
  // flips the shared `runsState` to 'error' too, and `useAsyncData`'s error
  // branch carries no data (only its 'ok' branch does) — so track the last
  // successful page here, outside the hook, rather than changing the hook's
  // error contract for every one of its other consumers. RunsTab uses this to
  // keep showing the table (with an inline warning) instead of blanking to a
  // full-page error on a background hiccup.
  const [lastGoodRuns, setLastGoodRuns] = useState<RunListPage | null>(null);
  // Adjust state during render (React's documented pattern for "remember the
  // latest X"), not in an effect — an effect would commit the stale render
  // first and only fix it up a tick later; this lint-clean form updates before
  // the browser paints. `runsState.data` is a fresh array reference only when
  // a fetch actually resolves, so the comparison can't loop.
  if (runsState.status === 'ok' && runsState.data !== lastGoodRuns) {
    setLastGoodRuns(runsState.data);
  }
  return (
    <Page>
      <Flex justify="space-between" align="center" gap={12} wrap>
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
          {
            key: 'runs',
            label: 'Runs',
            children: (
              <RunsTab runsState={runsState} lastGoodRuns={lastGoodRuns} reloadRuns={reloadRuns} />
            ),
          },
          {
            key: 'pipelines',
            label: 'Pipeline runs',
            children: <PipelineRunsTab runsState={runsState} reloadRuns={reloadRuns} />,
          },
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

function RunsTab({
  runsState: state,
  lastGoodRuns,
  reloadRuns,
}: {
  runsState: AsyncState<RunListPage>;
  /** The last successfully-loaded runs page, tracked by the parent (#1114) —
   *  non-null once any fetch has ever succeeded, regardless of `state`'s
   *  current status. */
  lastGoodRuns: RunListPage | null;
  reloadRuns: () => void;
}) {
  // Runs come from the parent (shared with PipelineRunsTab, #349); fetch the
  // accessible suites + connections locally (for id→name and the env /
  // datasource of each suite), then filter everything client-side — cheap at
  // this volume and avoids a refetch per filter change.
  const navigate = useNavigate();
  const { state: suitesState } = useAsyncData(() => listSuites());
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

  // The data to render: the live 'ok' page, or — on 'error' — the last page
  // that DID load, if any (#1114). Only a NEVER-successful load (no snapshot
  // yet) is a full-page failure; a background poll failure after a prior
  // success degrades to an inline warning instead, mirroring `metaFailed`
  // above and `runsJoinFailed` on the Pipeline tab.
  const runsData = state.status === 'ok' ? state.data : lastGoodRuns;
  const backgroundRunsFailed = state.status === 'error' && runsData !== null;
  // Honest truncation (#1108) — the same disclosure the Pipeline tab makes, and
  // the reason `/runs` gained `X-Total-Count` at all. The tab fetches ONE
  // `LIST_LIMIT`-row page, so beyond that the table showed the most recent
  // LIST_LIMIT runs while the footer counted them as the whole story. `total` is
  // the caller's accessible population, unfiltered — so this describes the
  // FETCH, not the filtered rows below it.
  const runsTruncated = runsData !== null && runsData.items.length < runsData.total;

  if (state.status === 'loading') return <Spin description="Loading runs…" size="large" />;
  if (state.status === 'error' && runsData === null) {
    return (
      <PageError
        error={state.error}
        kind={state.kind}
        httpStatus={state.httpStatus}
        requestId={state.requestId}
        onRetry={reloadRuns}
      />
    );
  }

  const windowDays = dateWindow === 'all' ? null : Number(dateWindow);
  const runs = (runsData?.items ?? []).filter((r: Run) => {
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
      {backgroundRunsFailed && (
        <Alert
          type="warning"
          showIcon
          title="Showing the last loaded runs"
          description="A background refresh of the runs list failed, so this table may be slightly out of date. Retrying automatically."
        />
      )}
      {metaFailed && (
        <Alert
          type="warning"
          showIcon
          title="Environment / datasource filters unavailable"
          description="Couldn't load suites or connections, so runs can't be filtered by environment or datasource. All runs are still shown."
        />
      )}
      {runsTruncated && runsData !== null && (
        <Alert
          type="info"
          showIcon
          title={`Loaded the ${runsData.items.length} most recent of ${runsData.total} runs`}
          description="The filters below only narrow what's already loaded, so older runs can't be reached from this page."
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
        scroll={{ x: 'max-content' }}
        rowKey="id"
        columns={columns}
        dataSource={runs}
        pagination={tablePagination('runs')}
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

function PipelineRunsTab({
  runsState,
  reloadRuns,
  pollMs = PIPELINE_POLL_MS,
}: {
  runsState: AsyncState<RunListPage>;
  reloadRuns: () => void;
  pollMs?: number;
}) {
  const navigate = useNavigate();
  // Pipeline runs fetched locally; the DQ runs they may have triggered come
  // from the parent (shared with RunsTab, #349). Both auto-refreshed so a
  // newly triggered run shows up against its pipeline run without a manual
  // reload.
  const { state, reload } = useAsyncData(() => listPipelineRuns({ limit: LIST_LIMIT }));
  const [provider, setProvider] = useState<'all' | OrchestrationProvider>('all');
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
    for (const r of runsState.data.items) {
      if (!r.triggered_by) continue;
      const list = map.get(r.triggered_by);
      if (list) list.push(r);
      else map.set(r.triggered_by, [r]);
    }
    return map;
  }, [runsState]);

  if (state.status === 'loading') return <Spin description="Loading pipeline runs…" size="large" />;
  if (state.status === 'error') {
    return (
      <PageError
        error={state.error}
        kind={state.kind}
        httpStatus={state.httpStatus}
        requestId={state.requestId}
        onRetry={reload}
      />
    );
  }

  const { items: pipelineRuns, total } = state.data;
  // Honest truncation (#1108): the tab fetches a single `LIST_LIMIT`-row page,
  // so on a monitored population bigger than that the table was silently
  // showing "everything" when it was really the most recent LIST_LIMIT rows.
  // `total` is the WHOLE monitored population from `X-Total-Count` — the request
  // sends no provider/date filter, so it is deliberately not filter-scoped, and
  // the note below must therefore describe the FETCH, never the filtered table.
  const truncated = pipelineRuns.length < total;

  const windowDays = dateWindow === 'all' ? null : Number(dateWindow);
  const rows = pipelineRuns.filter((p) => {
    if (provider !== 'all' && p.provider !== provider) return false;
    if (windowDays !== null && !isWithinWindowDays(p.started_at ?? p.created_at, windowDays))
      return false;
    return true;
  });

  const columns: ColumnsType<PipelineRun> = [
    {
      title: 'Provider',
      dataIndex: 'provider',
      width: 140,
      render: (p: OrchestrationProvider) => <Tag>{PROVIDER_LABELS[p]}</Tag>,
    },
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
    ellipsisColumn<PipelineRun>('Failure reason', 'failure_reason', 260),
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
          title="Triggered DQ runs unavailable"
          description="Couldn't load DataQ runs, so the “DQ run” column can't show which runs each pipeline triggered. Pipeline runs below are still accurate."
        />
      )}
      {truncated && (
        <Alert
          type="info"
          showIcon
          title={`Loaded the ${pipelineRuns.length} most recent of ${total} pipeline runs`}
          // Deliberately NOT "narrow the filters to see older runs": the provider
          // and date selects below are client-side over this already-loaded page,
          // so no filter choice can reach the runs that were never fetched.
          description="The filters below only narrow what's already loaded, so older pipeline runs can't be reached from this page."
        />
      )}
      <Flex gap={12} align="flex-end" wrap>
        <Filter label="Provider">
          <Select<'all' | OrchestrationProvider>
            value={provider}
            onChange={setProvider}
            style={{ width: 180 }}
            aria-label="Provider"
            options={[
              { value: 'all', label: 'All' },
              ...ORCHESTRATION_PROVIDERS.map((p) => ({ value: p, label: PROVIDER_LABELS[p] })),
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
        scroll={{ x: 'max-content' }}
        rowKey="id"
        columns={columns}
        dataSource={rows}
        pagination={tablePagination('pipeline runs')}
        locale={{ emptyText: <Empty description="No pipeline runs monitored yet." /> }}
      />
    </Flex>
  );
}
