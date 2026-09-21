import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  FireOutlined,
  MinusCircleOutlined,
  StopOutlined,
  SyncOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { Tag } from 'antd';
import type { ReactNode } from 'react';

import type { ResultStatus, RunStatus } from '../../api/runs';
import { RESULT_STATUS_COLORS, RUN_STATUS_COLORS } from '../results/resultsFormat';

/** Each status has its own glyph, so severity reads without colour (WCAG 1.4.1). */
const RESULT_STATUS_ICONS: Record<ResultStatus, ReactNode> = {
  pass: <CheckCircleOutlined />,
  warn: <WarningOutlined />,
  fail: <CloseCircleOutlined />,
  critical: <FireOutlined />,
  skip: <MinusCircleOutlined />,
  error: <ExclamationCircleOutlined />,
};

const RUN_STATUS_ICONS: Record<RunStatus, ReactNode> = {
  queued: <ClockCircleOutlined />,
  running: <SyncOutlined spin />,
  succeeded: <CheckCircleOutlined />,
  failed: <CloseCircleOutlined />,
  cancelled: <StopOutlined />,
};

export function ResultStatusTag({
  status,
  children,
}: {
  status: ResultStatus;
  children?: ReactNode;
}) {
  return (
    <Tag color={RESULT_STATUS_COLORS[status]} icon={RESULT_STATUS_ICONS[status]}>
      {children ?? status}
    </Tag>
  );
}

export function RunStatusTag({ status }: { status: RunStatus }) {
  return (
    <Tag color={RUN_STATUS_COLORS[status]} icon={RUN_STATUS_ICONS[status]}>
      {status}
    </Tag>
  );
}

/** The runs tables' `passed/total` chip. Its colour is the run's worst severity and used to be
 *  the only place that severity appeared; the glyph and the accessible name now carry it too. */
export function ChecksOutcomeTag({
  passed,
  total,
  worst,
}: {
  passed: number;
  total: number;
  worst: ResultStatus | null;
}) {
  const severity = worst ?? 'pass';
  return (
    <Tag
      color={RESULT_STATUS_COLORS[severity]}
      icon={RESULT_STATUS_ICONS[severity]}
      aria-label={`${passed} of ${total} checks passed, worst result ${severity}`}
    >
      {passed}/{total}
    </Tag>
  );
}
