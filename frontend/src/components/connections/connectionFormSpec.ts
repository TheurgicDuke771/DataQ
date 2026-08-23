import type { ConnectionType } from '../../api/connections';

/** Single source of truth for the add-connection form's per-type fields. */

export interface TextField {
  name: string;
  label: string;
  optional?: boolean;
  /**
   * `tags` renders a free-entry multi-value input whose config value is a `string[]` (e.g. dbt's
   * `jobs`); `toggle` renders a Switch whose config value is a boolean (e.g.
   */
  type?: 'text' | 'tags' | 'toggle';
  /** Helper text under the field. */
  extra?: string;
}

export interface AuthOption {
  value: string;
  label: string;
  /** Label for the secret this mode needs. */
  secretLabel: string;
  /** Secret is a multi-line PEM key rather than a single-line password. */
  multilineSecret?: boolean;
  /** An extra config field this mode needs (e.g. Airflow basic → username). */
  extraField?: TextField;
  /**
   * Present → the mode takes an optional second secret part (e.g. a key-pair private key's
   * passphrase) that rides the combined payload — see `composeSecret`.
   */
  passphraseLabel?: string;
  /**
   * Config text fields (by name) that this mode makes required even though the type declares them
   * optional (e.g. key-pair → role: the backend validates it.
   */
  requiredFields?: string[];
}

export interface TypeSpec {
  textFields: TextField[];
  /** Present → the type has an auth-type select; the first option is the default. */
  auth?: AuthOption[];
  /** Present (and no `auth`) → a single secret field with this label. */
  secretLabel?: string;
  /**
   * The single secret is **optional** (some configs need no credential — e.g. a dbt connection
   * whose artifacts live on a local `file://` path).
   */
  optionalSecret?: boolean;
  /** `config` values this type seeds even before the user touches the form — e.g. */
  defaultConfig?: Record<string, unknown>;
  /** A free-form, NON-SECRET `config.properties` dict this type accepts (e.g. */
  propertiesField?: { label: string; extra: string };
  /**
   * A SECOND credential this type may need (currently only the Iceberg SQL/hive catalog's DB
   * password, #754/#826/#1181) — a write-only field like the primary secret.
   */
  secondSecret?: {
    label: string;
    extra?: string;
    showWhen: (config: Record<string, unknown> | undefined) => boolean;
  };
  /** `config` fields that decide **where the PRIMARY credential is sent** (#1401). */
  destinationFields?: string[];
}

