import { createContext } from 'react';

export interface CurrentUser {
  name: string;
  username: string;
  homeAccountId: string;
  isDev: boolean;
}

export const CurrentUserContext = createContext<CurrentUser | null>(null);
