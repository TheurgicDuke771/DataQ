import type { AssetSummary } from '../../api/assets';
import { type DatasourceKind, datasourceKind, namespaceLabel } from './namespaceLabel';

/**
 * Hierarchical asset browse (#802) — pure, so it can be unit-tested without rendering antd (kept
 * out of the `.tsx` so the Tree component can fast-refresh).
 */

// `DatasourceKind` + `datasourceKind` live in `namespaceLabel` — one scheme table feeds both the
// icon and the label, so they can't drift apart.
export { type DatasourceKind, datasourceKind } from './namespaceLabel';

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

/** Split an asset `name` into its hierarchy segments. */
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

/** Get-or-create the folder chain for `segments` under `start`; returns the last
 *  node and its key path. */
function descend(
  start: MutableNode,
  startPath: string,
  segments: string[],
): { cursor: MutableNode; path: string } {
  let cursor = start;
  let path = startPath;
  for (const segment of segments) {
    path += `/${segment}`;
    let child = cursor.children.get(segment);
    if (!child) {
      child = { key: path, label: segment, children: new Map() };
      cursor.children.set(segment, child);
    }
    cursor = child;
  }
  return { cursor, path };
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

/** Build the connection-rooted asset tree from a flat asset list. */
export function buildAssetTree(assets: AssetSummary[]): AssetTreeNode[] {
  const roots = new Map<string, MutableNode>();
  for (const asset of assets) {
    const rootKey = `ns::${asset.namespace}`;
    let node = roots.get(rootKey);
    if (!node) {
      node = {
        key: rootKey,
        // Read the datasource, don't parse it: the raw namespace is a DSN for Iceberg (#830).
        label: namespaceLabel(asset.namespace),
        kind: datasourceKind(asset.namespace),
        namespace: asset.namespace,
        children: new Map(),
      };
      roots.set(rootKey, node);
    }
    const segments = nameSegments(asset.name);
    const { cursor } = descend(node, rootKey, segments);
    // The final segment is the asset itself — attach it (a node can already
    // have children from a longer sibling path, so this merges, not replaces).
    cursor.asset = asset;
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
