import { Card, Empty, Flex, Progress, Typography } from 'antd';

import type { PerformanceState, SuitePerformance as SuitePerf } from '../../api/dashboard';
import { SEVERITY_SCALE } from '../../theme';

/**
 * Suite Performance (prototype `SuitePerformance`): per-suite health from each suite's latest run,
 * worst-first (the order the summary endpoint returns).
 */
interface SuitePerformanceProps {
  suites: SuitePerf[];
}

const STATE_COLOR: Record<PerformanceState, string> = {
  optimal: SEVERITY_SCALE.good,
  stable: SEVERITY_SCALE.warning,
  critical: SEVERITY_SCALE.bad,
  unknown: SEVERITY_SCALE.neutral,
};

const STATE_LABEL: Record<PerformanceState, string> = {
  optimal: 'Optimal',
  stable: 'Stable',
  critical: 'Critical',
  unknown: 'No data',
};

export function SuitePerformance({ suites }: SuitePerformanceProps) {
  return (
    <Card size="small" style={{ height: '100%' }}>
      <Flex vertical gap={4} style={{ marginBottom: 16 }}>
        <Typography.Text strong style={{ fontSize: 16 }}>
          Suite Performance
        </Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          Health by suite, worst first
        </Typography.Text>
      </Flex>

      {suites.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No suites with runs yet" />
      ) : (
        <Flex vertical gap={16}>
          {suites.map((s) => (
            <div key={s.suite_id}>
              <Flex justify="space-between" align="center" style={{ marginBottom: 6 }} gap={8}>
                <Typography.Text strong ellipsis style={{ fontSize: 14 }}>
                  {s.name}
                </Typography.Text>
                <Typography.Text strong style={{ fontSize: 13, color: STATE_COLOR[s.state] }}>
                  {STATE_LABEL[s.state]}
                </Typography.Text>
              </Flex>
              <Progress
                percent={s.score ?? 0}
                showInfo={s.score !== null}
                format={(p) => `${p}`}
                strokeColor={STATE_COLOR[s.state]}
                size="small"
              />
            </div>
          ))}
        </Flex>
      )}
    </Card>
  );
}
