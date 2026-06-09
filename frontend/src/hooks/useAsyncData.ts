import { useEffect, useState } from 'react';

/** Three-state result of a one-shot async fetch. */
export type AsyncState<T> =
  | { status: 'loading' }
  | { status: 'ok'; data: T }
  | { status: 'error'; error: string };

/**
 * Fetch once on mount, with a cancelled-guard so a late resolution after unmount
 * doesn't set state, and rejection normalised to a string `error`. Shared by the
 * data pages (Home, Connections, …) so the cancelled-effect dance lives in one
 * place rather than being re-derived per page.
 */
export function useAsyncData<T>(fetcher: () => Promise<T>): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({ status: 'loading' });

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
    // Fetch once on mount; the fetcher identity is intentionally not a dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return state;
}
