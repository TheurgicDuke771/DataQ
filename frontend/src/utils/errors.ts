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

/** Where a failed fetch actually failed (#910) — the three cases render very
 *  differently, and conflating them tells the user something untrue:
 *  - `http`    — the server answered with a status. Trust it.
 *  - `network` — an HTTP request was made and nothing came back (server down,
 *                DNS, CORS, offline). "Service unavailable" is honest here.
 *  - `client`  — the fetcher threw before/without any HTTP exchange (an auth
 *                redirect rejecting in-flight, a TypeError in page code). NOT a
 *                server problem: reporting it as one sends the user (and
 *                support) after an outage that isn't happening. */
export type FailureKind = 'http' | 'network' | 'client';

/** What a failed fetch actually was — message plus the HTTP facts the dedicated
 *  error pages need. `status` is set only for `http`; `requestId` is the
 *  backend's X-Request-ID echo, shown on 5xx pages so support can trace the
 *  exact request. */
export interface FetchFailure {
  message: string;
  kind: FailureKind;
  status?: number;
  requestId?: string;
}

/** Classify an unknown thrown value from an API call into a `FetchFailure`. */
export function fetchFailure(err: unknown, fallback = 'unknown error'): FetchFailure {
  if (axios.isAxiosError(err)) {
    if (!err.response) {
      // Request went out, nothing came back.
      return { message: err.message, kind: 'network' };
    }
    const requestId: unknown = err.response.headers?.['x-request-id'];
    return {
      // The client interceptor already swaps in the error-envelope message.
      message: err.message,
      kind: 'http',
      status: err.response.status,
      requestId: typeof requestId === 'string' ? requestId : undefined,
    };
  }
  // Never reached the network — a client-side throw (#930 review: this used to
  // be reported as 503, which told users the backend was down during a routine
  // auth redirect).
  return { message: errorMessage(err, fallback), kind: 'client' };
}
