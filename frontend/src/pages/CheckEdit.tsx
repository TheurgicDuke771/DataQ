import { HistoryOutlined } from '@ant-design/icons';
import { App, Button, Card, Flex, Form, Input, Select, Spin, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { type ConnectionType, getConnection } from '../api/connections';
import { type Check, getCheck, getSuite, updateCheck } from '../api/suites';
import { buildCheckPayload, configToForm } from '../components/checks/checkForm';
import {
  ConfigFieldItem,
  DimensionField,
  SeverityThresholdFields,
} from '../components/checks/checkFormFields';
import { CheckHistoryDrawer } from '../components/checks/CheckHistoryDrawer';
import { ColumnProfilePanel } from '../components/checks/ColumnProfilePanel';
import { DryRunPreview } from '../components/checks/DryRunPreview';
import {
  configFieldsFor,
  EXPECTATION_BY_TYPE,
  expectationsByCategoryFor,
} from '../components/checks/expectationCatalog';
import { PageError } from '../components/feedback/PageError';
import { Page } from '../components/layout/Page';
import { useAsyncAction } from '../hooks/useAsyncAction';
import { useAsyncData } from '../hooks/useAsyncData';

/**
 * Dedicated full-page edit-check flow (ADR 0022 — replaces the edit drawer). The
 * expectation Select (grouped by category) drives which config fields render; the
 * submitted `config` is rebuilt from only the selected expectation's declared
 * fields, so switching types never leaks stale kwargs. Creating a check is the
 * dedicated `/suites/:suiteId/checks/new` page. Version history is still a drawer
 * (the surviving read-only drawer alongside Share). The fetch + form live in a
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
  const { state } = useAsyncData(async () => {
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
  onCancel,
  onSaved,
}: {
  suiteId: string;
  check: Check;
  target: Record<string, unknown> | null;
  connectionType?: ConnectionType;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const { run, loading: submitting } = useAsyncAction('Save failed');
  const [historyOpen, setHistoryOpen] = useState(false);
  const selectedType = Form.useWatch('expectation_type', form) as string | undefined;
  const column = Form.useWatch(['config', 'column'], form) as string | undefined;
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
      await updateCheck(suiteId, check.id, update);
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
            {configFieldsFor(spec, connectionType).map((field) => (
              <ConfigFieldItem key={field.name} field={field} connectionType={connectionType} />
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
          <Button icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>
            History
          </Button>
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
        onClose={() => setHistoryOpen(false)}
      />
    </>
  );
}
