import { DeleteOutlined } from '@ant-design/icons';
import { App, Button, Drawer, Empty, Flex, Select, Tag, Tooltip } from 'antd';
import SimpleList from '../SimpleList';
import { useState } from 'react';

import {
  grantShare,
  listShares,
  revokeShare,
  type Share,
  type SharePermission,
  updateShare,
  type UserSummary,
} from '../../api/shares';
import { useCurrentUser } from '../../auth/useCurrentUser';
import { useAsyncData } from '../../hooks/useAsyncData';
import { UserSearchSelect } from '../shared/UserSearchSelect';
import { AsyncBody } from '../AsyncBody';
import { errorMessage } from '../../utils/errors';

/** The grantable levels, in ladder order, with human labels. */
const PERMISSION_OPTIONS: { value: SharePermission; label: string }[] = [
  { value: 'view', label: 'Can view' },
  { value: 'edit', label: 'Can edit' },
];

/** Manage who can access a suite and at what level. */
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
  /** `null` when the creating user has been erased (#1319). An ownerless
   *  suite simply has nobody to exclude from the collaborator picker. */
  ownerId: string | null;
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
  /** `null` when the creating user has been erased (#1319). An ownerless
   *  suite simply has nobody to exclude from the collaborator picker. */
  ownerId: string | null;
  canManage: boolean;
}) {
  const { state, reload } = useAsyncData(() => listShares(suiteId));
  // Best-effort UX lock on the signed-in user's own row (OIDC UPN ≈ their share `email`): a non-
  // owner admin self-revoking/-downgrading would brick the panel (every later mutation 403s).
  const currentEmail = useCurrentUser()?.username;

  return (
    <AsyncBody
      state={state}
      loadingText="Loading collaborators…"
      errorTitle="Failed to load collaborators"
    >
      {(shares) => (
        <Flex vertical gap={16}>
          {canManage && (
            <AddCollaborator
              suiteId={suiteId}
              // The owner already has access, so they are never offered as a collaborator.
              excludedIds={[ownerId, ...shares.map((s) => s.user_id)].filter(
                (id): id is string => id !== null,
              )}
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
                  isSelf={
                    !!currentEmail && share.email.toLowerCase() === currentEmail.toLowerCase()
                  }
                  onChanged={reload}
                />
              )}
            />
          )}
        </Flex>
      )}
    </AsyncBody>
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
                // A manager's own row is locked: self-revoke/-downgrade would 403 every later
                // mutation and brick the panel (backend rejects it too,
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
  const [picked, setPicked] = useState<UserSummary>();
  const [permission, setPermission] = useState<SharePermission>('view');
  // A Viewer cannot hold `edit` (ADR 0033): the backend rejects the grant, and
  // `effective_permission` caps them at `view` regardless.
  const userId = picked?.id;
  const targetIsViewer = picked?.role === 'viewer';
  const [adding, setAdding] = useState(false);

  const onAdd = async () => {
    if (!userId) return;
    setAdding(true);
    try {
      const share = await grantShare(suiteId, {
        user_id: userId,
        permission: targetIsViewer ? 'view' : permission,
      });
      message.success(`${share.email}: shared`);
      setPicked(undefined);
      setPermission('view');
      onAdded();
    } catch (err) {
      message.error(`Share failed: ${errorMessage(err)}`);
    } finally {
      setAdding(false);
    }
  };

  return (
    <Flex gap={8} align="center" wrap>
      <UserSearchSelect
        value={picked}
        onChange={setPicked}
        exclude={(u) => excludedIds.includes(u.id)}
        style={{ flex: 1, minWidth: 160 }}
      />
      <Select
        value={targetIsViewer ? 'view' : permission}
        options={PERMISSION_OPTIONS.map((o) => ({
          ...o,
          disabled: targetIsViewer && o.value === 'edit',
        }))}
        onChange={setPermission}
        disabled={targetIsViewer}
        title={
          targetIsViewer
            ? 'Workspace viewers are read-only — change their role to member to grant edit'
            : undefined
        }
        style={{ width: 110 }}
      />
      <Button type="primary" loading={adding} disabled={!userId} onClick={onAdd}>
        Add
      </Button>
    </Flex>
  );
}
