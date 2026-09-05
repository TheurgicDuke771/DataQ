import { Alert, Button, Flex, Spin } from 'antd';
import { useState, type ReactNode } from 'react';

import { useAuthUser } from './authContext';
import { LoginPage } from './LoginPage';
import { OtpSignInPage } from './OtpSignInPage';
import { login } from './authClient';
import { authMode } from './config';
import { useOtpSession } from './otpSessionContext';

/** Gates children behind auth. */
export function AuthGate({ children }: { children: ReactNode }) {
  if (authMode === 'dev_bypass') return <>{children}</>;
  if (authMode === 'unconfigured') return <UnconfiguredBanner />;
  if (authMode === 'otp') return <OtpAuthGate>{children}</OtpAuthGate>;
  return <RealAuthGate>{children}</RealAuthGate>;
}

/** The backend's membership refusal — the one error a retry can never clear. */
const NOT_A_MEMBER = 'not_a_workspace_member';

/** The `otp` gate (ADR 0032). */
function OtpAuthGate({ children }: { children: ReactNode }) {
  const { state, adopt, retry, signOut } = useOtpSession();

  if (state.status === 'signed_in') return <>{children}</>;
  if (state.status === 'probing') {
    return (
      <Flex align="center" justify="center" style={{ minHeight: '100vh' }}>
        <Spin size="large" aria-label="Checking your session" />
      </Flex>
    );
  }
  if (state.status === 'error') {
    // A removed member holds a cookie that authenticates and then 403s for ever.
    // "Try again" cannot help, and without a way out they are wedged.
    const notAMember = state.code === NOT_A_MEMBER;
    return (
      <Alert
        type="error"
        showIcon
        title={
          notAMember
            ? 'This account is not a member of this workspace'
            : 'Could not check your sign-in status'
        }
        description={
          <Flex vertical gap={12} align="flex-start">
            <span>
              {notAMember
                ? 'A workspace admin can add you back. Signing out lets you use a different address.'
                : state.message}
            </span>
            {notAMember ? (
              <Button size="small" onClick={signOut}>
                Sign out
              </Button>
            ) : (
              <Button size="small" onClick={retry}>
                Try again
              </Button>
            )}
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
