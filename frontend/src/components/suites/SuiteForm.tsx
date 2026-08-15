import { LoadingOutlined } from '@ant-design/icons';
import {
  App,
  Button,
  Divider,
  Flex,
  Form,
  Input,
  InputNumber,
  Radio,
  Select,
  Typography,
} from 'antd';
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
  asSampleStrategy,
  assembleTarget,
  MAX_SAMPLE_ROWS,
  samplingNumber,
  supportsSampling,
  type TargetFormValues,
  type TargetKind,
  targetKind,
  targetSampling,
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
      const sampling = targetSampling(suite.target);
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
        // A stored block IS the toggle — there is no separate "enabled" flag on
        // the target, so its presence is the only truth about whether this suite
        // samples (#595).
        sampling_enabled: sampling !== undefined,
        sampling_strategy: asSampleStrategy(sampling?.strategy) ?? 'head',
        sampling_rows: samplingNumber(sampling, 'rows'),
        sampling_seed: samplingNumber(sampling, 'seed'),
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
    const { target, error } = kind
      ? assembleTarget(kind, values, activeConn?.type)
      : { target: null };
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
      {kind && (
        <TargetFields
          kind={kind}
          suiteId={isEdit ? suite.id : undefined}
          canSample={supportsSampling(activeConn?.type)}
        />
      )}
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
export function TargetFields({
  kind,
  suiteId,
  canSample = false,
}: {
  kind: TargetKind;
  suiteId?: string;
  /** Whether this connection's datasource accepts a `sampling` block (#595).
   *  When false the section is not rendered at all, because the backend answers
   *  a spec there with a 422 — offering a control whose only outcome is a save
   *  error would be worse than not offering it. */
  canSample?: boolean;
}) {
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

      {canSample && <SamplingFields />}
    </>
  );
}

/**
 * The `sampling` authoring block (#595/#1325) — rendered only for the datasources
 * whose runners materialise rows (ADLS Gen2 / S3 / Unity Catalog).
 *
 * Off by default and framed as what it is: sampling changes what a verdict
 * *means*, so the copy says so at the point of the decision rather than leaving
 * the reader to discover it from a "Sampled" tag on the results page. The
 * strategy names carry the same honesty — `head` is "the first N rows in storage
 * order", not "a sample", because it is systematically biased toward whatever
 * the writer wrote first.
 *
 * Seed is shown for `random` only, mirroring the backend's refusal of a seed on
 * `head`: a head sample always reads the same first rows and cannot be seeded, so
 * offering the field there would promise reproducibility of a different kind than
 * the one it delivers.
 */
