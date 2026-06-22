import {
  ApiOutlined,
  CheckCircleOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { Alert, Col, Flex, Row, Segmented, Typography } from 'antd';
import { useState } from 'react';

import { getDashboardSummary } from '../api/dashboard';
import { MetricCard } from '../components/dashboard/MetricCard';
import { QualityTrends } from '../components/dashboard/QualityTrends';
import { useAsyncData } from '../hooks/useAsyncData';

/**
 * Enhanced Monitoring Dashboard (`/dashboard`, ADR 0022) — the post-login
 * landing. Phase 2.2 ships the KPI row + range selector; the Quality Trends
 * chart, Suite Performance bars, and Recent Runs table land in 2.3–2.5.
 *
 * Every KPI is backed by the summary endpoint — the prototype's "Total
 * Anomalies" and "Avg. Time to Resolution" tiles are intentionally omitted
 * (no query backs them yet; KPI honesty, ADR 0022 / 0018).
 */

/** Range option label → trailing window in days for the summary query. */
const RANGES = [
  { label: 'Last 24h', days: 1 },
  { label: '7d', days: 7 },
  { label: '30d', days: 30 },
] as const;
type RangeLabel = (typeof RANGES)[number]['label'];

function pct(value: number | null): string | null {
  return value === null ? null : `${value}`;
}

export function Dashboard() {
  const [range, setRange] = useState<RangeLabel>('7d');
  const days = RANGES.find((r) => r.label === range)?.days ?? 7;
  // Re-fetch when the range changes: bump the fetcher via `reload` after the
  // range state updates (both batch into one render → the effect re-runs with
  // the new window).
  const { state, reload } = useAsyncData(() => getDashboardSummary(days));

  const onRangeChange = (value: RangeLabel) => {
    setRange(value);
    reload();
  };

  const loading = state.status === 'loading';
  const summary = state.status === 'ok' ? state.data : null;
  const kpis = summary?.kpis ?? null;

  return (
    <Flex vertical gap={20} style={{ maxWidth: 1200 }}>
      <Flex justify="space-between" align="center" gap={12} wrap>
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            Monitoring Dashboard
          </Typography.Title>
          <Typography.Text type="secondary">Your workspace data-quality health.</Typography.Text>
        </div>
        <Segmented<RangeLabel>
          value={range}
          onChange={onRangeChange}
          options={RANGES.map((r) => r.label)}
        />
      </Flex>

      {state.status === 'error' && (
        <Alert type="error" showIcon message="Failed to load dashboard" description={state.error} />
      )}

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard
            label="Data Integrity Score"
            value={pct(kpis?.health_score ?? null)}
            unit="%"
            icon={<SafetyCertificateOutlined />}
            progress={kpis?.health_score ?? null}
            loading={loading}
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard
            label="Pass Rate"
            value={pct(kpis?.pass_rate ?? null)}
            unit="%"
            icon={<CheckCircleOutlined />}
            progress={kpis?.pass_rate ?? null}
            loading={loading}
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard
            label="Total Runs"
            value={kpis ? kpis.total_runs : null}
            icon={<ThunderboltOutlined />}
            footnote={`Last ${days === 1 ? '24h' : `${days} days`}`}
            loading={loading}
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard
            label="Active Connections"
            value={kpis ? kpis.active_connections : null}
            unit="operational"
            icon={<ApiOutlined />}
            loading={loading}
          />
        </Col>
      </Row>

      <QualityTrends trend={summary?.trend ?? []} />
    </Flex>
  );
}
