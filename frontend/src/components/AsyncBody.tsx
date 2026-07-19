import type { ReactNode } from 'react';
import { Alert, Spin } from 'antd';

import type { AsyncState } from '../hooks/useAsyncData';
import { PageError } from './feedback/PageError';

/**
 * The `if loading → Spin / if error → render / else render-data` ladder that
 * every `useAsyncData` consumer hand-rolled. Renders the data via a render-prop
 * so the `'ok'` branch is type-narrowed — the child receives `T`, not
 * `AsyncState<T>`.
 *
 * Two error renderings (#910):
 * - default — the inline Alert, right for a PANEL inside an otherwise-working
 *   page (the page keeps its chrome and other panels);
 * - `page` — the dedicated error page (`PageError` → `ErrorState`) for a
 *   whole-page fetch, where the inline alert used to leave a bare husk of a
 *   page around a one-line error. Pass `onRetry` (usually the hook's `reload`)
 *   so the page offers an in-place retry.
 *
 * Pages with bespoke loading/empty presentation keep their own ladder; this
 * covers the panels/pages whose loading/error look is the plain default.
 */
export function AsyncBody<T>({
  state,
  loadingText,
  errorTitle,
  page = false,
  onRetry,
  children,
}: {
  state: AsyncState<T>;
  /** Caption for the default spinner. */
  loadingText?: string;
  errorTitle: string;
  /** Whole-page fetch → dedicated error page instead of the inline Alert. */
  page?: boolean;
  /** In-place retry for the page rendering (usually `reload`). */
  onRetry?: () => void;
  children: (data: T) => ReactNode;
}): ReactNode {
  if (state.status === 'loading') return <Spin description={loadingText} />;
  if (state.status === 'error') {
    if (page) {
      return (
        <PageError
          error={state.error}
          httpStatus={state.httpStatus}
          requestId={state.requestId}
          onRetry={onRetry}
        />
      );
    }
    return <Alert type="error" showIcon title={errorTitle} description={state.error} />;
  }
  return children(state.data);
}
