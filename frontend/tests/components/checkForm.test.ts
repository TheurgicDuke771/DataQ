import { describe, expect, it } from 'vitest';

import {
  buildCheckPayload,
  buildComparisonPayload,
  configToForm,
  parseList,
} from '../../src/components/checks/checkForm';
import {
  COMPARISON_EXPECTATION_TYPE,
  EXPECTATION_BY_TYPE,
} from '../../src/components/checks/expectationCatalog';

/**
 * `buildCheckPayload` (checkForm.ts) — the conditional-field (`ConfigField.showWhen`)
 * half of #593's generic mechanism. `expectationCatalog.test.ts` covers
 * `fieldVisible` itself and the anomaly catalog entry that declares it; this file
 * covers the payload builder that must honor the SAME condition, because antd's
 * `Form` preserves an unmounted field's last value by default — a naive
 * `formToConfig` would happily resubmit a stale `column` after the author
 * switched `target_metric` back to `row_count`, and the backend actively REJECTS
 * that combination (`anomaly_params`: "known key, inapplicable metric").
 */
describe('buildCheckPayload — anomaly conditional column field (#593)', () => {
  it('omits column from the submitted config when target_metric is row_count, even with a stale value present', () => {
    const payload = buildCheckPayload({
      name: 'orders row-count anomaly',
      expectation_type: 'monitor:anomaly',
      config: {
        target_metric: 'row_count',
        // A value antd's Form preserved from before the author switched away
        // from freshness_age_hours — must never reach the backend.
        column: 'loaded_at',
        window: 14,
        min_points: 7,
        seasonality: false,
      },
      fail_threshold: 3,
    });
    expect(payload.config).toEqual({
      target_metric: 'row_count',
      window: 14,
      min_points: 7,
      seasonality: false,
    });
  });

  it('includes column when target_metric is freshness_age_hours', () => {
    const payload = buildCheckPayload({
      name: 'orders freshness anomaly',
      expectation_type: 'monitor:anomaly',
      config: {
        target_metric: 'freshness_age_hours',
        column: 'loaded_at',
        window: 14,
        min_points: 7,
        seasonality: true,
      },
      fail_threshold: 3,
    });
    expect(payload.config).toEqual({
      target_metric: 'freshness_age_hours',
      column: 'loaded_at',
      window: 14,
      min_points: 7,
      seasonality: true,
    });
  });

  it('carries kind=anomaly and the anomaly expectation_type', () => {
    const payload = buildCheckPayload({
      name: 'orders anomaly',
      expectation_type: 'monitor:anomaly',
      config: { target_metric: 'row_count' },
      fail_threshold: 3,
    });
    expect(payload.kind).toBe('anomaly');
    expect(payload.expectation_type).toBe('monitor:anomaly');
  });
});

/**
 * #1410: the Stryker spike scored checkForm.ts at 67% with survivors clustered
 * on (a) the value-coercion helpers, (b) buildCheckPayload's threshold
 * numOrNull guards, and (c) buildComparisonPayload's query-vs-table source
 * assembly and its own threshold coercion. Each block below targets a named
 * survivor class, asserting the OUTPUT SHAPE the backend depends on — a
 * malformed threshold (string instead of null) or a silently-dropped
 * query-mode source would save cleanly and misbehave only at run time.
 */
describe('parseList — comma-list field coercion (#1410)', () => {
  it('splits, trims, and drops empty items', () => {
    expect(parseList(' a, b ,, c ,')).toEqual(['a', 'b', 'c']);
  });

  it('returns [] for undefined, null, and whitespace-only input', () => {
    expect(parseList(undefined)).toEqual([]);
    expect(parseList(null)).toEqual([]);
    expect(parseList('   ')).toEqual([]);
  });

  it('stringifies non-string scalars rather than throwing', () => {
    expect(parseList(42)).toEqual(['42']);
  });
});

describe('configToForm — edit-mode prefill (#1410)', () => {
  const inSetSpec = EXPECTATION_BY_TYPE['expect_column_values_to_be_in_set'];

  it('joins a list-typed array back into a comma string', () => {
    const form = configToForm(inSetSpec, { column: 'status', value_set: ['a', 'b'] });
    expect(form).toEqual({ column: 'status', value_set: 'a, b' });
  });

  it('passes a non-array value on a list field through unchanged', () => {
    // A hand-imported config may hold a plain string where an array belongs;
    // prefill must not crash on .join, and must not fabricate a different value.
    const form = configToForm(inSetSpec, { value_set: 'a,b' });
    expect(form).toEqual({ value_set: 'a,b' });
  });

  it('skips fields absent from the stored config and returns {} without a spec', () => {
    expect(configToForm(inSetSpec, {})).toEqual({});
    expect(configToForm(undefined, { column: 'x' })).toEqual({});
  });
});

