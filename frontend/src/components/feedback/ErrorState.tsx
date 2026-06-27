import { Button, Result, Typography } from 'antd';
import type { ResultStatusType } from 'antd/es/result';
import { Link } from 'react-router-dom';

/** The HTTP statuses the app renders an in-brand error page for. */
export type ErrorCode = 400 | 401 | 403 | 404 | 429 | 500 | 502 | 503 | 504;

interface CatalogEntry {
  /** antd Result status (drives the illustration). */
  status: ResultStatusType;
  title: string;
  subTitle: string;
}

// One catalog so every error page reads consistently. Codes antd doesn't draw a
// dedicated illustration for fall back to info/warning/error.
const CATALOG: Record<ErrorCode, CatalogEntry> = {
  400: { status: 'error', title: '400 — Bad request', subTitle: 'The request was malformed.' },
  401: {
    status: 'warning',
    title: '401 — Sign in required',
    subTitle: 'Your session has expired. Sign in again to continue.',
  },
  403: {
    status: '403',
    title: '403 — Forbidden',
    subTitle: "You don't have access to this page.",
  },
  404: {
    status: '404',
    title: '404 — Not found',
    subTitle: "This page doesn't exist or has moved.",
  },
  429: {
    status: 'warning',
    title: '429 — Too many requests',
    subTitle: 'Slow down for a moment, then try again.',
  },
  500: {
    status: '500',
    title: '500 — Something went wrong',
    subTitle: 'An unexpected error occurred on our side.',
  },
  502: {
    status: 'error',
    title: '502 — Bad gateway',
    subTitle: 'The server received an invalid upstream response.',
  },
  503: {
    status: 'error',
    title: '503 — Service unavailable',
    subTitle: 'The service is temporarily unavailable. Try again shortly.',
  },
  504: {
    status: 'error',
    title: '504 — Gateway timeout',
    subTitle: 'The server took too long to respond.',
  },
};

interface ErrorStateProps {
  code: ErrorCode;
  /** Override the catalog subtitle (e.g. a specific reason). */
  message?: string;
  /** Server-error correlation id — shown only for 5xx so support can trace it. */
  requestId?: string;
  /** A retry handler; when present, the primary action calls it. */
  onRetry?: () => void;
}

/**
 * The one in-brand error page (ADR 0022 ErrorState), switchable across the 4xx /
 * 5xx the app surfaces. Used by the router catch-all (404), the ErrorBoundary
 * fallback (500), and access denials (403). Recovery action follows the tone:
 * a retry handler wins; otherwise server errors offer Reload, client errors a
 * link home. Server errors show the request id when one is supplied.
 */
export function ErrorState({ code, message, requestId, onRetry }: ErrorStateProps) {
  const entry = CATALOG[code];
  const isServer = code >= 500;
  return (
    <Result
      status={entry.status}
      title={entry.title}
      subTitle={message ?? entry.subTitle}
      extra={
        <>
          {onRetry ? (
            <Button type="primary" onClick={onRetry}>
              Try again
            </Button>
          ) : isServer ? (
            <Button type="primary" onClick={() => window.location.reload()}>
              Reload
            </Button>
          ) : (
            <Link to="/">
              <Button type="primary">Back to app</Button>
            </Link>
          )}
          {isServer && requestId ? (
            <div style={{ marginTop: 12 }}>
              <Typography.Text type="secondary">Request id: </Typography.Text>
              <Typography.Text code copyable>
                {requestId}
              </Typography.Text>
            </div>
          ) : null}
        </>
      }
    />
  );
}
