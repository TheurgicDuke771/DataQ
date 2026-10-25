import { ArrowLeftOutlined, SwapOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Flex, Radio, Spin, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import {
  compareRuns,
  triggeredByText,
  type ComparisonRow,
  type ComparisonRun,
  type ComparisonSide,
  type RunComparison,
  type StatusChange,
} from '../api/runs';
import { getSuite } from '../api/suites';
import { DimensionTag, EngineTag } from '../components/checks/checkBadges';
import { AsOfCell } from '../components/results/checkAsOf';
import { PageError } from '../components/feedback/PageError';
import { Page } from '../components/layout/Page';
import { formatMetricDelta, formatTimestamp } from '../components/results/resultsFormat';
import { ScalarValue } from '../components/results/ScalarValue';
import { boundedTextStyle } from '../components/shared/ellipsisColumn';
import { ResultStatusTag, RunStatusTag } from '../components/shared/StatusTag';
import { useAsyncData } from '../hooks/useAsyncData';

/** The row filters, in display order. `all` is every row; the others match `delta.status_change`,
 *  with `changed` covering every status transition (improved, regressed, or through skip/error). */
const FILTERS = ['all', 'changed', 'regressed', 'improved', 'added', 'removed'] as const;
type Filter = (typeof FILTERS)[number];

const FILTER_LABEL: Record<Filter, string> = {
  all: 'All',
  changed: 'Changed',
  regressed: 'Regressed',
  improved: 'Improved',
  added: 'Added',
  removed: 'Removed',
};

const CHANGE_LABEL: Record<StatusChange, string> = {
  improved: 'Improved',
  regressed: 'Regressed',
  changed: 'Changed',
  same: 'Same',
  added: 'Added',
  removed: 'Removed',
};

/** Antd preset tag colours — theme tokens in both modes, like `RESULT_STATUS_COLORS`. */
const CHANGE_COLOR: Record<StatusChange, string> = {
  improved: 'success',
  regressed: 'error',
  changed: 'warning',
  same: 'default',
  added: 'processing',
  removed: 'default',
};

function matchesFilter(row: ComparisonRow, filter: Filter): boolean {
  const change = row.delta.status_change;
  if (filter === 'all') return true;
  if (filter === 'changed')
    return change === 'improved' || change === 'regressed' || change === 'changed';
  return change === filter;
}

/**
 * Routed run-comparison page (`/results/:runId/compare/:otherRunId`): two runs of one suite diffed
 * check by check, each side rendered as the check was at that run's time.
 */
export function RunCompare() {
  const { runId, otherRunId } = useParams<{ runId: string; otherRunId: string }>();
  // Keyed on the pair: "Swap sides" stays on this route, and the fetch must follow the params.
  return <RunCompareLoader key={`${runId}/${otherRunId}`} runId={runId} otherRunId={otherRunId} />;
}

function RunCompareLoader({ runId, otherRunId }: { runId?: string; otherRunId?: string }) {
  const navigate = useNavigate();

  const { state } = useAsyncData(async () => {
    if (!runId || !otherRunId) throw new Error('no run');
    const comparison = await compareRuns(runId, otherRunId);
    const suite = await getSuite(comparison.base.suite_id).catch(() => null);
    return { comparison, suiteName: suite?.name ?? null };
  });

  useEffect(() => {
    if (state.status !== 'ok') return;
    const previous = document.title;
    document.title = `Compare runs — ${state.data.suiteName ?? 'suite'} · DataQ`;
    return () => {
      document.title = previous;
    };
  }, [state]);

  return (
    <div data-testid="rc-screen">
      <Page gap={16}>
        <div>
          <Button
            type="text"
            icon={<ArrowLeftOutlined />}
            onClick={() => navigate('/results')}
            style={{ paddingLeft: 0 }}
          >
            Results
          </Button>
        </div>
        {state.status === 'loading' && <Spin description="Loading comparison…" size="large" />}
        {state.status === 'error' && (
          <PageError
            error={state.error}
            kind={state.kind}
            httpStatus={state.httpStatus}
            requestId={state.requestId}
          />
        )}
        {state.status === 'ok' && (
          <RunCompareBody comparison={state.data.comparison} suiteName={state.data.suiteName} />
        )}
      </Page>
    </div>
  );
}

function RunCompareBody({
  comparison,
  suiteName,
}: {
  comparison: RunComparison;
  suiteName: string | null;
}) {
  const navigate = useNavigate();
  const [filter, setFilter] = useState<Filter>('all');
  const { base, target, summary, rows } = comparison;
  const visible = useMemo(() => rows.filter((r) => matchesFilter(r, filter)), [rows, filter]);
  const partial = !base.results_final || !target.results_final;

  return (
    <Flex vertical gap={16}>
      <Flex justify="space-between" align="center" gap={12} wrap>
        <Typography.Title level={3} style={{ margin: 0 }}>
          Compare runs{suiteName ? ` — ${suiteName}` : ''}
        </Typography.Title>
        <Button
          icon={<SwapOutlined />}
          onClick={() => navigate(`/results/${target.id}/compare/${base.id}`)}
        >
          Swap sides
        </Button>
      </Flex>

      {partial && (
        <Alert
          type="warning"
          showIcon
          title="One of these runs has not finished"
          description="Its side of the comparison is partial: a check it has not reached yet reads as removed or added until the run completes."
        />
      )}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
          gap: 12,
        }}
      >
        <RunCard side="Base" run={base} />
        <RunCard side="Target" run={target} />
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
          gap: 12,
        }}
      >
        <Stat label="Changed">{summary.changed}</Stat>
        <Stat label="Regressed">{summary.regressed}</Stat>
        <Stat label="Improved">{summary.improved}</Stat>
        <Stat label="Added">{summary.added}</Stat>
        <Stat label="Removed">{summary.removed}</Stat>
        <Stat label="Metric changed">{summary.metric_changed}</Stat>
        <Stat label="Unchanged">{summary.unchanged}</Stat>
      </div>

      <Flex align="center" gap={12} wrap>
        <Radio.Group
          aria-label="Filter comparison rows"
          optionType="button"
          value={filter}
          onChange={(e) => setFilter(e.target.value as Filter)}
          options={FILTERS.map((f) => ({ value: f, label: FILTER_LABEL[f] }))}
        />
        {/* Announces the filter's effect without moving focus (rule 44: changed status is announced). */}
        <Typography.Text type="secondary" role="status" aria-live="polite">
          Showing {visible.length} of {rows.length} checks
        </Typography.Text>
      </Flex>

      <ComparisonTable rows={visible} />
    </Flex>
  );
}

