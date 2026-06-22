import { Alert, Spin } from 'antd';
import { CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts';

import { listCheckHistory } from '../../api/suites';
import { useAsyncData } from '../../hooks/useAsyncData';
import {
  AXIS_TICK,
  CHART_COLORS,
  GRID_PROPS,
  severityColor,
  TOOLTIP_STYLE,
} from '../charts/chartTheme';
import type { ResultStatus } from '../../api/runs';
import { ResponsiveChart } from '../charts/ResponsiveChart';

/**
 * Per-check historical trend (Phase 2.6, ADR 0022): a check's `metric_value`
 * over its recent runs, as a recharts line with each point coloured by that
 * run's severity. Lazily fetched per check (only when a run-detail row expands),
 * so it doesn't fan out a request per check on page load.
 *
 * Empty when the check has no runs, or records no metric (e.g. a binary
 * pass/fail check) — there's nothing to plot.
 */
interface CheckTrendProps {
  suiteId: string;
  checkId: string;
}

/** ISO timestamp → short `Jun 13` label. */
function shortDay(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: '2-digit' });
}

export function CheckTrend({ suiteId, checkId }: CheckTrendProps) {
  const { state } = useAsyncData(() => listCheckHistory(suiteId, checkId));

  if (state.status === 'loading') return <Spin size="small" />;
  if (state.status === 'error') {
    return <Alert type="error" showIcon message="Failed to load trend" description={state.error} />;
  }

  const withMetric = state.data.filter((p) => p.metric_value !== null);
  const data = withMetric.map((p) => ({
    label: shortDay(p.created_at),
    metric: p.metric_value,
    status: p.status as ResultStatus,
  }));

  return (
    <ResponsiveChart height={180} isEmpty={data.length === 0} emptyText="No metric history yet">
      <LineChart data={data} margin={{ top: 8, right: 12, left: -16, bottom: 0 }}>
        <CartesianGrid {...GRID_PROPS} vertical={false} />
        <XAxis dataKey="label" tick={AXIS_TICK} tickLine={false} minTickGap={24} />
        <YAxis tick={AXIS_TICK} tickLine={false} width={40} />
        <Tooltip contentStyle={TOOLTIP_STYLE} />
        <Line
          type="monotone"
          dataKey="metric"
          name="Metric"
          stroke={CHART_COLORS.primary}
          strokeWidth={2}
          dot={(props) => {
            const { cx, cy, payload, index } = props as {
              cx: number;
              cy: number;
              index: number;
              payload: { status: ResultStatus };
            };
            return (
              <circle
                key={index}
                cx={cx}
                cy={cy}
                r={3.5}
                fill={severityColor(payload.status)}
                stroke="#fff"
                strokeWidth={1}
              />
            );
          }}
        />
      </LineChart>
    </ResponsiveChart>
  );
}
