import { App, Button, Drawer, Flex, Form, Input, Select } from 'antd';
import { useEffect } from 'react';

import { CONNECTION_TYPE_LABELS, type Connection, envLabel } from '../../api/connections';
import { createSuite, type Suite, updateSuite } from '../../api/suites';

interface SuiteFormValues {
  name: string;
  description?: string;
  connection_id: string;
}

/**
 * Create or edit a suite. `suite === undefined` is create mode (connection is
 * chosen and then locked); editing exposes only name/description since
 * `connection_id` is immutable on the backend (re-pointing orphans child checks).
 */
export function SuiteDrawer({
  open,
  suite,
  connections,
  onClose,
  onSaved,
}: {
  open: boolean;
  suite?: Suite;
  /** Available connections for the create-mode picker. */
  connections: Connection[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm<SuiteFormValues>();
  const isEdit = suite !== undefined;

  // Prefill on open/edit; reset to a blank form for create.
  useEffect(() => {
    if (!open) return;
    if (suite) {
      form.setFieldsValue({
        name: suite.name,
        description: suite.description ?? undefined,
        connection_id: suite.connection_id,
      });
    } else {
      form.resetFields();
    }
  }, [open, suite, form]);

  const onSubmit = async () => {
    let values: SuiteFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return; // validation errors render inline
    }
    try {
      if (isEdit) {
        await updateSuite(suite.id, {
          name: values.name,
          description: values.description ?? null,
        });
        message.success(`${values.name}: saved`);
      } else {
        await createSuite({
          name: values.name,
          description: values.description ?? null,
          connection_id: values.connection_id,
        });
        message.success(`${values.name}: created`);
      }
      onSaved();
    } catch (err) {
      message.error(`Save failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    }
  };

  return (
    <Drawer
      title={isEdit ? `Edit “${suite.name}”` : 'New suite'}
      open={open}
      onClose={onClose}
      width={480}
      destroyOnHidden
      extra={
        <Flex gap={8}>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="primary" onClick={onSubmit}>
            {isEdit ? 'Save' : 'Create'}
          </Button>
        </Flex>
      }
    >
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="Name" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="description" label="Description (optional)">
          <Input.TextArea rows={3} />
        </Form.Item>
        <Form.Item
          name="connection_id"
          label="Connection"
          rules={[{ required: true }]}
          extra={isEdit ? 'The connection is fixed once a suite is created.' : undefined}
        >
          <Select
            disabled={isEdit}
            placeholder="Select a connection"
            options={connections.map((c) => ({
              value: c.id,
              label: `${c.name} · ${CONNECTION_TYPE_LABELS[c.type]} · ${envLabel(c.env)}`,
            }))}
          />
        </Form.Item>
      </Form>
    </Drawer>
  );
}