describe('buildCheckPayload — threshold coercion + list assembly (#1410)', () => {
  it('keeps numeric thresholds and nulls every non-number', () => {
    const payload = buildCheckPayload({
      name: 'c',
      expectation_type: 'expect_column_values_to_not_be_null',
      config: { column: 'id' },
      warn_threshold: 5,
      fail_threshold: '10', // a string must NOT pass through as '10'
      // critical_threshold absent entirely
    });
    expect(payload.warn_threshold).toBe(5);
    expect(payload.fail_threshold).toBeNull();
    expect(payload.critical_threshold).toBeNull();
  });

  it('parses list fields into arrays and drops an all-empty list entirely', () => {
    const withItems = buildCheckPayload({
      name: 'c',
      expectation_type: 'expect_column_values_to_be_in_set',
      config: { column: 'status', value_set: ' new , paid ' },
    });
    expect(withItems.config).toEqual({ column: 'status', value_set: ['new', 'paid'] });
    const empty = buildCheckPayload({
      name: 'c',
      expectation_type: 'expect_column_values_to_be_in_set',
      config: { column: 'status', value_set: ' , ' },
    });
    expect(empty.config).toEqual({ column: 'status' });
  });
});

describe('buildComparisonPayload — source assembly + coercion (ADR 0015, #1410)', () => {
  const base = {
    name: 'orders vs orders_stg',
    source_connection_id: 'conn-1',
    keys: ['order_id'],
  };

  it('table mode: copies only non-empty locator keys, never a query', () => {
    const payload = buildComparisonPayload({
      ...base,
      source_mode: 'table',
      source: { table: 'ORDERS', schema: 'RETAIL', catalog: '', namespace: null, path: undefined },
      source_query: 'SELECT 1', // present but table mode — must be ignored
    });
    expect(payload.config.source).toEqual({ table: 'ORDERS', schema: 'RETAIL' });
  });

  it('defaults to table mode when source_mode is unset', () => {
    const payload = buildComparisonPayload({ ...base, source: { table: 'ORDERS' } });
    expect(payload.config.source).toEqual({ table: 'ORDERS' });
  });

  it('query mode: the source is exactly {query} — table locators must not leak', () => {
    const payload = buildComparisonPayload({
      ...base,
      source_mode: 'query',
      source_query: 'SELECT * FROM orders',
      source: { table: 'STALE_TABLE' }, // antd-preserved stale value
    });
    expect(payload.config.source).toEqual({ query: 'SELECT * FROM orders' });
  });

  it('query mode with an EMPTY query falls back to the table locators', () => {
    // The `&& values.source_query` guard: an empty editor must not submit
    // {query: ''} — that would silently replace the table source with nothing.
    const payload = buildComparisonPayload({
      ...base,
      source_mode: 'query',
      source_query: '',
      source: { table: 'ORDERS' },
    });
    expect(payload.config.source).toEqual({ table: 'ORDERS' });
  });

  it('defaults keys to [] and includes optional knobs only when valid', () => {
    const payload = buildComparisonPayload({
      name: 'c',
      source_connection_id: 'conn-1',
      source: { table: 'T' },
      max_rows: '500', // string — must be dropped, not passed through
    });
    expect(payload.config.keys).toEqual([]);
    expect(payload.config).not.toHaveProperty('max_rows');
    expect(payload.config).not.toHaveProperty('target_query');
    expect(payload.config).not.toHaveProperty('tolerance');
  });

  it('assembles tolerance from whichever bounds are numeric, omitting it when neither is', () => {
    const both = buildComparisonPayload({
      ...base,
      source: { table: 'T' },
      tolerance_absolute: 5,
      tolerance_relative: 0.1,
    });
    expect(both.config.tolerance).toEqual({ absolute: 5, relative: 0.1 });
    const one = buildComparisonPayload({ ...base, source: { table: 'T' }, tolerance_relative: 0 });
    expect(one.config.tolerance).toEqual({ relative: 0 });
    const neither = buildComparisonPayload({
      ...base,
      source: { table: 'T' },
      tolerance_absolute: 'lots', // non-number — dropped
    });
    expect(neither.config).not.toHaveProperty('tolerance');
  });

  it('includes target_query and numeric max_rows when given', () => {
    const payload = buildComparisonPayload({
      ...base,
      source: { table: 'T' },
      target_query: 'SELECT * FROM target',
      max_rows: 1000,
    });
    expect(payload.config.target_query).toBe('SELECT * FROM target');
    expect(payload.config.max_rows).toBe(1000);
  });

  it('coerces thresholds to number-or-null exactly like the standard builder', () => {
    const payload = buildComparisonPayload({
      ...base,
      source: { table: 'T' },
      warn_threshold: 1,
      fail_threshold: '2', // string → null, never '2'
    });
    expect(payload.warn_threshold).toBe(1);
    expect(payload.fail_threshold).toBeNull();
    expect(payload.critical_threshold).toBeNull();
  });

  it('is kind=comparison with the records grain as the expectation_type fallback', () => {
    const fallback = buildComparisonPayload({ ...base, source: { table: 'T' } });
    expect(fallback.kind).toBe('comparison');
    expect(fallback.expectation_type).toBe(COMPARISON_EXPECTATION_TYPE);
    expect(fallback.source_connection_id).toBe('conn-1');
    const explicit = buildComparisonPayload({
      ...base,
      source: { table: 'T' },
      expectation_type: 'comparison:columns',
    });
    expect(explicit.expectation_type).toBe('comparison:columns');
  });
});

