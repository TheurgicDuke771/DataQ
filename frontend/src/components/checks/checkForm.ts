import type { CheckCreate } from '../../api/suites';
import { EXPECTATION_BY_TYPE, type ExpectationSpec } from './expectationCatalog';

/**
 * Pure check-authoring form helpers shared by the edit drawer (`CheckDrawer`)
 * and the dedicated create page (`CheckNew`): the form↔payload conversions, so
 * the two surfaces can't drift on kwarg shaping. The matching field components
 * live in `checkFormFields.tsx`.
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
    expectation_type: values.expectation_type as string,
    config: formToConfig(spec, (values.config ?? {}) as Record<string, unknown>),
    warn_threshold: numOrNull(values.warn_threshold),
    fail_threshold: numOrNull(values.fail_threshold),
    critical_threshold: numOrNull(values.critical_threshold),
  };
}
