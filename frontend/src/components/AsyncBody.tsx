import type { ReactNode } from 'react';
import { Alert, Spin } from 'antd';

import type { AsyncState } from '../hooks/useAsyncData';

/**
 * The `if loading → Spin / if error → Alert / else render` ladder that every
 * `useAsyncData` consumer hand-rolled. Renders the data via a render-prop so the
 * `'ok'` branch is type-narrowed — the child receives `T`, not `AsyncState<T>`.
 *
 * Pages with bespoke loading/empty presentation (large centred spinners, `Empty`
 * states, custom margins) keep their own ladder; this covers the panels whose
 * loading/error look is the plain default.
 */
export function AsyncBody<T>({
  state,
  loadingText,
  errorTitle,
  children,
}: {
  state: AsyncState<T>;
  /** Caption for the default spinner. */
  loadingText?: string;
  errorTitle: string;
  children: (data: T) => ReactNode;
}): ReactNode {
  if (state.status === 'loading') return <Spin description={loadingText} />;
  if (state.status === 'error') {
    return <Alert type="error" showIcon title={errorTitle} description={state.error} />;
  }
  return children(state.data);
}
