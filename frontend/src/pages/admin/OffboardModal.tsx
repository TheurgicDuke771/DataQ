import {
  Alert,
  App,
  Button,
  Descriptions,
  Flex,
  Input,
  List,
  Modal,
  Select,
  Spin,
  Tag,
  Typography,
} from 'antd';
import { useEffect, useRef, useState } from 'react';

import {
  type AdminUser,
  type OffboardPreview,
  type OffboardReceipt,
  offboardUser,
  previewOffboarding,
} from '../../api/admin';
import { searchUsers, type UserSummary } from '../../api/shares';
import { errorMessage } from '../../utils/errors';

/** Offboard a departing user in one pass: hand over their suites, revoke their
 *  PATs and browser sessions, withdraw their membership. Mount it keyed on the
 *  user — the preview and the picker are per-user and must not survive a
 *  different one being opened. */
export function OffboardModal({
  user,
  onClose,
  onOffboarded,
}: {
  /** `null` closes the modal. */
  user: AdminUser | null;
  onClose: () => void;
  onOffboarded: () => void;
}) {
  const { message } = App.useApp();
  const [preview, setPreview] = useState<OffboardPreview>();
  const [previewError, setPreviewError] = useState<string>();
  const [receipt, setReceipt] = useState<OffboardReceipt>();
  const [options, setOptions] = useState<UserSummary[]>([]);
  const [searching, setSearching] = useState(false);
  const [picked, setPicked] = useState<UserSummary>();
  const [typed, setTyped] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const latest = useRef(0);

  const userId = user?.id;

  // Fetch only. Per-user state is reset by REMOUNTING (the `key` in
  // `OffboardAction`), not by clearing it here — clearing state inside an effect
  // body cascades renders, and a stale receipt surviving into the next user's
  // modal is exactly the bug the key rules out.
  useEffect(() => {
    if (!userId) return;
    const controller = new AbortController();
    previewOffboarding(userId, controller.signal)
      .then(setPreview)
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setPreviewError(errorMessage(err));
      });
    return () => controller.abort();
  }, [userId]);

  useEffect(
    () => () => {
      clearTimeout(timer.current);
      latest.current = -1;
    },
    [],
  );

  const onSearch = (raw: string) => {
    const q = raw.trim();
    clearTimeout(timer.current);
    if (q.length < 2) {
      setOptions([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    const token = (latest.current += 1);
    timer.current = setTimeout(() => {
      searchUsers(q)
        .then((users) => {
          if (token !== latest.current) return;
          // A viewer cannot own a suite and the leaver cannot inherit their own —
          // the backend rejects both, so neither is offered.
          setOptions(users.filter((u) => u.role !== 'viewer' && u.id !== userId));
        })
        .catch(() => {
          if (token === latest.current) setOptions([]);
        })
        .finally(() => {
          if (token === latest.current) setSearching(false);
        });
    }, 300);
  };

  const needsOwner = (preview?.owned_suites.length ?? 0) > 0;
  const confirmed =
    preview !== undefined && typed.trim().toLowerCase() === preview.email.toLowerCase();
  const blocked = preview?.is_last_admin === true;
  const ready = confirmed && !blocked && (!needsOwner || picked !== undefined);

  const onOk = async () => {
    if (!userId || !preview || !ready) return;
    setSubmitting(true);
    try {
      const result = await offboardUser(userId, {
        new_owner_user_id: picked?.id ?? null,
        keep_previous_owner_access: false,
        confirm_email: preview.email,
      });
      setReceipt(result);
      message.success(`${preview.email} has been offboarded`);
      onOffboarded();
    } catch (err) {
      message.error(`Offboarding failed: ${errorMessage(err)}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title={user ? `Offboard ${user.email}` : 'Offboard user'}
      open={user !== null}
      onCancel={onClose}
      onOk={receipt ? onClose : onOk}
      okText={receipt ? 'Done' : 'Offboard'}
      okButtonProps={{
        danger: !receipt,
        disabled: !receipt && !ready,
        loading: submitting,
      }}
      cancelButtonProps={{ style: receipt ? { display: 'none' } : undefined }}
      destroyOnHidden
      width={620}
    >
      {receipt ? (
        <Receipt receipt={receipt} />
      ) : previewError ? (
        <Alert
          type="error"
          showIcon
          title="Could not load the preview"
          description={previewError}
        />
      ) : !preview ? (
        <Spin size="large" />
      ) : (
        <Flex vertical gap={12}>
          {blocked && (
            <Alert
              type="error"
              showIcon
              title="This is the last admin in the workspace"
              description="Promote another admin first — offboarding this account would leave nobody able to administer the workspace."
            />
          )}
          {preview.is_self && (
            <Alert
              type="warning"
              showIcon
              title="This is you"
              description="Offboarding your own account signs you out and revokes your own tokens."
            />
          )}
          <Descriptions size="small" column={1} bordered>
            <Descriptions.Item label="Suites they own">
              {preview.owned_suites.length}
            </Descriptions.Item>
            <Descriptions.Item label="Personal access tokens">
              {preview.open_api_key_count} live
            </Descriptions.Item>
            <Descriptions.Item label="Browser sessions">
              {preview.live_session_count} live
            </Descriptions.Item>
            <Descriptions.Item label="Workspace membership">
              <MembershipNote preview={preview} />
            </Descriptions.Item>
          </Descriptions>
          {needsOwner && (
            <>
              <List
                size="small"
                bordered
                header={<Typography.Text strong>These suites need a new owner</Typography.Text>}
                dataSource={preview.owned_suites}
                renderItem={(suite) => (
                  <List.Item>
                    <Typography.Text>{suite.name}</Typography.Text>
                    <Typography.Text type="secondary">
                      {suite.check_count} check(s) · {suite.run_count} run(s)
                    </Typography.Text>
                  </List.Item>
                )}
              />
              <Select
                showSearch={{ filterOption: false, onSearch }}
                value={picked?.id}
                placeholder="Search by email or name"
                onChange={(id: string) => setPicked(options.find((u) => u.id === id))}
                notFoundContent={searching ? <Spin size="small" /> : null}
                options={options.map((u) => ({
                  value: u.id,
                  label: u.display_name ? `${u.display_name} · ${u.email}` : u.email,
                }))}
                aria-label="New owner"
              />
              <Typography.Text type="secondary">
                The departing user keeps no access to the suites they hand over. Workspace viewers
                can’t own a suite, so they aren’t listed.
              </Typography.Text>
            </>
          )}
          <Typography.Text>
            Type <Typography.Text code>{preview.email}</Typography.Text> to confirm.
          </Typography.Text>
          <Input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={preview.email}
            aria-label="Confirm email address"
            disabled={blocked}
          />
          <Typography.Text type="secondary">
            Their authored history stays: the runs, results and checks they created keep their name
            on them, and the account itself is not deleted.
          </Typography.Text>
        </Flex>
      )}
    </Modal>
  );
}

function MembershipNote({ preview }: { preview: OffboardPreview }) {
  if (preview.membership_state === 'member') return <Tag color="blue">will be withdrawn</Tag>;
  return (
    <Flex vertical gap={4}>
      <Tag color="orange">
        {preview.membership_state === 'env_listed' ? 'env-listed' : 'not a member'}
      </Tag>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {preview.membership_note}
      </Typography.Text>
    </Flex>
  );
}

function Receipt({ receipt }: { receipt: OffboardReceipt }) {
  return (
    <Flex vertical gap={12}>
      <Alert type="success" showIcon title={`${receipt.email} has been offboarded`} />
      <Descriptions size="small" column={1} bordered>
        <Descriptions.Item label="Suites transferred">
          {receipt.transferred_suite_ids.length}
        </Descriptions.Item>
        <Descriptions.Item label="Tokens revoked">{receipt.api_keys_revoked}</Descriptions.Item>
        <Descriptions.Item label="Sessions revoked">{receipt.sessions_revoked}</Descriptions.Item>
        <Descriptions.Item label="Membership withdrawn">
          {receipt.membership_removed ? 'yes' : 'no'}
        </Descriptions.Item>
      </Descriptions>
      {receipt.skipped.length > 0 && (
        <List
          size="small"
          bordered
          header={<Typography.Text strong>Not done, and why</Typography.Text>}
          dataSource={receipt.skipped}
          renderItem={(step) => (
            <List.Item>
              <Typography.Text type="secondary">
                <Typography.Text code>{step.step}</Typography.Text> — {step.reason}
              </Typography.Text>
            </List.Item>
          )}
        />
      )}
    </Flex>
  );
}

/** The Members-table row action that opens the modal. */
export function OffboardAction({
  user,
  onOffboarded,
}: {
  user: AdminUser;
  onOffboarded: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button size="small" type="text" danger onClick={() => setOpen(true)}>
        Offboard
      </Button>
      <OffboardModal
        key={`${user.id}:${String(open)}`}
        user={open ? user : null}
        onClose={() => setOpen(false)}
        onOffboarded={onOffboarded}
      />
    </>
  );
}
