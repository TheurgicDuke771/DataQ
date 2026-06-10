import { App, Button, Drawer, Flex, Form, Input, Select } from 'antd';
import { useEffect, useState } from 'react';

import {
  CONNECTION_ENVS,
  CONNECTION_TYPE_LABELS,
  CONNECTION_TYPES,
  type Connection,
  type ConnectionCreate,
  type ConnectionType,
  createConnection,
  envLabel,
  updateConnection,
} from '../../api/connections';
import { ConnectionTypeFields } from './ConnectionTypeFields';
import { initialConfigForType } from './connectionFormSpec';

interface FormValues {
  name: string;
  type: ConnectionType;
  env: ConnectionCreate['env'];
  config?: Record<string, unknown>;
  secret?: string;
}

/**
 * Create or edit a connection. In edit mode (`connection` present), type + env
 * are immutable (the backend `ConnectionUpdate` rejects them) and the secret is
 * omitted — credential rotation is the separate Re-auth flow.
 */
export function ConnectionDrawer({
  open,
  onClose,
  onSaved,
  connection,
}: {
  open: boolean;
  onClose: () => void;
  /** Called after a successful create or update (so the list can refresh). */
  onSaved: () => void;
  connection?: Connection;
}) {
  const isEdit = connection !== undefined;
  const { message } = App.useApp();
  const [form] = Form.useForm<FormValues>();
  const [submitting, setSubmitting] = useState(false);
  const watchedType = Form.useWatch('type', form) as ConnectionType | undefined;
  const type = isEdit ? connection.type : watchedType;

  // Prefill when opening in edit mode (the Drawer's destroyOnHidden remounts the
  // form each open, so this re-seeds for whichever connection is being edited).
  useEffect(() => {
    if (open && connection) {
      form.setFieldsValue({
        name: connection.name,
        type: connection.type,
        env: connection.env,
        config: connection.config,
      });
    }
  }, [open, connection, form]);

  // Switching type (create only) invalidates the previous config fields.
  const onTypeChange = (next: ConnectionType) => {
    form.setFieldsValue({ config: initialConfigForType(next), secret: undefined });
  };

  const onFinish = async (values: FormValues) => {
    setSubmitting(true);
    try {
      if (isEdit) {
        await updateConnection(connection.id, {
          name: values.name,
          config: values.config ?? {},
        });
        message.success(`Connection “${values.name}” updated`);
      } else {
        await createConnection({
          name: values.name,
          type: values.type,
          env: values.env,
          config: values.config ?? {},
          secret: values.secret || undefined,
        });
        message.success(`Connection “${values.name}” created`);
      }
      form.resetFields();
      onSaved();
    } catch (err) {
      message.error(
        `${isEdit ? 'Update' : 'Create'} failed: ${err instanceof Error ? err.message : 'unknown error'}`,
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Drawer
      title={isEdit ? 'Edit connection' : 'Add connection'}
      open={open}
      onClose={onClose}
      width={520}
      destroyOnHidden
      footer={
        <Flex justify="end" gap={8}>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="primary" loading={submitting} onClick={() => form.submit()}>
            {isEdit ? 'Save' : 'Create'}
          </Button>
        </Flex>
      }
    >
      <Form form={form} layout="vertical" onFinish={onFinish} requiredMark="optional">
        <Form.Item name="name" label="Name" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="env" label="Environment" rules={[{ required: true }]}>
          <Select
            disabled={isEdit}
            options={CONNECTION_ENVS.map((e) => ({ value: e, label: envLabel(e) }))}
            placeholder="Select an environment"
          />
        </Form.Item>
        <Form.Item name="type" label="Type" rules={[{ required: true }]}>
          <Select
            disabled={isEdit}
            placeholder="Select a connection type"
            onChange={onTypeChange}
            options={CONNECTION_TYPES.map((t) => ({ value: t, label: CONNECTION_TYPE_LABELS[t] }))}
          />
        </Form.Item>
        {type && <ConnectionTypeFields type={type} form={form} showSecret={!isEdit} />}
      </Form>
    </Drawer>
  );
}
