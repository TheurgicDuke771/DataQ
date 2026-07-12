/**
 * A human label for an OpenLineage `namespace` (#830) — presentation only.
 *
 * The namespace is the asset's identity (ADR 0034): it is what makes DataQ's
 * identifiers join byte-for-byte with dbt/Spark emissions, so it must stay the
 * physical, globally-unique, *stable* location string. It is a terrible thing to
 * read, though — the Iceberg one is a full SQLAlchemy DSN, complete with driver,
 * host and query string.
 *
 * So we key on the namespace and *show* this. Note we deliberately do NOT show the
 * connection name: a namespace is not 1:1 with a connection (three Snowflake
 * connections against one account all resolve to `snowflake://{account}` — that
 * collapse is the point, it's what makes the same table reached two ways one
 * asset), so there is no well-defined connection name for a namespace root.
 *
 * Every branch falls back to the raw namespace rather than rendering blank: an
 * unparseable or future namespace must degrade to "ugly but true", never to
 * nothing. Callers keep the raw string reachable (tooltip / copy).
 */

/** The datasource's display name, and the instance within it (`Snowflake`, `ACCT`). */
export interface NamespaceLabel {
  /** e.g. `Snowflake` — empty for an unrecognised namespace. */
  source: string;
  /** e.g. `PVQSOEQ-ZGB34383` — the instance; the raw namespace when unrecognised. */
  instance: string;
  /** `source · instance`, or just the raw namespace when unrecognised. */
  text: string;
}

function label(source: string, instance: string, raw: string): NamespaceLabel {
  // An empty instance (a malformed `snowflake://`) would render a dangling
  // "Snowflake · " — fall back to the raw string instead.
  if (!instance) return { source: '', instance: raw, text: raw };
  return { source, instance, text: `${source} · ${instance}` };
}

export function namespaceLabel(namespace: string): NamespaceLabel {
  const raw = namespace.trim();
  if (!raw) return { source: '', instance: '', text: '' };

  if (raw.startsWith('snowflake://')) {
    return label('Snowflake', raw.slice('snowflake://'.length), raw);
  }

  if (raw.startsWith('unitycatalog://')) {
    // `dbc-4492dde4-090c.cloud.databricks.com` → `dbc-4492dde4-090c`. Keep the full
    // host if it isn't the familiar `<workspace>.<domain…>` shape (self-hosted, a
    // bare host, a port) rather than truncating something meaningful away.
    const host = raw.slice('unitycatalog://'.length);
    const workspace = host.includes('.') ? host.slice(0, host.indexOf('.')) : host;
    return label('Databricks', workspace || host, raw);
  }

  if (raw.startsWith('abfss://')) {
    // `abfss://container@account.dfs.core.windows.net` → `account/container`, which
    // reads the way a person names it ("the raw container on dataqharness").
    const rest = raw.slice('abfss://'.length);
    const at = rest.indexOf('@');
    if (at > 0) {
      const container = rest.slice(0, at);
      const account = rest.slice(at + 1).split('.')[0];
      return label('ADLS', account ? `${account}/${container}` : container, raw);
    }
    return label('ADLS', rest, raw);
  }

  if (raw.startsWith('s3://')) {
    return label('S3', raw.slice('s3://'.length), raw);
  }

  // An Iceberg namespace is the catalog URI verbatim (ADR 0030 / #826 — password
  // stripped), which can carry *any* scheme, so there is nothing here that proves
  // "this is Iceberg". We deliberately don't guess: claiming a source we can't
  // verify would mislabel the first datasource that ships a URI namespace, and it
  // would contradict `datasourceKind`, which honestly answers `other` for exactly
  // this reason. So shorten the location and claim no source.
  //
  // A `file` catalog isn't even a URI.
  if (raw === 'file') return { source: '', instance: raw, text: 'Local catalog' };
  if (raw.includes('://')) {
    const afterScheme = raw.slice(raw.indexOf('://') + 3).split('?')[0];
    // For a SQL-catalog DSN the *database* is the whole story — the driver,
    // credentials, host, port and query string are noise to a reader (and the host
    // and username are infra detail we'd rather not print at a glance).
    const path = afterScheme.split('/').slice(1).join('/');
    if (path) return { source: '', instance: path, text: path };
    // No path — a REST/thrift catalog (`thrift://hive:9083`): the host IS the catalog.
    const host = afterScheme.split('/')[0];
    return { source: '', instance: host || raw, text: host || raw };
  }

  return { source: '', instance: raw, text: raw };
}
