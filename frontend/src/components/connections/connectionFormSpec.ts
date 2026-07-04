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
}

export interface TypeSpec {
  textFields: TextField[];
  /** Present → the type has an auth-type select; the first option is the default. */
  auth?: AuthOption[];
  /** Present (and no `auth`) → a single secret field with this label. */
  secretLabel?: string;
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
 * (the backend Snowflake adapter parses it; #194). Without a passphrase the
 * secret is sent as-is (bare PEM = unencrypted key, unchanged).
 */
export function composeSecret(secret: string, passphrase?: string): string {
  return passphrase ? JSON.stringify({ private_key: secret, passphrase }) : secret;
}
