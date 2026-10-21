import { Card, Col, Row, Spin, Statistic, Table, Typography } from 'antd';
import { Link } from 'react-router-dom';

import { type FalsePositiveGroup, getCoverageFigures } from '../../api/dashboard';
import { useIsWorkspaceAdmin } from '../../auth/useMe';
import { useAsyncData } from '../../hooks/useAsyncData';

/**
 * How much of the inventory is watched, and how often automatic checks cry wolf (`/dashboard`).
 * Workspace-wide counts, like the panels around it. Every figure carries its denominator, because
 * "0%" over nothing measured and "0%" over forty incidents are different facts.
 */
export function CoveragePanel() {
  const { state } = useAsyncData(() => getCoverageFigures());
  const isAdmin = useIsWorkspaceAdmin();

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
  const byBaseline = f.false_positives_by_baseline ?? [];
  const byConnection = f.false_positives_by_connection ?? [];
  // The coverage settings govern these three only; schema and other checks have none.
  const tunable = byBaseline.some(
    (g) => g.false_positive > 0 && ['volume', 'freshness', 'profile'].includes(g.key),
  );
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
      {byBaseline.length + byConnection.length > 0 && (
        <Row gutter={[24, 16]} style={{ marginTop: 16 }}>
          <Col xs={24} md={12}>
            <Breakdown title="By automatic check" column="Check" groups={byBaseline} />
          </Col>
          <Col xs={24} md={12}>
            <Breakdown title="By connection" column="Connection" groups={byConnection} />
          </Col>
          <Col span={24}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Only checks and connections with a resolved incident that said what it was are listed;
              the rest were not measured.
              {tunable &&
                (isAdmin ? (
                  <>
                    {' '}
                    Row count, freshness and column profile can be made less sensitive in{' '}
                    <Link to="/admin/settings">Admin → Settings → Automatic coverage</Link>, unless
                    a check has been given its own thresholds.
                  </>
                ) : (
                  ' A workspace admin can make row count, freshness and column profile less sensitive.'
                ))}
            </Typography.Text>
          </Col>
        </Row>
      )}
    </Card>
  );
}

function Breakdown({
  title,
  column,
  groups,
}: {
  title: string;
  column: string;
  groups: FalsePositiveGroup[];
}) {
  if (groups.length === 0) return null;
  return (
    <Table<FalsePositiveGroup>
      size="small"
      title={() => <Typography.Text strong>{title}</Typography.Text>}
      components={{ table: (props: object) => <table {...props} aria-label={title} /> }}
      rowKey="key"
      pagination={false}
      dataSource={groups}
      columns={[
        { title: column, dataIndex: 'label' },
        {
          title: 'False positives',
          align: 'right',
          render: (_, g) => `${g.false_positive} of ${g.stated}`,
        },
        { title: 'Rate', align: 'right', render: (_, g) => `${g.rate}%` },
      ]}
    />
  );
}
