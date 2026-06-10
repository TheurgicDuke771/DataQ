import { App, Button, Drawer, Flex, Form, Input, Select } from 'antd';
import { useState } from 'react';

import {
  CONNECTION_ENVS,
  CONNECTION_TYPE_LABELS,
  CONNECTION_TYPES,
  type ConnectionCreate,
  type ConnectionType,
  createConnection,
} from '../../api/connections';
import { ConnectionTypeFields } from './ConnectionTypeFields';

interface FormValues {
  name: string;
  type: ConnectionType;
  env: ConnectionCreate['env'];
  config?: Record<string, unknown>;
  secret?: string;
}

/** Default `config.auth_type` for the types that have one (so the select starts set). */
const DEFAULT_AUTH_TYPE: Partial<Record<ConnectionType, string>> = {
  snowflake: 'password',
  adls_gen2: 'sas',
  s3: 'access_key',
  airflow: 'token',
};

export function AddConnectionDrawer({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  /** Called after a successful create (so the list can refresh). */
  onCreated: () => void;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm<FormValues>();
  const [submitting, setSubmitting] = useState(false);
  const type = Form.useWatch('type', form) as ConnectionType | undefined;

  // Switching type invalidates the previous config fields — reset them and seed
  // the new type's default auth_type so its conditional fields render correctly.
  const onTypeChange = (next: ConnectionType) => {
    const authType = DEFAULT_AUTH_TYPE[next];
    form.setFieldsValue({ config: authType ? { auth_type: authType } : {}, secret: undefined });
  };

  const onFinish = async (values: FormValues) => {
    setSubmitting(true);
    try {
      await createConnection({
        name: values.name,
        type: values.type,
        env: values.env,
        config: values.config ?? {},
        secret: values.secret || undefined,
      });
      message.success(`Connection “${values.name}” created`);
      form.resetFields();
      onCreated();
    } catch (err) {
      message.error(`Create failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Drawer
      title="Add connection"
      open={open}
      onClose={onClose}
      width={520}
      destroyOnHidden
      footer={
        <Flex justify="end" gap={8}>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="primary" loading={submitting} onClick={() => form.submit()}>
            Create
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
            options={CONNECTION_ENVS.map((e) => ({ value: e, label: e.toUpperCase() }))}
            placeholder="Select an environment"
          />
        </Form.Item>
        <Form.Item name="type" label="Type" rules={[{ required: true }]}>
          <Select
            placeholder="Select a connection type"
            onChange={onTypeChange}
            options={CONNECTION_TYPES.map((t) => ({ value: t, label: CONNECTION_TYPE_LABELS[t] }))}
          />
        </Form.Item>
        {type && <ConnectionTypeFields type={type} form={form} />}
      </Form>
    </Drawer>
  );
}