function RunCard({ side, run }: { side: 'Base' | 'Target'; run: ComparisonRun }) {
  return (
    <Card
      size="small"
      title={
        <Flex align="center" gap={8} wrap>
          <span>{side}</span>
          <RunStatusTag status={run.status} />
          {!run.results_final && <Tag color="warning">results not final</Tag>}
        </Flex>
      }
      extra={<Link to={`/results/${run.id}`}>Open {side.toLowerCase()} run</Link>}
    >
      <Flex vertical gap={4}>
        <Typography.Text>
          Started {formatTimestamp(run.started_at ?? run.created_at)}
        </Typography.Text>
        <Typography.Text>
          Checks passed{' '}
          {run.checks_total === 0 ? '—' : `${run.checks_passed} / ${run.checks_total}`}
        </Typography.Text>
        <Typography.Text type="secondary">Triggered by {triggeredByText(run)}</Typography.Text>
      </Flex>
    </Card>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Card size="small" style={{ height: '100%' }}>
      <Flex vertical gap={4}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {label}
        </Typography.Text>
        <span style={{ fontSize: 18 }}>{children}</span>
      </Flex>
    </Card>
  );
}

function SideStatus({ side }: { side: ComparisonSide | null }) {
  if (!side) {
    return (
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        not in this run
      </Typography.Text>
    );
  }
  return <ResultStatusTag status={side.status} />;
}

