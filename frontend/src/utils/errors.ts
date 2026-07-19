import axios from 'axios';

/**
 * Normalise an unknown thrown value to a user-facing string.
 *
 * Collapses the `instanceof Error` message-or-fallback ternary that recurred
 * across the toast/catch sites into one place. The default `'unknown error'`
 * fallback suits user-facing toasts; the fetch-error sites that want the raw
 * `String(err)` for a non-Error throw pass it explicitly.
 */
export function errorMessage(err: unknown, fallback = 'unknown error'): string {
  return err instanceof Error ? err.message : fallback;
}

/** What a failed fetch actually was — message plus the HTTP facts the dedicated
 *  error pages need (#910). `status` is undefined for a network-level failure
 *  (server unreachable, aborted); `requestId` is the backend's X-Request-ID
 *  echo, shown on 5xx pages so support can trace the exact request. */
export interface FetchFailure {
  message: string;
  status?: number;
  requestId?: string;
}

/** Classify an unknown thrown value from an API call into a `FetchFailure`.
 *  Axios errors carry the response status + headers; anything else degrades to
 *  message-only (rendered as a network-level failure by `PageError`). */
export function fetchFailure(err: unknown, fallback = 'unknown error'): FetchFailure {
  if (axios.isAxiosError(err)) {
    const requestId: unknown = err.response?.headers?.['x-request-id'];
    return {
      // The client interceptor already swaps in the error-envelope message.
      message: err.message,
      status: err.response?.status,
      requestId: typeof requestId === 'string' ? requestId : undefined,
    };
  }
  return { message: errorMessage(err, fallback) };
}
