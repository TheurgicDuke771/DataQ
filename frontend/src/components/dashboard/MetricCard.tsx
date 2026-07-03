import { FallOutlined, RiseOutlined } from '@ant-design/icons';
import { Card, Flex, Progress, Skeleton, Typography } from 'antd';
import type { ReactNode } from 'react';

import { BRAND } from '../../theme';

/**
 * A single KPI tile on the dashboard (prototype `MetricCard`): a label, a large
 * value, an optional unit suffix, an optional progress bar, an optional
 * period-over-period delta, and an optional footnote — fronted by a tinted
 * icon chip.
 *
 * The delta (#352) is backed by a real prior-window aggregate from the summary
 * endpoint — `null`/omitted renders nothing rather than a fabricated 0 (KPI
 * honesty, ADR 0022 / 0018). `deltaGoodWhen` colours it by whether the
 * movement is an improvement ('down' for durations).
 *
 * `value === null` renders an em dash (no data for the window) rather than 0,
 * so an empty workspace reads as "nothing yet", not "scored zero".
 */
interface MetricCardProps {
  label: string;
  /** Pre-formatted display value, or `null` for the no-data em dash. */
  value: ReactNode | null;
  icon: ReactNode;
  /** Trailing unit shown next to the value (e.g. "%", "operational"). */
  unit?: string;
  /** 0–100 progress bar under the value; omit for plain count metrics. */
  progress?: number | null;
  /** Period-over-period delta; `null`/omitted → not rendered. */
  delta?: number | null;
  /** Suffix on the delta value (e.g. "%" for %-change, " pts" for points). */
  deltaUnit?: string;
  /** Which direction is an improvement (default 'up'; 'down' for durations). */
  deltaGoodWhen?: 'up' | 'down';
  footnote?: string;
  loading?: boolean;
}

const DELTA_GOOD = '#3f8600';
const DELTA_BAD = '#cf1322';

function DeltaBadge({
  delta,
  unit,
  goodWhen,
}: {
  delta: number;
  unit: string;
  goodWhen: 'up' | 'down';
}) {
  const up = delta > 0;
  const improved = delta === 0 ? null : up === (goodWhen === 'up');
  const color = improved === null ? undefined : improved ? DELTA_GOOD : DELTA_BAD;
  return (
    <Typography.Text
      style={{ fontSize: 12, color }}
      type={improved === null ? 'secondary' : undefined}
    >
      {up ? <RiseOutlined /> : delta < 0 ? <FallOutlined /> : null}{' '}
      {`${up ? '+' : ''}${delta}${unit} vs prior period`}
    </Typography.Text>
  );
}

export function MetricCard({
  label,
  value,
  icon,
  unit,
  progress,
  delta,
  deltaUnit = '%',
  deltaGoodWhen = 'up',
  footnote,
  loading = false,
}: MetricCardProps) {
  return (
    <Card size="small" style={{ height: '100%' }}>
      <Flex vertical gap={12}>
        <Flex justify="space-between" align="flex-start" gap={8}>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            {label}
          </Typography.Text>
          <span
            aria-hidden
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 32,
              height: 32,
              borderRadius: 8,
              background: BRAND.selectedBg,
              color: BRAND.primary,
              flexShrink: 0,
            }}
          >
            {icon}
          </span>
        </Flex>

        {loading ? (
          <Skeleton.Input active size="large" style={{ width: 96 }} />
        ) : (
          <Flex align="baseline" gap={6}>
            <Typography.Title level={2} style={{ margin: 0, lineHeight: 1, color: BRAND.ink }}>
              {value === null ? '—' : value}
            </Typography.Title>
            {unit && value !== null && (
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                {unit}
              </Typography.Text>
            )}
          </Flex>
        )}

        {progress != null && !loading && (
          <Progress percent={Math.round(progress)} showInfo={false} size="small" />
        )}
        {delta != null && !loading && value !== null && (
          <DeltaBadge delta={delta} unit={deltaUnit} goodWhen={deltaGoodWhen} />
        )}
        {footnote && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {footnote}
          </Typography.Text>
        )}
      </Flex>
    </Card>
  );
}
