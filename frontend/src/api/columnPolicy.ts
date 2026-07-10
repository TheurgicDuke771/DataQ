import { api } from './client';

/**
 * Per-suite failing-sample redaction policy (#415): the shown `identifier_column`
 * (a non-PII row locator) + the always-masked `pii_columns`. `suggest` profiles +
 * classifies the target and returns a proposed policy (not saved). Reading needs
 * `view`, setting/suggesting needs `edit` on the suite (backend-gated).
 */

export interface ColumnPolicy {
  identifier_column: string | null;
  pii_columns: string[];
}

/** The target to profile for a suggestion — the suite's run target fields. */
export interface ColumnPolicySuggestTarget {
  table?: string;
  schema?: string;
  catalog?: string;
  /** Iceberg namespace (folded to `namespace.table` by the backend resolver). */
  namespace?: string;
  path?: string;
  file_format?: 'csv' | 'parquet';
}

export async function getColumnPolicy(suiteId: string): Promise<ColumnPolicy> {
  const { data } = await api.get<ColumnPolicy>(`/suites/${suiteId}/column-policy`);
  return data;
}

export async function setColumnPolicy(
  suiteId: string,
  payload: ColumnPolicy,
): Promise<ColumnPolicy> {
  const { data } = await api.put<ColumnPolicy>(`/suites/${suiteId}/column-policy`, payload);
  return data;
}

export async function suggestColumnPolicy(
  suiteId: string,
  target: ColumnPolicySuggestTarget,
): Promise<ColumnPolicy> {
  const { data } = await api.post<ColumnPolicy>(`/suites/${suiteId}/column-policy/suggest`, target);
  return data;
}
