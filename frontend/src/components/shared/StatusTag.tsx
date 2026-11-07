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
import { Tag, Typography } from 'antd';
import type { ReactNode } from 'react';

import type { ResultStatus, Run, RunStatus } from '../../api/runs';
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

/** `status` is accepted as a plain string because several API shapes carry it that way
 *  (incident evidence, pipeline runs). An unrecognised value renders as a neutral, glyph-less tag. */
export function ResultStatusTag({
  status,
  children,
}: {
  status: ResultStatus | (string & {});
  children?: ReactNode;
}) {
  const known = status as ResultStatus;
  return (
    <Tag color={RESULT_STATUS_COLORS[known] ?? 'default'} icon={RESULT_STATUS_ICONS[known]}>
      {children ?? status}
    </Tag>
  );
}

export function RunStatusTag({ status }: { status: RunStatus | (string & {}) }) {
  const known = status as RunStatus;
  return (
    <Tag color={RUN_STATUS_COLORS[known] ?? 'default'} icon={RUN_STATUS_ICONS[known]}>
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

/** A runs table's "Checks" cell. A `succeeded` run that evaluated no check (none was enabled, or
 *  every one skipped) verified nothing, and says so rather than a bare dash beside a green status. */
export function RunChecksCell({
  run,
}: {
  run: Pick<Run, 'status' | 'checks_total' | 'checks_passed' | 'worst_severity'>;
}) {
  if (run.checks_total > 0) {
    return (
      <ChecksOutcomeTag
        passed={run.checks_passed}
        total={run.checks_total}
        worst={run.worst_severity}
      />
    );
  }
  if (run.status === 'succeeded') {
    return <Typography.Text type="warning">Nothing evaluated</Typography.Text>;
  }
  return <Typography.Text type="secondary">—</Typography.Text>;
}
