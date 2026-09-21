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
  /** Typed loosely on purpose: the API carries `status` as a plain string. */
  status: ResultStatus | (string & {});
  r?: number;
}) {
  const shape = SHAPE[status as ResultStatus] as ((r: number) => string) | undefined;
  const known = shape !== undefined;
  const color = known ? severityColor(status as ResultStatus) : 'var(--dq-severity-neutral)';
  // An unrecognised status degrades to the hollow ring rather than throwing: the app's only
  // error boundary wraps the whole tree.
  if (!known || status === 'pass' || status === 'skip') {
    const hollow = status !== 'pass';
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
      d={shape(big)}
      transform={`translate(${cx},${cy})`}
      fill={status === 'error' ? 'none' : color}
      stroke={status === 'error' ? color : 'var(--dq-surface)'}
      strokeWidth={status === 'error' ? 2 : 1}
      strokeLinecap="round"
      data-status={status}
    />
  );
}

const KEY_ORDER: ResultStatus[] = ['pass', 'warn', 'fail', 'critical', 'skip', 'error'];

/** The key for `StatusMarker` — without it the shapes are a code nobody was given. */
export function StatusMarkerKey({ statuses = KEY_ORDER }: { statuses?: ResultStatus[] }) {
  return (
    <ul
      aria-label="Point shapes by result"
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: '4px 14px',
        justifyContent: 'center',
        listStyle: 'none',
        margin: '4px 0 0',
        padding: 0,
        fontSize: 12,
        color: 'var(--dq-muted)',
      }}
    >
      {KEY_ORDER.filter((s) => statuses.includes(s)).map((s) => (
        <li key={s} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          <svg width={14} height={14} aria-hidden focusable={false}>
            <StatusMarker cx={7} cy={7} status={s} />
          </svg>
          {s}
        </li>
      ))}
    </ul>
  );
}
