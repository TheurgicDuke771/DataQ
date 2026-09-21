import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { RUN_STATUSES, type ResultStatus } from '../../src/api/runs';
import {
  ChecksOutcomeTag,
  ResultStatusTag,
  RunStatusTag,
} from '../../src/components/shared/StatusTag';

// `satisfies` makes a status added to the union a compile error here until it has a glyph test.
const RESULT_STATUSES = Object.keys({
  pass: 0,
  warn: 0,
  fail: 0,
  critical: 0,
  skip: 0,
  error: 0,
} satisfies Record<ResultStatus, 0>) as ResultStatus[];

const glyph = (el: HTMLElement) => el.querySelector('.anticon')?.getAttribute('aria-label');

describe('status tags carry severity without colour', () => {
  it('gives every result status its own glyph', () => {
    const glyphs = RESULT_STATUSES.map((status) => {
      const { container, unmount } = render(<ResultStatusTag status={status} />);
      const g = glyph(container);
      unmount();
      return g;
    });
    expect(glyphs.every(Boolean)).toBe(true);
    expect(new Set(glyphs).size).toBe(RESULT_STATUSES.length);
  });

  it('gives every run status its own glyph', () => {
    const glyphs = RUN_STATUSES.map((status) => {
      const { container, unmount } = render(<RunStatusTag status={status} />);
      const g = glyph(container);
      unmount();
      return g;
    });
    expect(glyphs.every(Boolean)).toBe(true);
    expect(new Set(glyphs).size).toBe(RUN_STATUSES.length);
  });

  it('names the worst severity on the checks chip, which shows only a ratio', () => {
    render(<ChecksOutcomeTag passed={1} total={2} worst="critical" />);
    expect(screen.getByLabelText('1 of 2 checks passed, worst result critical')).toHaveTextContent(
      '1/2',
    );
  });

  it('distinguishes a critical chip from a failed one by glyph, not just colour', () => {
    const a = render(<ChecksOutcomeTag passed={1} total={2} worst="critical" />);
    const critical = glyph(a.container);
    a.unmount();
    const b = render(<ChecksOutcomeTag passed={1} total={2} worst="fail" />);
    expect(glyph(b.container)).not.toBe(critical);
  });

  it('treats a run with no recorded severity as passing', () => {
    render(<ChecksOutcomeTag passed={3} total={3} worst={null} />);
    expect(screen.getByLabelText('3 of 3 checks passed, worst result pass')).toBeInTheDocument();
  });
});
