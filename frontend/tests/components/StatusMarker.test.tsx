import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { ResultStatus } from '../../src/api/runs';
import { StatusMarker } from '../../src/components/charts/StatusMarker';

const STATUSES = Object.keys({
  pass: 0,
  warn: 0,
  fail: 0,
  critical: 0,
  skip: 0,
  error: 0,
} satisfies Record<ResultStatus, 0>) as ResultStatus[];

/** Everything about a marker EXCEPT its colour: element, outline path, filled-or-hollow. */
function silhouette(status: ResultStatus): string {
  const { container } = render(
    <svg>
      <StatusMarker cx={10} cy={10} status={status} />
    </svg>,
  );
  const el = container.querySelector('[data-status]');
  if (!el) throw new Error(`no marker for ${status}`);
  const fill = el.getAttribute('fill') ?? '';
  const hollow = fill === 'none' || fill === 'var(--dq-surface)';
  return `${el.tagName}|${el.getAttribute('d') ?? ''}|${hollow ? 'hollow' : 'filled'}`;
}

describe('StatusMarker', () => {
  it('gives every result status a silhouette that differs without colour', () => {
    const shapes = STATUSES.map(silhouette);
    expect(new Set(shapes).size).toBe(STATUSES.length);
  });
});
