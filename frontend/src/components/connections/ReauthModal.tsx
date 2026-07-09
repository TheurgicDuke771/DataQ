import { App, Form, Modal } from 'antd';

import { type Connection, reauthConnection } from '../../api/connections';
import { PassphraseField, SecretField } from './ConnectionTypeFields';
import { activeAuthOption, composeSecret, CONNECTION_FORM_SPECS } from './connectionFormSpec';
import { useAsyncAction } from '../../hooks/useAsyncAction';

/**
 * Rotate a connection's stored credential. The backend verifies the new
 * credential against the datasource, so a bad value surfaces as an error and the
 * old credential is unaffected. The fields follow the connection's auth mode
 * (from CONNECTION_FORM_SPECS): the shared SecretField/PassphraseField render a
 * multi-line input for PEM keys plus the optional passphrase for key-pair modes,
 * composed the same way as on create (`composeSecret`). `connection === null`
 * means the modal is closed.
 */
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

  const auth = connection ? activeAuthOption(connection.type, connection.config) : undefined;
  const secretLabel =
    auth?.secretLabel ??
    (connection && CONNECTION_FORM_SPECS[connection.type].secretLabel) ??
    'Credential';

  // Values must not survive a close — a passphrase typed for one connection
  // (then cancelled) must never ride into another connection's rotation.
  const close = () => {
    form.resetFields();
    onClose();
  };

  const onOk = async () => {
    if (!connection) return;
    // antd's Modal `onOk` doesn't catch a rejected handler, so guard the
    // validation rejection here (errors render inline) rather than letting it
    // escape as an unhandled promise rejection.
    let secret: string;
    let secretPassphrase: string | undefined;
    try {
      ({ secret, secretPassphrase } = await form.validateFields());
    } catch {
      return;
    }
    await run(async () => {
      await reauthConnection(
        connection.id,
        composeSecret(secret, auth?.passphraseLabel ? secretPassphrase : undefined),
      );
      message.success(`${connection.name}: credential rotated`);
      form.resetFields();
      onDone();
    });
  };

  return (
    <Modal
      title={connection ? `Re-authenticate “${connection.name}”` : 'Re-authenticate'}
      open={connection !== null}
      onOk={onOk}
      onCancel={close}
      confirmLoading={submitting}
      okText="Rotate credential"
      destroyOnHidden
    >
      <Form form={form} layout="vertical" requiredMark="optional">
        <SecretField
          label={`New: ${secretLabel}`}
          multiline={auth?.multilineSecret}
          extra="Rotates the stored credential and verifies it against the datasource."
        />
        {auth?.passphraseLabel && <PassphraseField label={auth.passphraseLabel} />}
      </Form>
    </Modal>
  );
}
