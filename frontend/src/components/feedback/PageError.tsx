import type { FailureKind } from '../../utils/errors';
import { type ErrorCode, ErrorState } from './ErrorState';

/** The statuses `ErrorState` has a dedicated page for. */
const KNOWN_CODES: ReadonlySet<number> = new Set([400, 401, 403, 404, 429, 500, 502, 503, 504]);

/** Map a failure onto the error-page catalog. Known statuses render their own
 *  page; other 5xx fold into 500 and other 4xx into 400. A `network` failure —
 *  request sent, nothing came back — is 503. A `client` failure never reached
 *  the network, so it renders as 500 ("something went wrong") rather than as a
 *  claim that the service is down: a routine auth redirect rejecting in-flight
 *  used to paint a confident "503 Service unavailable" over a healthy backend
 *  (#930 review). */
function toErrorCode(kind: FailureKind, status?: number): ErrorCode {
  if (kind === 'network') return 503;
  if (kind === 'client' || status === undefined) return 500;
  if (KNOWN_CODES.has(status)) return status as ErrorCode;
  return status >= 500 ? 500 : 400;
}

/**
 * The page-level fetch-failure rendering (#910): the dedicated in-brand error
 * page for the failure, instead of the generic inline Alert (which stays the
 * right rendering for a PANEL inside an otherwise-working page).
 *
 * What the user is told depends on who actually failed:
 * - a **server 5xx** keeps the catalog's calm copy (the server's own message is
 *   noise — `psycopg2.ProgrammingError: …` — and can leak internals) and shows
 *   the request id so support can trace the exact request;
 * - a **4xx** shows the backend envelope's actionable message;
 * - a **network or client** failure keeps its message, because nothing
 *   authoritative answered and that message is the only information there is.
 *
 * `onRetry` re-runs the page's own fetcher — cheaper and less destructive than
 * the full `window.location.reload()` the catalog falls back to.
 */
export function PageError({
  error,
  kind = 'http',
  httpStatus,
  requestId,
  onRetry,
}: {
  /** The normalised failure message (`AsyncState`'s `error`). */
  error: string;
  kind?: FailureKind;
  httpStatus?: number;
  requestId?: string;
  onRetry?: () => void;
}) {
  const code = toErrorCode(kind, httpStatus);
  const isServerResponse = kind === 'http' && httpStatus !== undefined && httpStatus >= 500;
  return (
    <ErrorState
      code={code}
      message={isServerResponse ? undefined : error}
      requestId={requestId}
      onRetry={onRetry}
    />
  );
}
