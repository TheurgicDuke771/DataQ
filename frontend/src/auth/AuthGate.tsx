import { useIsAuthenticated, useMsal } from '@azure/msal-react';
import { Alert, Button, Flex, Typography } from 'antd';
import type { ReactNode } from 'react';

import { authConfig, authMode } from './config';

const { Title, Paragraph } = Typography;

/**
 * Gates children behind auth. Three paths:
 * - dev_bypass: renders children directly.
 * - unconfigured: renders a setup-needed banner (no MSAL, no children).
 * - real: renders sign-in button when no account, children when authenticated.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  if (authMode === 'dev_bypass') return <>{children}</>;
  if (authMode === 'unconfigured') return <UnconfiguredBanner />;
  return <RealAuthGate>{children}</RealAuthGate>;
}

function RealAuthGate({ children }: { children: ReactNode }) {
  const isAuthenticated = useIsAuthenticated();
  const { instance } = useMsal();

  if (isAuthenticated) return <>{children}</>;

  const onLogin = () => {
    void instance.loginRedirect({
      scopes: authConfig.apiScopeUri ? [authConfig.apiScopeUri] : [],
    });
  };

  return (
    <Flex vertical align="center" justify="center" gap={16} style={{ paddingTop: 80 }}>
      <Title level={3}>Sign in to DataQ</Title>
      <Paragraph type="secondary">Use your organisation Microsoft account.</Paragraph>
      <Button type="primary" size="large" onClick={onLogin}>
        Sign in with Microsoft
      </Button>
    </Flex>
  );
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
