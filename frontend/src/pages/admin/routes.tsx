// Route-table module: the lazy page constants are components, the exported route trees are not.
/* eslint-disable react-refresh/only-export-components */
import { lazy } from 'react';
import { Navigate, Route } from 'react-router-dom';

import { RequireRole } from '../../auth/RequireRole';

const AdminLayout = lazy(() => import('./AdminLayout').then((m) => ({ default: m.AdminLayout })));
const AdminOverview = lazy(() =>
  import('./AdminOverview').then((m) => ({ default: m.AdminOverview })),
);
const AdminMembers = lazy(() =>
  import('./AdminMembers').then((m) => ({ default: m.AdminMembers })),
);
const AdminSuites = lazy(() => import('./AdminSuites').then((m) => ({ default: m.AdminSuites })));
const AdminSettings = lazy(() =>
  import('./AdminSettings').then((m) => ({ default: m.AdminSettings })),
);
const AdminCompliance = lazy(() =>
  import('./AdminCompliance').then((m) => ({ default: m.AdminCompliance })),
);
const AdminIntegrations = lazy(() =>
  import('./AdminIntegrations').then((m) => ({ default: m.AdminIntegrations })),
);

/** `/admin/:tab` (#1694). The gate sits on the parent route, so a deep link to any
 *  sub-page is refused before that page's hooks mount (ADR 0033, #743). */
export const ADMIN_ROUTES = (
  <Route
    path="/admin"
    element={
      <RequireRole
        minimum="admin"
        message="The admin control centre is restricted to workspace admins."
      >
        <AdminLayout />
      </RequireRole>
    }
  >
    <Route index element={<Navigate to="/admin/overview" replace />} />
    <Route path="overview" element={<AdminOverview />} />
    <Route path="members" element={<AdminMembers />} />
    <Route path="suites" element={<AdminSuites />} />
    <Route path="settings" element={<AdminSettings />} />
    <Route path="compliance" element={<AdminCompliance />} />
    <Route path="integrations" element={<AdminIntegrations />} />
    {/* Unknown sub-page → Overview, not a 404 under a tab bar with nothing selected. */}
    <Route path="*" element={<Navigate to="/admin/overview" replace />} />
  </Route>
);

/** The retired standalone Settings page now lives at `/admin/settings`; the old URL is
 *  gated the same way rather than redirecting an unauthorized caller into the admin area. */
export const SETTINGS_REDIRECT_ROUTE = (
  <Route
    path="/settings"
    element={
      <RequireRole minimum="admin" message="Workspace settings are restricted to workspace admins.">
        <Navigate to="/admin/settings" replace />
      </RequireRole>
    }
  />
);
