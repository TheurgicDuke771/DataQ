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
    render(<EngineTag />);
    expect(screen.getByText('GX')).toBeInTheDocument();
  });

  it('renders dmf distinctly from gx — the whole point of the badge', () => {
    render(<EngineTag engine="dmf" />);
    expect(screen.getByText('DMF')).toBeInTheDocument();
  });

  it('engineShortLabel uppercases and defaults to gx', () => {
    expect(engineShortLabel(undefined)).toBe('GX');
    expect(engineShortLabel('dmf')).toBe('DMF');
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
});
