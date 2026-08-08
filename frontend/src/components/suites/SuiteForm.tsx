import { LoadingOutlined } from '@ant-design/icons';
import { App, Button, Divider, Flex, Form, Input, Radio, Select, Typography } from 'antd';
import axios from 'axios';
import { useEffect, useRef, useState } from 'react';

import { CONNECTION_KIND, type Connection, connectionOptionLabel } from '../../api/connections';
import {
  createSuite,
  previewBatchTarget,
  type Suite,
  targetString,
  updateSuite,
} from '../../api/suites';
import {
  asBatchStrategy,
  asFileFormat,
  assembleTarget,
  type TargetFormValues,
  type TargetKind,
  targetKind,
} from './suiteTarget';
import { useAsyncAction } from '../../hooks/useAsyncAction';
import { errorMessage } from '../../utils/errors';

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
      {kind && <TargetFields kind={kind} suiteId={isEdit ? suite.id : undefined} />}
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
 * only by hand-editing the stored target. `assembleTarget`'s client-side checks
 * are a light authoring aid and the backend's own 422 on save is still the
 * authoritative validator, but editing an existing suite additionally gets a
 * live "resolves to: `<path>`" hint (#1193, `BatchPreviewHint` below) against
 * the connection's real object listing — create mode has no suite id yet to
 * preview against, so it stays summary-only there.
 */
export function TargetFields({ kind, suiteId }: { kind: TargetKind; suiteId?: string }) {
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
              <BatchPreviewHint suiteId={suiteId} />
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

/** The `error.code` values `preview_batch_target` (backend `suites.py`) can 422
 *  with — only `batch_preview_no_data` gets its own canned copy below; every
 *  other 422 (a malformed pattern, `specific` with no capture group, a
 *  non-flat-file connection) already carries a helpful backend message. */
const BATCH_PREVIEW_NO_DATA_CODE = 'batch_preview_no_data';

type BatchPreviewState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'resolved'; path: string }
  | { status: 'no-match' }
  | { status: 'error'; message: string };

/**
 * Live "resolves to: `<path>`" hint next to the batch fields (#1193): debounces
 * `GET /suites/{id}/batch-preview` as prefix/pattern/strategy/batch change, so an
 * author gets the same before-you-save confidence a literal file path already
 * has, instead of finding out at the next scheduled run whether the pattern
 * actually matches anything. Mirrors `SharePanel`'s directory-search debounce
 * (a monotonic token so a slow earlier request can never overwrite a newer
 * one's result).
 *
 * Renders nothing without a `suiteId` (create mode — no suite to preview
 * against yet), without a pattern, or while `specific` is picked with no batch
 * key — those are exactly the states `assembleTarget` would also refuse to
 * resolve client-side, so there is nothing worth calling the backend for yet.
 *
 * All `setState` calls happen inside the debounce timer's callback or the
 * fetch's `.then`/`.catch` — never synchronously in the effect body — so this
 * never trips `react-hooks/set-state-in-effect`, and render only ever reads
 * `state` (never a ref), so it never trips `react-hooks/refs`. The one
 * consequence: while a new debounce is ticking, the hint keeps showing the
 * previous fields' outcome (if any) until the timer fires — the same "stale
 * results until the next answer" behaviour any debounced search box has.
 */
function BatchPreviewHint({ suiteId }: { suiteId?: string }) {
  const form = Form.useFormInstance();
  const prefix = (Form.useWatch('target_prefix', form) as string | undefined)?.trim();
  const pattern = (Form.useWatch('target_pattern', form) as string | undefined)?.trim();
  const strategy =
    (Form.useWatch('target_strategy', form) as 'latest' | 'specific' | undefined) ?? 'latest';
  const batch = (Form.useWatch('target_batch', form) as string | undefined)?.trim();
  const active = Boolean(suiteId && pattern && !(strategy === 'specific' && !batch));

  const [state, setState] = useState<BatchPreviewState>({ status: 'idle' });
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  // Monotonic token so a slow earlier request can't overwrite a newer one's
  // result (mirrors SharePanel's directory-search debounce); only ever read
  // inside a callback (never during render), so it's exempt from the
  // refs-during-render rule.
  const token = useRef(0);
  useEffect(
    () => () => {
      clearTimeout(timer.current);
      token.current = -1;
    },
    [],
  );

  useEffect(() => {
    clearTimeout(timer.current);
    if (!active) return;
    const current = (token.current += 1);
    timer.current = setTimeout(() => {
      if (current !== token.current) return; // superseded before the debounce even fired
      setState({ status: 'loading' });
      previewBatchTarget(suiteId as string, {
        pattern: pattern as string,
        strategy,
        ...(strategy === 'specific' ? { batch: batch as string } : {}),
        ...(prefix ? { prefix } : {}),
      })
        .then((path) => {
          if (current !== token.current) return;
          setState({ status: 'resolved', path });
        })
        .catch((err: unknown) => {
          if (current !== token.current) return;
          const code = axios.isAxiosError(err)
            ? (err.response?.data as { error?: { code?: string } } | undefined)?.error?.code
            : undefined;
          setState(
            code === BATCH_PREVIEW_NO_DATA_CODE
              ? { status: 'no-match' }
              : { status: 'error', message: errorMessage(err) },
          );
        });
    }, 400);
  }, [active, suiteId, prefix, pattern, strategy, batch]);

  if (!active || state.status === 'idle') return null;

  if (state.status === 'loading') {
    return (
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        <LoadingOutlined style={{ marginRight: 4 }} />
        Checking the live listing…
      </Typography.Text>
    );
  }
  if (state.status === 'resolved') {
    return (
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Resolves to: <Typography.Text code>{state.path}</Typography.Text>
      </Typography.Text>
    );
  }
  if (state.status === 'no-match') {
    return (
      <Typography.Text type="warning" style={{ fontSize: 12 }}>
        No file currently matches this pattern.
      </Typography.Text>
    );
  }
  return (
    <Typography.Text type="danger" style={{ fontSize: 12 }}>
      {state.message}
    </Typography.Text>
  );
}