function ComparisonTable({ rows }: { rows: ComparisonRow[] }) {
  const columns: ColumnsType<ComparisonRow> = [
    {
      title: 'Check',
      key: 'check',
      render: (_, row) => {
        const shown = row.target?.check ?? row.base?.check;
        const renamedFrom =
          row.base && row.target && row.base.check.name !== row.target.check.name
            ? row.base.check.name
            : null;
        return (
          <Flex vertical gap={2} style={{ minWidth: 0 }}>
            <span style={boundedTextStyle(280)} title={row.label}>
              {row.label}
            </span>
            {renamedFrom && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                was “{renamedFrom}” in the base run
              </Typography.Text>
            )}
            {shown?.expectation_type && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {shown.expectation_type}
                {shown.column ? ` · ${shown.column}` : ''}
              </Typography.Text>
            )}
          </Flex>
        );
      },
    },
    {
      title: 'Dimension',
      key: 'dimension',
      render: (_, row) => {
        const check = (row.target ?? row.base)?.check;
        return (
          <AsOfCell source={check?.resolved_from ?? undefined}>
            <DimensionTag dimension={check?.dimension} />
          </AsOfCell>
        );
      },
    },
    {
      title: 'Engine',
      key: 'engine',
      render: (_, row) => {
        const check = (row.target ?? row.base)?.check;
        return (
          <AsOfCell source={check?.resolved_from ?? undefined}>
            <EngineTag engine={check?.engine} />
          </AsOfCell>
        );
      },
    },
    { title: 'Base', key: 'base', render: (_, row) => <SideStatus side={row.base} /> },
    { title: 'Target', key: 'target', render: (_, row) => <SideStatus side={row.target} /> },
    {
      title: 'Change',
      key: 'change',
      render: (_, row) => (
        <Tag color={CHANGE_COLOR[row.delta.status_change]}>
          {CHANGE_LABEL[row.delta.status_change]}
        </Tag>
      ),
    },
    {
      title: 'Base metric',
      key: 'base_metric',
      align: 'right',
      render: (_, row) => <ScalarValue value={row.base?.metric_value ?? null} />,
    },
    {
      title: 'Target metric',
      key: 'target_metric',
      align: 'right',
      render: (_, row) => <ScalarValue value={row.target?.metric_value ?? null} />,
    },
    {
      title: 'Δ metric',
      key: 'delta',
      align: 'right',
      render: (_, row) =>
        row.delta.metric_changed && row.delta.metric_delta === null ? (
          // Both sides exist and only one carries a metric: changed, but no difference to state.
          <Typography.Text type="secondary">one side only</Typography.Text>
        ) : (
          <Typography.Text type={row.delta.metric_changed ? undefined : 'secondary'}>
            {formatMetricDelta(row.delta.metric_delta, row.delta.metric_delta_pct)}
          </Typography.Text>
        ),
    },
  ];

  return (
    <Table<ComparisonRow>
      rowKey="check_id"
      size="small"
      columns={columns}
      dataSource={rows}
      pagination={false}
      scroll={{ x: 'max-content' }}
      locale={{ emptyText: 'No checks match this filter.' }}
      expandable={{
        rowExpandable: (row) => Boolean(row.base?.observed_value || row.target?.observed_value),
        expandedRowRender: (row) => (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
              gap: 12,
            }}
          >
            <SideDetail side="Base" value={row.base} />
            <SideDetail side="Target" value={row.target} />
          </div>
        ),
      }}
    />
  );
}

function SideDetail({ side, value }: { side: 'Base' | 'Target'; value: ComparisonSide | null }) {
  return (
    <Flex vertical gap={4}>
      <Typography.Text strong>{side}</Typography.Text>
      {value ? (
        <>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {value.check.name ?? '—'} · {value.check.expectation_type ?? '—'}
            {value.check.column ? ` · ${value.check.column}` : ''}
          </Typography.Text>
          <span>
            Observed: <ScalarValue value={value.observed_value} />
          </span>
        </>
      ) : (
        <Typography.Text type="secondary">not in this run</Typography.Text>
      )}
    </Flex>
  );
}
