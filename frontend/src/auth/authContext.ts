import { createContext, useContext } from 'react';

import type { User } from './authClient';

/**
 * OIDC auth context — the signed-in user, provided by AuthProvider. Split from
 * the provider component so the hook + context live in a non-component module
 * (react-refresh/only-export-components), matching currentUserContext.ts.
 */
export interface AuthState {
  user: User | null;
}

export const AuthContext = createContext<AuthState>({ user: null });

/** The current OIDC user (null when not signed in / not in real auth mode). */
export function useAuthUser(): User | null {
  return useContext(AuthContext).user;
}