/**
 * Survivor-targeted additions after the first #1410 re-run (84.73%). Several
 * mutants survived only because `toEqual` treats `{key: undefined}` as `{}` —
 * these use toStrictEqual / key-presence so a guard that stops filtering
 * undefined values is actually caught. One mutant is equivalent and stays:
 * `(source_mode ?? 'table')` → `(source_mode ?? '')` — both fallbacks fail the
 * `=== 'query'` comparison identically, so table mode results either way.
 */
describe('checkForm — survivor-targeted edges (#1410)', () => {
  it('an unknown expectation_type yields kind=expectation and an empty config', () => {
    const payload = buildCheckPayload({
      name: 'c',
      expectation_type: 'not_a_real_type',
      config: { column: 'id' },
    });
    expect(payload.kind).toBe('expectation');
    expect(payload.config).toStrictEqual({});
  });

  it('formToConfig drops undefined, null, and empty-string values — the keys must not exist', () => {
    const payload = buildCheckPayload({
      name: 'c',
      expectation_type: 'expect_column_values_to_be_in_set',
      config: { column: '', value_set: undefined },
    });
    expect(Object.keys(payload.config)).toStrictEqual([]);
    const nullCase = buildCheckPayload({
      name: 'c',
      expectation_type: 'expect_column_values_to_not_be_null',
      config: { column: null },
    });
    expect(Object.keys(nullCase.config)).toStrictEqual([]);
    // A scalar field that is simply ABSENT reads as undefined — it must not
    // materialize as a `column: undefined` key either.
    const absentCase = buildCheckPayload({
      name: 'c',
      expectation_type: 'expect_column_values_to_not_be_null',
      config: {},
    });
    expect(Object.keys(absentCase.config)).toStrictEqual([]);
  });

  it('configToForm never materializes keys for absent values', () => {
    const spec = EXPECTATION_BY_TYPE['expect_column_values_to_be_in_set'];
    const form = configToForm(spec, { column: 'status', value_set: undefined });
    expect(Object.keys(form)).toStrictEqual(['column']);
  });

  it('configToForm joins ONLY list-typed fields — a stray array on a string field is untouched', () => {
    const spec = EXPECTATION_BY_TYPE['expect_column_values_to_be_in_set'];
    const form = configToForm(spec, { column: ['not', 'joined'] });
    expect(form.column).toStrictEqual(['not', 'joined']);
  });

  it('table mode copies every locator key the backend accepts, and only real values become keys', () => {
    const payload = buildComparisonPayload({
      name: 'c',
      source_connection_id: 'conn-1',
      keys: ['k'],
      source: {
        table: 'T',
        schema: 'S',
        catalog: 'C',
        namespace: 'N',
        path: '/landing/x.csv',
        extra_key: 'never-copied',
      },
    });
    expect(payload.config.source).toStrictEqual({
      table: 'T',
      schema: 'S',
      catalog: 'C',
      namespace: 'N',
      path: '/landing/x.csv',
    });
    const sparse = buildComparisonPayload({
      name: 'c',
      source_connection_id: 'conn-1',
      keys: ['k'],
      source: { table: 'T', schema: undefined },
    });
    expect(Object.keys(sparse.config.source as Record<string, unknown>)).toStrictEqual(['table']);
  });

  it('every threshold field coerces independently — number kept, non-number nulled, absent nulled', () => {
    const payload = buildComparisonPayload({
      name: 'c',
      source_connection_id: 'conn-1',
      keys: [],
      source: { table: 'T' },
      warn_threshold: 'not-a-number',
      critical_threshold: 3,
      // fail_threshold absent
    });
    expect(payload.warn_threshold).toBeNull();
    expect(payload.fail_threshold).toBeNull();
    expect(payload.critical_threshold).toBe(3);
    const std = buildCheckPayload({
      name: 'c',
      expectation_type: 'expect_column_values_to_not_be_null',
      config: { column: 'id' },
      warn_threshold: '5',
      critical_threshold: 9,
    });
    expect(std.warn_threshold).toBeNull();
    expect(std.critical_threshold).toBe(9);
  });
});
