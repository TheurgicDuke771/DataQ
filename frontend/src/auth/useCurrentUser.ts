import { useContext } from 'react';

import { CurrentUserContext, type CurrentUser } from './currentUserContext';

export function useCurrentUser(): CurrentUser | null {
  return useContext(CurrentUserContext);
}
