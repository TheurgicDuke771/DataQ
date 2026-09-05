import type { AsyncState } from '../../hooks/useAsyncData';

/** Row count of a loaded list, or `null` while loading / on error. */
export function count<T>(state: AsyncState<T[]>): number | null {
  return state.status === 'ok' ? state.data.length : null;
}

/** Project an `ok` AsyncState's data, passing `loading`/`error` through unchanged. */
export function mapAsync<T, U>(state: AsyncState<T>, fn: (data: T) => U): AsyncState<U> {
  return state.status === 'ok' ? { ...state, data: fn(state.data) } : state;
}
