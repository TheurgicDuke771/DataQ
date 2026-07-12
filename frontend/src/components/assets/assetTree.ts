import type { AssetSummary } from '../../api/assets';
import { namespaceLabel } from './namespaceLabel';

/**
 * Hierarchical asset browse (#802) — pure, so it can be unit-tested without
 * rendering antd (kept out of the `.tsx` so the Tree component can fast-refresh).
 *
 * This is a **presentation regrouping**, not a new identity: assets stay keyed by
 * the ADR 0034 OpenLineage `namespace` + `name`, which already encode the
 * datasource root and the dotted/slashed path down to the leaf. We derive the
 * tree straight from those strings:
 *
 * - **root** = the OL `namespace` (the datasource instance — Snowflake account,
 *   UC workspace, ADLS container, S3 bucket, Iceberg catalog). Assets are
 *   connection-*agnostic* by ADR 0034 (the same physical table reached via two
 *   connections is one asset), so the namespace is the honest "connection root".
 * - **middle + leaf** = the `name` split into segments. Warehouse names are
 *   dotted (`DB.SCHEMA.TABLE`); flat-file names are slashed paths
 *   (`raw/orders/2024.csv`). The last segment is the asset (a selectable leaf);
 *   the earlier segments are the database/catalog → schema/namespace folders,
 *   merged across assets that share a prefix.
 *
 * A node can be *both* a folder and a selectable asset — a flat-file `raw/orders`
 * leaf can coexist with `raw/orders/2024.csv` under it — so `asset` and
 * `children` are independent.
 */

export type DatasourceKind =
  'snowflake' | 'unity_catalog' | 'adls_gen2' | 's3' | 'iceberg' | 'other';

export interface AssetTreeNode {
  /** Stable, unique key: the full `ns::{namespace}/seg/seg…` path. */
  key: string;
  /** The segment label (a human datasource label on roots, one path segment otherwise). */
  label: string;
  /** Datasource kind — set on root (namespace) nodes only, for the icon. */
  kind?: DatasourceKind;
  /** The raw OL namespace — set on root nodes only. The label is for reading; this
   *  is the identity, kept so the UI can still surface it (tooltip) (#830). */
  namespace?: string;
  /** The asset — set on leaf (and folder-leaf) nodes; makes the node openable. */
  asset?: AssetSummary;
  children: AssetTreeNode[];
}

/** Classify an OL namespace by its scheme, for the root-node icon/label. */
export function datasourceKind(namespace: string): DatasourceKind {
  if (namespace.startsWith('snowflake://')) return 'snowflake';
  if (namespace.startsWith('unitycatalog://')) return 'unity_catalog';
  if (namespace.startsWith('abfss://')) return 'adls_gen2';
  if (namespace.startsWith('s3://')) return 's3';
  // Iceberg namespaces are the raw catalog_uri (thrift://…, http://…, "file") —
  // no single stable scheme, so everything unrecognised falls here.
  return 'other';
}

/**
 * Split an asset `name` into its hierarchy segments. Slashed names (flat-file
 * paths) split on `/`; everything else (dotted warehouse/UC/Iceberg names) on
 * `.`. Empty segments are dropped; a name that reduces to nothing falls back to
 * the whole name so an asset always has at least one (leaf) segment.
 */
export function nameSegments(name: string): string[] {
  const sep = name.includes('/') ? '/' : '.';
  const parts = name.split(sep).filter((s) => s.length > 0);
  return parts.length > 0 ? parts : [name];
}

interface MutableNode {
  key: string;
  label: string;
  kind?: DatasourceKind;
  namespace?: string;
  asset?: AssetSummary;
  children: Map<string, MutableNode>;
}

function freeze(node: MutableNode): AssetTreeNode {
  return {
    key: node.key,
    label: node.label,
    ...(node.kind ? { kind: node.kind } : {}),
    ...(node.namespace ? { namespace: node.namespace } : {}),
    ...(node.asset ? { asset: node.asset } : {}),
    children: [...node.children.values()]
      .map(freeze)
      .sort((a, b) => a.label.localeCompare(b.label)),
  };
}

/**
 * Build the connection-rooted asset tree from a flat asset list. Roots are
 * sorted by their (human) label, children by label, both case-insensitively; the
 * shape is deterministic for a given input (test-friendly, no render churn).
 */
export function buildAssetTree(assets: AssetSummary[]): AssetTreeNode[] {
  const roots = new Map<string, MutableNode>();
  for (const asset of assets) {
    const rootKey = `ns::${asset.namespace}`;
    let node = roots.get(rootKey);
    if (!node) {
      node = {
        key: rootKey,
        // Read the datasource, don't parse it: the raw namespace is a DSN for
        // Iceberg (#830). The key/sort still ride on the namespace, so grouping is
        // unchanged — only what's printed differs.
        label: namespaceLabel(asset.namespace).text,
        kind: datasourceKind(asset.namespace),
        namespace: asset.namespace,
        children: new Map(),
      };
      roots.set(rootKey, node);
    }
    const segments = nameSegments(asset.name);
    let cursor: MutableNode = node;
    let path = rootKey;
    segments.forEach((segment, i) => {
      path += `/${segment}`;
      let child = cursor.children.get(segment);
      if (!child) {
        child = { key: path, label: segment, children: new Map() };
        cursor.children.set(segment, child);
      }
      cursor = child;
      // The final segment is the asset itself — attach it (a node can already
      // have children from a longer sibling path, so this merges, not replaces).
      if (i === segments.length - 1) cursor.asset = asset;
    });
  }
  // Sort roots by what the user reads (the label), tie-broken by the namespace so
  // two datasources that shorten to the same label still order deterministically.
  return [...roots.values()]
    .map(freeze)
    .sort(
      (a, b) =>
        a.label.localeCompare(b.label) || (a.namespace ?? '').localeCompare(b.namespace ?? ''),
    );
}

/** Every node key that has descendants — the default-expanded set (roots + folders). */
export function expandableKeys(nodes: AssetTreeNode[]): string[] {
  const keys: string[] = [];
  const walk = (list: AssetTreeNode[]) => {
    for (const n of list) {
      if (n.children.length > 0) {
        keys.push(n.key);
        walk(n.children);
      }
    }
  };
  walk(nodes);
  return keys;
}
