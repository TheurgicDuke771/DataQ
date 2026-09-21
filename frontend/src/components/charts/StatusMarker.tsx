import type { ResultStatus } from '../../api/runs';
import { severityColor } from './chartTheme';

/** Marker outline per result status, so a trend point reads without colour: the passing
 *  majority stays a quiet dot and every non-pass outcome gets a distinct silhouette. */
const SHAPE: Record<ResultStatus, (r: number) => string> = {
  pass: () => '',
  warn: (r) => `M0,${-r} L${r},${r} L${-r},${r} Z`, // triangle
  fail: (r) => `M${-r},${-r} H${r} V${r} H${-r} Z`, // square
  critical: (r) => `M0,${-r * 1.3} L${r * 1.3},0 L0,${r * 1.3} L${-r * 1.3},0 Z`, // diamond
  skip: () => '', // hollow ring
  error: (r) => `M${-r},${-r} L${r},${r} M${r},${-r} L${-r},${r}`, // cross
};

export function StatusMarker({
  cx,
  cy,
  status,
  r = 3.5,
}: {
  cx: number;
  cy: number;
  status: ResultStatus;
  r?: number;
}) {
  const color = severityColor(status);
  if (status === 'pass' || status === 'skip') {
    const hollow = status === 'skip';
    return (
      <circle
        cx={cx}
        cy={cy}
        r={r}
        fill={hollow ? 'var(--dq-surface)' : color}
        stroke={hollow ? color : 'var(--dq-surface)'}
        strokeWidth={hollow ? 1.5 : 1}
        data-status={status}
      />
    );
  }
  const big = r * 1.3;
  return (
    <path
      d={SHAPE[status](big)}
      transform={`translate(${cx},${cy})`}
      fill={status === 'error' ? 'none' : color}
      stroke={status === 'error' ? color : 'var(--dq-surface)'}
      strokeWidth={status === 'error' ? 2 : 1}
      strokeLinecap="round"
      data-status={status}
    />
  );
}
