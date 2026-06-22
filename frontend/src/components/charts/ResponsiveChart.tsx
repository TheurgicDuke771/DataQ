import { Empty } from 'antd';
import type { ReactElement } from 'react';
import { ResponsiveContainer } from 'recharts';

/**
 * Thin wrapper every dashboard chart mounts through, so they share one height /
 * width contract and one empty state. recharts is only ever imported from here
 * (and the chart widgets that use it), and the chart widgets live on lazy routes
 * (`/dashboard`, run/check detail) — so recharts stays out of the initial bundle
 * and only ships when a chart route loads (ADR 0022).
 *
 * `ResponsiveContainer` needs exactly one child element — pass a single recharts
 * chart (`<BarChart>`, `<LineChart>`, …).
 */
interface ResponsiveChartProps {
  /** A single recharts chart element. */
  children: ReactElement;
  height?: number;
  /** Render the empty state instead of the chart (no data for the range). */
  isEmpty?: boolean;
  emptyText?: string;
}

export function ResponsiveChart({
  children,
  height = 240,
  isEmpty = false,
  emptyText = 'No data for this range yet',
}: ResponsiveChartProps) {
  if (isEmpty) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={emptyText}
        style={{
          height,
          margin: 0,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      />
    );
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      {children}
    </ResponsiveContainer>
  );
}
