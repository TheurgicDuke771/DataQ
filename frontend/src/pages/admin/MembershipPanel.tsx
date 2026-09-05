import { Alert, App, Button, Flex, Popconfirm, Space, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';

import {
  type MembershipView,
  type WorkspaceMember,
  confirmWorkspaceMember,
  listWorkspaceMembers,
  removeWorkspaceMember,
} from '../../api/admin';
import { useMe } from '../../auth/useMe';
import { formatTimestamp } from '../../components/results/resultsFormat';
import { useAsyncData } from '../../hooks/useAsyncData';
import { errorMessage } from '../../utils/errors';
import { AddMemberModal } from './AddMemberModal';
import { DataTable, Section } from './parts';

/** Who is admitted to the workspace (ADR 0043) — the axis beside the role editor. */
export function MembershipPanel() {
  const { state, reload } = useAsyncData(listWorkspaceMembers);
  const me = useMe();
  // `App.useApp()`, not the static `Modal`/`message` — the static ones render
  // outside the theme provider, which is the rule RoleEditor already follows.
  const { modal, message } = App.useApp();
  const [adding, setAdding] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const myEmail = me.status === 'ok' ? me.data.email.toLowerCase() : null;
  const view: MembershipView | null = state.status === 'ok' ? state.data : null;
  const imported = view ? view.members.filter((m) => m.source === 'auto_import') : [];

  const act = async (id: string, run: () => Promise<unknown>) => {
    setBusyId(id);
    try {
      await run();
      reload();
    } catch (err: unknown) {
      message.error(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  const confirm = (member: WorkspaceMember) =>
    void act(member.id, () => confirmWorkspaceMember(member.id));

  const remove = (member: WorkspaceMember) => {
    const isSelf = myEmail !== null && member.email.toLowerCase() === myEmail;
    if (!isSelf) {
      void act(member.id, () => removeWorkspaceMember(member.id));
      return;
    }
    modal.confirm({
      title: 'Remove your own membership?',
      content:
        'You will be signed out of this workspace on your next request, and every API key ' +
        'you hold stops working. Another admin has to add you back.',
      okText: 'Remove my membership',
      okButtonProps: { danger: true },
      onOk: () => act(member.id, () => removeWorkspaceMember(member.id, true)),
    });
  };

  return (
    <Section title="Workspace membership">
      {view && view.enforcement_active && !view.enforced && (
        <Alert
          type="warning"
          showIcon
          title="Membership is not enforced on this stack"
          description={
            view.enforced_reason
              ? `${view.enforced_reason} — the list below is recorded, but no door is checking it.`
              : 'No door is checking the list below.'
          }
        />
      )}

      {view && !view.enforcement_active && (
        <Alert
          type="info"
          showIcon
          title="Membership is not enforced yet"
          description={
            `Who may sign in is decided entirely by this deployment's allowlist settings. ` +
            `Adding the first member turns enforcement on and imports your ` +
            `${view.unmanaged_user_count} existing user${view.unmanaged_user_count === 1 ? '' : 's'} for review.`
          }
        />
      )}

      {imported.length > 0 && (
        <Alert
          type="warning"
          showIcon
          title={`Review ${imported.length} imported member${imported.length === 1 ? '' : 's'}`}
          description={
            'These were admitted automatically when enforcement was turned on, so nobody lost ' +
            'access. A user row proves somebody signed in once, not that they still belong here. ' +
            'Confirm or remove each one in the table below.'
          }
        />
      )}

      {view && view.env_allowed_domains.length > 0 && (
        <Alert
          type="info"
          showIcon
          title="Some addresses are admitted by domain"
          description={
            `Anyone at ${view.env_allowed_domains.join(', ')} is admitted by this deployment's ` +
            'environment. Those people cannot be listed or removed here — edit the allowlist ' +
            'variable and restart.'
          }
        />
      )}

      <Flex justify="flex-end">
        <Button type="primary" onClick={() => setAdding(true)}>
          Add member
        </Button>
      </Flex>

      <DataTable
        state={state.status === 'ok' ? { status: 'ok', data: state.data.members } : state}
        columns={columns(busyId, remove, confirm)}
        rowKey={(m) => m.id}
        errorMessage="Failed to load workspace members"
      />

      <AddMemberModal
        open={adding}
        onClose={() => setAdding(false)}
        onAdded={() => reload()}
        enforcementActive={view?.enforcement_active ?? true}
        unmanagedUserCount={view?.unmanaged_user_count ?? 0}
      />
    </Section>
  );
}

const columns = (
  busyId: string | null,
  remove: (m: WorkspaceMember) => void,
  confirm: (m: WorkspaceMember) => void,
): ColumnsType<WorkspaceMember> => [
  { title: 'Email', dataIndex: 'email' },
  { title: 'Initial role', dataIndex: 'initial_role' },
  {
    title: 'Source',
    dataIndex: 'source',
    render: (source: string) => {
      if (source === 'auto_import') return <Tag color="orange">imported — review</Tag>;
      if (source === 'env') return <Tag>listed in the environment</Tag>;
      return <Tag color="blue">added</Tag>;
    },
  },
  {
    title: 'Added by',
    dataIndex: 'invited_by_email',
    render: (v: string | null) => v ?? '—',
  },
  {
    title: 'Added',
    dataIndex: 'created_at',
    render: (v: string) => formatTimestamp(v),
  },
  {
    title: 'Status',
    dataIndex: 'status',
    render: (status: string) =>
      status === 'active' ? <Tag color="green">active</Tag> : <Tag>pending first sign-in</Tag>,
  },
  {
    title: '',
    key: 'actions',
    render: (_, m) => (
      <Space>
        {m.source === 'auto_import' && (
          <Button size="small" loading={busyId === m.id} onClick={() => confirm(m)}>
            Confirm
          </Button>
        )}
        {m.removable ? (
          <Popconfirm
            title="Remove this member?"
            description={
              m.env_listed
                ? 'An env var also names this address, so they keep access until it is removed there too.'
                : 'They lose access on their next request, including any API keys they hold.'
            }
            okText="Remove member"
            okButtonProps={{ danger: true }}
            onConfirm={() => remove(m)}
          >
            <Button size="small" danger loading={busyId === m.id}>
              Remove
            </Button>
          </Popconfirm>
        ) : (
          <Tooltip title="Listed in the environment — remove it there">
            <Button size="small" danger disabled>
              Remove
            </Button>
          </Tooltip>
        )}
      </Space>
    ),
  },
];
