import type { ReactNode } from 'react';
import { Alert, Spin } from 'antd';

import type { AsyncState } from '../hooks/useAsyncData';
import { PageError } from './feedback/PageError';

/**
 * The `if loading → Spin / if error → render / else render-data` ladder that every `useAsyncData`
 * consumer hand-rolled.
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
          kind={state.kind}
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
