import { Divider, Flex, Form, Input, InputNumber, Skeleton, Typography } from 'antd';
import type { Rule } from 'antd/es/form';
import { lazy, Suspense } from 'react';

import { parseList } from './checkForm';
import { validateCustomSqlQuery } from './customSql';
import type { ConfigField } from './expectationCatalog';

/**
 * Shared check-form field components, used by both the edit drawer (`CheckDrawer`)
 * and the dedicated create page (`CheckNew`): the dynamic config-field renderer
 * and the severity-threshold block. Pure conversions live in `checkForm.ts`.
 */

// Monaco lives in its own lazy chunk, pulled in only when a custom-SQL ('sql')
// field renders. The wrapper is the direct Form.Item child so antd's value/onChange
// injection reaches the editor through the Suspense boundary.
const LazySqlEditor = lazy(() => import('./SqlEditorField'));

function SqlEditorControl({
  value,
  onChange,
}: {
  value?: string;
  onChange?: (value: string) => void;
}) {
  return (
    <Suspense fallback={<Skeleton.Input active block style={{ height: 180 }} />}>
      <LazySqlEditor value={value} onChange={onChange} />
    </Suspense>
  );
}

export function ConfigFieldItem({ field }: { field: ConfigField }) {
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
  if (field.type === 'sql') {
    // Inline mirror of the backend read-only guardrail (ADR 0019) for fast
    // feedback; the backend is authoritative. `required` is covered by the same
    // check (empty → message), so it replaces the bare required rule.
    return (
      <Form.Item
        name={['config', field.name]}
        label={label}
        extra={field.help}
        rules={[
          {
            validator: (_: unknown, value: unknown) => {
              const error = validateCustomSqlQuery(value as string | undefined);
              return error ? Promise.reject(new Error(error)) : Promise.resolve();
            },
          },
        ]}
      >
        <SqlEditorControl />
      </Form.Item>
    );
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

/** The optional warn / fail / critical severity-threshold inputs (ADR 0016). */
export function SeverityThresholdFields() {
  return (
    <>
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
    </>
  );
}
