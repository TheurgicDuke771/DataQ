import { Card, Flex, Typography } from 'antd';
import { Bar, BarChart, CartesianGrid, Legend, Tooltip, XAxis, YAxis } from 'recharts';

import type { TrendPoint } from '../../api/dashboard';
import {
  AXIS_TICK,
  GRID_PROPS,
  RUN_STATUS_CHART_COLORS,
  TOOLTIP_STYLE,
} from '../charts/chartTheme';
import { ResponsiveChart } from '../charts/ResponsiveChart';

/**
 * Quality Trends (prototype `QualityTrends`): succeeded vs failed runs per day
 * over the selected window, as a stacked bar. Maps the summary's `trend` (a
 * zero-filled daily series, ADR 0022) onto the run-status chart palette so the
 * bars read the same green/red as the run-status tags elsewhere.
 *
 * Rendered in a light card rather than the prototype's dark panel — the app's
 * theme is a light canvas (theme.ts) and a single dark card would fight it; the
 * data, palette, and legend match the prototype.
 */
interface QualityTrendsProps {
  trend: TrendPoint[];
}

/** ISO `YYYY-MM-DD` → short `Jun 13` axis label. */
function shortDay(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return d.toLocaleDateString('en-US', { month: 'short', day: '2-digit', timeZone: 'UTC' });
}

export function QualityTrends({ trend }: QualityTrendsProps) {
  const isEmpty = trend.every((p) => p.succeeded === 0 && p.failed === 0);
  const data = trend.map((p) => ({ ...p, label: shortDay(p.day) }));

  return (
    <Card size="small" style={{ height: '100%' }}>
      <Flex vertical gap={4} style={{ marginBottom: 8 }}>
        <Typography.Text strong style={{ fontSize: 16 }}>
          Quality Trends
        </Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          Succeeded vs failed runs per day
        </Typography.Text>
      </Flex>
      <ResponsiveChart height={260} isEmpty={isEmpty} emptyText="No runs in this range yet">
        <BarChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
          <CartesianGrid {...GRID_PROPS} vertical={false} />
          <XAxis dataKey="label" tick={AXIS_TICK} tickLine={false} minTickGap={24} />
          <YAxis tick={AXIS_TICK} tickLine={false} allowDecimals={false} width={36} />
          <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: 'rgba(0,0,0,0.04)' }} />
          <Legend iconType="circle" wrapperStyle={{ fontSize: 13 }} />
          <Bar
            dataKey="succeeded"
            stackId="runs"
            name="Succeeded"
            fill={RUN_STATUS_CHART_COLORS.succeeded}
            radius={[0, 0, 0, 0]}
          />
          <Bar
            dataKey="failed"
            stackId="runs"
            name="Failed"
            fill={RUN_STATUS_CHART_COLORS.failed}
            radius={[4, 4, 0, 0]}
          />
        </BarChart>
      </ResponsiveChart>
    </Card>
  );
}
