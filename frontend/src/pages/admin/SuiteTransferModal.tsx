import { App, Alert, Checkbox, Flex, Modal, Typography } from 'antd';
import { useState } from 'react';

import { type AdminSuite, transferAdminSuite } from '../../api/admin';
import type { UserSummary } from '../../api/shares';
import { UserSearchSelect } from '../../components/shared/UserSearchSelect';
import { useAsyncAction } from '../../hooks/useAsyncAction';

/** Hand a suite to another user (#1698) — the offboarding primitive. Mount it
 *  keyed on the suite so the picker state never survives a different one being opened. */
export function SuiteTransferModal({
  suite,
  onClose,
  onTransferred,
}: {
  /** `null` closes the modal. */
  suite: AdminSuite | null;
  onClose: () => void;
  onTransferred: () => void;
}) {
  const { message } = App.useApp();
  const [picked, setPicked] = useState<UserSummary>();
  const [keepAccess, setKeepAccess] = useState(true);
  const { run, loading } = useAsyncAction('Transfer failed');

  const onOk = () => {
    if (!suite || !picked) return;
    const target = picked;
    void run(async () => {
      const result = await transferAdminSuite(suite.id, {
        new_owner_user_id: target.id,
        keep_previous_owner_access: keepAccess,
      });
      message.success(
        result.previous_owner_permission
          ? `${suite.name} now belongs to ${target.email}; the previous owner keeps ${result.previous_owner_permission} access`
          : `${suite.name} now belongs to ${target.email}`,
      );
      onTransferred();
      onClose();
    });
  };

  return (
    <Modal
      title={suite ? `Transfer “${suite.name}”` : 'Transfer suite'}
      open={suite !== null}
      onCancel={onClose}
      onOk={onOk}
      okText="Transfer"
      okButtonProps={{ disabled: !picked, loading }}
      destroyOnHidden
    >
      <Flex vertical gap={12}>
        <Typography.Text type="secondary">
          The new owner gets full control of the suite, its checks and its history.
        </Typography.Text>
        {/* A Viewer cannot own a suite (ADR 0033) and the current owner already does. */}
        <UserSearchSelect
          value={picked}
          onChange={setPicked}
          exclude={(u) => u.role === 'viewer' || u.id === suite?.owner_id}
          ariaLabel="New owner"
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
