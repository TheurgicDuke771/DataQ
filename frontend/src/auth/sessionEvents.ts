/** A one-event bus for "the server just told us this session is no longer valid". */

type Listener = () => void;

const listeners = new Set<Listener>();

/** Subscribe; returns the unsubscribe. */
export function onSessionInvalidated(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Announce that the session is gone (expired server-side, revoked by a logout elsewhere, or the
 * cookie was cleared).
 */
export function notifySessionInvalidated(): void {
  for (const listener of [...listeners]) listener();
}

/** Test-only: drop every subscription so one spec can't leak into the next. */
export function resetSessionListeners(): void {
  listeners.clear();
}
