import { useCallback, useEffect, useState } from 'react';

/** Three-state result of an async fetch. */
export type AsyncState<T> =
  | { status: 'loading' }
  | { status: 'ok'; data: T }
  | { status: 'error'; error: string };

/**
 * Fetch on mount (and on `reload()`), with a cancelled-guard so a late
 * resolution after unmount doesn't set state, and rejection normalised to a
 * string `error`. Shared by the data pages (Home, Connections, …) so the
 * cancelled-effect dance lives in one place rather than being re-derived per page.
 *
 * `reload` re-runs the fetcher (e.g. after a mutation) while keeping the current
 * data visible until the refetch resolves — no flash back to the loading state.
 */
export function useAsyncData<T>(fetcher: () => Promise<T>): {
  state: AsyncState<T>;
  reload: () => void;
} {
  const [state, setState] = useState<AsyncState<T>>({ status: 'loading' });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetcher()
      .then((data) => {
        if (!cancelled) setState({ status: 'ok', data });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({ status: 'error', error: err instanceof Error ? err.message : String(err) });
        }
      });
    return () => {
      cancelled = true;
    };
    // Re-run on mount and whenever `reload` bumps the nonce; the fetcher identity
    // is intentionally not a dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { state, reload };
}
