import { CONNECTION_TYPES, type ConnectionType } from '../../api/connections';

/**
 * Presentation grouping for the add-connection source picker (ADR 0022 prototype).
 * Finer than the load-bearing datasource/orchestration split (`CONNECTION_KIND`):
 * it fans the four datasources into product-shaped buckets and — crucially — leads
 * with **Orchestration**, because DataQ's integration story starts there (watch the
 * provider's runs, then trigger suites on completion). Picker-only; the runtime
 * datasource-vs-orchestration distinction still flows through `CONNECTION_KIND`.
 */
export const SOURCE_CATEGORIES = [
  'Orchestration',
  'Warehouses',
  'Lakehouses',
  'Cloud Storage',
] as const;
export type SourceCategory = (typeof SOURCE_CATEGORIES)[number];

export const SOURCE_CATEGORY: Record<ConnectionType, SourceCategory> = {
  adf: 'Orchestration',
  airflow: 'Orchestration',
  snowflake: 'Warehouses',
  unity_catalog: 'Lakehouses',
  adls_gen2: 'Cloud Storage',
  s3: 'Cloud Storage',
};

/** One-line "what is this" subtitle under each source's label in the picker. */
export const CONNECTION_BLURB: Record<ConnectionType, string> = {
  snowflake: 'Cloud data warehouse',
  unity_catalog: 'Databricks governance layer',
  adls_gen2: 'Azure Data Lake Storage',
  s3: 'Object storage buckets',
  adf: 'Trigger & monitor pipeline runs',
  airflow: 'Monitor DAG runs',
};

/** Lead-in copy shown under a category heading (only Orchestration has one). */
export const SOURCE_CATEGORY_NOTE: Partial<Record<SourceCategory, string>> = {
  Orchestration:
    'Add your orchestration provider first — DataQ integrates by watching its runs, then triggers suites on completion.',
};

export interface SourceGroup {
  category: SourceCategory;
  types: ConnectionType[];
  note?: string;
}

/** Source types grouped by category in display order (Orchestration first), each
 *  group in canonical `CONNECTION_TYPES` order. Empty categories are dropped. */
export function sourcesByCategory(): SourceGroup[] {
  return SOURCE_CATEGORIES.map((category) => ({
    category,
    types: CONNECTION_TYPES.filter((t) => SOURCE_CATEGORY[t] === category),
    note: SOURCE_CATEGORY_NOTE[category],
  })).filter((g) => g.types.length > 0);
}
