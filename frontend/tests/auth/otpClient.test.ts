import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * Email-OTP transport (ADR 0032, #736).
 *
 * The seam under test is the CONTRACT with the backend, so the axios instance is
 * stubbed but the semantics are not: which HTTP verb, which path, which body, and
 * — the part that actually matters — what each failure is allowed to be turned
 * into. `probeSession` collapsing a 500 into "signed out" would be a silent
 * failure that paints a sign-in page during an outage, so it gets its own case.
 */

const post = vi.fn();
const get = vi.fn();

beforeEach(() => {
  vi.resetModules();
  post.mockReset();
  get.mockReset();
  vi.doMock('../../src/api/client', () => ({ api: { post, get } }));
});

afterEach(() => {
  vi.doUnmock('../../src/api/client');
});

const load = () => import('../../src/auth/otpClient');

/** An axios-shaped rejection (only the field the code reads). */
const httpError = (status: number, message = 'boom') =>
  Object.assign(new Error(message), { response: { status } });

describe('requestCode', () => {
  it('POSTs the address to the mint endpoint', async () => {
    post.mockResolvedValue({ data: { status: 'ok' } });
    const { requestCode } = await load();
    await requestCode('ada@acme.io');
    expect(post).toHaveBeenCalledWith('/auth/otp/request', { email: 'ada@acme.io' });
  });

  it('RESOLVES for an ineligible address — the backend answers ok either way', async () => {
    // The anti-enumeration property (ADR 0032 decision 4) only holds if the client
    // treats the uniform ok as success. A client that inferred "unknown address"
    // from anything here would re-open the oracle the backend closed.
    post.mockResolvedValue({ data: { status: 'ok' } });
    const { requestCode } = await load();
    await expect(requestCode('nobody@nowhere.test')).resolves.toBeUndefined();
  });

  it('propagates a real transport failure (502) rather than swallowing it', async () => {
    post.mockRejectedValue(httpError(502, 'Could not send the sign-in code'));
    const { requestCode } = await load();
    await expect(requestCode('ada@acme.io')).rejects.toThrow('Could not send the sign-in code');
  });
});

describe('verifyCode', () => {
  it('POSTs email + code and returns the /me body the response carries', async () => {
    post.mockResolvedValue({ data: { id: 'u1', email: 'ada@acme.io', is_workspace_admin: true } });
    const { verifyCode } = await load();
    const me = await verifyCode('ada@acme.io', '123456');
    expect(post).toHaveBeenCalledWith('/auth/otp/verify', {
      email: 'ada@acme.io',
      code: '123456',
    });
    expect(me.is_workspace_admin).toBe(true);
  });

  it('never returns a token — the session is the HttpOnly cookie only', async () => {
    // If a token ever appears in this body, the ADR 0032 decision-3 property
    // ("the SPA never holds the token") has been broken server-side, and this
    // test is where that should be noticed.
    post.mockResolvedValue({ data: { id: 'u1', email: 'ada@acme.io' } });
    const { verifyCode } = await load();
    const me = (await verifyCode('ada@acme.io', '123456')) as unknown as Record<string, unknown>;
    expect(Object.keys(me)).not.toContain('token');
    expect(Object.keys(me)).not.toContain('access_token');
  });

  it('rejects on the uniform 401 (wrong / expired / used / out of attempts)', async () => {
    post.mockRejectedValue(httpError(401, 'That sign-in code is not valid. Request a new one.'));
    const { verifyCode } = await load();
    await expect(verifyCode('ada@acme.io', '000000')).rejects.toThrow('not valid');
  });
});

describe('endSession', () => {
  it('POSTs to logout — POST, because SameSite=Lax only protects non-GET mutations', async () => {
    post.mockResolvedValue({ status: 204 });
    const { endSession } = await load();
    await endSession();
    expect(post).toHaveBeenCalledWith('/auth/logout');
  });
});

describe('probeSession', () => {
  it('returns the user when the cookie resolves', async () => {
    get.mockResolvedValue({ data: { id: 'u1', email: 'ada@acme.io' } });
    const { probeSession } = await load();
    await expect(probeSession()).resolves.toMatchObject({ id: 'u1' });
    expect(get).toHaveBeenCalledWith('/me');
  });

  it('returns null on a clean 401 — the only way the SPA can learn it is signed out', async () => {
    get.mockRejectedValue(httpError(401));
    const { probeSession } = await load();
    await expect(probeSession()).resolves.toBeNull();
  });

  it.each([500, 502, 503, 429])(
    'RE-THROWS a %i instead of reporting "signed out"',
    async (status) => {
      // The silent-failure case this exists to prevent: an unreachable API is not
      // a signed-out user. Collapsing it to null paints the sign-in form during an
      // outage and invites the user to burn a single-use code against a server
      // that cannot check it.
      get.mockRejectedValue(httpError(status));
      const { probeSession } = await load();
      await expect(probeSession()).rejects.toBeTruthy();
    },
  );

  it('re-throws a network error (no response at all)', async () => {
    get.mockRejectedValue(new Error('Network Error'));
    const { probeSession } = await load();
    await expect(probeSession()).rejects.toThrow('Network Error');
  });
});

describe('statusOf', () => {
  it('reads an axios status, and is undefined for anything else', async () => {
    const { statusOf } = await load();
    expect(statusOf(httpError(418))).toBe(418);
    expect(statusOf(new Error('plain'))).toBeUndefined();
    expect(statusOf(undefined)).toBeUndefined();
    expect(statusOf(null)).toBeUndefined();
  });
});
