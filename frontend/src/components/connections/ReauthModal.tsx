import { App, Form, Input, Modal } from 'antd';
import { useState } from 'react';

import { type Connection, reauthConnection } from '../../api/connections';
import { activeAuthOption, composeSecret, CONNECTION_FORM_SPECS } from './connectionFormSpec';

/**
 * Rotate a connection's stored credential. The backend verifies the new
 * credential against the datasource, so a bad value surfaces as an error and the
 * old credential is unaffected. The fields follow the connection's auth mode
 * (from CONNECTION_FORM_SPECS): a multi-line input for PEM keys, plus the
 * optional passphrase for key-pair modes — composed the same way as on create
 * (`composeSecret`). `connection === null` means the modal is closed.
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
  const [submitting, setSubmitting] = useState(false);

  const auth = connection ? activeAuthOption(connection.type, connection.config) : undefined;
  const secretLabel =
    auth?.secretLabel ??
    (connection && CONNECTION_FORM_SPECS[connection.type].secretLabel) ??
    'Credential';

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
    setSubmitting(true);
    try {
      await reauthConnection(connection.id, composeSecret(secret, secretPassphrase));
      message.success(`${connection.name}: credential rotated`);
      form.resetFields();
      onDone();
    } catch (err) {
      message.error(`Re-auth failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title={connection ? `Re-authenticate “${connection.name}”` : 'Re-authenticate'}
      open={connection !== null}
      onOk={onOk}
      onCancel={onClose}
      confirmLoading={submitting}
      okText="Rotate credential"
      destroyOnHidden
    >
      <Form form={form} layout="vertical">
        <Form.Item
          name="secret"
          label={`New: ${secretLabel}`}
          rules={[{ required: true }]}
          extra="Rotates the stored credential and verifies it against the datasource."
        >
          {auth?.multilineSecret ? (
            <Input.TextArea rows={4} autoComplete="off" />
          ) : (
            <Input.Password autoComplete="off" />
          )}
        </Form.Item>
        {auth?.passphraseLabel && (
          <Form.Item
            name="secretPassphrase"
            label={`${auth.passphraseLabel} (optional)`}
            extra="Only for passphrase-protected keys; leave blank for an unencrypted key."
          >
            <Input.Password autoComplete="off" />
          </Form.Item>
        )}
      </Form>
    </Modal>
  );
}
