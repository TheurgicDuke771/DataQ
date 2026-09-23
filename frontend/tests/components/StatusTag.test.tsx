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

// The glyph is decorative to assistive tech, so it is identified by antd's per-icon class.
const glyph = (el: HTMLElement) =>
  [...(el.querySelector('.anticon')?.classList ?? [])].find(
    (c) => c.startsWith('anticon-') && c !== 'anticon-spin',
  );

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

  it('states the worst severity on the checks chip as text, since it shows only a ratio', () => {
    const { container } = render(<ChecksOutcomeTag passed={1} total={2} worst="critical" />);
    expect(container).toHaveTextContent('1/2 checks passed, worst result critical');
  });

  it('hides the glyph from assistive tech — antd would announce it by its icon name', () => {
    const { container } = render(<ChecksOutcomeTag passed={1} total={2} worst="critical" />);
    expect(container.querySelector('.anticon')).toHaveAttribute('aria-hidden', 'true');
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('distinguishes a critical chip from a failed one by glyph, not just colour', () => {
    const a = render(<ChecksOutcomeTag passed={1} total={2} worst="critical" />);
    const critical = glyph(a.container);
    a.unmount();
    const b = render(<ChecksOutcomeTag passed={1} total={2} worst="fail" />);
    expect(glyph(b.container)).not.toBe(critical);
  });

  it('treats a run with no recorded severity as passing', () => {
    const { container } = render(<ChecksOutcomeTag passed={3} total={3} worst={null} />);
    expect(container).toHaveTextContent('3/3 checks passed, worst result pass');
  });

  it('renders a status it does not know as a neutral, glyph-less tag rather than throwing', () => {
    const { container } = render(<RunStatusTag status="something-new" />);
    expect(container).toHaveTextContent('something-new');
    expect(container.querySelector('.anticon')).toBeNull();
    const result = render(<ResultStatusTag status="quarantined" />);
    expect(result.container).toHaveTextContent('quarantined');
  });
});
