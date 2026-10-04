import { Card, Col, Row, Spin, Statistic, Typography } from 'antd';

import { getCoverageFigures } from '../../api/dashboard';
import { useAsyncData } from '../../hooks/useAsyncData';

/**
 * How much of the inventory is watched, and how often automatic checks cry wolf (`/dashboard`).
 * Workspace-wide counts, like the panels around it. Every figure carries its denominator, because
 * "0%" over nothing measured and "0%" over forty incidents are different facts.
 */
export function CoveragePanel() {
  const { state } = useAsyncData(() => getCoverageFigures());

  if (state.status === 'loading') return <Spin />;
  if (state.status === 'error') {
    return (
      <Typography.Text type="secondary">
        Coverage figures are unavailable right now.
      </Typography.Text>
    );
  }
  const f = state.data;
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;
  return (
    <Card size="small" title="Coverage, across the workspace" data-testid="coverage-panel">
      <Row gutter={[24, 16]}>
        <Col xs={24} md={12}>
          <Statistic
            title={`Assets watched in the last ${f.coverage_window_days} days`}
            value={f.coverage_pct ?? '—'}
            suffix={f.coverage_pct === null ? undefined : '%'}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {f.assets_total === 0
              ? 'No assets in the inventory yet.'
              : `${f.assets_watched} of ${plural(f.assets_total, 'asset')} had a suite complete a run: ${f.assets_watched_authored} with authored checks, ${f.assets_watched_auto_only} by automatic coverage only.`}
          </Typography.Text>
        </Col>
        <Col xs={24} md={12}>
          <Statistic
            title={`False positives, automatic checks, last ${f.false_positive_window_days} days`}
            value={f.false_positive_rate ?? '—'}
            suffix={f.false_positive_rate === null ? undefined : '%'}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {f.stated === 0
              ? `Not measured: ${f.resolved === 0 ? 'no incident on an automatic suite was resolved by a person' : `${plural(f.resolved, 'incident')} resolved, none said what it turned out to be`}.`
              : `${f.false_positive} of ${plural(f.stated, 'resolved incident')} marked a false positive.${f.unstated > 0 ? ` ${f.unstated} more resolved without saying.` : ''}`}
          </Typography.Text>
        </Col>
      </Row>
    </Card>
  );
}
