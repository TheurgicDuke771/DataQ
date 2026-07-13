/**
 * A human label for an OpenLineage `namespace`, and the datasource kind it implies
 * (#830) — presentation only.
 *
 * The namespace is the asset's identity (ADR 0034): it is what makes DataQ's
 * identifiers join byte-for-byte with dbt/Spark emissions, so it must stay the
 * physical, globally-unique, *stable* location string. It is a terrible thing to
 * read, though — the Iceberg one is a full SQLAlchemy DSN, complete with driver,
 * userinfo, host and query string.
 *
 * So we key on the namespace and *show* `namespaceLabel()`.
 *
 * Two things this deliberately does NOT do:
 *
 * - **It does not label by connection name.** A namespace is not 1:1 with a
 *   connection (three Snowflake connections against one account all resolve to
 *   `snowflake://{account}` — that collapse is the point, it's what makes the same
 *   table reached two ways *one* asset), so there is no well-defined connection
 *   name for a namespace root.
 * - **It does not claim a source it cannot prove.** An Iceberg namespace is a bare
 *   catalog URI of *any* scheme, so a URI alone proves nothing about the type.
 *   Guessing "Iceberg" would mislabel the first datasource that ships a URI
 *   namespace, so an unrecognised URI gets a shortened location and no source name.
 *
 * `SCHEMES` is the single source of truth for both the label and the icon kind:
 * they used to be two hand-synced prefix tables, which drifts silently (add `gs://`
 * to one and you get an `S3 ·` label under an `other` icon, or vice versa).
 *
 * Every branch falls back to the raw namespace rather than rendering blank — an
 * unparseable or future namespace must degrade to "ugly but true", never to nothing.
 * Callers keep the raw string reachable (tooltip / copy).
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
    // `dbc-4492dde4-090c.cloud.databricks.com` → `dbc-4492dde4-090c`. A host with no
    // dotted suffix (self-hosted, a bare host) has nothing to drop — keep it whole
    // rather than truncating something meaningful away.
    instance: (rest) => (rest.includes('.') ? rest.slice(0, rest.indexOf('.')) : rest),
  },
  {
    prefix: 'abfss://',
    kind: 'adls_gen2',
    // `container@account.dfs.core.windows.net` → `account/container`, which reads the
    // way a person names it ("the raw container on dataqharness").
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

/**
 * Catalog schemes whose URI has no *database* in it — the host itself is the
 * catalog. Everything else with a path is treated as a driver DSN whose last path
 * segment names the database.
 *
 * This split is why an Iceberg REST catalog doesn't end up labelled `v1`: for
 * `https://rest-catalog.example.com/v1` the path is an API route, not a name, so
 * two different REST catalogs would both read `v1` — meaningless, and identical.
 */
const HOST_IS_THE_CATALOG = new Set(['http', 'https', 'thrift', 'grpc']);

/** Classify an OL namespace by its scheme, for the root-node icon. */
export function datasourceKind(namespace: string): DatasourceKind {
  const raw = namespace.trim().toLowerCase();
  const spec = SCHEMES.find((s) => raw.startsWith(s.prefix));
  // An Iceberg namespace is the catalog URI verbatim (thrift://…, http://…, a
  // driver DSN, or the bare token "file") — no stable scheme, so it can't be
  // told apart from a future datasource's URI namespace. Don't guess.
  return spec ? spec.kind : 'other';
}

/** The authority (`host[:port]`) of a URI's post-scheme remainder, userinfo removed. */
function authority(afterScheme: string): string {
  const hostAndPath = afterScheme.split('/')[0];
  // Drop `user:pass@` / `user@`. The backend's `strip_uri_credentials` deliberately
  // KEEPS the username when it strips the password (it's an identifier, and part of
  // what makes the URI a stable identity), so namespaces really do arrive carrying
  // one — and a username is infra detail we shouldn't print at a glance.
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
      // A driver DSN (`postgresql+psycopg2://…/iceberg_catalog?sslmode=require`):
      // the *database* is the whole story. Driver, userinfo, host, port and query
      // string are noise to a reader.
      const path = afterScheme.split('/').filter(Boolean).slice(1);
      const database = path.length > 0 ? path[path.length - 1] : '';
      if (database) return database;
    }
    // REST/thrift catalog, or a DSN with no database path: the host IS the catalog.
    if (host) return host;
  }

  return raw;
}
