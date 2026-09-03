// Dumps the check editor's expectation catalog as JSON for scripts/docs/gen-check-catalog.py.
// Built with Vite's SSR build (extensionless TS imports need a bundler), see that script.
import { EXPECTATION_CATALOG } from '../src/components/checks/expectationCatalog';

const out = EXPECTATION_CATALOG.map((s) => ({
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
process.stdout.write(JSON.stringify(out, null, 2));
