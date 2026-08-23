/**
 * A human label for an OpenLineage `namespace`, and the datasource kind it implies (#830) —
 * presentation only.
 */

export type DatasourceKind =
  'snowflake' | 'unity_catalog' | 'adls_gen2' | 's3' | 'iceberg' | 'other';

interface SchemeSpec {
  prefix: string;
  kind: DatasourceKind;
  source: string;
  /** The instance name, from the namespace with `prefix` already removed. */
  instance: (rest: string) => string;
}

const SCHEMES: SchemeSpec[] = [
  {
    prefix: 'snowflake://',
    kind: 'snowflake',
    source: 'Snowflake',
    // Account identifiers are case-significant to a reader — never fold them
    // (which is also why this can't be `new URL()`, whose `host` lowercases).
    instance: (rest) => rest,
  },
  {
    prefix: 'unitycatalog://',
    kind: 'unity_catalog',
    source: 'Databricks',
    // `dbc-1234abcd-5678.cloud.databricks.com` → `dbc-1234abcd-5678`.
    instance: (rest) => (rest.includes('.') ? rest.slice(0, rest.indexOf('.')) : rest),
  },
  {
    prefix: 'abfss://',
    kind: 'adls_gen2',
    // `container@account.dfs.core.windows.net` → `account/container`, which reads the
    // way a person names it ("the raw container on acmelake").
    source: 'ADLS',
    instance: (rest) => {
      const at = rest.indexOf('@');
      if (at <= 0) return rest;
      const container = rest.slice(0, at);
      const account = rest.slice(at + 1).split('.')[0];
      return account ? `${account}/${container}` : container;
    },
  },
  { prefix: 's3://', kind: 's3', source: 'S3', instance: (rest) => rest },
];

/** Catalog schemes whose URI has no *database* in it — the host itself is the catalog. */
const HOST_IS_THE_CATALOG = new Set(['http', 'https', 'thrift', 'grpc']);

/** Classify an OL namespace by its scheme, for the root-node icon. */
export function datasourceKind(namespace: string): DatasourceKind {
  const raw = namespace.trim().toLowerCase();
  const spec = SCHEMES.find((s) => raw.startsWith(s.prefix));
  // An Iceberg namespace is the catalog URI verbatim (thrift://…, http://…, a driver DSN, or the
  // bare token "file") — no stable scheme.
  return spec ? spec.kind : 'other';
}

/** The authority (`host[:port]`) of a URI's post-scheme remainder, userinfo removed. */
function authority(afterScheme: string): string {
  const hostAndPath = afterScheme.split('/')[0];
  // Drop `user:pass@` / `user@`.
  const at = hostAndPath.lastIndexOf('@');
  return at >= 0 ? hostAndPath.slice(at + 1) : hostAndPath;
}

export function namespaceLabel(namespace: string): string {
  const raw = namespace.trim();
  // Not `''`: a namespace that is only whitespace must still degrade to the raw
  // string, per the invariant above — never to a blank label under a lone icon.
  if (!raw) return namespace;

  const lower = raw.toLowerCase();
  const spec = SCHEMES.find((s) => lower.startsWith(s.prefix));
  if (spec) {
    const instance = spec.instance(raw.slice(spec.prefix.length));
    // A malformed `snowflake://` has no instance — "Snowflake · " is worse than
    // useless, so the raw string wins.
    return instance ? `${spec.source} · ${instance}` : raw;
  }

  // A `file` catalog isn't a URI at all.
  if (lower === 'file') return 'Local catalog';

  const sep = raw.indexOf('://');
  if (sep > 0) {
    const scheme = lower.slice(0, sep);
    const afterScheme = raw.slice(sep + 3).split('?')[0];
    const host = authority(afterScheme);

    if (!HOST_IS_THE_CATALOG.has(scheme)) {
      // A driver DSN (`postgresql+psycopg2://…/iceberg_catalog?sslmode=require`): the *database* is
      // the whole story.
      const path = afterScheme.split('/').filter(Boolean).slice(1);
      const database = path.length > 0 ? path[path.length - 1] : '';
      if (database) return database;
    }
    // REST/thrift catalog, or a DSN with no database path: the host IS the catalog.
    if (host) return host;
  }

  return raw;
}
