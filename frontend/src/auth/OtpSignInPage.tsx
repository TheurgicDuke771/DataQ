import { Alert, Button, Form, Input, Space, Typography } from 'antd';
import type { InputRef } from 'antd';
import { useCallback, useEffect, useRef, useState } from 'react';

import type { MeResponse } from '../api/me';
import { LoginShell } from './LoginPage';
import { requestCode, statusOf, verifyCode } from './otpClient';

/** Seconds before "Resend code" re-arms. */
export const RESEND_COOLDOWN_SECONDS = 30;

/** Minutes a code stays valid — mirrors the backend's `CODE_TTL_MINUTES`. */
const CODE_TTL_MINUTES = 10;

/**
 * The **uniform** acknowledgement (ADR 0032 decision 4).
 *
 * The backend answers `{"status":"ok"}` for an eligible address, an ineligible
 * one, AND a throttled one — byte-identical, so that a stranger cannot use this
 * form to discover who has an account. The copy has to be uniform too: "we sent
 * you a code" would leak the very thing the response was built to hide, because
 * a user who is NOT eligible receives nothing and would learn that from the
 * mismatch. Hence the conditional phrasing, and hence no "resent!" success toast
 * either — every send says exactly this much and no more.
 */
function eligibilityNotice(email: string) {
  return (
    <>
      If <strong>{email}</strong> can sign in to this workspace, a {CODE_TTL_MINUTES}-minute code is
      on its way. Enter it below.
    </>
  );
}

type Step = 'email' | 'code';

/**
 * Two-step email one-time-code sign-in (ADR 0032, #736).
 *
 * Step 1 takes an address, step 2 takes the code. On success the caller adopts
 * the returned `/me` body — the session itself is an HttpOnly cookie the verify
 * response set, which this component never sees and cannot store.
 *
 * Error copy is the SERVER's, always. The backend collapses wrong / expired /
 * already-used / out-of-attempts into one 401 with one message, deliberately, and
 * a friendlier client-side taxonomy on top of it would be a fabrication — the SPA
 * has no way to tell those cases apart and must not imply that it can.
 */