export const CONNECTION_FORM_SPECS: Record<ConnectionType, TypeSpec> = {
  snowflake: {
    textFields: [
      { name: 'account', label: 'Account' },
      { name: 'user', label: 'User' },
      { name: 'database', label: 'Database' },
      { name: 'schema', label: 'Schema' },
      { name: 'warehouse', label: 'Warehouse' },
      { name: 'role', label: 'Role' },
      {
        name: 'inventory_sync',
        label: 'Inventory sync',
        type: 'toggle',
        optional: true,
        extra: 'Daily sync of every table in this database into the asset view (ADR 0040).',
      },
    ],
    auth: [
      { value: 'password', label: 'Password', secretLabel: 'Password' },
      {
        value: 'key_pair',
        label: 'Key pair (RSA)',
        secretLabel: 'Private key (PEM)',
        multilineSecret: true,
        passphraseLabel: 'Key passphrase',
        requiredFields: ['role'],
      },
    ],
    destinationFields: ['account'],
  },
  adls_gen2: {
    textFields: [
      { name: 'account_url', label: 'Account URL' },
      { name: 'container', label: 'Container' },
    ],
    secretLabel: 'SAS token',
    destinationFields: ['account_url'],
  },
  s3: {
    // AWS by default; setting an endpoint points the same connection at any
    // S3-compatible store — MinIO, Ceph, R2, Wasabi, Backblaze (#1063).
    textFields: [
      { name: 'bucket', label: 'Bucket' },
      { name: 'region', label: 'Region' },
      { name: 'access_key_id', label: 'Access key ID' },
      {
        name: 'endpoint_url',
        label: 'Endpoint URL',
        optional: true,
        extra: 'S3-compatible store, e.g. https://minio.example.com:9000 — leave blank for AWS',
      },
      {
        name: 'addressing_style',
        label: 'Addressing style',
        optional: true,
        extra:
          'auto (default) · path · virtual — auto uses path addressing when an endpoint is set',
      },
    ],
    secretLabel: 'Secret access key',
    destinationFields: ['endpoint_url'],
  },
  unity_catalog: {
    textFields: [
      { name: 'workspace_url', label: 'Workspace URL' },
      { name: 'warehouse_id', label: 'Warehouse ID' },
      {
        name: 'inventory_sync',
        label: 'Inventory sync',
        type: 'toggle',
        optional: true,
        extra:
          'Daily sync of every table this workspace exposes into the asset view (ADR 0040). ' +
          'Needs SELECT on system.information_schema for this PAT.',
      },
    ],
    secretLabel: 'Personal access token (PAT)',
    destinationFields: ['workspace_url'],
  },
  iceberg: {
    // Native pyiceberg read (ADR 0030).
    textFields: [
      { name: 'catalog_type', label: 'Catalog type', extra: 'rest · sql · glue · hive' },
      {
        name: 'catalog_uri',
        label: 'Catalog URI',
        optional: true,
        extra: 'REST endpoint / SQL or metastore URI (required for rest, sql, hive)',
      },
      {
        name: 'catalog_name',
        label: 'Catalog name',
        optional: true,
        extra:
          'The pyiceberg catalog name — a SQL catalog scopes tables by this name; ' +
          'a mismatch fails every read.',
      },
      {
        name: 'warehouse',
        label: 'Warehouse location',
        optional: true,
        extra: 'Table warehouse / storage root, e.g. s3://bucket/warehouse',
      },
      {
        name: 'secret_property',
        label: 'Credential property',
        optional: true,
        extra: 'Catalog property the credential fills, e.g. token or s3.secret-access-key',
      },
    ],
    defaultConfig: { catalog_name: 'default' },
    propertiesField: {
      label: 'Catalog / storage properties',
      extra:
        'Extra non-secret catalog + storage options, e.g. s3.endpoint, s3.path-style-access, ' +
        'py-io-impl. These are stored in plaintext — never put a credential in a property ' +
        'value; use the credential fields below instead.',
    },
    secretLabel: 'Storage / catalog credential',
    optionalSecret: true,
    secondSecret: {
      label: 'Catalog DB password',
      extra:
        'The SQL/hive catalog’s own database password (distinct from the storage ' +
        'credential above) — never persisted in the catalog URI (#754/#826).',
      showWhen: (config) => config?.catalog_type === 'sql' || config?.catalog_type === 'hive',
    },
    // `catalog_uri` steers BOTH credentials — see the backend adapter's comment.
    destinationFields: ['catalog_uri', 'warehouse', 'properties', 'secret_property'],
  },
  adf: {
    textFields: [
      { name: 'subscription_id', label: 'Subscription ID' },
      { name: 'resource_group', label: 'Resource group' },
      { name: 'factory_name', label: 'Factory name' },
      { name: 'tenant_id', label: 'Tenant ID' },
      { name: 'client_id', label: 'Client ID' },
    ],
    secretLabel: 'Client secret',
  },
  airflow: {
    textFields: [{ name: 'base_url', label: 'Base URL' }],
    auth: [
      { value: 'token', label: 'Bearer token', secretLabel: 'Bearer token' },
      {
        value: 'basic',
        label: 'Basic auth',
        secretLabel: 'Password',
        extraField: { name: 'username', label: 'Username' },
      },
    ],
    destinationFields: ['base_url'],
  },
  // dbt is an OrchestrationProvider (ADR 0029), not a datasource — it binds to dbt's universal
  // surface (the run_results.json artifact + a post-build callback), never a host API.
  dbt: {
    textFields: [
      { name: 'project_name', label: 'Project name' },
      {
        name: 'artifacts_uri',
        label: 'Artifacts URI',
        extra: 'Base location of run_results.json — adls://…, s3://…, or file://…',
      },
      {
        name: 'jobs',
        label: 'Jobs',
        type: 'tags',
        extra: 'dbt job names polled under the artifacts URI. Type a name and press Enter.',
      },
      { name: 'region', label: 'Region (S3 only)', optional: true },
      { name: 'access_key_id', label: 'Access key ID (S3 only)', optional: true },
      // Same pair as the s3 datasource (#1063) — without these the artifacts poll would be the one
      // S3 path pinned to AWS.
      {
        name: 'endpoint_url',
        label: 'Endpoint URL (S3 only)',
        optional: true,
        extra: 'S3-compatible store, e.g. https://minio.example.com:9000 — blank for AWS',
      },
      {
        name: 'addressing_style',
        label: 'Addressing style (S3 only)',
        optional: true,
        extra:
          'auto (default) · path · virtual — auto uses path addressing when an endpoint is set',
      },
    ],
    secretLabel: 'Artifacts read credential (ADLS SAS / S3 secret key)',
    optionalSecret: true,
    destinationFields: ['artifacts_uri', 'endpoint_url'],
  },
};

/** Initial `config` for a freshly-selected type — seeds the default auth_type
 * (if any) plus the type's own `defaultConfig` (e.g. Iceberg's `catalog_name`). */
export function initialConfigForType(type: ConnectionType): Record<string, unknown> {
  const spec = CONNECTION_FORM_SPECS[type];
  const auth = spec.auth ? { auth_type: spec.auth[0].value } : {};
  return { ...spec.defaultConfig, ...auth };
}

/** The auth mode a connection's config selects (undefined for single-secret types). */
export function activeAuthOption(
  type: ConnectionType,
  config: Record<string, unknown> | undefined,
): AuthOption | undefined {
  const auth = CONNECTION_FORM_SPECS[type].auth;
  if (!auth) return undefined;
  return auth.find((a) => a.value === config?.auth_type) ?? auth[0];
}

/** Compose the write-only secret payload. */
export function composeSecret(secret: string, passphrase?: string): string {
  return passphrase?.trim() ? JSON.stringify({ private_key: secret, passphrase }) : secret;
}

/**
 * Which of a type's `destinationFields` the edited config has moved away from the stored
 * connection (#1401).
 */
export function movedDestinationFields(
  type: ConnectionType,
  edited: Record<string, unknown> | undefined,
  stored: Record<string, unknown>,
): string[] {
  if (edited === undefined) return [];
  return (CONNECTION_FORM_SPECS[type].destinationFields ?? []).filter(
    (field) => JSON.stringify(edited[field] ?? null) !== JSON.stringify(stored[field] ?? null),
  );
}
