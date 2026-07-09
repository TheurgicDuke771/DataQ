import { DeleteOutlined } from '@ant-design/icons';
import { App, Alert, Button, Drawer, Empty, Flex, Select, Spin, Tag, Tooltip } from 'antd';
import SimpleList from '../SimpleList';
import { useEffect, useRef, useState } from 'react';

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
import { useCurrentUser } from '../../auth/useCurrentUser';
import { useAsyncData } from '../../hooks/useAsyncData';
import { errorMessage } from '../../utils/errors';

/** The grantable levels, in ladder order, with human labels. `admin` is the
 *  workspace-admin (implicit on every suite, never granted) and `owner` is the
 *  creator — neither is grantable (ADR 0027 / #482). */
const PERMISSION_OPTIONS: { value: SharePermission; label: string }[] = [
  { value: 'view', label: 'Can view' },
  { value: 'edit', label: 'Can edit' },
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
    // `destroyOnHidden` unmounts the body on close, so it (and its share-list
    // fetch) starts fresh on each open — matching the other drawers in the app.
    <Drawer title="Share suite" open={open} onClose={onClose} size={480} destroyOnHidden>
      <SharePanelBody suiteId={suiteId} ownerId={ownerId} canManage={canManage} />
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
  // Best-effort UX lock on the signed-in user's own row (OIDC UPN ≈ their share
  // `email`): a non-owner admin self-revoking/-downgrading would brick the panel
  // (every later mutation 403s). The durable guard is server-side
  // (share_service._reject_self_target) since UPN can differ from mail and the
  // API is reachable directly; this just hides the footgun in the common case. #240.
  const currentEmail = useCurrentUser()?.username;

  if (state.status === 'loading') {
    return <Spin description="Loading collaborators…" />;
  }
  if (state.status === 'error') {
    return (
      <Alert type="error" showIcon title="Failed to load collaborators" description={state.error} />
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
        <SimpleList
          dataSource={shares}
          renderItem={(share) => (
            <ShareRow
              key={share.user_id}
              suiteId={suiteId}
              share={share}
              canManage={canManage}
              isSelf={!!currentEmail && share.email.toLowerCase() === currentEmail.toLowerCase()}
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
  isSelf,
  onChanged,
}: {
  suiteId: string;
  share: Share;
  canManage: boolean;
  /** This row is the signed-in user — lock it so they can't remove their own access. */
  isSelf: boolean;
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
      message.error(`Update failed: ${errorMessage(err)}`);
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
      message.error(`Remove failed: ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <SimpleList.Item
      actions={
        !canManage
          ? // Read-only for anyone without manage rights — including their own row.
            [<Tag key="perm">{share.permission}</Tag>]
          : isSelf
            ? [
                // A manager's own row is locked: self-revoke/-downgrade would 403
                // every later mutation and brick the panel (backend rejects it too,
                // share_service._reject_self_target). #240.
                <Tooltip key="perm" title="You can’t change your own access">
                  <Tag>{share.permission} · You</Tag>
                </Tooltip>,
              ]
            : [
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
      }
    >
      <SimpleList.Item.Meta
        title={share.display_name ?? share.email}
        description={share.display_name ? share.email : undefined}
      />
    </SimpleList.Item>
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
  // Monotonic token so a slow earlier search can't overwrite a newer one's
  // results (last-wins); unmount bumps it to a sentinel to drop any in-flight
  // response, and clears the pending debounce so it never fires post-unmount.
  const latest = useRef(0);
  useEffect(
    () => () => {
      clearTimeout(timer.current);
      latest.current = -1;
    },
    [],
  );

  // Debounce the directory query so a fast typist fires one search, not one per
  // keystroke. The 2-char floor mirrors the backend (a shorter query returns []).
  const onSearch = (raw: string) => {
    const q = raw.trim();
    clearTimeout(timer.current);
    if (q.length < 2) {
      setOptions([]);
      setSearching(false); // a pending debounce was cancelled — drop its spinner
      return;
    }
    setSearching(true);
    const token = (latest.current += 1);
    timer.current = setTimeout(() => {
      searchUsers(q)
        .then((users) => {
          if (token !== latest.current) return; // superseded by a newer search
          setOptions(users.filter((u) => !excludedIds.includes(u.id)));
        })
        .catch(() => {
          if (token === latest.current) setOptions([]);
        })
        .finally(() => {
          if (token === latest.current) setSearching(false);
        });
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
      message.error(`Share failed: ${errorMessage(err)}`);
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
