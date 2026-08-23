import { App, Select, Space, Tag, Tooltip } from 'antd';
import { useState } from 'react';

import {
  type AdminUser,
  type WorkspaceRole,
  WORKSPACE_ROLES,
  setAdminUserRole,
} from '../../api/admin';
import { fetchMe } from '../../api/me';
import { useMe, useUpdateMe } from '../../auth/useMe';
import { errorMessage } from '../../utils/errors';

/** Inline workspace-role editor for one row of the Admin → Members table (ADR 0033, #742). */
export function RoleEditor({
  user,
  onChanged,
}: {
  user: AdminUser;
  onChanged: (updated: AdminUser) => void;
}) {
  // `App.useApp()`, not antd's static `message`.
  const { message } = App.useApp();
  const me = useMe();
  const updateMe = useUpdateMe();
  const [saving, setSaving] = useState(false);

  async function change(role: WorkspaceRole) {
    if (role === user.role) return;
    setSaving(true);
    try {
      const updated = await setAdminUserRole(user.id, role);
      onChanged(updated);
      message.success(`${user.email} is now ${role}`);
      if (me.status === 'ok' && me.data.id === user.id) {
        // Self-change: refetch rather than patching the context locally, because `/me` reports the
        // EFFECTIVE role.
        updateMe(await fetchMe());
      }
    } catch (err) {
      // The server's message is the useful one ("cannot remove the last workspace admin — promote
      // another user first"; "the dev-bypass identity's role cannot be changed").
      message.error(`Could not change role: ${errorMessage(err)}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Space size={4}>
      <Select<WorkspaceRole>
        value={user.role}
        onChange={change}
        loading={saving}
        disabled={saving}
        size="small"
        style={{ width: 108 }}
        options={WORKSPACE_ROLES.map((r) => ({ value: r, label: r }))}
        aria-label={`Workspace role for ${user.email}`}
      />
      {user.allowlist_admin && (
        <Tooltip
          title={
            'This address is in WORKSPACE_ADMIN_EMAILS, so it resolves to Admin ' +
            'regardless of the role stored here. Remove it from that environment ' +
            'variable to make the stored role take effect.'
          }
        >
          <Tag color="gold">via allowlist</Tag>
        </Tooltip>
      )}
    </Space>
  );
}
