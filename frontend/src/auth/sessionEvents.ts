/**
 * A one-event bus for "the server just told us this session is no longer valid".
 *
 * Exists purely to break an import cycle. The axios instance (`api/client.ts`) is
 * where a 401 is first observed, and `OtpSessionProvider` is what has to react to
 * it — but the provider calls the API, so the provider must import the client.
 * A leaf module with **no imports of its own** lets the dependency run one way.
 *
 * Only meaningful in `otp` mode: an OIDC 401 is the token layer's business
 * (oidc-client-ts renews or redirects), and dev-bypass has no session to lose.
 */

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
 * Announce that the session is gone (expired server-side, revoked by a logout
 * elsewhere, or the cookie was cleared). Iterates a COPY, so a listener that
 * unsubscribes itself while handling the event can't corrupt the iteration.
 */
export function notifySessionInvalidated(): void {
  for (const listener of [...listeners]) listener();
}

/** Test-only: drop every subscription so one spec can't leak into the next. */
export function resetSessionListeners(): void {
  listeners.clear();
}
