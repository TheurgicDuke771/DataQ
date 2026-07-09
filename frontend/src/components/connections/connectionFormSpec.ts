import type { ConnectionType } from '../../api/connections';

/**
 * Single source of truth for the add-connection form's per-type fields.
 *
 * Each type declares its config text fields and either an auth-type select (the
 * first option is the default) or a single secret. v1 only declares the auth
 * modes the backend accepts — ADLS Gen2's managed-identity and S3's IAM-role
 * modes are deferred, so they're absent here; every declared mode needs a secret.
 */

export interface TextField {
  name: string;
  label: string;
  optional?: boolean;
  /**
   * `tags` renders a free-entry multi-value input whose config value is a
   * `string[]` (e.g. dbt's `jobs`); default `text` is a single-line string.
   */
  type?: 'text' | 'tags';
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
   * Present → the mode takes an optional second secret part (e.g. a key-pair
   * private key's passphrase) that rides the combined payload — see
   * `composeSecret`.
   */
  passphraseLabel?: string;
  /**
   * Config text fields (by name) that this mode makes required even though
   * the type declares them optional (e.g. key-pair → role: the backend
   * validates it, since GX's key-pair form mandates a role for suite runs).
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
   * The single secret is **optional** (some configs need no credential — e.g. a
   * dbt connection whose artifacts live on a local `file://` path). Only meaningful
   * with `secretLabel`.
   */
  optionalSecret?: boolean;
}

export const CONNECTION_FORM_SPECS: Record<ConnectionType, TypeSpec> = {
  snowflake: {
    textFields: [
      { name: 'account', label: 'Account' },
      { name: 'user', label: 'User' },
      { name: 'database', label: 'Database' },
      { name: 'schema', label: 'Schema' },
      { name: 'warehouse', label: 'Warehouse' },
      { name: 'role', label: 'Role', optional: true },
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
  },
  adls_gen2: {
    textFields: [
      { name: 'account_url', label: 'Account URL' },
      { name: 'container', label: 'Container' },
    ],
    secretLabel: 'SAS token',
  },
  s3: {
    textFields: [
      { name: 'bucket', label: 'Bucket' },
      { name: 'region', label: 'Region' },
      { name: 'access_key_id', label: 'Access key ID' },
    ],
    secretLabel: 'Secret access key',
  },
  unity_catalog: {
    textFields: [
      { name: 'workspace_url', label: 'Workspace URL' },
      { name: 'warehouse_id', label: 'Warehouse ID' },
    ],
    secretLabel: 'Personal access token (PAT)',
  },
  iceberg: {
    // Native pyiceberg read (ADR 0030). The catalog `properties` dict and a named
    // `catalog_name` are advanced (API-settable); the form covers the common
    // REST/SQL self-hosted cases. `catalog_uri` is required for rest/sql/hive
    // (backend-validated), optional for glue; the single secret is injected as the
    // `secret_property` catalog property (e.g. `token`, `s3.secret-access-key`).
    textFields: [
      { name: 'catalog_type', label: 'Catalog type', extra: 'rest · sql · glue · hive' },
      {
        name: 'catalog_uri',
        label: 'Catalog URI',
        optional: true,
        extra: 'REST endpoint / SQL or metastore URI (required for rest, sql, hive)',
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
    secretLabel: 'Storage / catalog credential',
    optionalSecret: true,
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
  },
  // dbt is an OrchestrationProvider (ADR 0029), not a datasource — it binds to
  // dbt's universal surface (the run_results.json artifact + a post-build
  // callback), never a host API. The connection is a dbt *project* (resolved by
  // `project_name`); `jobs` are the trigger units polled under `artifacts_uri`.
  // The secret is the artifacts-store read credential (SAS / S3 secret key), and
  // it's optional — a local `file://` artifacts path needs none.
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
    ],
    secretLabel: 'Artifacts read credential (ADLS SAS / S3 secret key)',
    optionalSecret: true,
  },
};

/** Initial `config` for a freshly-selected type (seeds the default auth_type). */
export function initialConfigForType(type: ConnectionType): Record<string, unknown> {
  const auth = CONNECTION_FORM_SPECS[type].auth;
  return auth ? { auth_type: auth[0].value } : {};
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

/**
 * Compose the write-only secret payload. A passphrase rides a combined JSON
 * payload — one SecretStore entry per connection, so rotation stays atomic
 * (the backend Snowflake adapter parses it; #194). Without a passphrase —
 * including a whitespace-only one, which is a stray keystroke, not a real
 * passphrase — the secret is sent as-is (bare PEM = unencrypted key, unchanged).
 */
export function composeSecret(secret: string, passphrase?: string): string {
  return passphrase?.trim() ? JSON.stringify({ private_key: secret, passphrase }) : secret;
}
