import { App, Form, Modal } from 'antd';
import { useState } from 'react';

import { type Connection, reauthConnection, SAVE_TEST_FAILED_CODE } from '../../api/connections';
import { PassphraseField, SecretField } from './ConnectionTypeFields';
import { activeAuthOption, composeSecret, CONNECTION_FORM_SPECS } from './connectionFormSpec';
import { SaveTestFailedAlert } from './SaveTestFailedAlert';
import { useAsyncAction } from '../../hooks/useAsyncAction';
import { errorMessage } from '../../utils/errors';
import { apiFieldError } from '../../utils/fieldErrors';

/** Rotate a connection's stored credential — tested first, rotated only if it works (#1927). */
export function ReauthModal({
  connection,
  onClose,
  onDone,
}: {
  connection: Connection | null;
  onClose: () => void;
  /** Called after a successful rotation (so the list can refresh `has_secret`). */
  onDone: () => void;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm<{ secret: string; secretPassphrase?: string }>();
  const { run, loading: submitting } = useAsyncAction('Re-auth failed');
  const [testFailure, setTestFailure] = useState<string>();

  const auth = connection ? activeAuthOption(connection.type, connection.config) : undefined;
  const secretLabel =
    auth?.secretLabel ??
    (connection && CONNECTION_FORM_SPECS[connection.type].secretLabel) ??
    'Credential';

  // Values must not survive a close — a passphrase typed for one connection
  // (then cancelled) must never ride into another connection's rotation.
  const close = () => {
    form.resetFields();
    setTestFailure(undefined);
    onClose();
  };

  const rotate = async (skipTest: boolean) => {
    if (!connection) return;
    // antd's Modal `onOk` doesn't catch a rejected handler, so guard the validation rejection here
    // (errors render inline) rather than letting it escape as an unhandled promise rejection.
    let secret: string;
    let secretPassphrase: string | undefined;
    try {
      ({ secret, secretPassphrase } = await form.validateFields());
    } catch {
      return;
    }
    await run(async () => {
      try {
        await reauthConnection(
          connection.id,
          composeSecret(secret, auth?.passphraseLabel ? secretPassphrase : undefined),
          skipTest,
        );
      } catch (err) {
        if (apiFieldError(err)?.code !== SAVE_TEST_FAILED_CODE) throw err;
        setTestFailure(errorMessage(err));
        return;
      }
      message.success(
        `${connection.name}: credential rotated${skipTest ? ' without testing' : ''}`,
      );
      form.resetFields();
      setTestFailure(undefined);
      onDone();
    });
  };

  return (
    <Modal
      title={connection ? `Re-authenticate “${connection.name}”` : 'Re-authenticate'}
      open={connection !== null}
      onOk={() => rotate(false)}
      onCancel={close}
      confirmLoading={submitting}
      okText="Rotate credential"
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        requiredMark="optional"
        onValuesChange={() => setTestFailure(undefined)}
      >
        <SecretField
          label={`New: ${secretLabel}`}
          multiline={auth?.multilineSecret}
          extra="The new credential is tested first; the stored one is replaced only if it works."
        />
        {auth?.passphraseLabel && <PassphraseField label={auth.passphraseLabel} />}
      </Form>
      {connection && testFailure !== undefined && (
        <SaveTestFailedAlert
          type={connection.type}
          reason={`${testFailure}. The stored credential was not changed.`}
          skipLabel="Rotate without testing"
          skipping={submitting}
          onSkip={() => rotate(true)}
        />
      )}
    </Modal>
  );
}
