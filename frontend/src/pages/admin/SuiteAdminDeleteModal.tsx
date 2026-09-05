import { App, Alert, Flex, Input, Modal, Typography } from 'antd';
import { useEffect, useState } from 'react';

import { type AdminSuite, deleteAdminSuite } from '../../api/admin';
import { getSuiteDeletionImpact } from '../../api/suites';
import {
  DELETION_IMPACT_UNAVAILABLE,
  describeDeletionImpact,
} from '../../components/suites/deletionImpact';
import { errorMessage } from '../../utils/errors';

/** Admin delete of any suite (#1698) — states the blast radius and requires the
 *  suite's name to be typed, because this one is run on a suite the admin does
 *  not own and may not recognise. Mount it keyed on the suite — the typed
 *  confirmation is per-suite and must never carry over. */
export function SuiteAdminDeleteModal({
  suite,
  onClose,
  onDeleted,
}: {
  /** `null` closes the modal. */
  suite: AdminSuite | null;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const { message } = App.useApp();
  const [impact, setImpact] = useState<string | null>(null);
  const [typed, setTyped] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!suite) return;
    let live = true;
    // A failed count never blocks the delete — it degrades to the plain warning.
    getSuiteDeletionImpact(suite.id)
      .then(describeDeletionImpact)
      .catch(() => DELETION_IMPACT_UNAVAILABLE)
      .then((text) => {
        if (live) setImpact(text);
      });
    return () => {
      live = false;
    };
  }, [suite]);

  const confirmed = suite !== null && typed.trim() === suite.name;

  const onOk = async () => {
    if (!suite || !confirmed) return;
    setSubmitting(true);
    try {
      await deleteAdminSuite(suite.id);
      message.success(`${suite.name} deleted`);
      onDeleted();
      onClose();
    } catch (err) {
      message.error(`Delete failed: ${errorMessage(err)}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title={suite ? `Delete “${suite.name}”?` : 'Delete suite'}
      open={suite !== null}
      onCancel={onClose}
      onOk={onOk}
      okText="Delete"
      okType="danger"
      okButtonProps={{ disabled: !confirmed, loading: submitting }}
      destroyOnHidden
    >
      <Flex vertical gap={12}>
        <Alert type="error" showIcon title={impact ?? 'Counting what this would remove…'} />
        {suite && (
          <Typography.Text>
            Type <Typography.Text code>{suite.name}</Typography.Text> to confirm.
          </Typography.Text>
        )}
        <Input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={suite?.name}
          aria-label="Suite name confirmation"
        />
      </Flex>
    </Modal>
  );
}
