// Dumps the check editor's expectation catalog + the datasource capability sets as JSON for
// scripts/docs/gen-check-catalog.py. Built with Vite's SSR build (extensionless TS imports need
// a bundler), see that script.
import {
  CONNECTION_TYPE_LABELS,
  DATASOURCE_TYPES,
  FILE_TYPES,
  MONITOR_CAPABLE_TYPES,
  SQL_BATCH_TYPES,
  SQL_QUERYABLE_TYPES,
} from '../src/api/connections';
import {
  EXPECTATION_CATALOG,
  MONITOR_CATEGORIES,
} from '../src/components/checks/expectationCatalog';

const catalog = EXPECTATION_CATALOG.map((s) => ({
  type: s.type,
  kind: s.kind ?? 'expectation',
  engine: s.engine ?? 'gx',
  label: s.label,
  description: s.description,
  category: s.category,
  dimension: s.dimension ?? null,
  noThresholds: s.noThresholds ?? false,
  dataframeOnly: s.dataframeOnly ?? false,
  requireFailOrCritical: s.thresholds?.requireFailOrCritical ?? false,
  fields: s.fields.map((f) => ({
    name: f.name,
    label: f.label,
    type: f.type,
    optional: f.optional ?? false,
    help: f.help ?? null,
  })),
}));

// Mirrors expectationsByCategoryFor(): which connection types see which category.
const datasources = {
  all: DATASOURCE_TYPES,
  sqlQueryable: SQL_QUERYABLE_TYPES,
  monitorCapable: MONITOR_CAPABLE_TYPES,
  sqlBatch: SQL_BATCH_TYPES,
  file: FILE_TYPES,
  monitorCategories: MONITOR_CATEGORIES,
  labels: CONNECTION_TYPE_LABELS,
};

process.stdout.write(JSON.stringify({ catalog, datasources }, null, 2));
