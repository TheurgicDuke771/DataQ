import { Alert, Button, Flex, Input, Modal, Typography } from 'antd';
import { useState } from 'react';

import { authMode } from '../../auth/config';
import { useMe } from '../../auth/useMe';
import { useSaveDisplayName } from '../../auth/useSaveDisplayName';
import { errorMessage } from '../../utils/errors';

const SKIP_STORAGE_KEY = 'dataq:profileCompletionPrompt:skipped';

/**
 * Read/write through `sessionStorage`, failing open (never throwing) so a private-browsing tab or
 * a storage-disabled browser degrades to "the prompt may reappear more than once" rather than a br
 */
function readSkipped(): boolean {
  try {
    return sessionStorage.getItem(SKIP_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

function markSkipped(): void {
  try {
    sessionStorage.setItem(SKIP_STORAGE_KEY, '1');
  } catch {
    // Nothing to recover, nothing to surface — see readSkipped().
  }
}

/** First-login profile completion (#1139) — `otp` mode only. */
export function ProfileCompletionPrompt() {
  const me = useMe();
  const save = useSaveDisplayName();
  const [dismissed, setDismissed] = useState(readSkipped);
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const shouldShow =
    authMode === 'otp' && me.status === 'ok' && me.data.display_name === null && !dismissed;

  const dismiss = () => {
    markSkipped();
    setDismissed(true);
  };

  const onSave = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setSaving(true);
    setError(null);
    try {
      await save(trimmed);
      // No need to also markSkipped(): display_name is no longer null, so
      // `shouldShow` is false on the very next render regardless.
    } catch (err) {
      setError(errorMessage(err, 'Could not save your name.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={shouldShow}
      title="Welcome to DataQ"
      onCancel={dismiss}
      mask={{ closable: false }}
      destroyOnHidden
      footer={[
        <Button key="skip" onClick={dismiss}>
          Skip for now
        </Button>,
        <Button
          key="save"
          type="primary"
          loading={saving}
          disabled={!name.trim()}
          onClick={() => void onSave()}
        >
          Save
        </Button>,
      ]}
    >
      <Flex vertical gap={12}>
        <Typography.Paragraph style={{ margin: 0 }}>
          What should we call you? This is shown wherever your name appears — shared suites, the
          admin user list — instead of your email address. You can change it any time from{' '}
          <Typography.Text strong>Profile</Typography.Text>.
        </Typography.Paragraph>
        {error && <Alert type="error" showIcon title={error} />}
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Olivia Rivera"
          maxLength={256}
          onPressEnter={() => void onSave()}
          autoFocus
          aria-label="Display name"
        />
      </Flex>
    </Modal>
  );
}
