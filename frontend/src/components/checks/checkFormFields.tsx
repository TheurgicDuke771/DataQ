import {
  Divider,
  Flex,
  Form,
  Input,
  InputNumber,
  Select,
  Skeleton,
  Switch,
  Typography,
} from 'antd';
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
 * Shared check-form field components, used by both the edit page (`CheckEdit`) and the create page
 * (`CheckNew`): the dynamic config-field renderer and the severity-threshold block.
 */

// Monaco lives in its own lazy chunk, pulled in only when a custom-SQL ('sql') field renders.
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
  configValues,
}: {
  field: ConfigField;
  /** Suite's connection type — drives the `type_` field's datasource-tailored
   *  help (issue #768). Every other field ignores it. */
  connectionType?: ConnectionType;
  /** Live sibling config values (the same object `fieldVisible`/`showWhen` reads). */
  configValues?: Record<string, unknown>;
}) {
  const label = field.optional ? `${field.label} (optional)` : field.label;
  const rules: Rule[] = field.optional ? [] : [{ required: true }];
  // `expect_column_values_to_be_of_type`'s `type_` field: GX compares against a different type
  // vocabulary per execution engine (SQL dialect type vs pandas dtype).
  const help = field.name === TYPE_FIELD_NAME ? typeFieldHint(connectionType) : field.help;
  // A required list of only delimiters ("," / " , ") is non-empty (so it passes `required`) but
  // parses to zero items.
  if (field.type === 'list' && !field.optional) {
    rules.push({
      validator: (_: unknown, value: unknown) =>
        parseList(value).length > 0
          ? Promise.resolve()
          : Promise.reject(new Error('Enter at least one value')),
    });
  }
  // `maxFrom` (anomaly's `min_points` <= `window`, #593 review): a submit-time check, not just the
  // InputNumber's `max` prop below — that prop only bounds FUTURE typing/stepping.
  const dynamicMax =
    field.type === 'number' && field.maxFrom && typeof configValues?.[field.maxFrom] === 'number'
      ? (configValues[field.maxFrom] as number)
      : undefined;
  if (field.type === 'number' && field.maxFrom) {
    rules.push({
      validator: (_: unknown, value: unknown) =>
        typeof value === 'number' && dynamicMax !== undefined && value > dynamicMax
          ? Promise.reject(
              new Error(`${field.label} must be ≤ ${field.maxFromLabel ?? field.maxFrom}`),
            )
          : Promise.resolve(),
    });
  }
  if (field.type === 'sql') {
    // Inline mirror of the backend read-only guardrail (ADR 0019) for fast feedback; the backend is
    // authoritative.
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
    <Form.Item
      name={['config', field.name]}
      label={label}
      rules={rules}
      extra={help}
      // CREATE-mode pre-fill (mirrors the backend's own default, e.g. anomaly's window=14 —
      // ConfigField.defaultValue docstring).
      initialValue={field.defaultValue}
      valuePropName={field.type === 'boolean' ? 'checked' : 'value'}
      // `maxFrom`: re-run this field's rules (and — via the parent's `configValues` watch —
      // recompute `dynamicMax` below) whenever the sibling field changes.
      dependencies={field.maxFrom ? [['config', field.maxFrom]] : undefined}
    >
      {field.type === 'number' ? (
        <InputNumber
          style={{ width: '100%' }}
          min={field.min}
          max={dynamicMax !== undefined ? Math.min(field.max ?? dynamicMax, dynamicMax) : field.max}
        />
      ) : field.type === 'select' ? (
        <Select options={field.options} placeholder={label} />
      ) : field.type === 'boolean' ? (
        <Switch />
      ) : (
        <Input placeholder={field.type === 'list' ? 'value1, value2, value3' : undefined} />
      )}
    </Form.Item>
  );
}

