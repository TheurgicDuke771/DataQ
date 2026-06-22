import { Card, Flex, Progress, Skeleton, Typography } from 'antd';
import type { ReactNode } from 'react';

import { BRAND } from '../../theme';

/**
 * A single KPI tile on the dashboard (prototype `MetricCard`): a label, a large
 * value, an optional unit suffix, an optional progress bar, and an optional
 * footnote — fronted by a tinted icon chip.
 *
 * The prototype also shows a period-over-period delta ("+0.4%", trend arrow);
 * we omit it deliberately — the summary endpoint has no historical comparison
 * to back it, and a fabricated delta would violate KPI honesty (ADR 0022 /
 * 0018). Add it only once a query backs it.
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
  footnote?: string;
  loading?: boolean;
}

export function MetricCard({
  label,
  value,
  icon,
  unit,
  progress,
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
        {footnote && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {footnote}
          </Typography.Text>
        )}
      </Flex>
    </Card>
  );
}
