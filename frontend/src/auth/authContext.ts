import { createContext, useContext } from 'react';

import type { User } from './authClient';

/** OIDC auth context — the signed-in user, provided by AuthProvider. */
export interface AuthState {
  user: User | null;
}

export const AuthContext = createContext<AuthState>({ user: null });

/** The current OIDC user (null when not signed in / not in real auth mode). */
export function useAuthUser(): User | null {
  return useContext(AuthContext).user;
}
