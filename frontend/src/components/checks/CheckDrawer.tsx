import {
  App,
  Button,
  Divider,
  Drawer,
  Flex,
  Form,
  Input,
  InputNumber,
  Select,
  Typography,
} from 'antd';
import { useEffect, useState } from 'react';

import type { Rule } from 'antd/es/form';

import { type Check, createCheck, updateCheck } from '../../api/suites';
import {
  type ConfigField,
  EXPECTATION_BY_TYPE,
  EXPECTATIONS_BY_CATEGORY,
  type ExpectationSpec,
} from './expectationCatalog';

/**
 * Author a GX expectation (v1's only check kind). The expectation Select (grouped
 * by category) drives which config fields render (from the expectation catalog);
 * the submitted `config` is rebuilt from only the selected expectation's declared
 * fields, so switching types never leaks stale kwargs to the backend.
 */
export function CheckDrawer({
  open,
  suiteId,
  check,
  onClose,
  onSaved,
}: {
  open: boolean;
  suiteId: string;
  /** undefined = create; a check = edit. */
  check?: Check;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);
  const isEdit = check !== undefined;
  const selectedType = Form.useWatch('expectation_type', form) as string | undefined;
  const spec = selectedType ? EXPECTATION_BY_TYPE[selectedType] : undefined;

  // Reset first (antd keeps values for unmounted fields, so stale config can
  // survive an open→close→open with a different check), then prefill on edit.
  useEffect(() => {
    if (!open) return;
    form.resetFields();
    if (check) {
      form.setFieldsValue({
        name: check.name,
        expectation_type: check.expectation_type,
        config: configToForm(EXPECTATION_BY_TYPE[check.expectation_type], check.config),
        warn_threshold: check.warn_threshold ?? undefined,
        fail_threshold: check.fail_threshold ?? undefined,
        critical_threshold: check.critical_threshold ?? undefined,
      });
    }
  }, [open, check, form]);

  const onSubmit = async () => {
    let values: Record<string, unknown>;
    try {
      values = await form.validateFields();
    } catch {
      return; // inline validation errors
    }
    const activeSpec = EXPECTATION_BY_TYPE[values.expectation_type as string];
    const payload = {
      name: values.name as string,
      expectation_type: values.expectation_type as string,
      config: formToConfig(activeSpec, (values.config ?? {}) as Record<string, unknown>),
      warn_threshold: numOrNull(values.warn_threshold),
      fail_threshold: numOrNull(values.fail_threshold),
      critical_threshold: numOrNull(values.critical_threshold),
    };
    setSubmitting(true);
    try {
      if (isEdit) {
        await updateCheck(suiteId, check.id, payload);
        message.success(`${payload.name}: saved`);
      } else {
        await createCheck(suiteId, payload);
        message.success(`${payload.name}: created`);
      }
      onSaved();
    } catch (err) {
      message.error(`Save failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Drawer
      title={isEdit ? `Edit “${check.name}”` : 'New check'}
      open={open}
      onClose={onClose}
      width={520}
      destroyOnHidden
      extra={
        <Flex gap={8}>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="primary" loading={submitting} onClick={onSubmit}>
            {isEdit ? 'Save' : 'Create'}
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
            options={EXPECTATIONS_BY_CATEGORY.map((g) => ({
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
            {spec.fields.map((field) => (
              <ConfigFieldItem key={field.name} field={field} />
            ))}
          </>
        )}

        <Divider style={{ margin: '8px 0 16px' }}>Severity thresholds (optional)</Divider>
        <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
          Band the GX unexpected-% to warn / fail / critical (higher = worse). Leave blank for a
          binary pass/fail.
        </Typography.Paragraph>
        <Flex gap={12}>
          <Form.Item name="warn_threshold" label="Warn ≥" style={{ flex: 1 }}>
            <InputNumber min={0} max={100} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="fail_threshold" label="Fail ≥" style={{ flex: 1 }}>
            <InputNumber min={0} max={100} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="critical_threshold" label="Critical ≥" style={{ flex: 1 }}>
            <InputNumber min={0} max={100} style={{ width: '100%' }} />
          </Form.Item>
        </Flex>
      </Form>
    </Drawer>
  );
}

/** Split a comma-separated list field into trimmed, non-empty items. */
function parseList(value: unknown): string[] {
  return String(value ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

function ConfigFieldItem({ field }: { field: ConfigField }) {
  const label = field.optional ? `${field.label} (optional)` : field.label;
  const rules: Rule[] = field.optional ? [] : [{ required: true }];
  // A required list of only delimiters ("," / " , ") is non-empty (so it passes
  // `required`) but parses to zero items — reject it inline rather than letting
  // the check save with an empty value_set that only fails later at GX run time.
  if (field.type === 'list' && !field.optional) {
    rules.push({
      validator: (_: unknown, value: unknown) =>
        parseList(value).length > 0
          ? Promise.resolve()
          : Promise.reject(new Error('Enter at least one value')),
    });
  }
  return (
    <Form.Item name={['config', field.name]} label={label} rules={rules} extra={field.help}>
      {field.type === 'number' ? (
        <InputNumber style={{ width: '100%' }} />
      ) : (
        <Input placeholder={field.type === 'list' ? 'value1, value2, value3' : undefined} />
      )}
    </Form.Item>
  );
}

function numOrNull(v: unknown): number | null {
  return typeof v === 'number' ? v : null;
}

/** Build the GX `config` kwargs from only the selected expectation's fields. */
function formToConfig(
  spec: ExpectationSpec | undefined,
  raw: Record<string, unknown>,
): Record<string, unknown> {
  const config: Record<string, unknown> = {};
  if (!spec) return config;
  for (const field of spec.fields) {
    const value = raw[field.name];
    if (value === undefined || value === null || value === '') continue;
    if (field.type === 'list') {
      const items = parseList(value);
      if (items.length > 0) config[field.name] = items;
    } else {
      config[field.name] = value;
    }
  }
  return config;
}

/** Inverse of formToConfig for edit-mode prefill (list array → comma string). */
function configToForm(
  spec: ExpectationSpec | undefined,
  config: Record<string, unknown>,
): Record<string, unknown> {
  const form: Record<string, unknown> = {};
  if (!spec) return form;
  for (const field of spec.fields) {
    const value = config[field.name];
    if (value === undefined) continue;
    form[field.name] = field.type === 'list' && Array.isArray(value) ? value.join(', ') : value;
  }
  return form;
}