export function OtpSignInPage({
  onSignedIn,
  cooldownSeconds = RESEND_COOLDOWN_SECONDS,
}: {
  onSignedIn: (me: MeResponse) => void;
  /**
   * Seconds before "Resend code" re-arms. Overridable so a test can watch a real
   * countdown expire in ~1s instead of faking timers around an async submit —
   * fake timers plus in-flight promises here produced a hang, and a timing knob
   * is a smaller price than a test that cannot express the case.
   */
  cooldownSeconds?: number;
}) {
  const [step, setStep] = useState<Step>('email');
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);
  const codeInputRef = useRef<InputRef>(null);

  // Tick the resend cooldown. One interval for the whole countdown, cleared on
  // unmount so a sign-in doesn't leave a timer running against a dead component.
  useEffect(() => {
    if (cooldown <= 0) return;
    const id = setInterval(() => setCooldown((n) => (n > 0 ? n - 1 : 0)), 1000);
    return () => clearInterval(id);
  }, [cooldown]);

  const send = useCallback(
    async (address: string) => {
      setBusy(true);
      setError(null);
      try {
        await requestCode(address);
        setStep('code');
        setCooldown(cooldownSeconds);
        return true;
      } catch (err) {
        // A rejection here is a real fault — mail transport down (502), OTP not
        // enabled on this deployment (503), or the per-IP limiter (429). It is
        // NOT "that address is unknown": ineligible addresses resolve.
        setError(messageOf(err, 'Could not request a sign-in code. Try again shortly.'));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [cooldownSeconds],
  );

  const onSubmitEmail = useCallback(() => {
    void send(email.trim());
  }, [email, send]);

  const onResend = useCallback(() => {
    if (cooldown > 0) return;
    void send(email.trim()).then((ok) => {
      // A resend invalidates the previous code server-side, so anything already
      // typed is now guaranteed wrong. Clearing it is honesty, not tidiness.
      if (ok) setCode('');
    });
  }, [cooldown, email, send]);

  const onSubmitCode = useCallback(() => {
    setBusy(true);
    setError(null);
    void verifyCode(email.trim(), code.trim())
      .then((me) => onSignedIn(me))
      .catch((err: unknown) => {
        setError(messageOf(err, 'That sign-in code is not valid. Request a new one.'));
        // Only the code is cleared, never the address — retyping an email after
        // every mistyped digit is the thing that makes these forms hateful.
        setCode('');
        codeInputRef.current?.focus();
      })
      .finally(() => setBusy(false));
  }, [code, email, onSignedIn]);

  const onUseAnotherAddress = useCallback(() => {
    setStep('email');
    setCode('');
    setError(null);
    setCooldown(0);
  }, []);

  return (
    <LoginShell
      title="Sign in to DataQ"
      subtitle={
        step === 'email'
          ? 'Enter your email and we’ll send you a one-time code.'
          : 'Enter the code from your email.'
      }
      footer="No password to remember. Access is granted per suite by a workspace admin."
    >
      {error && (
        <Alert
          type="error"
          showIcon
          message={error}
          style={{ marginBottom: 16, textAlign: 'left' }}
          role="alert"
        />
      )}

      {step === 'email' ? (
        <Form layout="vertical" className="dqlogin-form" onFinish={onSubmitEmail}>
          <Form.Item label="Email address">
            <Input
              size="large"
              type="email"
              autoComplete="email"
              autoFocus
              inputMode="email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              aria-label="Email address"
            />
          </Form.Item>
          <Button
            type="primary"
            size="large"
            block
            htmlType="submit"
            className="dqlogin-btn"
            loading={busy}
            disabled={email.trim().length === 0}
          >
            {busy ? 'Sending…' : 'Send code'}
          </Button>
        </Form>
      ) : (
        <Form layout="vertical" className="dqlogin-form" onFinish={onSubmitCode}>
          <p className="dqlogin-sent">{eligibilityNotice(email.trim())}</p>
          <Form.Item label="Sign-in code">
            <Input
              ref={codeInputRef}
              size="large"
              className="dqlogin-code-input"
              // `one-time-code` is what lets iOS/Android/macOS offer the code
              // straight from the Mail notification.
              autoComplete="one-time-code"
              inputMode="numeric"
              autoFocus
              maxLength={6}
              placeholder="000000"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              aria-label="Sign-in code"
            />
          </Form.Item>
          <Button
            type="primary"
            size="large"
            block
            htmlType="submit"
            className="dqlogin-btn"
            loading={busy}
            disabled={code.trim().length === 0}
          >
            {busy ? 'Verifying…' : 'Verify and sign in'}
          </Button>
          <Space
            style={{ marginTop: 14, width: '100%', justifyContent: 'space-between' }}
            size="small"
          >
            <Button type="link" size="small" onClick={onResend} disabled={busy || cooldown > 0}>
              {cooldown > 0 ? `Resend code in ${cooldown}s` : 'Resend code'}
            </Button>
            <Button type="link" size="small" onClick={onUseAnotherAddress} disabled={busy}>
              Use a different address
            </Button>
          </Space>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: '10px 0 0' }}>
            Codes can be used once and expire after {CODE_TTL_MINUTES} minutes.
          </Typography.Paragraph>
        </Form>
      )}
    </LoginShell>
  );
}

/**
 * The server's message when it sent one, our fallback when it did not.
 *
 * `api/client.ts` has already lifted the DataQ error envelope's human message
 * onto `error.message` (and appended a retry hint on a 429), so the axios default
 * "Request failed with status code 502" only surfaces when the backend genuinely
 * returned no envelope — a proxy error page, say.
 */
function messageOf(err: unknown, fallback: string): string {
  const message = err instanceof Error ? err.message : '';
  if (!message || /^Request failed with status code/.test(message)) {
    // Keep the status when we have one: "…(HTTP 502)" is the difference between
    // an operator being able to act and a user re-reading the same sentence.
    const status = statusOf(err);
    return status ? `${fallback} (HTTP ${status})` : fallback;
  }
  return message;
}
