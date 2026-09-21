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
 * Quality Trends (prototype `QualityTrends`): succeeded vs failed runs per day over the selected
 * window, as a stacked bar.
 */
const FAILED_HATCH_ID = 'dq-hatch-failed';

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
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            // recharts colours each row with the bar's fill, which for Failed is `url(#…)`.
            itemStyle={{ color: 'var(--dq-ink)' }}
            cursor={{ fill: 'rgba(0,0,0,0.04)' }}
          />
          {/* Failed is hatched, not just red: red-on-green is the pair most colour-vision
              deficiencies merge. The legend swatch inherits the hatch from the bar's fill. */}
          <defs>
            <pattern
              id={FAILED_HATCH_ID}
              width={6}
              height={6}
              patternUnits="userSpaceOnUse"
              patternTransform="rotate(45)"
            >
              <rect width={6} height={6} fill={RUN_STATUS_CHART_COLORS.failed} />
              <rect width={2.5} height={6} fill="var(--dq-surface)" fillOpacity={0.75} />
            </pattern>
          </defs>
          <Legend
            iconType="square"
            wrapperStyle={{ fontSize: 13 }}
            formatter={(value: string) => <span style={{ color: 'var(--dq-ink)' }}>{value}</span>}
          />
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
            fill={`url(#${FAILED_HATCH_ID})`}
            stroke={RUN_STATUS_CHART_COLORS.failed}
            radius={[4, 4, 0, 0]}
          />
        </BarChart>
      </ResponsiveChart>
    </Card>
  );
}
