import { Alert, Form, Input, Modal, Select, Typography } from 'antd';
import { useState } from 'react';

import {
  type MemberAdded,
  WORKSPACE_ROLES,
  type WorkspaceRole,
  addWorkspaceMember,
} from '../../api/admin';
import { authMode } from '../../auth/config';
import { fetchFailure } from '../../utils/errors';

/** Admit an address to the workspace. Deliberately says what this does NOT do:
 *  with an identity provider in front, the account must exist there first. */
export function AddMemberModal({
  open,
  onClose,
  onAdded,
  enforcementActive,
  unmanagedUserCount,
}: {
  open: boolean;
  onClose: () => void;
  onAdded: (result: MemberAdded) => void;
  enforcementActive: boolean;
  unmanagedUserCount: number;
}) {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<WorkspaceRole>('member');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      const result = await addWorkspaceMember(email.trim(), role);
      setEmail('');
      setRole('member');
      onAdded(result);
      onClose();
    } catch (err: unknown) {
      setError(fetchFailure(err, String(err)).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title="Add member"
      okText="Add"
      onOk={submit}
      confirmLoading={saving}
      onCancel={onClose}
      okButtonProps={{ disabled: !email.trim() }}
      destroyOnHidden
    >
      <Form layout="vertical">
        {authMode === 'real' && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            title="They need an account with your identity provider"
            description={
              'DataQ admits people to this workspace. It does not create accounts. ' +
              'If they cannot sign in with your identity provider yet, add them there first.'
            }
          />
        )}
        {!enforcementActive && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            title="This turns membership enforcement on"
            description={
              `Adding the first managed member turns enforcement on and imports your ` +
              `${unmanagedUserCount} existing user${unmanagedUserCount === 1 ? '' : 's'} ` +
              'as members for review, so nobody currently signed in loses access. ' +
              'After that, only people on this list (or on the deployment allowlist) may sign in.'
            }
          />
        )}
        <Form.Item label="Email address" required>
          <Input
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="person@example.com"
            autoComplete="off"
          />
        </Form.Item>
        <Form.Item label="Initial role">
          <Select
            value={role}
            onChange={setRole}
            options={WORKSPACE_ROLES.map((r) => ({ value: r, label: r }))}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Applied when they first sign in. Change it in the members table after that.
          </Typography.Text>
        </Form.Item>
        {error && <Alert type="error" showIcon title={error} />}
      </Form>
    </Modal>
  );
}
