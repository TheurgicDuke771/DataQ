import { Flex, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';

import { type AdminAccess, type AdminUser, listAdminAccess, listAdminUsers } from '../../api/admin';
import { RoleEditor } from '../../components/admin/RoleEditor';
import { formatTimestamp } from '../../components/results/resultsFormat';
import { type AsyncState, useAsyncData } from '../../hooks/useAsyncData';
import { AccessGrantActions } from './AccessGrantActions';
import { MembershipPanel } from './MembershipPanel';
import { DataTable, Identity, Section } from './parts';

/** Workspace membership: stored roles (ADR 0033) + every per-suite grant (ADR 0027). */
export function AdminMembers() {
  const users = useAsyncData(listAdminUsers);
  const access = useAsyncData(listAdminAccess);
  // Rows the admin has just re-roled, keyed by id.
  const [rerolled, setRerolled] = useState<Record<string, AdminUser>>({});
  const userState = overlayUsers(users.state, rerolled);

  return (
    <Flex vertical gap={16}>
      <MembershipPanel />
      <Section title="Members">
        <DataTable
          state={userState}
          columns={userColumns((updated) =>
            setRerolled((prev) => ({ ...prev, [updated.id]: updated })),
          )}
          rowKey={(u) => u.id}
          errorMessage="Failed to load members"
        />
      </Section>
      <Section title="Access grants">
        <DataTable
          state={access.state}
          columns={accessColumns(access.reload)}
          // A user appears once per suite (owner or a single share row).
          rowKey={(a) => `${a.suite_id}:${a.user_id}`}
          errorMessage="Failed to load access overview"
        />
      </Section>
    </Flex>
  );
}

/** Replace any row the admin has just re-roled with the server's own response. */
function overlayUsers(
  state: AsyncState<AdminUser[]>,
  rerolled: Record<string, AdminUser>,
): AsyncState<AdminUser[]> {
  if (state.status !== 'ok' || Object.keys(rerolled).length === 0) return state;
  return { ...state, data: state.data.map((u) => rerolled[u.id] ?? u) };
}

/** A factory, not a constant, because the role cell needs the update callback. */
const userColumns = (onChanged: (u: AdminUser) => void): ColumnsType<AdminUser> => [
  {
    title: 'Member',
    key: 'user',
    render: (_, u) => <Identity name={u.display_name} email={u.email} />,
  },
  {
    title: 'Role',
    key: 'role',
    render: (_, u) => <RoleEditor user={u} onChanged={onChanged} />,
  },
  { title: 'Suites owned', dataIndex: 'owned_suite_count', align: 'right' },
  { title: 'Shared with them', dataIndex: 'shared_suite_count', align: 'right' },
  {
    title: 'Last seen',
    dataIndex: 'last_seen_at',
    render: (v: string | null) => formatTimestamp(v),
  },
];

const PERMISSION_COLORS: Record<string, string> = {
  owner: 'gold',
  admin: 'volcano',
  edit: 'blue',
  view: 'default',
};

const accessColumns = (onRevoked: () => void): ColumnsType<AdminAccess> => [
  { title: 'Suite', dataIndex: 'suite_name' },
  {
    title: 'User',
    key: 'user',
    render: (_, a) => <Identity name={a.user_name} email={a.user_email} />,
  },
  {
    title: 'Permission',
    dataIndex: 'permission',
    render: (p: string) => <Tag color={PERMISSION_COLORS[p] ?? 'default'}>{p}</Tag>,
  },
  {
    title: 'Actions',
    key: 'actions',
    render: (_, a) => <AccessGrantActions grant={a} onRevoked={onRevoked} />,
  },
];
