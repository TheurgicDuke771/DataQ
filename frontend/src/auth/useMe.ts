import { useContext } from 'react';

import type { MeResponse } from '../api/me';
import type { AsyncState } from '../hooks/useAsyncData';
import { MeContext } from './meContext';

/** The shared `/me` fetch state (identity + `is_workspace_admin`). */
export function useMe(): AsyncState<MeResponse> {
  return useContext(MeContext);
}

/** Convenience: true only once `/me` has resolved and the user is a workspace admin. */
export function useIsWorkspaceAdmin(): boolean {
  const me = useMe();
  return me.status === 'ok' && me.data.is_workspace_admin;
}
