import { App, Form, Input, Modal } from 'antd';
import { useState } from 'react';

import { type Connection, reauthConnection } from '../../api/connections';

/**
 * Rotate a connection's stored credential. The backend verifies the new
 * credential against the datasource, so a bad value surfaces as an error and the
 * old credential is unaffected. `connection === null` means the modal is closed.
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
  const [form] = Form.useForm<{ secret: string }>();
  const [submitting, setSubmitting] = useState(false);

  const onOk = async () => {
    if (!connection) return;
    const { secret } = await form.validateFields();
    setSubmitting(true);
    try {
      await reauthConnection(connection.id, secret);
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
          label="New credential"
          rules={[{ required: true }]}
          extra="Rotates the stored credential and verifies it against the datasource."
        >
          <Input.Password autoComplete="off" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
