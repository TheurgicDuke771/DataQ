import { useContext } from 'react';

import type { WorkspaceRole } from '../api/admin';
import type { MeResponse } from '../api/me';
import type { AsyncState } from '../hooks/useAsyncData';
import { MeContext, MeUpdateContext } from './meContext';

/** The shared `/me` fetch state (identity + `is_workspace_admin`). */
export function useMe(): AsyncState<MeResponse> {
  return useContext(MeContext);
}

/** Push a fresh `/me` body (e.g. the result of a `PATCH /me`) into the shared
 *  context, so every reader — the Profile page, the completion prompt, a
 *  future admin-list — sees it without a second fetch (#1139). */
export function useUpdateMe(): (me: MeResponse) => void {
  return useContext(MeUpdateContext);
}

/** Convenience: true only once `/me` has resolved and the user is a workspace admin. */
export function useIsWorkspaceAdmin(): boolean {
  const me = useMe();
  return me.status === 'ok' && me.data.is_workspace_admin;
}

/**
 * The caller's effective workspace role, or `null` until `/me` resolves.
 *
 * `null` is a real third state and callers must treat it as "not yet known",
 * NOT as "no permission": rendering a read-only shell during the `/me` fetch and
 * then popping the controls in is worse than rendering nothing, and rendering
 * them optimistically is worse still. The capability hooks below return `false`
 * while loading for exactly that reason — a control that appears late is a
 * smaller problem than one that appears and then vanishes.
 */
export function useWorkspaceRole(): WorkspaceRole | null {
  const me = useMe();
  return me.status === 'ok' ? me.data.role : null;
}

/**
 * May the caller create / edit / delete / re-auth a connection? (ADR 0033)
 *
 * Admin only. This mirrors the server, which stays the decider — every gated
 * endpoint re-enforces with a 403, and hiding a control is presentation, never
 * security. The point of hiding it is honesty: offering an action that will be
 * refused is a worse experience than not offering it.
 */
export function useCanMutateConnections(): boolean {
  return useWorkspaceRole() === 'admin';
}

/**
 * May the caller author — create/import suites, and test a connection?
 *
 * Member or Admin. A Viewer is read-only, so any control that would produce a
 * 403 is hidden from them rather than shown and then refused.
 */
export function useCanAuthor(): boolean {
  const role = useWorkspaceRole();
  return role === 'admin' || role === 'member';
}
