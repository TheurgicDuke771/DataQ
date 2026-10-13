import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { Check } from '../../src/api/suites';
import {
  hasOwnValues,
  InheritanceNotice,
  InheritedTag,
} from '../../src/components/checks/InheritanceNotice';

const base = {
  id: 'c1',
  suite_id: 's1',
  name: 'Row count is normal',
  kind: 'anomaly',
  origin: 'auto',
  expectation_type: 'monitor:anomaly',
  config: { target_metric: 'row_count', window: 8, min_points: 4, seasonality: true },
  warn_threshold: 3,
  fail_threshold: 4,
  critical_threshold: 6,
  alert_snoozed_until: null,
} as Check;
const inheriting: Check = { ...base, inherited: { sensitivity: 'workspace', baseline: 'default' } };

describe('InheritanceNotice (ADR 0047)', () => {
  it('says where an inheriting check’s numbers come from and what editing does', () => {
    render(<InheritanceNotice check={inheriting} resuming={false} onResume={vi.fn()} />);

    expect(
      screen.getByText('These thresholds come from the coverage settings'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Thresholds: workspace setting\. History: built-in default\./),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('offers the way back on an automatic check with its own values', async () => {
    const onResume = vi.fn();
    render(
      <InheritanceNotice
        check={{ ...base, inherited: null }}
        resuming={false}
        onResume={onResume}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: 'Follow the coverage settings' }));

    expect(onResume).toHaveBeenCalled();
  });

  it.each([
    ['a check a person authored', { ...base, origin: 'user' }],
    ['an automatic schema check', { ...base, kind: 'schema_drift', config: {} }],
    ['an anomaly check on another metric', { ...base, config: { target_metric: 'other' } }],
  ])('shows nothing on %s', (_label, check) => {
    const { container } = render(
      <InheritanceNotice check={check as Check} resuming={false} onResume={vi.fn()} />,
    );

    expect(container).toBeEmptyDOMElement();
    expect(hasOwnValues(check as Check)).toBe(false);
  });

  it('tags only an inheriting check', () => {
    const { rerender } = render(<InheritedTag check={inheriting} />);
    expect(screen.getByText('Inherited thresholds')).toBeInTheDocument();

    rerender(<InheritedTag check={base} />);
    expect(screen.queryByText('Inherited thresholds')).not.toBeInTheDocument();
  });
});
