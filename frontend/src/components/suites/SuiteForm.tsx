import { App, Button, Divider, Flex, Form, Input, Radio, Select, Typography } from 'antd';
import { useEffect } from 'react';

import { CONNECTION_KIND, type Connection, connectionOptionLabel } from '../../api/connections';
import { createSuite, type Suite, targetString, updateSuite } from '../../api/suites';
import {
  asBatchStrategy,
  asFileFormat,
  assembleTarget,
  type TargetFormValues,
  type TargetKind,
  targetKind,
} from './suiteTarget';
import { useAsyncAction } from '../../hooks/useAsyncAction';

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
  const { run, loading: submitting } = useAsyncAction('Save failed');
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

  // Prefill once on mount/edit; create starts blank. A flat-file target is a
  // batch selector iff it carries `pattern` (#1180) — mutually exclusive with
  // the literal `path` per the backend `_flatfile_target` resolver, so the
  // stored shape alone tells us which mode to reopen the form in.
  useEffect(() => {
    if (suite) {
      const pattern = targetString(suite.target, 'pattern');
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
        target_mode: pattern ? 'batch' : 'single',
        target_prefix: targetString(suite.target, 'prefix'),
        target_pattern: pattern,
        target_strategy: asBatchStrategy(targetString(suite.target, 'strategy')) ?? 'latest',
        target_batch: targetString(suite.target, 'batch'),
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
    await run(async () => {
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
    });
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
            label: connectionOptionLabel(c),
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
 *
 * Flat-file connections additionally offer a Single file / Batch pattern mode
 * toggle (#1180): Batch mode exposes the prefix/pattern/strategy inputs the
 * backend `resolve_batch`/`BatchSpec` already supports, previously reachable
 * only by hand-editing the stored target. There's no cheap batch-resolution
 * preview endpoint today (see #1180's follow-up comment) — `assembleTarget`'s
 * client-side checks are a light authoring aid, and the backend's own 422 on
 * save is the authoritative validator.
 */
export function TargetFields({ kind }: { kind: TargetKind }) {
  const form = Form.useFormInstance();
  const mode = (Form.useWatch('target_mode', form) as 'single' | 'batch' | undefined) ?? 'single';
  const strategy =
    (Form.useWatch('target_strategy', form) as 'latest' | 'specific' | undefined) ?? 'latest';

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
          <Form.Item name="target_mode" label="Target mode" initialValue="single">
            <Radio.Group
              data-testid="flatfile-target-mode"
              optionType="button"
              size="small"
              options={[
                { label: 'Single file', value: 'single' },
                { label: 'Batch pattern', value: 'batch' },
              ]}
            />
          </Form.Item>
          {mode === 'batch' ? (
            <>
              <Form.Item
                name="target_prefix"
                label="Prefix (optional)"
                extra="Scopes the object listing, e.g. a container/folder path."
              >
                <Input placeholder="adls_flatfile/logistics_tracking/" />
              </Form.Item>
              <Form.Item
                name="target_pattern"
                label="Filename pattern (regex)"
                extra="The first capture group is the batch key — required for the 'specific' strategy."
              >
                <Input placeholder="tracking_events_([a-z_]+)\.csv" />
              </Form.Item>
              <Form.Item name="target_strategy" label="Strategy" initialValue="latest">
                <Select
                  options={[
                    { value: 'latest', label: 'Latest (greatest batch key)' },
                    { value: 'specific', label: 'Specific batch key' },
                  ]}
                />
              </Form.Item>
              {strategy === 'specific' && (
                <Form.Item
                  name="target_batch"
                  label="Batch key"
                  extra="The exact value the pattern's capture group must match."
                >
                  <Input placeholder="ready" />
                </Form.Item>
              )}
            </>
          ) : (
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
          )}
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
