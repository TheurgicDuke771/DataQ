import { App, Button, Divider, Flex, Form, Input, Select, Typography } from 'antd';
import { useEffect, useState } from 'react';

import {
  CONNECTION_KIND,
  CONNECTION_TYPE_LABELS,
  type Connection,
  envLabel,
} from '../../api/connections';
import { createSuite, type Suite, targetString, updateSuite } from '../../api/suites';
import {
  asFileFormat,
  assembleTarget,
  type TargetFormValues,
  type TargetKind,
  targetKind,
} from './suiteTarget';

interface SuiteFormValues extends TargetFormValues {
  name: string;
  description?: string;
  connection_id: string;
}

/**
 * Create or edit a suite — the form body shared by the `/suites/new` page and the
 * `/suites/:id/edit` page (the drawer is retired in W6, ADR 0022). `suite ===
 * undefined` is create mode (connection is chosen then locked); editing exposes
 * name/description + the run target (`connection_id` is immutable on the backend —
 * re-pointing orphans child checks). The target is datasource-shaped (#215): the
 * fields shown depend on the selected connection's type, and the target is optional
 * (a suite may stay targetless = not-yet-runnable, which disables Run until set).
 */
export function SuiteForm({
  suite,
  connections,
  onSaved,
  onCancel,
}: {
  suite?: Suite;
  /** Available connections for the create-mode picker. */
  connections: Connection[];
  /** Called with the saved suite (created or updated). */
  onSaved: (suite: Suite) => void;
  onCancel: () => void;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm<SuiteFormValues>();
  const [submitting, setSubmitting] = useState(false);
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

  // Prefill once on mount/edit; create starts blank.
  useEffect(() => {
    if (suite) {
      form.setFieldsValue({
        name: suite.name,
        description: suite.description ?? undefined,
        connection_id: suite.connection_id,
        target_table: targetString(suite.target, 'table'),
        target_schema: targetString(suite.target, 'schema'),
        target_catalog: targetString(suite.target, 'catalog'),
        target_namespace: targetString(suite.target, 'namespace'),
        target_path: targetString(suite.target, 'path'),
        target_format: asFileFormat(targetString(suite.target, 'file_format')),
      });
    }
  }, [suite, form]);

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
    setSubmitting(true);
    try {
      const saved = isEdit
        ? await updateSuite(suite.id, {
            name: values.name,
            description: values.description ?? null,
            target,
          })
        : await createSuite({
            name: values.name,
            description: values.description ?? null,
            connection_id: values.connection_id,
            target,
          });
      message.success(`${values.name}: ${isEdit ? 'saved' : 'created'}`);
      onSaved(saved);
    } catch (err) {
      message.error(`Save failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Form form={form} layout="vertical" onFinish={onSubmit}>
      <Form.Item name="name" label="Name" rules={[{ required: true }]}>
        <Input placeholder="Daily Revenue Audit" />
      </Form.Item>
      <Form.Item name="description" label="Description (optional)">
        <Input.TextArea rows={3} placeholder="What this suite validates and why." />
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
      <Flex justify="end" gap={8}>
        <Button onClick={onCancel}>Cancel</Button>
        <Button type="primary" htmlType="submit" loading={submitting}>
          {isEdit ? 'Save' : 'Create & add checks'}
        </Button>
      </Flex>
    </Form>
  );
}

/**
 * The datasource-shaped run-target inputs. Optional as a whole (leave blank for a
 * not-yet-runnable suite); when started, the required field for the datasource is
 * enforced at submit by `assembleTarget`. Field names match `TargetFormValues`.
 */
export function TargetFields({ kind }: { kind: TargetKind }) {
  return (
    <>
      <Divider style={{ marginTop: 4 }} />
      <Flex vertical gap={2} style={{ marginBottom: 12 }}>
        <Typography.Text strong>Run target</Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Where this suite’s checks run. Optional — required to run the suite.
        </Typography.Text>
      </Flex>

      {kind === 'flatfile' ? (
        <>
          <Form.Item name="target_path" label="File path">
            <Input placeholder="container/path/to/data.csv" />
          </Form.Item>
          <Form.Item name="target_format" label="File format">
            <Select
              allowClear
              placeholder="Infer from extension"
              options={[
                { value: 'csv', label: 'CSV' },
                { value: 'parquet', label: 'Parquet' },
              ]}
            />
          </Form.Item>
        </>
      ) : kind === 'iceberg' ? (
        <>
          {/* Iceberg addresses a table by `namespace.table` (no SQL schema). Put the
              namespace in its own field — don't also dot-qualify Table, or the two
              fold to `namespace.namespace.table`. */}
          <Form.Item name="target_namespace" label="Namespace (optional)">
            <Input placeholder="sales" />
          </Form.Item>
          <Form.Item name="target_table" label="Table">
            <Input placeholder="orders" />
          </Form.Item>
        </>
      ) : (
        <>
          {kind === 'uc' && (
            <Form.Item name="target_catalog" label="Catalog">
              <Input placeholder="main" />
            </Form.Item>
          )}
          <Form.Item name="target_schema" label="Schema (optional)">
            <Input placeholder={kind === 'uc' ? 'default' : 'PUBLIC'} />
          </Form.Item>
          <Form.Item name="target_table" label="Table">
            <Input placeholder={kind === 'uc' ? 'orders' : 'ANALYTICS.ORDERS'} />
          </Form.Item>
        </>
      )}
    </>
  );
}