/** The GX/DMF engine choice (ADR 0036) — shown only for Freshness on Snowflake. */
export function EngineField({ initialValue }: { initialValue?: string }) {
  return (
    <Form.Item
      name="engine"
      label="Engine"
      initialValue={initialValue ?? 'gx'}
      extra="Great Expectations reads the data into a batch to evaluate it; Snowflake DMF runs SNOWFLAKE.CORE.FRESHNESS natively in the warehouse — no data leaves Snowflake, but it's Snowflake-only and evaluates fewer check types."
    >
      <Select
        options={[
          { value: 'gx', label: 'Great Expectations (gx)' },
          { value: 'dmf', label: 'Snowflake DMF (native)' },
        ]}
      />
    </Form.Item>
  );
}

/** The DQ-dimension select (ADR 0038) — *what quality aspect* this check measures. */
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
      // Only the CREATE page seeds the derived default.
      initialValue={initialValue}
      extra={
        derived
          ? 'Defaulted from the check type — change it if this check means something else.'
          : 'This check type has no obvious dimension. Pick one, or leave blank to record it as unclassified.'
      }
    >
      <Select
        // Clearable ONLY when the type has no derived default.
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

/** The optional warn / fail / critical severity-threshold inputs (ADR 0016). */
export function SeverityThresholdFields({ monitor }: { monitor?: MonitorThresholdSpec }) {
  const required = monitor?.requireFailOrCritical ?? false;
  const heading = required
    ? 'Severity thresholds (fail or critical required)'
    : 'Severity thresholds (optional)';
  const help =
    monitor?.help ??
    'Band the GX unexpected-% to warn / fail / critical (higher = worse). Leave blank for a binary pass/fail.';
  // "At least one of fail/critical is set to a POSITIVE value" — attached to ONLY the fail field
  // (so a single error message renders, not one under each).
  const failOrCriticalRule: Rule = ({ getFieldValue }) => ({
    validator: () => {
      if (!required) return Promise.resolve();
      const fail = getFieldValue('fail_threshold');
      const critical = getFieldValue('critical_threshold');
      return (typeof fail === 'number' && fail > 0) ||
        (typeof critical === 'number' && critical > 0)
        ? Promise.resolve()
        : Promise.reject(new Error('Set a fail or critical threshold'));
    },
  });
  // #568: mirror the backend's ordering guard (`check_service.validate_threshold_ordering`)
  // so an inverted set (e.g. warn=90/fail=50/critical=10) 422s here instead of round-tripping.
  const failOrderRule: Rule = ({ getFieldValue }) => ({
    validator: () => {
      const warn = getFieldValue('warn_threshold');
      const fail = getFieldValue('fail_threshold');
      return warn != null && fail != null && warn > fail
        ? Promise.reject(new Error('Fail must be ≥ Warn'))
        : Promise.resolve();
    },
  });
  const criticalOrderRule: Rule = ({ getFieldValue }) => ({
    validator: () => {
      const warn = getFieldValue('warn_threshold');
      const fail = getFieldValue('fail_threshold');
      const critical = getFieldValue('critical_threshold');
      if (fail != null && critical != null && fail > critical) {
        return Promise.reject(new Error('Critical must be ≥ Fail'));
      }
      if (warn != null && critical != null && warn > critical) {
        return Promise.reject(new Error('Critical must be ≥ Warn'));
      }
      return Promise.resolve();
    },
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
          dependencies={['warn_threshold', 'critical_threshold']}
          rules={required ? [failOrCriticalRule, failOrderRule] : [failOrderRule]}
        >
          <InputNumber min={0} max={monitor?.max} style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item
          name="critical_threshold"
          label="Critical ≥"
          style={{ flex: 1 }}
          dependencies={['warn_threshold', 'fail_threshold']}
          rules={[criticalOrderRule]}
        >
          <InputNumber min={0} max={monitor?.max} style={{ width: '100%' }} />
        </Form.Item>
      </Flex>
    </>
  );
}
