import { describe, expect, it } from 'vitest';

import type { AssetSummary } from '../../src/api/assets';
import {
  buildAssetTree,
  datasourceKind,
  expandableKeys,
  nameSegments,
} from '../../src/components/assets/assetTree';

// A minimal asset factory — the tree only reads id/namespace/name/env, but the
// leaf carries the whole summary so the UI can render its health tag.
function asset(
  over: Partial<AssetSummary> & Pick<AssetSummary, 'id' | 'namespace' | 'name'>,
): AssetSummary {
  return {
    env: null,
    description: null,
    owner_user_id: null,
    last_seen: '2026-07-01T10:00:00Z',
    suite_count: 1,
    worst_severity: null,
    checks_total: 0,
    checks_passed: 0,
    last_run_at: null,
    has_failed_run: false,
    has_active_run: false,
    has_operational_error: false,
    has_cancelled_run: false,
    has_skip: false,
    is_accessible: true,
    ...over,
  };
}

describe('datasourceKind', () => {
  it('classifies each OL namespace scheme, unknown → other', () => {
    expect(datasourceKind('snowflake://acct')).toBe('snowflake');
    expect(datasourceKind('unitycatalog://host')).toBe('unity_catalog');
    expect(datasourceKind('abfss://c@a.dfs.core.windows.net')).toBe('adls_gen2');
    expect(datasourceKind('s3://bucket')).toBe('s3');
    expect(datasourceKind('thrift://iceberg:9083')).toBe('other');
    expect(datasourceKind('file')).toBe('other');
  });
});

describe('nameSegments', () => {
  it('splits dotted warehouse names', () => {
    expect(nameSegments('ANALYTICS.PUBLIC.ORDERS')).toEqual(['ANALYTICS', 'PUBLIC', 'ORDERS']);
  });
  it('splits slashed flat-file paths', () => {
    expect(nameSegments('raw/orders/2024.csv')).toEqual(['raw', 'orders', '2024.csv']);
  });
  it('drops empty segments and never returns nothing', () => {
    expect(nameSegments('raw/orders/')).toEqual(['raw', 'orders']);
    expect(nameSegments('.')).toEqual(['.']); // pathological → whole name as one leaf
  });
});

describe('buildAssetTree', () => {
  it('roots by namespace and nests db → schema → table, leaf carries the asset', () => {
    const a = asset({ id: 'a1', namespace: 'snowflake://acct', name: 'ANALYTICS.PUBLIC.ORDERS' });
    const [root] = buildAssetTree([a]);
    // The root *reads* as a datasource but is still keyed on the raw OL namespace,
    // which it keeps so the UI can surface the identity on hover (#830).
    expect(root.label).toBe('Snowflake · acct');
    expect(root.namespace).toBe('snowflake://acct');
    expect(root.key).toBe('ns::snowflake://acct');
    expect(root.kind).toBe('snowflake');
    expect(root.asset).toBeUndefined();
    const db = root.children[0];
    expect(db.label).toBe('ANALYTICS');
    const schema = db.children[0];
    expect(schema.label).toBe('PUBLIC');
    const table = schema.children[0];
    expect(table.label).toBe('ORDERS');
    expect(table.asset).toBe(a);
    expect(table.children).toEqual([]);
  });

  it('merges assets that share a namespace/schema prefix', () => {
    const orders = asset({ id: 'a1', namespace: 'snowflake://acct', name: 'DB.S.ORDERS' });
    const customers = asset({ id: 'a2', namespace: 'snowflake://acct', name: 'DB.S.CUSTOMERS' });
    const roots = buildAssetTree([orders, customers]);
    expect(roots).toHaveLength(1); // one namespace root
    const schema = roots[0].children[0].children[0];
    // Two tables under the shared DB.S schema, sorted by label.
    expect(schema.children.map((c) => c.label)).toEqual(['CUSTOMERS', 'ORDERS']);
  });

  it('keeps distinct namespaces as separate roots (env distinctness, ADR 0034)', () => {
    const dev = asset({ id: 'a1', namespace: 'snowflake://dev', name: 'DB.S.T', env: 'dev' });
    const qa = asset({ id: 'a2', namespace: 'snowflake://qa', name: 'DB.S.T', env: 'qa' });
    const roots = buildAssetTree([dev, qa]);
    expect(roots.map((r) => r.label)).toEqual(['Snowflake · dev', 'Snowflake · qa']);
    // Two roots, still keyed on the distinct namespaces — the friendlier label must
    // not collapse them (the grouping rides on the namespace, not the label).
    expect(roots.map((r) => r.namespace)).toEqual(['snowflake://dev', 'snowflake://qa']);
  });

  it('handles a folder-and-leaf collision (a path that is both)', () => {
    const dir = asset({ id: 'a1', namespace: 's3://b', name: 'raw/orders' });
    const file = asset({ id: 'a2', namespace: 's3://b', name: 'raw/orders/2024.csv' });
    const [root] = buildAssetTree([dir, file]);
    const raw = root.children[0];
    const orders = raw.children[0];
    expect(orders.label).toBe('orders');
    expect(orders.asset).toBe(dir); // selectable as its own asset…
    expect(orders.children[0].asset).toBe(file); // …and a folder for the file below
  });
});

