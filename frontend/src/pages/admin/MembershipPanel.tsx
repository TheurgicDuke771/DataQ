import { Alert, Button, Flex, Modal, Popconfirm, Space, Tag, Typography } from 'antd';
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
import { fetchFailure } from '../../utils/errors';
import { AddMemberModal } from './AddMemberModal';
import { DataTable, Section } from './parts';

/** Who is admitted to the workspace (ADR 0043) — the axis beside the role editor. */
export function MembershipPanel() {
  const { state, reload } = useAsyncData(listWorkspaceMembers);
  const me = useMe();
  const [adding, setAdding] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const myEmail = me.status === 'ok' ? me.data.email.toLowerCase() : null;
  const view: MembershipView | null = state.status === 'ok' ? state.data : null;
  const imported = view ? view.members.filter((m) => m.source === 'auto_import') : [];

  const act = async (id: string, run: () => Promise<unknown>) => {
    setBusyId(id);
    setError(null);
    try {
      await run();
      reload();
    } catch (err: unknown) {
      setError(fetchFailure(err, String(err)).message);
    } finally {
      setBusyId(null);
    }
  };

  const remove = (member: WorkspaceMember) => {
    const isSelf = myEmail !== null && member.email.toLowerCase() === myEmail;
    if (!isSelf) {
      void act(member.id, () => removeWorkspaceMember(member.id));
      return;
    }
    Modal.confirm({
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
      {error && (
        <Alert type="error" showIcon title={error} closable={{ onClose: () => setError(null) }} />
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
            <Flex vertical gap={8}>
              <Typography.Text>
                These were admitted automatically when enforcement was turned on, so nobody lost
                access. A user row proves somebody signed in once, not that they still belong here.
              </Typography.Text>
              {imported.map((m) => (
                <Flex key={m.id} gap={8} align="center" wrap>
                  <Typography.Text code>{m.email}</Typography.Text>
                  <Button
                    size="small"
                    loading={busyId === m.id}
                    onClick={() => act(m.id, () => confirmWorkspaceMember(m.id))}
                  >
                    Confirm
                  </Button>
                  <Popconfirm
                    title="Remove this member?"
                    description="They lose access on their next request, including any API keys."
                    okText="Remove member"
                    okButtonProps={{ danger: true }}
                    onConfirm={() => remove(m)}
                  >
                    <Button size="small" danger loading={busyId === m.id}>
                      Remove
                    </Button>
                  </Popconfirm>
                </Flex>
              ))}
            </Flex>
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
        columns={columns(busyId, remove)}
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
): ColumnsType<WorkspaceMember> => [
  { title: 'Email', dataIndex: 'email' },
  { title: 'Initial role', dataIndex: 'initial_role' },
  {
    title: 'Source',
    dataIndex: 'source',
    render: (source: string) =>
      source === 'auto_import' ? (
        <Tag color="orange">imported — review</Tag>
      ) : (
        <Tag color="blue">added</Tag>
      ),
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
        <Popconfirm
          title="Remove this member?"
          description="They lose access on their next request, including any API keys they hold."
          okText="Remove member"
          okButtonProps={{ danger: true }}
          onConfirm={() => remove(m)}
        >
          <Button size="small" danger loading={busyId === m.id}>
            Remove
          </Button>
        </Popconfirm>
      </Space>
    ),
  },
];
