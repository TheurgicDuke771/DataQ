import { describe, expect, it } from 'vitest';

import { CREDENTIAL_EXPIRY_WARN_DAYS, expiryLabel, expiryStatus } from '../../src/utils/expiry';

// A fixed "now" — a clock-relative test that passes today and fails in a
// fortnight is the classic way this kind of helper rots unnoticed.
const NOW = new Date('2026-07-25T12:00:00Z').getTime();
const inDays = (d: number) => new Date(NOW + d * 86_400_000).toISOString();

describe('expiryStatus', () => {
  it('says nothing about a credential with no readable expiry', () => {
    // The load-bearing case: unknown must never collapse into "fine". A missing
    // field is the absence of evidence, and #838 exists because the product
    // treated silence as health for six days.
    expect(expiryStatus(null, NOW)).toEqual({ kind: 'unknown' });
    expect(expiryStatus(undefined, NOW)).toEqual({ kind: 'unknown' });
    expect(expiryStatus('not-a-date', NOW)).toEqual({ kind: 'unknown' });
  });

  it('stays quiet while the expiry is comfortably far off', () => {
    expect(expiryStatus(inDays(90), NOW).kind).toBe('ok');
  });

  it('warns inside the window and not one day outside it', () => {
    // Pins the boundary in both directions — a window that silently never fires
    // (or always fires) looks identical to a working one until an outage.
    expect(expiryStatus(inDays(CREDENTIAL_EXPIRY_WARN_DAYS), NOW).kind).toBe('expiring');
    expect(expiryStatus(inDays(CREDENTIAL_EXPIRY_WARN_DAYS + 1), NOW).kind).toBe('ok');
  });

  it('reports an already-dead credential as expired, not merely expiring', () => {
    expect(expiryStatus(inDays(-1), NOW).kind).toBe('expired');
  });

  it('rounds a part-day up, so a deadline never reads as already gone', () => {
    // 18 hours left is "1 day", not "0 days" — rounding a deadline down makes a
    // live credential look dead and sends someone to rotate a working token.
    expect(expiryStatus(new Date(NOW + 18 * 3_600_000).toISOString(), NOW)).toEqual({
      kind: 'expiring',
      daysLeft: 1,
    });
  });
});

describe('expiryLabel', () => {
  it('labels only the states worth interrupting someone for', () => {
    expect(expiryLabel(expiryStatus(inDays(3), NOW))).toBe('credential expires in 3d');
    expect(expiryLabel(expiryStatus(inDays(-2), NOW))).toBe('credential expired');
    expect(expiryLabel(expiryStatus(inDays(90), NOW))).toBeNull();
    expect(expiryLabel(expiryStatus(null, NOW))).toBeNull();
  });
});
