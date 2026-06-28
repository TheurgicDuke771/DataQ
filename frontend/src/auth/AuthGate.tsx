import { InteractionStatus } from '@azure/msal-browser';
import { useIsAuthenticated, useMsal } from '@azure/msal-react';
import { Alert } from 'antd';
import type { ReactNode } from 'react';

import { LoginPage } from './LoginPage';
import { authConfig, authMode } from './config';

/**
 * Gates children behind auth. Three paths:
 * - dev_bypass: renders children directly.
 * - unconfigured: renders a setup-needed banner (no MSAL, no children).
 * - real: renders the sign-in page when no account, children when authenticated.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  if (authMode === 'dev_bypass') return <>{children}</>;
  if (authMode === 'unconfigured') return <UnconfiguredBanner />;
  return <RealAuthGate>{children}</RealAuthGate>;
}

function RealAuthGate({ children }: { children: ReactNode }) {
  const isAuthenticated = useIsAuthenticated();
  const { instance, inProgress } = useMsal();

  if (isAuthenticated) return <>{children}</>;

  const onSignIn = () => {
    void instance.loginRedirect({
      scopes: authConfig.apiScopeUri ? [authConfig.apiScopeUri] : [],
    });
  };

  // A redirect is mid-flight (the click, or the post-redirect handshake) → keep
  // the button busy so the page doesn't look idle while MSAL navigates.
  const signingIn = inProgress !== InteractionStatus.None;

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
          Set <code>VITE_AZURE_TENANT_ID</code> + <code>VITE_AZURE_SPA_CLIENT_ID</code>, or run a
          DEV build with <code>VITE_AUTH_DEV_BYPASS=true</code>. See <code>.env.example</code>.
        </>
      }
      style={{ margin: 24 }}
    />
  );
}