function SamplingFields() {
  const form = Form.useFormInstance();
  const enabled = Boolean(Form.useWatch('sampling_enabled', form));
  const strategy =
    (Form.useWatch('sampling_strategy', form) as 'head' | 'random' | undefined) ?? 'head';

  return (
    <>
      <Divider style={{ marginTop: 4 }} />
      <Flex vertical gap={2} style={{ marginBottom: 12 }}>
        <Typography.Text strong>Sampling (optional)</Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Run this suite’s checks against a bounded sample instead of the whole dataset. Keeps a
          large target inside the worker’s memory — but every verdict then describes the sample, so
          a check can pass while failing rows sit outside it.
        </Typography.Text>
      </Flex>

      <Form.Item name="sampling_enabled" label="Read" initialValue={false}>
        <Radio.Group
          data-testid="sampling-enabled"
          optionType="button"
          size="small"
          options={[
            { label: 'Whole dataset', value: false },
            { label: 'A sample', value: true },
          ]}
        />
      </Form.Item>

      {enabled && (
        <>
          {/* "Sample strategy", not "Strategy" — the flat-file batch selector
              above already owns a field called Strategy, and two identically
              labelled controls in one form is an ambiguity for a screen reader
              and for anyone reading the saved target back. */}
          <Form.Item name="sampling_strategy" label="Sample strategy" initialValue="head">
            <Select
              data-testid="sampling-strategy"
              options={[
                { value: 'head', label: 'Head — the first rows in storage order (cheapest)' },
                { value: 'random', label: 'Random — drawn uniformly across the dataset' },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="sampling_rows"
            label="Rows"
            extra={`How many rows the checks see. Up to ${MAX_SAMPLE_ROWS.toLocaleString()}.`}
          >
            <InputNumber
              data-testid="sampling-rows"
              min={1}
              max={MAX_SAMPLE_ROWS}
              step={1000}
              precision={0}
              style={{ width: '100%' }}
              placeholder="100000"
            />
          </Form.Item>
          {strategy === 'random' && (
            <Form.Item
              name="sampling_seed"
              label="Seed (optional)"
              extra="Fixes the draw, so consecutive runs read the same rows and a change in verdict means a change in the data."
            >
              <InputNumber
                data-testid="sampling-seed"
                precision={0}
                style={{ width: '100%' }}
                placeholder="7"
              />
            </Form.Item>
          )}
        </>
      )}
    </>
  );
}

/** The `error.code` values the batch preview (backend `services/run_target.py`)
 *  can 422 with — only `batch_preview_no_data` gets its own canned copy below;
 *  every other 422 (a malformed pattern, `specific` with no capture group, a
 *  non-flat-file connection, a prefix too broad to scan) carries a backend
 *  message that is safe to render, because the backend never echoes an adapter
 *  exception verbatim — anything it can't classify becomes a generic 502. */
const BATCH_PREVIEW_NO_DATA_CODE = 'batch_preview_no_data';

interface BatchPreviewErrorEnvelope {
  error?: { code?: string; detail?: { reason?: unknown } };
}

/** Every non-idle state carries the exact spec it describes, so render can tell
 *  "this answer is about what the form says now" from "this answer is about what
 *  the form said 300ms ago". Without it the hint keeps asserting `Resolves to:
 *  <path>` through the whole debounce window after the author edits the pattern
 *  — and worse, re-shows a `latest` answer as if it were the resolution of a
 *  newly-entered `specific` batch key when `active` flips false→true. A preview
 *  whose entire purpose is before-you-save confidence must never label a stale
 *  answer as the current one. */
type BatchPreviewSpec = string;

type BatchPreviewState =
  | { status: 'idle' }
  | { status: 'loading'; spec: BatchPreviewSpec }
  | { status: 'resolved'; spec: BatchPreviewSpec; path: string }
  | { status: 'no-match'; spec: BatchPreviewSpec }
  | { status: 'error'; spec: BatchPreviewSpec; message: string };

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
 * `state` (never a ref), so it never trips `react-hooks/refs`. That would
 * otherwise leave a stale answer on screen for the whole debounce window, so
 * every answer carries the spec it is about and render falls back to
 * "Checking…" whenever that spec is no longer the form's — the deferred write
 * costs nothing as long as the display never mislabels what it is showing.
 */
function BatchPreviewHint({ suiteId }: { suiteId?: string }) {
  const form = Form.useFormInstance();
  const prefix = (Form.useWatch('target_prefix', form) as string | undefined)?.trim();
  const pattern = (Form.useWatch('target_pattern', form) as string | undefined)?.trim();
  const strategy =
    (Form.useWatch('target_strategy', form) as 'latest' | 'specific' | undefined) ?? 'latest';
  const batch = (Form.useWatch('target_batch', form) as string | undefined)?.trim();
  const active = Boolean(suiteId && pattern && !(strategy === 'specific' && !batch));
  // JSON so the four fields can't collide across boundaries (a prefix ending in
  // the separator vs a pattern starting with it).
  const spec: BatchPreviewSpec = JSON.stringify([prefix, pattern, strategy, batch]);

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
      setState({ status: 'loading', spec });
      previewBatchTarget(suiteId as string, {
        pattern: pattern as string,
        strategy,
        ...(strategy === 'specific' ? { batch: batch as string } : {}),
        ...(prefix ? { prefix } : {}),
      })
        .then((path) => {
          if (current !== token.current) return;
          setState({ status: 'resolved', spec, path });
        })
        .catch((err: unknown) => {
          if (current !== token.current) return;
          const envelope = axios.isAxiosError(err)
            ? (err.response?.data as BatchPreviewErrorEnvelope | undefined)?.error
            : undefined;
          if (envelope?.code === BATCH_PREVIEW_NO_DATA_CODE) {
            setState({ status: 'no-match', spec });
            return;
          }
          // The 502's own message is deliberately generic ("could not list the
          // datasource store") because the backend must never echo an adapter
          // exception; the actionable half is the classified `detail.reason`
          // (`failure_classifier` — bad credential vs unreachable vs
          // misconfigured). Showing only the message throws away the only part
          // that tells the author what to go fix.
          const reason = typeof envelope?.detail?.reason === 'string' ? envelope.detail.reason : '';
          const message = [errorMessage(err), reason].filter(Boolean).join(' ');
          setState({ status: 'error', spec, message });
        });
    }, 400);
  }, [active, suiteId, prefix, pattern, strategy, batch, spec]);

  if (!active || state.status === 'idle') return null;

  // The stored answer is about an older spec — a debounce is still ticking for
  // the current one. Show that, rather than a stale claim wearing a fresh label.
  if (state.spec !== spec || state.status === 'loading') {
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
