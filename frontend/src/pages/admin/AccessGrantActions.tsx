import { App, Button, Popconfirm, Tooltip } from 'antd';
import { useState } from 'react';

import { type AdminAccess, revokeAdminGrant } from '../../api/admin';
import { errorMessage } from '../../utils/errors';

/** Revoke any per-suite grant from the Members page (#1698). An owner row carries
 *  no `grant_id` — there is nothing to revoke, so the action explains itself
 *  rather than offering a button that always fails. */
export function AccessGrantActions({
  grant,
  onRevoked,
}: {
  grant: AdminAccess;
  onRevoked: () => void;
}) {
  const { message } = App.useApp();
  const [busy, setBusy] = useState(false);

  const grantId = grant.grant_id;
  if (grantId === null) {
    return (
      <Tooltip title="Ownership isn’t a grant — transfer the suite on the Suites tab to move it.">
        <Button size="small" type="text" disabled>
          Revoke
        </Button>
      </Tooltip>
    );
  }

  const onRevoke = async () => {
    setBusy(true);
    try {
      await revokeAdminGrant(grant.suite_id, grantId);
      message.success(`${grant.user_email}: access to ${grant.suite_name} revoked`);
      onRevoked();
    } catch (err) {
      message.error(`Revoke failed: ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Popconfirm
      title="Revoke this access?"
      description={`${grant.user_email} loses ${grant.permission} access to ${grant.suite_name}.`}
      okText="Revoke"
      okButtonProps={{ danger: true }}
      onConfirm={onRevoke}
    >
      <Button size="small" type="text" danger loading={busy}>
        Revoke
      </Button>
    </Popconfirm>
  );
}
