import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  notifySessionInvalidated,
  onSessionInvalidated,
  resetSessionListeners,
} from '../../src/auth/sessionEvents';

/**
 * The leaf event bus that lets `api/client.ts` tell `OtpSessionProvider` a
 * session is gone without the two importing each other (ADR 0032, #736).
 *
 * Small, but it is the only path a server-side revocation has into the UI — if a
 * listener is dropped or an unsubscribe misses, a revoked session keeps rendering
 * an authenticated shell until the tab is reloaded.
 */
beforeEach(() => resetSessionListeners());

describe('sessionEvents', () => {
  it('notifies every subscriber', () => {
    const a = vi.fn();
    const b = vi.fn();
    onSessionInvalidated(a);
    onSessionInvalidated(b);
    notifySessionInvalidated();
    expect(a).toHaveBeenCalledOnce();
    expect(b).toHaveBeenCalledOnce();
  });

  it('stops notifying after unsubscribe', () => {
    const listener = vi.fn();
    const off = onSessionInvalidated(listener);
    off();
    notifySessionInvalidated();
    expect(listener).not.toHaveBeenCalled();
  });

  it('survives a listener that unsubscribes itself mid-notify', () => {
    // Iterating the live Set would skip the next listener when one removes itself
    // during the callback — so the sibling below is the actual assertion.
    const sibling = vi.fn();
    let off: () => void = () => {};
    off = onSessionInvalidated(() => off());
    onSessionInvalidated(sibling);
    expect(() => notifySessionInvalidated()).not.toThrow();
    expect(sibling).toHaveBeenCalledOnce();
  });

  it('is a no-op with nothing subscribed', () => {
    expect(() => notifySessionInvalidated()).not.toThrow();
  });
});
