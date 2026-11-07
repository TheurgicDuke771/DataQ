import type { ReactNode } from 'react';
import { useParams } from 'react-router-dom';

import { NotFound } from '../pages/NotFound';
import { isUuid } from '../utils/uuid';

/**
 * Route guard: a page that fetches by a URL id renders the 404 state for a malformed one
 * instead of issuing a request with it.
 */
export function RequireUuidParams({ children }: { children: ReactNode }) {
  const params = useParams();
  const ids = Object.entries(params).filter(([key]) => key !== '*');
  if (ids.some(([, value]) => !isUuid(value))) return <NotFound />;

  return <>{children}</>;
}
