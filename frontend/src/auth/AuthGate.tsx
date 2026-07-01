import { Alert } from 'antd';
import { useState, type ReactNode } from 'react';

import { useAuthUser } from './authContext';
import { LoginPage } from './LoginPage';
import { login } from './authClient';
import { authMode } from './config';

/**
 * Gates children behind auth. Three paths:
 * - dev_bypass: renders children directly.
 * - unconfigured: renders a setup-needed banner (no auth client, no children).
 * - real: renders the sign-in page when signed out, children when authenticated.
 *
 * The OIDC user comes from AuthProvider, mounted above this in main.tsx.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  if (authMode === 'dev_bypass') return <>{children}</>;
  if (authMode === 'unconfigured') return <UnconfiguredBanner />;
  return <RealAuthGate>{children}</RealAuthGate>;
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
          Set <code>DATAQ_AUTH_AUTHORITY</code> + <code>DATAQ_AUTH_CLIENT_ID</code> (real sign-in),
          or <code>DATAQ_AUTH_MODE=bypass</code> for a local eval stack. See the deployment guide.
        </>
      }
      style={{ margin: 24 }}
    />
  );
}
