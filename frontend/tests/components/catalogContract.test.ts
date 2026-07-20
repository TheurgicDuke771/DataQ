import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { EXPECTATION_CATALOG } from '../../src/components/checks/expectationCatalog';

/**
 * Catalog↔GX contract, frontend half (#205).
 *
 * The backend has no server catalog — `expectationCatalog.ts` is the only
 * source of truth for expectation `type` (→ GX class) and field names
 * (→ GX kwargs), and a typo only surfaces at suite-run time deep in the
 * worker. The contract test pinning that seam lives in the backend
 * (`backend/tests/datasources/test_catalog_gx_contract.py`) against the
 * pinned GX version; it reads the JSON fixture below. This test keeps the
 * fixture in lock-step with the live catalog, so catalog edits that skip
 * regeneration fail CI here rather than silently un-pinning the seam.
 *
 * To regenerate after a catalog change:
 *   UPDATE_CATALOG_FIXTURE=1 pnpm vitest run tests/components/catalogContract.test.ts
 */

const FIXTURE_PATH = resolve(__dirname, '../../../backend/tests/fixtures/expectation_catalog.json');

/** The contract surface: exactly what the backend validates against GX. */
function contractShape() {
  return EXPECTATION_CATALOG.map((spec) => ({
    type: spec.type,
    kind: spec.kind ?? 'expectation',
    // ADR 0038: the catalog's dimension is the editor's derived default and
    // MIRRORS the backend map. Carrying it in the fixture is what lets the
    // backend contract test prove the two agree — a silent divergence would show
    // the author one classification and store another. `null`, not omitted:
    // "underivable" is a real value the backend must also produce.
    dimension: spec.dimension ?? null,
    fields: spec.fields.map((f) => f.name),
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
