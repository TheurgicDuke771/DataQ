import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  DimensionTag,
  EngineTag,
  engineShortLabel,
  formatThresholdsCompact,
} from '../../src/components/checks/checkBadges';

describe('EngineTag (#1551)', () => {
  it('renders gx by name, defaulting when engine is omitted (pre-ADR-0036 fixtures)', () => {
    const { container } = render(<EngineTag />);
    expect(screen.getByText('GX')).toBeInTheDocument();
    expect(container.querySelector('.ant-tag-blue')).not.toBeNull();
  });

  it('renders dmf distinctly from gx — the whole point of the badge', () => {
    const { container } = render(<EngineTag engine="dmf" />);
    expect(screen.getByText('DMF')).toBeInTheDocument();
    expect(container.querySelector('.ant-tag-purple')).not.toBeNull();
  });

  it('defaults an empty-string engine to gx (truthy check, not nullish)', () => {
    const { container } = render(<EngineTag engine="" />);
    expect(screen.getByText('GX')).toBeInTheDocument();
    expect(container.querySelector('.ant-tag-blue')).not.toBeNull();
  });

  it('falls back to a neutral color + the raw name for an engine outside the known set (dqx/dataplex, ADR 0036 §6) — never silently matches gx or dmf', () => {
    const { container } = render(<EngineTag engine="dqx" />);
    expect(screen.getByText('DQX')).toBeInTheDocument();
    expect(container.querySelector('.ant-tag-blue')).toBeNull();
    expect(container.querySelector('.ant-tag-purple')).toBeNull();
    expect(container.querySelector('.ant-tag-default')).not.toBeNull();
  });

  it('engineShortLabel uppercases, defaults to gx, and treats an empty string as falsy', () => {
    expect(engineShortLabel(undefined)).toBe('GX');
    expect(engineShortLabel('dmf')).toBe('DMF');
    expect(engineShortLabel('')).toBe('GX');
  });
});

describe('DimensionTag (ADR 0038)', () => {
  it('renders the classified dimension, title-cased', () => {
    render(<DimensionTag dimension="timeliness" />);
    expect(screen.getByText('Timeliness')).toBeInTheDocument();
  });

  it('renders an explicit "Unclassified" state for null — never hides the coverage gap', () => {
    render(<DimensionTag dimension={null} />);
    expect(screen.getByText('Unclassified')).toBeInTheDocument();
  });

  it('renders "Unclassified" for undefined too (pre-ADR-0038 fixtures)', () => {
    render(<DimensionTag />);
    expect(screen.getByText('Unclassified')).toBeInTheDocument();
  });

  it('renders an unrecognized value verbatim, uncolored — not a colored tag with a silently-empty tooltip', () => {
    const { container } = render(<DimensionTag dimension="legacy_value" />);
    expect(screen.getByText('legacy_value')).toBeInTheDocument();
    expect(container.querySelector('.ant-tag-geekblue')).toBeNull();
  });
});

describe('formatThresholdsCompact', () => {
  it('omits every tier when none are set', () => {
    expect(
      formatThresholdsCompact({
        warn_threshold: null,
        fail_threshold: null,
        critical_threshold: null,
      }),
    ).toBeNull();
  });

  it('renders only the set tiers, in warn/fail/critical order', () => {
    expect(
      formatThresholdsCompact({
        warn_threshold: 5,
        fail_threshold: 10,
        critical_threshold: null,
      }),
    ).toBe('warn 5 · fail 10');
  });

  it('renders all three tiers when all are set', () => {
    expect(
      formatThresholdsCompact({
        warn_threshold: 5,
        fail_threshold: 10,
        critical_threshold: 20,
      }),
    ).toBe('warn 5 · fail 10 · critical 20');
  });

  it('renders an explicit 0 threshold (checks against `!== null`, not truthiness)', () => {
    expect(
      formatThresholdsCompact({
        warn_threshold: 0,
        fail_threshold: null,
        critical_threshold: null,
      }),
    ).toBe('warn 0');
  });
});
