import { type ErrorCode, ErrorState } from './ErrorState';

/** The statuses `ErrorState` has a dedicated page for. */
const KNOWN_CODES: ReadonlySet<number> = new Set([400, 401, 403, 404, 429, 500, 502, 503, 504]);

/** Map an HTTP status (or its absence) onto the error-page catalog:
 *  known codes render their own page; other 5xx fold into 500, other 4xx into
 *  400; no status at all = the server never answered → 503. */
function toErrorCode(status?: number): ErrorCode {
  if (status !== undefined && KNOWN_CODES.has(status)) return status as ErrorCode;
  if (status === undefined) return 503;
  return status >= 500 ? 500 : 400;
}

/**
 * The page-level fetch-failure rendering (#910): the dedicated in-brand error
 * page for the failure's status, instead of the generic inline Alert (which
 * stays the right rendering for a PANEL inside an otherwise-working page).
 *
 * 4xx failures carry the backend envelope's actionable message; 5xx pages keep
 * the catalog's calm generic copy (a raw 500 message is noise) but surface the
 * request id so support can trace the exact request. `onRetry` re-runs the
 * page's fetcher in place — cheaper than the full reload the catalog defaults
 * to.
 */
export function PageError({
  error,
  httpStatus,
  requestId,
  onRetry,
}: {
  /** The normalised failure message (`AsyncState`'s `error`). */
  error: string;
  httpStatus?: number;
  requestId?: string;
  onRetry?: () => void;
}) {
  const code = toErrorCode(httpStatus);
  // Only a real 5xx RESPONSE gets the catalog's generic copy: the server's own
  // message there is noise ("psycopg2.ProgrammingError: …") and often leaky.
  // A status-less failure is NOT that — no server answered, so whatever the
  // client caught (a network error, a thrown app error) is the only information
  // there is, and swallowing it would leave the user with a blank verdict.
  const isServerResponse = httpStatus !== undefined && httpStatus >= 500;
  return (
    <ErrorState
      code={code}
      message={isServerResponse ? undefined : error}
      requestId={requestId}
      onRetry={onRetry}
    />
  );
}
