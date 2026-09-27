import type { ConnectionType } from '../../api/connections';

/** Single source of truth for the add-connection form's per-type fields. */

export interface TextField {
  name: string;
  label: string;
  optional?: boolean;
  /**
   * `tags` renders a free-entry multi-value input whose config value is a `string[]` (e.g. dbt's
   * `jobs`); `toggle` renders a Switch whose config value is a boolean (e.g.
   * `inventory_sync`); `multiline` renders a textarea (e.g. a PEM CA bundle).
   */
  type?: 'text' | 'tags' | 'toggle' | 'multiline';
  /** Helper text under the field. */
  extra?: string;
  /**
   * A closed vocabulary: renders a clearable Select instead of free text, so a typo can't
   * reach the backend and clearing it sends nothing (the backend default applies).
   */
  options?: string[];
  /**
   * `toggle` only: the value an absent config key should be treated as. Must be applied by
   * merging into the config object before it reaches the form (see `withToggleDefaults`) —
   * a per-`Form.Item` `initialValue` is silently overwritten the moment `setFieldsValue`
   * replaces the whole `config` subtree, which both the create and edit seeding paths do.
   */
  default?: boolean;
}

export interface AuthOption {
  value: string;
  label: string;
  /** Label for the secret this mode needs. */
  secretLabel: string;
  /** Secret is a multi-line PEM key rather than a single-line password. */
  multilineSecret?: boolean;
  /**
   * Extra config fields this mode needs (e.g. Airflow basic → username). Unmounted — and so not
   * submitted — under any other mode.
   */
  extraFields?: TextField[];
  /**
   * Shown where the expiry badge would be: this mode's credential has an expiry DataQ cannot
   * read, so the absence of a badge must never read as "does not expire".
   */
  expiryNotReadable?: string;
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
        default: true,
        extra: 'Daily sync of every table in this database into the asset view.',
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
    // Any ADLS-compatible Blob endpoint — e.g. Fabric OneLake (#1680), which needs a service
    // principal (its SAS is short-lived by design).
    textFields: [
      {
        name: 'account_url',
        label: 'Account URL',
        extra:
          'https://<account>.blob.core.windows.net, or an ADLS-compatible endpoint such as ' +
          'https://onelake.blob.fabric.microsoft.com',
      },
      {
        name: 'container',
        label: 'Container',
        extra: 'The container (for OneLake: the workspace name or ID)',
      },
    ],
    auth: [
      { value: 'sas', label: 'SAS token', secretLabel: 'SAS token' },
      {
        value: 'service_principal',
        label: 'Service principal (Entra ID)',
        secretLabel: 'Client secret',
        extraFields: [
          { name: 'tenant_id', label: 'Tenant ID' },
          { name: 'client_id', label: 'Client ID' },
        ],
        expiryNotReadable:
          "DataQ cannot read a client secret's expiry — track it in Entra ID, where it was " +
          'created, and re-authenticate before it lapses.',
      },
    ],
    destinationFields: ['account_url', 'auth_type', 'tenant_id', 'client_id'],
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
        default: true,
        extra:
          'Daily sync of every table this workspace exposes into the asset view. ' +
          'Needs SELECT on system.information_schema for this PAT.',
      },
    ],
    secretLabel: 'Personal access token (PAT)',
    destinationFields: ['workspace_url'],
  },
  postgres: {
    // One engine-generic adapter for any PostgreSQL server (#1678) — never a hosting vendor.
    textFields: [
      { name: 'host', label: 'Host', extra: 'Hostname or IP only — no scheme, port or path' },
      { name: 'port', label: 'Port', optional: true, extra: 'Defaults to 5432' },
      { name: 'database', label: 'Database' },
      { name: 'user', label: 'User' },
      {
        name: 'schema',
        label: 'Default schema',
        optional: true,
        extra: 'Where an unqualified run target resolves — defaults to public',
      },
      {
        name: 'sslmode',
        label: 'TLS mode',
        optional: true,
        options: ['require', 'verify-full', 'verify-ca', 'disable'],
        extra:
          'require when left empty · verify-* also checks the server certificate against the ' +
          'system trust store · disable sends everything in plaintext',
      },
      {
        name: 'inventory_sync',
        label: 'Inventory sync',
        type: 'toggle',
        optional: true,
        default: true,
        extra: 'Daily sync of every table this user can read into the asset view.',
      },
    ],
    secretLabel: 'Password',
    destinationFields: ['host', 'port'],
  },
  mysql: {
    // One engine-generic adapter for any MySQL or MariaDB server (#1684), via the MIT PyMySQL driver.
    textFields: [
      { name: 'host', label: 'Host', extra: 'Hostname or IP only — no scheme, port or path' },
      { name: 'port', label: 'Port', optional: true, extra: 'Defaults to 3306' },
      {
        name: 'database',
        label: 'Database',
        extra: 'Where an unqualified run target resolves (a MySQL schema is a database)',
      },
      { name: 'user', label: 'User' },
      {
        name: 'sslmode',
        label: 'TLS mode',
        optional: true,
        options: ['require', 'verify-full', 'verify-ca', 'disable'],
        extra:
          'require when left empty · verify-* also checks the server certificate against the ' +
          'system trust store · disable sends everything in plaintext',
      },
      {
        name: 'inventory_sync',
        label: 'Inventory sync',
        type: 'toggle',
        optional: true,
        default: true,
        extra: 'Daily sync of every table this user can read into the asset view.',
      },
    ],
    secretLabel: 'Password',
    destinationFields: ['host', 'port'],
  },
  mssql: {
    // One engine-generic adapter for anything that speaks SQL Server's TDS protocol (#1679,
    // ADR 0044) — SQL Server, Azure SQL, Synapse, Fabric SQL. TLS is always verified.
    textFields: [
      {
        name: 'host',
        label: 'Host',
        extra:
          'Hostname only — e.g. myserver.database.windows.net or ' +
          '<id>.datawarehouse.fabric.microsoft.com (no scheme, port or \\instance)',
      },
      { name: 'port', label: 'Port', optional: true, extra: 'Defaults to 1433' },
      { name: 'database', label: 'Database' },
      {
        name: 'schema',
        label: 'Default schema',
        optional: true,
        extra: 'Where an unqualified run target resolves — defaults to dbo',
      },
      {
        name: 'driver',
        label: 'Driver',
        optional: true,
        options: ['python-tds', 'odbc'],
        extra:
          'python-tds (default) ships with DataQ. odbc uses Microsoft ODBC Driver 18, which you ' +
          'must install in your own DataQ image — needed today for Microsoft Fabric SQL endpoints.',
      },
      {
        name: 'ca_certificate',
        label: 'Private CA certificate',
        optional: true,
        type: 'multiline',
        extra:
          'PEM of the CA your server certificate chains to, for a self-hosted server — leave ' +
          'empty for public CAs (Azure SQL, Fabric). python-tds driver only.',
      },
      {
        name: 'inventory_sync',
        label: 'Inventory sync',
        type: 'toggle',
        optional: true,
        default: true,
        extra: 'Daily sync of every table this login can read into the asset view.',
      },
    ],
    defaultConfig: { driver: 'python-tds' },
    auth: [
      {
        value: 'sql',
        label: 'SQL login',
        secretLabel: 'Password',
        extraFields: [{ name: 'user', label: 'User' }],
      },
      {
        value: 'entra_service_principal',
        label: 'Service principal (Entra ID)',
        secretLabel: 'Client secret',
        extraFields: [
          { name: 'tenant_id', label: 'Tenant ID' },
          { name: 'client_id', label: 'Client ID' },
        ],
        expiryNotReadable:
          "DataQ cannot read a client secret's expiry — track it in Entra ID, where it was " +
          'created, and re-authenticate before it lapses.',
      },
    ],
    destinationFields: [
      'host',
      'port',
      'auth_type',
      'tenant_id',
      'client_id',
      'ca_certificate',
      'driver',
    ],
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
        'credential above) — never persisted in the catalog URI.',
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
        extraFields: [{ name: 'username', label: 'Username' }],
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

/**
 * Fill in any `toggle` field's default for a key the config doesn't carry — a key already
 * present (`true`, or an explicit `false` opt-out) is left untouched. Must run on every config
 * object before it reaches the form: a `Form.Item`'s own `initialValue` is silently discarded
 * the moment `setFieldsValue` replaces the whole `config` subtree, which both the create and
 * edit seeding paths in `ConnectionForm` do.
 */
export function withToggleDefaults(
  type: ConnectionType,
  config: Record<string, unknown> | undefined,
): Record<string, unknown> {
  const result = { ...config };
  for (const field of CONNECTION_FORM_SPECS[type].textFields) {
    if (field.type === 'toggle' && field.default !== undefined && !(field.name in result)) {
      result[field.name] = field.default;
    }
  }
  return result;
}

/**
 * The type's default auth_type for a stored config that carries none (connections created before
 * the type had auth modes). Present keys are left untouched.
 */
export function withAuthDefault(
  type: ConnectionType,
  config: Record<string, unknown> | undefined,
): Record<string, unknown> {
  const auth = CONNECTION_FORM_SPECS[type].auth;
  if (!auth || config?.auth_type !== undefined) return { ...config };
  return { ...config, auth_type: auth[0].value };
}

/** Initial `config` for a freshly-selected type — seeds the default auth_type
 * (if any) plus the type's own `defaultConfig` (e.g. Iceberg's `catalog_name`), and any
 * `toggle` field's default (e.g. `inventory_sync`). */
export function initialConfigForType(type: ConnectionType): Record<string, unknown> {
  const spec = CONNECTION_FORM_SPECS[type];
  const auth = spec.auth ? { auth_type: spec.auth[0].value } : {};
  return withToggleDefaults(type, { ...spec.defaultConfig, ...auth });
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
  // An absent auth_type IS the default one — the backend compares them the same way.
  const was = withAuthDefault(type, stored);
  const now = withAuthDefault(type, edited);
  return (CONNECTION_FORM_SPECS[type].destinationFields ?? []).filter(
    (field) => JSON.stringify(now[field] ?? null) !== JSON.stringify(was[field] ?? null),
  );
}
