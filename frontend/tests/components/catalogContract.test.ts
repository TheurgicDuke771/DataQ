import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { EXPECTATION_CATALOG } from '../../src/components/checks/expectationCatalog';

/** Catalog↔GX contract, frontend half (#205). */

const FIXTURE_PATH = resolve(__dirname, '../../../backend/tests/fixtures/expectation_catalog.json');

/** The contract surface: exactly what the backend validates against GX. */
function contractShape() {
  return EXPECTATION_CATALOG.map((spec) => ({
    type: spec.type,
    kind: spec.kind ?? 'expectation',
    // ADR 0038: the catalog's dimension is the editor's derived default and MIRRORS the backend
    // map.
    dimension: spec.dimension ?? null,
    fields: spec.fields.map((f) => f.name),
    // ADR 0036: mirrors DMF_UNBANDABLE_TYPES — a type the backend rejects any threshold on.
    noThresholds: spec.noThresholds ?? false,
  }));
}

describe('expectation catalog fixture (backend contract input)', () => {
  it('matches the checked-in JSON fixture', () => {
    const live = contractShape();
    // Strictly '1': any other value (including '0'/'false') must NOT flip the
    // guard into self-healing write-then-compare mode.
    if (process.env.UPDATE_CATALOG_FIXTURE === '1') {
      mkdirSync(dirname(FIXTURE_PATH), { recursive: true });
      writeFileSync(FIXTURE_PATH, `${JSON.stringify(live, null, 2)}\n`);
    }
    let fixture: unknown;
    try {
      fixture = JSON.parse(readFileSync(FIXTURE_PATH, 'utf-8'));
    } catch {
      throw new Error(
        `Missing/unreadable ${FIXTURE_PATH} — regenerate with UPDATE_CATALOG_FIXTURE=1 (see file docstring)`,
      );
    }
    expect(fixture, 'catalog changed without regenerating the backend fixture').toEqual(live);
  });

  it('declares a config-key name for every field', () => {
    for (const spec of EXPECTATION_CATALOG) {
      for (const field of spec.fields) {
        expect(field.name, `${spec.type} has a field without a name`).toBeTruthy();
      }
    }
  });
});
