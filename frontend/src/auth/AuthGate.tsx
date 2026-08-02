import { Alert, Button, Flex, Spin } from 'antd';
import { useState, type ReactNode } from 'react';

import { useAuthUser } from './authContext';
import { LoginPage } from './LoginPage';
import { OtpSignInPage } from './OtpSignInPage';
import { login } from './authClient';
import { authMode } from './config';
import { useOtpSession } from './otpSessionContext';

/**
 * Gates children behind auth. Four paths:
 * - dev_bypass: renders children directly.
 * - unconfigured: renders a setup-needed banner (no auth client, no children).
 * - real: renders the sign-in page when signed out, children when authenticated.
 * - otp: renders the two-step code form when signed out (ADR 0032), children when
 *   the session cookie resolves.
 *
 * The OIDC user comes from AuthProvider, and the OTP session from
 * OtpSessionProvider — both mounted above this in main.tsx.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  if (authMode === 'dev_bypass') return <>{children}</>;
  if (authMode === 'unconfigured') return <UnconfiguredBanner />;
  if (authMode === 'otp') return <OtpAuthGate>{children}</OtpAuthGate>;
  return <RealAuthGate>{children}</RealAuthGate>;
}

/**
 * The `otp` gate (ADR 0032). Four states, and the two that are easy to conflate
 * are kept apart on purpose:
 *
 * - `probing` shows a spinner. The session is an HttpOnly cookie, so until
 *   `GET /me` answers the SPA genuinely does not know — flashing the sign-in form
 *   here would make every reload look like a sign-out.
 * - `error` shows the failure and a retry, NOT the sign-in form: an unreachable
 *   API is not a signed-out user, and inviting somebody to type a code at a
 *   server that cannot verify it wastes the code (they are single-use).
 */
function OtpAuthGate({ children }: { children: ReactNode }) {
  const { state, adopt, retry } = useOtpSession();

  if (state.status === 'signed_in') return <>{children}</>;
  if (state.status === 'probing') {
    return (
      <Flex align="center" justify="center" style={{ minHeight: '100vh' }}>
        <Spin size="large" aria-label="Checking your session" />
      </Flex>
    );
  }
  if (state.status === 'error') {
    return (
      <Alert
        type="error"
        showIcon
        title="Could not check your sign-in status"
        description={
          <Flex vertical gap={12} align="flex-start">
            <span>{state.message}</span>
            <Button size="small" onClick={retry}>
              Try again
            </Button>
          </Flex>
        }
        style={{ margin: 24 }}
      />
    );
  }
  return <OtpSignInPage onSignedIn={adopt} />;
}

function RealAuthGate({ children }: { children: ReactNode }) {
  const user = useAuthUser();
  const [signingIn, setSigningIn] = useState(false);

  if (user) return <>{children}</>;

  const onSignIn = () => {
    // signinRedirect navigates away, so this state mainly guards a double-click
    // before the redirect takes effect.
    setSigningIn(true);
    void login().catch(() => setSigningIn(false));
  };

  return <LoginPage onSignIn={onSignIn} signingIn={signingIn} />;
}

function UnconfiguredBanner() {
  return (
    <Alert
      type="warning"
      showIcon
      title="Authentication is not configured"
      description={
        <>
          Set <code>DATAQ_AUTH_AUTHORITY</code> + <code>DATAQ_AUTH_CLIENT_ID</code> (SSO),{' '}
          <code>DATAQ_AUTH_MODE=otp</code> for email sign-in codes (plus the backend&apos;s{' '}
          <code>AUTH_EMAIL_*</code> block and signup allowlist), or{' '}
          <code>DATAQ_AUTH_MODE=bypass</code> for a local eval stack. See the deployment guide.
        </>
      }
      style={{ margin: 24 }}
    />
  );
}
