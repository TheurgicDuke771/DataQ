import { App, Button, Drawer, Flex, Form, Input, Tag, Typography } from 'antd';
import { useEffect, useState } from 'react';

import {
  CONNECTION_TYPE_LABELS,
  type Connection,
  ENV_COLORS,
  envLabel,
  updateConnection,
} from '../../api/connections';
import { ConnectionTypeFields } from './ConnectionTypeFields';

interface FormValues {
  name: string;
  config?: Record<string, unknown>;
}

/**
 * Edit an existing connection. Type + env are immutable (the backend
 * `ConnectionUpdate` rejects them) and shown read-only; the secret is omitted —
 * credential rotation is the separate Re-auth flow. Creating a connection is a
 * dedicated page (`/connections/new`), not this drawer.
 */
export function ConnectionDrawer({
  open,
  onClose,
  onSaved,
  connection,
}: {
  open: boolean;
  onClose: () => void;
  /** Called after a successful update (so the list can refresh). */
  onSaved: () => void;
  connection?: Connection;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm<FormValues>();
  const [submitting, setSubmitting] = useState(false);

  // Prefill when opening (destroyOnHidden remounts the form each open, so this
  // re-seeds for whichever connection is being edited).
  useEffect(() => {
    if (open && connection) {
      form.setFieldsValue({ name: connection.name, config: connection.config });
    }
  }, [open, connection, form]);

  const onFinish = async (values: FormValues) => {
    if (!connection) return;
    setSubmitting(true);
    try {
      await updateConnection(connection.id, {
        name: values.name,
        config: values.config ?? {},
      });
      message.success(`Connection “${values.name}” updated`);
      form.resetFields();
      onSaved();
    } catch (err) {
      message.error(`Update failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Drawer
      title="Edit connection"
      open={open}
      onClose={onClose}
      width={520}
      destroyOnHidden
      footer={
        <Flex justify="end" gap={8}>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="primary" loading={submitting} onClick={() => form.submit()}>
            Save
          </Button>
        </Flex>
      }
    >
      {connection && (
        <Form form={form} layout="vertical" onFinish={onFinish} requiredMark="optional">
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          {/* Type + env are fixed once a connection is created — show, don't edit. */}
          <Form.Item label="Type">
            <Typography.Text>{CONNECTION_TYPE_LABELS[connection.type]}</Typography.Text>
          </Form.Item>
          <Form.Item label="Environment">
            <Tag color={ENV_COLORS[connection.env]}>{envLabel(connection.env)}</Tag>
          </Form.Item>
          <ConnectionTypeFields type={connection.type} form={form} showSecret={false} />
        </Form>
      )}
    </Drawer>
  );
}
