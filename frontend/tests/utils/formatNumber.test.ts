import { describe, expect, it } from 'vitest';

import { formatSig, formatTick, fullNumber } from '../../src/utils/formatNumber';

describe('formatSig', () => {
  it('keeps three significant figures of a long float', () => {
    expect(formatSig(0.05875440658049354)).toBe('0.0588');
    expect(formatSig(0.11474469305794606)).toBe('0.115');
    expect(formatSig(2.352266207687894)).toBe('2.35');
    expect(formatSig(0.0028785644444444)).toBe('0.00288');
  });

  it('leaves integers alone, however large', () => {
    expect(formatSig(2100)).toBe('2100');
    expect(formatSig(0)).toBe('0');
    expect(formatSig(1234567)).toBe('1234567');
  });

  it('rounds a large non-integer to a whole number instead of exponent notation', () => {
    expect(formatSig(12345.678)).toBe('12346');
  });

  it('never rounds a tiny non-zero value to a misleading 0', () => {
    expect(formatSig(0.0000123)).toBe('<0.001');
    expect(formatSig(-0.0000123)).toBe('>-0.001');
  });

  it('passes non-finite values through', () => {
    expect(formatSig(Number.NaN)).toBe('NaN');
  });
});

describe('fullNumber', () => {
  it('keeps every digit', () => {
    expect(fullNumber(0.05875440658049354)).toBe('0.05875440658049354');
  });
});

describe('formatTick', () => {
  it('is compact and keeps small fractions readable', () => {
    expect(formatTick(2100)).toBe('2.1K');
    expect(formatTick(0.06)).toBe('0.06');
    expect(formatTick(0.045)).toBe('0.045');
    expect(formatTick(0)).toBe('0');
  });
});
