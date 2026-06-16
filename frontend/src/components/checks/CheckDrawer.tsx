import { HistoryOutlined } from '@ant-design/icons';
import { App, Button, Drawer, Flex, Form, Input, Select, Typography } from 'antd';
import { useEffect, useState } from 'react';

import type { ConnectionType } from '../../api/connections';
import { type Check, updateCheck } from '../../api/suites';
import { buildCheckPayload, configToForm } from './checkForm';
import { ConfigFieldItem, SeverityThresholdFields } from './checkFormFields';
import { CheckHistoryDrawer } from './CheckHistoryDrawer';
import { ColumnProfilePanel } from './ColumnProfilePanel';
import { DryRunPreview } from './DryRunPreview';
import { EXPECTATION_BY_TYPE, expectationsByCategoryFor } from './expectationCatalog';

/**
 * Edit a GX check. The expectation Select (grouped by category) drives which
 * config fields render (from the expectation catalog); the submitted `config` is
 * rebuilt from only the selected expectation's declared fields, so switching
 * types never leaks stale kwargs. Creating a check is the dedicated
 * `/suites/:suiteId/checks/new` page, not this drawer.
 */
export function CheckDrawer({
  open,
  suiteId,
  check,
  target,
  connectionType,
  onClose,
  onSaved,
}: {
  open: boolean;
  suiteId: string;
  check?: Check;
  /** The suite's run target (#215) — drives the dry-run preview's table/schema. */
  target: Record<string, unknown> | null;
  /** The suite's datasource type — gates the Custom-SQL category (ADR 0019). */
  connectionType?: ConnectionType;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const selectedType = Form.useWatch('expectation_type', form) as string | undefined;
  const column = Form.useWatch(['config', 'column'], form) as string | undefined;
  const spec = selectedType ? EXPECTATION_BY_TYPE[selectedType] : undefined;

  // Reset first (antd keeps values for unmounted fields, so stale config can
  // survive an open→close→open with a different check), then prefill.
  useEffect(() => {
    if (!open || !check) return;
    form.resetFields();
    form.setFieldsValue({
      name: check.name,
      expectation_type: check.expectation_type,
      config: configToForm(EXPECTATION_BY_TYPE[check.expectation_type], check.config),
      warn_threshold: check.warn_threshold ?? undefined,
      fail_threshold: check.fail_threshold ?? undefined,
      critical_threshold: check.critical_threshold ?? undefined,
    });
  }, [open, check, form]);

  // Close the history sub-drawer whenever this drawer closes or switches to a
  // different check — otherwise a left-open history would re-pop (showing the
  // new check) the next time the editor opens. Render-phase reset (the same
  // "adjust state when a prop changes" pattern as ImportSuiteDrawer's prevOpen),
  // not an effect, which can't setState synchronously.
  const histScope = open ? (check?.id ?? null) : null;
  const [histScopeSeen, setHistScopeSeen] = useState(histScope);
  if (histScope !== histScopeSeen) {
    setHistScopeSeen(histScope);
    setHistoryOpen(false);
  }

  const onSubmit = async () => {
    if (!check) return;
    let values: Record<string, unknown>;
    try {
      values = await form.validateFields();
    } catch {
      return; // inline validation errors
    }
    const payload = buildCheckPayload(values);
    setSubmitting(true);
    try {
      await updateCheck(suiteId, check.id, payload);
      message.success(`${payload.name}: saved`);
      onSaved();
    } catch (err) {
      message.error(`Save failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Drawer
      title={check ? `Edit “${check.name}”` : 'Edit check'}
      open={open}
      onClose={onClose}
      width={520}
      destroyOnHidden
      extra={
        <Flex gap={8}>
          {check && (
            <Button icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>
              History
            </Button>
          )}
          <Button onClick={onClose}>Cancel</Button>
          <Button type="primary" loading={submitting} onClick={onSubmit}>
            Save
          </Button>
        </Flex>
      }
    >
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="Name" rules={[{ required: true }]}>
          <Input placeholder="e.g. order_id not null" />
        </Form.Item>
        <Form.Item name="expectation_type" label="Expectation" rules={[{ required: true }]}>
          <Select
            placeholder="Select an expectation"
            // Grouped by category (antd optgroups) — the GX-Cloud-style picker.
            // Pass the check's current type so Custom SQL stays selectable when
            // editing one even before the connection type loads (else its
            // prefilled value would have no matching option).
            options={expectationsByCategoryFor(connectionType, check?.expectation_type).map(
              (g) => ({
                label: g.category,
                options: g.specs.map((e) => ({ value: e.type, label: e.label })),
              }),
            )}
          />
        </Form.Item>

        {spec && (
          <>
            <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
              {spec.description}
            </Typography.Paragraph>
            {spec.fields.map((field) => (
              <ConfigFieldItem key={field.name} field={field} />
            ))}
          </>
        )}

        <SeverityThresholdFields />

        <ColumnProfilePanel suiteId={suiteId} target={target} column={column} />

        <DryRunPreview
          suiteId={suiteId}
          expectationType={selectedType}
          target={target}
          form={form}
        />
      </Form>

      <CheckHistoryDrawer
        open={historyOpen}
        suiteId={suiteId}
        check={check ?? null}
        onClose={() => setHistoryOpen(false)}
      />
    </Drawer>
  );
}
