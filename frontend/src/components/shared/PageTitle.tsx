import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';

import { pageTitleFor } from '../../utils/pageTitle';

/** Titles the document from the path. Mounted inside the auth gate on purpose: a signed-out user
 *  holding a deep link sees the sign-in screen, and the title must not name a page that is not
 *  on screen. */
export function PageTitle(): null {
  const { pathname } = useLocation();
  useEffect(() => {
    document.title = pageTitleFor(pathname);
  }, [pathname]);
  return null;
}
