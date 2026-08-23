import axios from 'axios';

/** Normalise an unknown thrown value to a user-facing string. */
export function errorMessage(err: unknown, fallback = 'unknown error'): string {
  return err instanceof Error ? err.message : fallback;
}

/**
 * Where a failed fetch actually failed (#910) — the three cases render very differently, and
 * conflating them tells the user something untrue: - `http` — the server answered with a status.
 */
export type FailureKind = 'http' | 'network' | 'client';

/** What a failed fetch actually was — message plus the HTTP facts the dedicated error pages need. */
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
  // Never reached the network — a client-side throw (#930 review: this used to be reported as 503,
  // which told users the backend was down during a routine auth redirect).
  return { message: errorMessage(err, fallback), kind: 'client' };
}
