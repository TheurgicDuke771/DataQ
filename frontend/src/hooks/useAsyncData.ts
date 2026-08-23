import { useCallback, useEffect, useState } from 'react';

import { type FailureKind, fetchFailure } from '../utils/errors';

/** Three-state result of an async fetch. */
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
 * Fetch on mount (and on `reload()`), with a cancelled-guard so a late resolution after unmount
 * doesn't set state, and rejection normalised to a string `error`.
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
