import { DeleteOutlined } from '@ant-design/icons';
import { App, Alert, Button, Drawer, Empty, Flex, List, Select, Spin, Tag } from 'antd';
import { useRef, useState } from 'react';

import {
  grantShare,
  listShares,
  revokeShare,
  type Share,
  type SharePermission,
  searchUsers,
  updateShare,
  type UserSummary,
} from '../../api/shares';
import { useAsyncData } from '../../hooks/useAsyncData';

/** The three grantable levels, in ladder order, with human labels. */
const PERMISSION_OPTIONS: { value: SharePermission; label: string }[] = [
  { value: 'view', label: 'Can view' },
  { value: 'edit', label: 'Can edit' },
  { value: 'admin', label: 'Admin' },
];

/**
 * Manage who can access a suite and at what level. Anyone with `view` can see
 * the collaborator list; only `owner`/`admin` (`canManage`) gets the add/change/
 * remove controls — matching the backend gate, which 403s an under-privileged
 * mutation regardless. Mounted only while open (`destroyOnHidden`), so the share
 * list refetches each time it's opened.
 */
export function SharePanel({
  open,
  suiteId,
  ownerId,
  canManage,
  onClose,
}: {
  open: boolean;
  suiteId: string;
  /** The suite's `created_by` — the owner can't be added as a share. */
  ownerId: string;
  canManage: boolean;
  onClose: () => void;
}) {
  return (
    <Drawer title="Share suite" open={open} onClose={onClose} width={480} destroyOnHidden>
      {open && <SharePanelBody suiteId={suiteId} ownerId={ownerId} canManage={canManage} />}
    </Drawer>
  );
}

function SharePanelBody({
  suiteId,
  ownerId,
  canManage,
}: {
  suiteId: string;
  ownerId: string;
  canManage: boolean;
}) {
  const { state, reload } = useAsyncData(() => listShares(suiteId));

  if (state.status === 'loading') {
    return <Spin tip="Loading collaborators…" />;
  }
  if (state.status === 'error') {
    return (
      <Alert
        type="error"
        showIcon
        message="Failed to load collaborators"
        description={state.error}
      />
    );
  }
  const shares = state.data;

  return (
    <Flex vertical gap={16}>
      {canManage && (
        <AddCollaborator
          suiteId={suiteId}
          excludedIds={[ownerId, ...shares.map((s) => s.user_id)]}
          onAdded={reload}
        />
      )}
      {shares.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Not shared with anyone yet." />
      ) : (
        <List
          dataSource={shares}
          renderItem={(share) => (
            <ShareRow
              key={share.user_id}
              suiteId={suiteId}
              share={share}
              canManage={canManage}
              onChanged={reload}
            />
          )}
        />
      )}
    </Flex>
  );
}

function ShareRow({
  suiteId,
  share,
  canManage,
  onChanged,
}: {
  suiteId: string;
  share: Share;
  canManage: boolean;
  onChanged: () => void;
}) {
  const { message } = App.useApp();
  const [busy, setBusy] = useState(false);

  const onPermissionChange = async (permission: SharePermission) => {
    setBusy(true);
    try {
      await updateShare(suiteId, share.user_id, permission);
      message.success(`${share.email}: ${permission}`);
      onChanged();
    } catch (err) {
      message.error(`Update failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setBusy(false);
    }
  };

  const onRevoke = async () => {
    setBusy(true);
    try {
      await revokeShare(suiteId, share.user_id);
      message.success(`${share.email}: removed`);
      onChanged();
    } catch (err) {
      message.error(`Remove failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <List.Item
      actions={
        canManage
          ? [
              <Select
                key="perm"
                size="small"
                value={share.permission}
                options={PERMISSION_OPTIONS}
                disabled={busy}
                onChange={onPermissionChange}
                style={{ width: 110 }}
              />,
              <Button
                key="remove"
                size="small"
                type="text"
                danger
                icon={<DeleteOutlined />}
                loading={busy}
                onClick={onRevoke}
                aria-label={`Remove ${share.email}`}
              />,
            ]
          : [<Tag key="perm">{share.permission}</Tag>]
      }
    >
      <List.Item.Meta
        title={share.display_name ?? share.email}
        description={share.display_name ? share.email : undefined}
      />
    </List.Item>
  );
}

function AddCollaborator({
  suiteId,
  excludedIds,
  onAdded,
}: {
  suiteId: string;
  /** Owner + already-shared users — hidden from the picker (backend rejects them too). */
  excludedIds: string[];
  onAdded: () => void;
}) {
  const { message } = App.useApp();
  const [options, setOptions] = useState<UserSummary[]>([]);
  const [searching, setSearching] = useState(false);
  const [userId, setUserId] = useState<string>();
  const [permission, setPermission] = useState<SharePermission>('view');
  const [adding, setAdding] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Debounce the directory query so a fast typist fires one search, not one per
  // keystroke. The 2-char floor mirrors the backend (a shorter query returns []).
  const onSearch = (raw: string) => {
    const q = raw.trim();
    clearTimeout(timer.current);
    if (q.length < 2) {
      setOptions([]);
      return;
    }
    setSearching(true);
    timer.current = setTimeout(() => {
      searchUsers(q)
        .then((users) => setOptions(users.filter((u) => !excludedIds.includes(u.id))))
        .catch(() => setOptions([]))
        .finally(() => setSearching(false));
    }, 300);
  };

  const onAdd = async () => {
    if (!userId) return;
    setAdding(true);
    try {
      const share = await grantShare(suiteId, { user_id: userId, permission });
      message.success(`${share.email}: shared`);
      setUserId(undefined);
      setOptions([]);
      setPermission('view');
      onAdded();
    } catch (err) {
      message.error(`Share failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setAdding(false);
    }
  };

  return (
    <Flex gap={8} align="center">
      <Select
        showSearch
        value={userId}
        placeholder="Search by email or name"
        filterOption={false}
        onSearch={onSearch}
        onChange={setUserId}
        notFoundContent={searching ? <Spin size="small" /> : null}
        options={options.map((u) => ({
          value: u.id,
          label: u.display_name ? `${u.display_name} · ${u.email}` : u.email,
        }))}
        style={{ flex: 1 }}
      />
      <Select
        value={permission}
        options={PERMISSION_OPTIONS}
        onChange={setPermission}
        style={{ width: 110 }}
      />
      <Button type="primary" loading={adding} disabled={!userId} onClick={onAdd}>
        Add
      </Button>
    </Flex>
  );
}
