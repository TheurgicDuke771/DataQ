import { App, Button, Drawer, Flex, Form, Input, Select } from 'antd';
import { useEffect } from 'react';

import {
  CONNECTION_KIND,
  CONNECTION_TYPE_LABELS,
  type Connection,
  envLabel,
} from '../../api/connections';
import { createSuite, type Suite, targetString, updateSuite } from '../../api/suites';
import { TargetFields } from './SuiteForm';
import { asFileFormat, assembleTarget, type TargetFormValues, targetKind } from './suiteTarget';

interface SuiteFormValues extends TargetFormValues {
  name: string;
  description?: string;
  connection_id: string;
}

/**
 * Create or edit a suite. `suite === undefined` is create mode (connection is
 * chosen and then locked); editing exposes name/description + the run target
 * (`connection_id` is immutable on the backend — re-pointing orphans child
 * checks). The target is datasource-shaped (#215): the fields shown depend on
 * the selected connection's type, and the target is optional (a suite may stay
 * targetless = not-yet-runnable, which disables the Run button until it's set).
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
  // A suite's connection is its datasource — orchestration providers (ADF/
  // Airflow) are never queryable, so they can't back a suite (CLAUDE.md §4, #242).
  const datasourceConnections = connections.filter((c) => CONNECTION_KIND[c.type] === 'datasource');

  // The target fields follow the active connection's datasource type: fixed on
  // edit, live-tracked from the picker on create.
  const watchedConnId = Form.useWatch('connection_id', form);
  const activeConnId = isEdit ? suite.connection_id : watchedConnId;
  const activeConn = connections.find((c) => c.id === activeConnId);
  const kind = activeConn ? targetKind(activeConn.type) : null;

  // Prefill on open/edit; reset to a blank form for create.
  useEffect(() => {
    if (!open) return;
    if (suite) {
      form.setFieldsValue({
        name: suite.name,
        description: suite.description ?? undefined,
        connection_id: suite.connection_id,
        target_table: targetString(suite.target, 'table'),
        target_schema: targetString(suite.target, 'schema'),
        target_catalog: targetString(suite.target, 'catalog'),
        target_path: targetString(suite.target, 'path'),
        target_format: asFileFormat(targetString(suite.target, 'file_format')),
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
    // Assemble the datasource-shaped target; flag a partially-filled section
    // inline rather than letting the backend 422 on save.
    const { target, error } = kind ? assembleTarget(kind, values) : { target: null };
    if (error) {
      form.setFields([{ name: error.field, errors: [error.message] }]);
      return;
    }
    // The backend update treats a null target as "leave unchanged" (it never
    // clears a target back to NULL), so clearing the fields on a suite that has
    // a target would silently keep the old one. Say so rather than no-op.
    const hadTarget = isEdit && !!suite.target && Object.keys(suite.target).length > 0;
    if (hadTarget && target === null) {
      message.error('A run target can’t be removed once set — edit it to point elsewhere instead.');
      return;
    }
    try {
      if (isEdit) {
        await updateSuite(suite.id, {
          name: values.name,
          description: values.description ?? null,
          target,
        });
        message.success(`${values.name}: saved`);
      } else {
        await createSuite({
          name: values.name,
          description: values.description ?? null,
          connection_id: values.connection_id,
          target,
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
            placeholder="Select a datasource connection"
            options={datasourceConnections.map((c) => ({
              value: c.id,
              label: `${c.name} · ${CONNECTION_TYPE_LABELS[c.type]} · ${envLabel(c.env)}`,
            }))}
          />
        </Form.Item>
        {kind && <TargetFields kind={kind} />}
      </Form>
    </Drawer>
  );
}
