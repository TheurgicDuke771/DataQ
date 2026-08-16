import { App, Select, Space, Tag, Tooltip } from 'antd';
import { useState } from 'react';

import {
  type AdminUser,
  type WorkspaceRole,
  WORKSPACE_ROLES,
  setAdminUserRole,
} from '../../api/admin';
import { errorMessage } from '../../utils/errors';

/**
 * Inline workspace-role editor for one row of the Admin → Members table
 * (ADR 0033, #742).
 *
 * Three things this deliberately does NOT do:
 *
 * 1. **It does not pre-judge whether a change is allowed.** The last-admin guard
 *    lives on the server, under a row lock, and is the only place that can decide
 *    correctly — a client-side "is this the last admin?" check would race, and
 *    would also disagree with the server about whether allowlist admins count
 *    (they deliberately do not). So the select stays enabled and the server's
 *    refusal is surfaced verbatim. Both refusals it can return explain what to do
 *    instead, which is worth more than a disabled control that explains nothing.
 *
 * 2. **It does not optimistically update.** A role change that appears to succeed
 *    and then silently reverts is the exact failure this feature must not have;
 *    the row is replaced with the server's own response, or left untouched.
 *
 * 3. **It does not show the *effective* role.** `user.role` is the stored column.
 *    A break-glass allowlist admin reads `member` here and carries the
 *    "via allowlist" tag beside it — showing `admin` would make the editor look
 *    broken when demoting them changes nothing visible.
 */
export function RoleEditor({
  user,
  onChanged,
}: {
  user: AdminUser;
  onChanged: (updated: AdminUser) => void;
}) {
  // `App.useApp()`, not antd's static `message` — the static API is detached
  // from the theme/context provider and antd v6 rejects it outright (it threw
  // here and took the whole Admin page's render down with it, which is how the
  // convention got noticed).
  const { message } = App.useApp();
  const [saving, setSaving] = useState(false);

  async function change(role: WorkspaceRole) {
    if (role === user.role) return;
    setSaving(true);
    try {
      const updated = await setAdminUserRole(user.id, role);
      onChanged(updated);
      message.success(`${user.email} is now ${role}`);
    } catch (err) {
      // The server's message is the useful one ("cannot remove the last workspace
      // admin — promote another user first"; "the dev-bypass identity's role
      // cannot be changed"). A generic "failed to update role" would discard
      // precisely the part that tells the admin what to do.
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
