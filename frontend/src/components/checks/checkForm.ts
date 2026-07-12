import type { CheckCreate } from '../../api/suites';

import { COMPARISON_EXPECTATION_TYPE } from './expectationCatalog';
import { EXPECTATION_BY_TYPE, type ExpectationSpec } from './expectationCatalog';

/**
 * Pure check-authoring form helpers shared by the edit page (`CheckEdit`)
 * and the create page (`CheckNew`): the form↔payload conversions, so the two
 * surfaces can't drift on kwarg shaping. The matching field components live in
 * `checkFormFields.tsx`.
 */

/** Split a comma-separated list field into trimmed, non-empty items. */
export function parseList(value: unknown): string[] {
  return String(value ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
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
export function configToForm(
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

/** Assemble the create/update payload from validated form values. Rebuilds
 *  `config` from only the selected expectation's fields, so switching types
 *  never leaks stale kwargs. */
export function buildCheckPayload(values: Record<string, unknown>): CheckCreate {
  const spec = EXPECTATION_BY_TYPE[values.expectation_type as string];
  return {
    name: values.name as string,
    // The monitor kinds (freshness/volume) carry a non-default kind; expectations
    // (incl. custom-SQL) stay 'expectation'. The backend defaults to 'expectation'.
    kind: spec?.kind ?? 'expectation',
    expectation_type: values.expectation_type as string,
    config: formToConfig(spec, (values.config ?? {}) as Record<string, unknown>),
    warn_threshold: numOrNull(values.warn_threshold),
    fail_threshold: numOrNull(values.fail_threshold),
    critical_threshold: numOrNull(values.critical_threshold),
  };
}

/** Assemble a comparison check's payload (ADR 0015) from the side-by-side
 *  form's structured values — kept beside `buildCheckPayload` so the two
 *  payload shapes can't drift apart. */
export function buildComparisonPayload(values: Record<string, unknown>): CheckCreate {
  const raw = (values.source ?? {}) as Record<string, unknown>;
  const source: Record<string, unknown> = {};
  if ((values.source_mode ?? 'table') === 'query' && values.source_query) {
    source.query = values.source_query;
  } else {
    for (const key of ['table', 'schema', 'catalog', 'namespace', 'path']) {
      const value = raw[key];
      if (value !== undefined && value !== null && value !== '') source[key] = value;
    }
  }
  const config: Record<string, unknown> = {
    source,
    keys: (values.keys as string[] | undefined) ?? [],
  };
  if (values.target_query) config.target_query = values.target_query;
  if (typeof values.max_rows === 'number') config.max_rows = values.max_rows;
  return {
    name: values.name as string,
    kind: 'comparison',
    expectation_type: COMPARISON_EXPECTATION_TYPE,
    config,
    source_connection_id: values.source_connection_id as string,
    warn_threshold: typeof values.warn_threshold === 'number' ? values.warn_threshold : null,
    fail_threshold: typeof values.fail_threshold === 'number' ? values.fail_threshold : null,
    critical_threshold:
      typeof values.critical_threshold === 'number' ? values.critical_threshold : null,
  };
}
