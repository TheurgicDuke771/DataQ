import { App, Alert, Checkbox, Flex, Modal, Select, Spin, Typography } from 'antd';
import { useEffect, useRef, useState } from 'react';

import { type AdminSuite, transferAdminSuite } from '../../api/admin';
import { searchUsers, type UserSummary } from '../../api/shares';
import { errorMessage } from '../../utils/errors';

/** Hand a suite to another user (#1698) — the offboarding primitive. Mount it
 *  keyed on the suite: the picker state is per-suite and must not survive a
 *  different one being opened. */
export function SuiteTransferModal({
  suite,
  onClose,
  onTransferred,
}: {
  /** `null` closes the modal — the picker state is rebuilt on each open. */
  suite: AdminSuite | null;
  onClose: () => void;
  onTransferred: () => void;
}) {
  const { message } = App.useApp();
  const [options, setOptions] = useState<UserSummary[]>([]);
  const [searching, setSearching] = useState(false);
  const [picked, setPicked] = useState<UserSummary>();
  const [keepAccess, setKeepAccess] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  // Monotonic token so a slow earlier search can't overwrite a newer one (last-wins).
  const latest = useRef(0);

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
          // A workspace Viewer cannot own a suite (ADR 0033) and the current owner
          // already does — the backend rejects both, so neither is offered.
          setOptions(users.filter((u) => u.role !== 'viewer' && u.id !== suite?.owner_id));
        })
        .catch(() => {
          if (token === latest.current) setOptions([]);
        })
        .finally(() => {
          if (token === latest.current) setSearching(false);
        });
    }, 300);
  };

  const onOk = async () => {
    if (!suite || !picked) return;
    setSubmitting(true);
    try {
      const result = await transferAdminSuite(suite.id, {
        new_owner_user_id: picked.id,
        keep_previous_owner_access: keepAccess,
      });
      message.success(
        result.previous_owner_permission
          ? `${suite.name} now belongs to ${picked.email}; the previous owner keeps ${result.previous_owner_permission} access`
          : `${suite.name} now belongs to ${picked.email}`,
      );
      onTransferred();
      onClose();
    } catch (err) {
      message.error(`Transfer failed: ${errorMessage(err)}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title={suite ? `Transfer “${suite.name}”` : 'Transfer suite'}
      open={suite !== null}
      onCancel={onClose}
      onOk={onOk}
      okText="Transfer"
      okButtonProps={{ disabled: !picked, loading: submitting }}
      destroyOnHidden
    >
      <Flex vertical gap={12}>
        <Typography.Text type="secondary">
          The new owner gets full control of the suite, its checks and its history.
        </Typography.Text>
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
        <Checkbox checked={keepAccess} onChange={(e) => setKeepAccess(e.target.checked)}>
          Keep the previous owner’s access as an editor
        </Checkbox>
        {!keepAccess && (
          <Alert
            type="warning"
            showIcon
            title="The previous owner loses all access to this suite."
          />
        )}
        <Typography.Text type="secondary">
          Workspace viewers can’t own a suite, so they aren’t listed. Change their role first.
        </Typography.Text>
      </Flex>
    </Modal>
  );
}
