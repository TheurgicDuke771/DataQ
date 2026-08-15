import { HistoryOutlined, LineChartOutlined } from '@ant-design/icons';
import { App, Button, Card, Drawer, Flex, Form, Input, Select, Spin, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { type ConnectionType, getConnection } from '../api/connections';
import { canRunSuite, type Check, getCheck, getSuite, updateCheck } from '../api/suites';
import { buildCheckPayload, configToForm } from '../components/checks/checkForm';
import {
  ConfigFieldItem,
  DimensionField,
  SeverityThresholdFields,
} from '../components/checks/checkFormFields';
import { CheckHistoryDrawer } from '../components/checks/CheckHistoryDrawer';
import { CheckTrend } from '../components/checks/CheckTrend';
import { ColumnProfilePanel } from '../components/checks/ColumnProfilePanel';
import { DryRunPreview } from '../components/checks/DryRunPreview';
import {
  configFieldsFor,
  EXPECTATION_BY_TYPE,
  expectationsByCategoryFor,
  fieldVisible,
} from '../components/checks/expectationCatalog';
import { PageError } from '../components/feedback/PageError';
import { Page } from '../components/layout/Page';
import { useAsyncAction } from '../hooks/useAsyncAction';
import { apiFieldError } from '../utils/fieldErrors';
import { useAsyncData } from '../hooks/useAsyncData';

/**
 * Dedicated full-page edit-check flow (ADR 0022 — replaces the edit drawer). The
 * expectation Select (grouped by category) drives which config fields render; the
 * submitted `config` is rebuilt from only the selected expectation's declared
 * fields, so switching types never leaks stale kwargs. Creating a check is the
 * dedicated `/suites/:suiteId/checks/new` page. Version history is still a drawer
 * (the surviving drawer alongside Share) and now offers Restore (#283) when the
 * caller can edit — a successful restore reloads the page's suite/check/connection
 * fetch so the form re-seeds from the restored state. The fetch + form live in a
 * view keyed on the check id so a param-only route change reloads cleanly.
 */
export function CheckEdit() {
  const { suiteId, checkId } = useParams<{ suiteId: string; checkId: string }>();
  return <CheckEditView key={checkId} suiteId={suiteId} checkId={checkId} />;
}

function CheckEditView({ suiteId, checkId }: { suiteId?: string; checkId?: string }) {
  const navigate = useNavigate();
  const back = () => navigate(suiteId ? `/suites/${suiteId}` : '/suites');
  // Load the suite (target + datasource type) and the check together: the target
  // drives the dry-run preview, the connection type gates Custom SQL (ADR 0019),
  // and the check seeds the form. The connection only depends on the suite, so it
  // chains off getSuite while getCheck runs alongside (not serially after both).
  const { state, reload } = useAsyncData(async () => {
    if (!suiteId || !checkId) throw new Error('no check');
    const suiteP = getSuite(suiteId);
    // Best-effort: a suite may be readable while its connection isn't (shared
    // suite). The connection only gates the Custom-SQL category.
    const connectionP = suiteP.then((s) => getConnection(s.connection_id)).catch(() => null);
    const [suite, check, connection] = await Promise.all([
      suiteP,
      getCheck(suiteId, checkId),
      connectionP,
    ]);
    return { suite, check, connection };
  });

  return (
    <Page width={'form'}>
      <Flex justify="space-between" align="center" gap={12} wrap>
        <Typography.Title level={3} style={{ margin: 0 }}>
          {state.status === 'ok' ? `Edit “${state.data.check.name}”` : 'Edit check'}
        </Typography.Title>
        <Button onClick={back}>Cancel</Button>
      </Flex>

      {state.status === 'loading' && <Spin description="Loading check…" />}
      {state.status === 'error' && (
        <PageError
          error={state.error}
          kind={state.kind}
          httpStatus={state.httpStatus}
          requestId={state.requestId}
        />
      )}
      {state.status === 'ok' && suiteId && (
        <Card size="small">
          <CheckEditForm
            suiteId={suiteId}
            check={state.data.check}
            target={state.data.suite.target}
            connectionType={state.data.connection?.type}
            // History's Restore action is edit-gated the same way the rest of
            // the suite's write actions are (Suites.tsx `canRun`).
            canRestore={canRunSuite(state.data.suite)}
            onRestored={reload}
            onCancel={back}
            onSaved={back}
          />
        </Card>
      )}
    </Page>
  );
}

function CheckEditForm({
  suiteId,
  check,
  target,
  connectionType,
  canRestore,
  onRestored,
  onCancel,
  onSaved,
}: {
  suiteId: string;
  check: Check;
  target: Record<string, unknown> | null;
  connectionType?: ConnectionType;
  /** Whether the caller may restore a version (#283) — passed straight through
   *  to `CheckHistoryDrawer`. */
  canRestore: boolean;
  /** Refetches the suite/check/connection after a successful restore, so the
   *  form re-seeds from the restored (live) state. */
  onRestored: () => void;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const { run, loading: submitting } = useAsyncAction('Save failed');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [trendOpen, setTrendOpen] = useState(false);
  const selectedType = Form.useWatch('expectation_type', form) as string | undefined;
  const column = Form.useWatch(['config', 'column'], form) as string | undefined;
  // Drives which conditional fields render (anomaly's `column`, #593 —
  // ConfigField.showWhen); see CheckNew's matching watch for the rationale.
  const configValues = Form.useWatch('config', form) as Record<string, unknown> | undefined;
  const spec = selectedType ? EXPECTATION_BY_TYPE[selectedType] : undefined;
  // `kind` is immutable on update (a freshness check can't become an expectation),
  // so a monitor check locks its type — only its config + thresholds are editable.
  const isMonitor = check.kind !== 'expectation';
  // Comparison checks (ADR 0015) edit only name + thresholds here — the
  // source/dataset config is authored on the dedicated side-by-side page
  // (recreate to re-shape; repointing stays an API affair for now).
  const isComparison = check.kind === 'comparison';

  // Seed from the loaded check once.
  useEffect(() => {
    form.setFieldsValue({
      name: check.name,
      expectation_type: check.expectation_type,
      config: configToForm(EXPECTATION_BY_TYPE[check.expectation_type], check.config),
      // The STORED value, not the derived default (ADR 0038): an override must
      // survive a re-open, and a check saved as unclassified must not silently
      // acquire a classification just because someone opened the editor.
      dimension: check.dimension ?? undefined,
      warn_threshold: check.warn_threshold ?? undefined,
      fail_threshold: check.fail_threshold ?? undefined,
      critical_threshold: check.critical_threshold ?? undefined,
    });
  }, [check, form]);

  // Changing the expectation type re-derives the dimension, mirroring the create
  // page (which resets the whole form on type change). Without this the select
  // keeps the OLD type's classification while the help text below it claims to be
  // "defaulted from the check type" — and `buildCheckPayload` then sends the stale
  // value explicitly, so a uniqueness check ends up filed as completeness and
  // looks like a deliberate override forever.
  const initialType = check.expectation_type;
  useEffect(() => {
    if (selectedType && selectedType !== initialType) {
      form.setFieldsValue({ dimension: EXPECTATION_BY_TYPE[selectedType]?.dimension });
    }
  }, [selectedType, initialType, form]);

  const onSubmit = async () => {
    let values: Record<string, unknown>;
    try {
      values = await form.validateFields();
    } catch {
      return; // inline validation errors
    }
    await run(async () => {
      // `kind` is immutable on update — omit it from the PATCH (don't rely on the
      // backend silently ignoring an extra field). A comparison PATCH carries
      // only name + thresholds: sending a rebuilt (empty) config would 422 on
      // the comparison validator — and this page doesn't edit that config.
      const update = isComparison
        ? {
            name: values.name as string,
            dimension: (values.dimension as string | undefined) ?? null,
            warn_threshold:
              typeof values.warn_threshold === 'number' ? values.warn_threshold : null,
            fail_threshold:
              typeof values.fail_threshold === 'number' ? values.fail_threshold : null,
            critical_threshold:
              typeof values.critical_threshold === 'number' ? values.critical_threshold : null,
          }
        : (() => {
            const u = buildCheckPayload(values);
            delete u.kind;
            return u;
          })();
      try {
        await updateCheck(suiteId, check.id, update);
      } catch (err) {
        // The backend names the field it refused on (e.g. the sampling to
        // row-count conflict, #1333 F5). Put it there rather than in a toast that
        // dismisses itself — this refusal is about the interaction between this
        // check and the suite's run target, which takes a moment to read.
        const api = apiFieldError(err);
        const field = api?.detail.field;
        if (api && typeof field === 'string') {
          form.setFields([{ name: field, errors: [api.message] }]);
          return;
        }
        throw err;
      }
      message.success(`${values.name as string}: saved`);
      onSaved();
    });
  };

  return (
    <>
      <Form form={form} layout="vertical" onFinish={onSubmit}>
        <Form.Item name="name" label="Name" rules={[{ required: true }]}>
          <Input placeholder="e.g. order_id not null" />
        </Form.Item>
        <Form.Item
          name="expectation_type"
          label={isMonitor ? 'Monitor' : 'Expectation'}
          rules={[{ required: true }]}
          extra={
            isMonitor ? 'A monitor’s kind is fixed — recreate the check to change it.' : undefined
          }
        >
          <Select
            placeholder="Select an expectation"
            // A monitor's kind is immutable, so lock the type for monitor checks.
            disabled={isMonitor}
            // Grouped by category (antd optgroups). Pass the check's current type
            // so Custom SQL / monitor categories stay selectable even before the
            // connection loads.
            options={expectationsByCategoryFor(connectionType, check.expectation_type).map((g) => ({
              label: g.category,
              options: g.specs.map((e) => ({ value: e.type, label: e.label })),
            }))}
          />
        </Form.Item>

        {spec && (
          <>
            <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
              {spec.description}
            </Typography.Paragraph>
            {configFieldsFor(spec, connectionType)
              .filter((field) => fieldVisible(field, configValues))
              .map((field) => (
                <ConfigFieldItem
                  key={field.name}
                  field={field}
                  connectionType={connectionType}
                  configValues={configValues}
                />
              ))}
            <DimensionField spec={spec} />
          </>
        )}

        <SeverityThresholdFields monitor={spec?.thresholds} />

        {!isComparison && (
          <Form.Item>
            <ColumnProfilePanel suiteId={suiteId} target={target} column={column} />
          </Form.Item>
        )}
        {/* Dry-run previews a GX expectation; monitor kinds aren't GX, so skip it. */}
        {!spec?.kind && (
          <Form.Item>
            <DryRunPreview
              suiteId={suiteId}
              expectationType={selectedType}
              target={target}
              form={form}
            />
          </Form.Item>
        )}

        <Flex justify="space-between" align="center" gap={8}>
          <Flex gap={8}>
            <Button icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>
              History
            </Button>
            <Button icon={<LineChartOutlined />} onClick={() => setTrendOpen(true)}>
              Trend
            </Button>
          </Flex>
          <Flex gap={8}>
            <Button onClick={onCancel}>Cancel</Button>
            <Button type="primary" htmlType="submit" loading={submitting}>
              Save
            </Button>
          </Flex>
        </Flex>
      </Form>

      <CheckHistoryDrawer
        open={historyOpen}
        suiteId={suiteId}
        check={check}
        canRestore={canRestore}
        onRestored={onRestored}
        onClose={() => setHistoryOpen(false)}
      />

      {/* Metric trend (#594) — linked from the check editor as well as run-detail,
          so a user chasing a threshold/anomaly question doesn't have to find a
          past run first. `CheckTrend` is a static import above (same as
          RunDetail's), so recharts loads with THIS PAGE's route chunk, not on
          drawer open — the ADR 0022 lazy boundary is the route, not this
          drawer. Gating the render on `trendOpen` only defers mounting
          `CheckTrend` (and its history/baseline fetches) until the drawer is
          actually opened. */}
      <Drawer
        title={`${check.name} — trend`}
        open={trendOpen}
        onClose={() => setTrendOpen(false)}
        size={520}
      >
        {trendOpen && <CheckTrend suiteId={suiteId} check={check} />}
      </Drawer>
    </>
  );
}
