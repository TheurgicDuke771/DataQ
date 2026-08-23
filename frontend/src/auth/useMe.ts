import { useContext } from 'react';

import type { WorkspaceRole } from '../api/admin';
import type { MeResponse } from '../api/me';
import type { AsyncState } from '../hooks/useAsyncData';
import { MeContext, MeUpdateContext } from './meContext';

/** The shared `/me` fetch state (identity + `is_workspace_admin`). */
export function useMe(): AsyncState<MeResponse> {
  return useContext(MeContext);
}

/**
 * Push a fresh `/me` body (e.g. the result of a `PATCH /me`) into the shared context, so every
 * reader — the Profile page, the completion prompt, a future admin-list.
 */
export function useUpdateMe(): (me: MeResponse) => void {
  return useContext(MeUpdateContext);
}

/** Convenience: true only once `/me` has resolved and the user is a workspace admin. */
export function useIsWorkspaceAdmin(): boolean {
  const me = useMe();
  return me.status === 'ok' && me.data.is_workspace_admin;
}

/** The caller's effective workspace role, or `null` until `/me` resolves. */
export function useWorkspaceRole(): WorkspaceRole | null {
  const me = useMe();
  return me.status === 'ok' ? me.data.role : null;
}

/** May the caller create / edit / delete / re-auth a connection? */
export function useCanMutateConnections(): boolean {
  return useWorkspaceRole() === 'admin';
}

/** May the caller author — create/import suites, and test a connection? */
export function useCanAuthor(): boolean {
  const role = useWorkspaceRole();
  return role === 'admin' || role === 'member';
}
