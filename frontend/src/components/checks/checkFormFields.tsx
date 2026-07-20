import { Divider, Flex, Form, Input, InputNumber, Select, Skeleton, Typography } from 'antd';
import type { Rule } from 'antd/es/form';
import { lazy, Suspense } from 'react';

import type { ConnectionType } from '../../api/connections';
import { parseList } from './checkForm';
import { validateCustomSqlQuery } from './customSql';
import {
  DQ_DIMENSION_HELP,
  DQ_DIMENSIONS,
  TYPE_FIELD_NAME,
  typeFieldHint,
  type ConfigField,
  type DqDimension,
  type ExpectationSpec,
  type MonitorThresholdSpec,
} from './expectationCatalog';

/**
 * Shared check-form field components, used by both the edit page (`CheckEdit`)
 * and the create page (`CheckNew`): the dynamic config-field renderer
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

export function ConfigFieldItem({
  field,
  connectionType,
}: {
  field: ConfigField;
  /** Suite's connection type — drives the `type_` field's datasource-tailored
   *  help (issue #768). Every other field ignores it. */
  connectionType?: ConnectionType;
}) {
  const label = field.optional ? `${field.label} (optional)` : field.label;
  const rules: Rule[] = field.optional ? [] : [{ required: true }];
  // `expect_column_values_to_be_of_type`'s `type_` field: GX compares against a
  // different type vocabulary per execution engine (SQL dialect type vs pandas
  // dtype) — swap in the datasource-tailored hint over the catalog's generic
  // fallback help.
  const help = field.name === TYPE_FIELD_NAME ? typeFieldHint(connectionType) : field.help;
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
    <Form.Item name={['config', field.name]} label={label} rules={rules} extra={help}>
      {field.type === 'number' ? (
        <InputNumber style={{ width: '100%' }} />
      ) : (
        <Input placeholder={field.type === 'list' ? 'value1, value2, value3' : undefined} />
      )}
    </Form.Item>
  );
}

/**
 * The DQ-dimension select (ADR 0038) — *what quality aspect* this check measures.
 *
 * Pre-filled with the spec's derived default as a real selected value, not a
 * placeholder: what the author sees is what gets stored. Always editable, because
 * derivation is a guess about intent — the same between-check is Validity bounding
 * a percentage and Accuracy asserting a reconciled total.
 *
 * For an underivable type (custom SQL) it starts empty and stays optional, since
 * "unclassified" is a legitimate outcome the scorecard renders as a coverage gap.
 * Deliberately NOT required: forcing a guess would fill the coverage view with
 * noise, which is exactly what it exists to surface.
 */
export function DimensionField({
  spec,
  initialValue,
}: {
  spec?: ExpectationSpec;
  initialValue?: DqDimension;
}) {
  const derived = spec?.dimension;
  return (
    <Form.Item
      name="dimension"
      label="DQ dimension"
      // Only the CREATE page seeds the derived default. In edit mode the stored
      // value drives the field: applying the default there would silently
      // reclassify a check deliberately saved as unclassified, just because
      // someone opened the editor.
      initialValue={initialValue}
      extra={
        derived
          ? 'Defaulted from the check type — change it if this check means something else.'
          : 'This check type has no obvious dimension. Pick one, or leave blank to record it as unclassified.'
      }
    >
      <Select
        // Clearable ONLY when the type has no derived default. Blank means
        // "derive" to the backend, not "clear" — so on a derivable type a clear
        // affordance would silently restore the default on create, and be a
        // no-op on edit. Where there IS no default, blank genuinely stores NULL.
        allowClear={derived === undefined}
        placeholder="Unclassified"
        options={DQ_DIMENSIONS.map((d: DqDimension) => ({
          value: d,
          label: `${d.charAt(0).toUpperCase()}${d.slice(1)} — ${DQ_DIMENSION_HELP[d]}`,
        }))}
      />
    </Form.Item>
  );
}

/**
 * The optional warn / fail / critical severity-threshold inputs (ADR 0016).
 *
 * For GX expectations the bands are the unexpected-% (0–100). A `monitor` spec
 * overrides the help text + bounds (freshness = age-hours, unbounded; volume =
 * deviation-%, 0–100) and can make a fail/critical threshold **required**
 * (freshness has no in-config bound, so without one it can never fail — the #426
 * silent-green guard, also enforced by the backend 422).
 */
export function SeverityThresholdFields({ monitor }: { monitor?: MonitorThresholdSpec }) {
  const required = monitor?.requireFailOrCritical ?? false;
  const heading = required
    ? 'Severity thresholds (fail or critical required)'
    : 'Severity thresholds (optional)';
  const help =
    monitor?.help ??
    'Band the GX unexpected-% to warn / fail / critical (higher = worse). Leave blank for a binary pass/fail.';
  // "At least one of fail/critical is set" — attached to ONLY the fail field (so a
  // single error message renders, not one under each), with a dependency on
  // critical so filling critical clears it.
  const failOrCriticalRule: Rule = ({ getFieldValue }) => ({
    validator: () =>
      !required ||
      getFieldValue('fail_threshold') != null ||
      getFieldValue('critical_threshold') != null
        ? Promise.resolve()
        : Promise.reject(new Error('Set a fail or critical threshold')),
  });
  return (
    <>
      <Divider style={{ margin: '8px 0 16px' }}>{heading}</Divider>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        {help}
      </Typography.Paragraph>
      <Flex gap={12}>
        <Form.Item name="warn_threshold" label="Warn ≥" style={{ flex: 1 }}>
          <InputNumber min={0} max={monitor?.max} style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item
          name="fail_threshold"
          label="Fail ≥"
          style={{ flex: 1 }}
          dependencies={['critical_threshold']}
          rules={required ? [failOrCriticalRule] : []}
        >
          <InputNumber min={0} max={monitor?.max} style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item name="critical_threshold" label="Critical ≥" style={{ flex: 1 }}>
          <InputNumber min={0} max={monitor?.max} style={{ width: '100%' }} />
        </Form.Item>
      </Flex>
    </>
  );
}
