import type { FailureKind } from '../../utils/errors';
import { type ErrorCode, ErrorState } from './ErrorState';

/** The statuses `ErrorState` has a dedicated page for. */
const KNOWN_CODES: ReadonlySet<number> = new Set([400, 401, 403, 404, 429, 500, 502, 503, 504]);

/** Map a failure onto the error-page catalog. */
function toErrorCode(kind: FailureKind, status?: number): ErrorCode {
  if (kind === 'network') return 503;
  if (kind === 'client' || status === undefined) return 500;
  if (KNOWN_CODES.has(status)) return status as ErrorCode;
  return status >= 500 ? 500 : 400;
}

/**
 * The page-level fetch-failure rendering (#910): the dedicated in-brand error page for the
 * failure.
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
