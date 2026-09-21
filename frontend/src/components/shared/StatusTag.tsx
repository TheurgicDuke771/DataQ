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

/** Each status has its own glyph, so severity reads without colour (WCAG 1.4.1). Hidden from
 *  assistive tech: antd names an icon after itself ("fire"), and the text beside it already says it. */
const RESULT_STATUS_ICONS: Record<ResultStatus, ReactNode> = {
  pass: <CheckCircleOutlined aria-hidden />,
  warn: <WarningOutlined aria-hidden />,
  fail: <CloseCircleOutlined aria-hidden />,
  critical: <FireOutlined aria-hidden />,
  skip: <MinusCircleOutlined aria-hidden />,
  error: <ExclamationCircleOutlined aria-hidden />,
};

const RUN_STATUS_ICONS: Record<RunStatus, ReactNode> = {
  queued: <ClockCircleOutlined aria-hidden />,
  running: <SyncOutlined spin aria-hidden />,
  succeeded: <CheckCircleOutlined aria-hidden />,
  failed: <CloseCircleOutlined aria-hidden />,
  cancelled: <StopOutlined aria-hidden />,
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
 *  the only place that severity appeared; the glyph and a visually-hidden phrase now carry it too. */
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
    <Tag color={RESULT_STATUS_COLORS[severity]} icon={RESULT_STATUS_ICONS[severity]}>
      {passed}/{total}
      {/* Real text, not aria-label: a role-less span's aria-label is dropped (ARIA: name prohibited). */}
      <span className="dq-sr-only"> checks passed, worst result {severity}</span>
    </Tag>
  );
}