describe('expandableKeys', () => {
  it('returns roots + folders but not leaves', () => {
    const a = asset({ id: 'a1', namespace: 'snowflake://acct', name: 'DB.S.T' });
    const tree = buildAssetTree([a]);
    const keys = expandableKeys(tree);
    // root, DB, S are expandable; the leaf T is not.
    expect(keys).toContain('ns::snowflake://acct');
    expect(keys).toContain('ns::snowflake://acct/DB');
    expect(keys).toContain('ns::snowflake://acct/DB/S');
    expect(keys).not.toContain('ns::snowflake://acct/DB/S/T');
  });
});

describe('restricted rows (#920)', () => {
  it('places a locked leaf under the disclosed prefix path, with no name anywhere', () => {
    const tree = buildAssetTree([
      asset({
        id: 'a1',
        namespace: 'snowflake://acct',
        name: 'DATAQ_DB.ANALYTICS.MART_CUSTOMER_ORDERS',
      }),
      asset({
        id: 'r1',
        namespace: 'snowflake://acct',
        name: null,
        name_prefix_segments: ['DATAQ_DB', 'ANALYTICS'],
        is_accessible: false,
      }),
    ]);
    const [root] = tree;
    const db = root.children.find((c) => c.label === 'DATAQ_DB');
    const schema = db?.children.find((c) => c.label === 'ANALYTICS');
    expect(schema).toBeDefined();
    const labels = (schema?.children ?? []).map((c) => c.label);
    expect(labels).toContain('MART_CUSTOMER_ORDERS');
    expect(labels).toContain('Restricted');
    const locked = schema?.children.find((c) => c.restricted);
    expect(locked?.asset).toBeUndefined(); // nothing to open — the detail 404s it
    expect(locked?.key).toContain('r1'); // keyed by id, so siblings coexist
  });

  it('multiple restricted assets in one group each get their own leaf', () => {
    const tree = buildAssetTree([
      asset({
        id: 'r1',
        namespace: 'snowflake://acct',
        name: null,
        name_prefix_segments: ['DB', 'S'],
        is_accessible: false,
      }),
      asset({
        id: 'r2',
        namespace: 'snowflake://acct',
        name: null,
        name_prefix_segments: ['DB', 'S'],
        is_accessible: false,
      }),
    ]);
    const schema = tree[0].children[0].children[0];
    expect(schema.children.filter((c) => c.restricted)).toHaveLength(2);
  });

  it('a restricted asset with no prefix lands under the datasource root', () => {
    const tree = buildAssetTree([
      asset({ id: 'r3', namespace: 's3://bucket', name: null, is_accessible: false }),
    ]);
    expect(tree[0].children.some((c) => c.restricted)).toBe(true);
  });
});
