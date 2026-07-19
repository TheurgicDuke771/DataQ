import { AxiosError, AxiosHeaders } from 'axios';
import { describe, expect, it } from 'vitest';

import { errorMessage, fetchFailure } from '../../src/utils/errors';

/** An axios error shaped like the ones the API client actually rejects with —
 *  the response interceptor has already swapped the envelope message onto
 *  `error.message` by the time `fetchFailure` sees it. */
function axiosFailure(status: number, message: string, requestId?: string): AxiosError {
  const err = new AxiosError(message);
  err.response = {
    status,
    statusText: '',
    data: {},
    headers: new AxiosHeaders(requestId ? { 'x-request-id': requestId } : {}),
    config: { headers: new AxiosHeaders() },
  };
  return err;
}

describe('errorMessage', () => {
  it('reads an Error message and falls back for a non-Error throw', () => {
    expect(errorMessage(new Error('boom'))).toBe('boom');
    expect(errorMessage('a string', 'fallback')).toBe('fallback');
    expect(errorMessage(undefined)).toBe('unknown error');
  });
});

describe('fetchFailure (#910)', () => {
  it('extracts the status and request id from an axios failure', () => {
    const failure = fetchFailure(axiosFailure(500, 'Internal Server Error', 'req-42'));
    expect(failure).toEqual({
      message: 'Internal Server Error',
      kind: 'http',
      status: 500,
      requestId: 'req-42',
    });
  });

  it('leaves the request id undefined when the header is absent', () => {
    expect(fetchFailure(axiosFailure(404, 'not found')).requestId).toBeUndefined();
  });

  it('classifies an axios error with no response as a NETWORK failure', () => {
    // Request sent, nothing came back — PageError renders this as 503.
    const failure = fetchFailure(new AxiosError('Network Error'));
    expect(failure).toEqual({ message: 'Network Error', kind: 'network' });
  });

  it('classifies a non-axios throw as CLIENT, never as a server outage', () => {
    // #930 review: these never reached the network, so they must not be
    // reported as the service being unavailable.
    expect(fetchFailure(new Error('plain'))).toEqual({ message: 'plain', kind: 'client' });
    expect(fetchFailure({ weird: true }, 'fallback')).toEqual({
      message: 'fallback',
      kind: 'client',
    });
  });
});
