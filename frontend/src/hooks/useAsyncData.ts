import { useCallback, useEffect, useState } from 'react';

import { type FailureKind, fetchFailure } from '../utils/errors';

/** Three-state result of an async fetch. The error branch keeps `error` (the
 *  message, what every consumer reads) and adds the HTTP facts (#910) so a
 *  page-level failure can render the dedicated error page for its status. */
export type AsyncState<T> =
  | { status: 'loading' }
  | { status: 'ok'; data: T }
  | {
      status: 'error';
      error: string;
      kind: FailureKind;
      httpStatus?: number;
      requestId?: string;
    };

/**
 * Fetch on mount (and on `reload()`), with a cancelled-guard so a late
 * resolution after unmount doesn't set state, and rejection normalised to a
 * string `error`. Shared by the data pages (Home, Connections, …) so the
 * cancelled-effect dance lives in one place rather than being re-derived per page.
 *
 * `reload` re-runs the fetcher (e.g. after a mutation) while keeping the current
 * data visible until the refetch resolves — no flash back to the loading state.
 *
 * The fetcher receives an `AbortSignal` (#1107) that fires on unmount AND on
 * every `reload()` (a fresh nonce aborts whatever the previous one started) —
 * a multi-request fetcher (e.g. a paged walk) can check it per iteration to
 * stop issuing further requests once the caller has moved on, instead of
 * relying solely on the `cancelled` guard below to suppress the eventual
 * `setState`. Callers that don't need it can just ignore the parameter.
 */
export function useAsyncData<T>(fetcher: (signal: AbortSignal) => Promise<T>): {
  state: AsyncState<T>;
  reload: () => void;
} {
  const [state, setState] = useState<AsyncState<T>>({ status: 'loading' });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    fetcher(controller.signal)
      .then((data) => {
        if (!cancelled) setState({ status: 'ok', data });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const failure = fetchFailure(err, String(err));
          setState({
            status: 'error',
            error: failure.message,
            kind: failure.kind,
            httpStatus: failure.status,
            requestId: failure.requestId,
          });
        }
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
    // Re-run on mount and whenever `reload` bumps the nonce; the fetcher identity
    // is intentionally not a dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { state, reload };
}
