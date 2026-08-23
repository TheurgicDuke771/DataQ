import type { ReactNode } from 'react';

import type { WorkspaceRole } from '../../src/api/admin';
import type { MeResponse } from '../../src/api/me';
import { MeContext } from '../../src/auth/meContext';

/** Wrap a tree in a resolved `/me` at a given workspace role (ADR 0033, #743). */
function meAt(role: WorkspaceRole): MeResponse {
  return {
    id: 'me-1',
    aad_object_id: 'oid-1',
    email: `${role}@dataq.local`,
    display_name: 'Test User',
    last_seen_at: null,
    role,
    is_workspace_admin: role === 'admin',
  };
}

export function WithMe({
  role = 'admin',
  children,
}: {
  role?: WorkspaceRole;
  children: ReactNode;
}) {
  return (
    <MeContext.Provider value={{ status: 'ok', data: meAt(role) }}>{children}</MeContext.Provider>
  );
}
