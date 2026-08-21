import { DeleteOutlined } from '@ant-design/icons';
import { App, Button, Drawer, Empty, Flex, Select, Spin, Tag, Tooltip } from 'antd';
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
import { AsyncBody } from '../AsyncBody';
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
  // Best-effort UX lock on the signed-in user's own row (OIDC UPN ≈ their share
  // `email`): a non-owner admin self-revoking/-downgrading would brick the panel
  // (every later mutation 403s). The durable guard is server-side
  // (share_service._reject_self_target) since UPN can differ from mail and the
  // API is reachable directly; this just hides the footgun in the common case. #240.
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
              // The owner already has access, so they are never offered as a
              // collaborator. With no owner there is nobody extra to exclude —
              // filtering keeps a stray `null` out of the id list rather than
              // silently matching a user whose id is nullish.
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
  // The PICKED USER, held in state rather than re-derived from `options` on each
  // render. `options` is the transient result of the last directory search, so a
  // derived lookup silently becomes `undefined` the moment the admin searches
  // again — which un-clamped the permission Select while a viewer was still
  // selected, and let Add POST `edit` for them: exactly the 422 this mirror
  // exists to avoid.
  const [picked, setPicked] = useState<UserSummary>();
  const [permission, setPermission] = useState<SharePermission>('view');
  // A Viewer cannot hold `edit` (ADR 0033): the backend rejects the grant, and
  // `effective_permission` caps them at `view` regardless. Mirrored here so the
  // level is never offered — the server stays authoritative, this only stops us
  // proposing something it will refuse.
  const userId = picked?.id;
  const targetIsViewer = picked?.role === 'viewer';
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
      // Never send `edit` for a Viewer even if state got there some other way —
      // the displayed value is already clamped above, and this keeps the request
      // consistent with what the user was shown.
      const share = await grantShare(suiteId, {
        user_id: userId,
        permission: targetIsViewer ? 'view' : permission,
      });
      message.success(`${share.email}: shared`);
      setPicked(undefined);
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
    // `wrap` + a search field that can actually shrink — the same shape TriggersPanel
    // and SchedulesPanel already use for their control rows; this row was the one
    // that never got it. An antd Select has a min-content width that `flex: 1` alone
    // can't shrink past, so on a phone (342px content box) the row demanded 383px and
    // pushed the Add button off-screen, making a suite unshareable from mobile (#829).
    // At that width the search field and the permission picker share line 1 (160 + 8 +
    // 110 ≤ 342) and the Add button wraps to line 2; at the drawer's desktop width the
    // whole row fits on one line.
    <Flex gap={8} align="center" wrap>
      <Select
        showSearch={{ filterOption: false, onSearch }}
        value={userId}
        placeholder="Search by email or name"
        onChange={(id: string) => setPicked(options.find((u) => u.id === id))}
        notFoundContent={searching ? <Spin size="small" /> : null}
        options={options.map((u) => ({
          value: u.id,
          label: u.display_name ? `${u.display_name} · ${u.email}` : u.email,
        }))}
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
        // The tooltip is what stops a disabled control being a dead end: it says
        // which lever actually changes the answer (their workspace role).
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
