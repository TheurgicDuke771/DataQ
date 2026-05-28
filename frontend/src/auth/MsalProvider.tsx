import { MsalProvider as ReactMsalProvider } from '@azure/msal-react';
import type { ReactNode } from 'react';

import { getMsalInstance } from './msalInstance';

/**
 * Wraps children in MSAL's React provider when running in real auth mode.
 * In dev_bypass / unconfigured modes, this is a passthrough — no MsalProvider
 * is mounted, so useMsal()-using components must not be rendered (handled by
 * CurrentUserProvider and AuthGate dispatching by mode).
 */
export function MsalProvider({ children }: { children: ReactNode }) {
  const instance = getMsalInstance();
  if (!instance) return <>{children}</>;
  return <ReactMsalProvider instance={instance}>{children}</ReactMsalProvider>;
}
