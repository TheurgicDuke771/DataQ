import { Form, Input, Select, type FormInstance } from 'antd';

import type { ConnectionType } from '../../api/connections';

/**
 * Type-specific config + secret form fields for the add-connection drawer.
 *
 * Config fields are namespaced under `config` (e.g. name={['config','account']})
 * so the drawer can submit `config` as one object; the write-only credential is
 * the top-level `secret`. Each type's fields mirror its backend Pydantic config
 * (extra="forbid"), and the auth-type selects drive which credential fields show
 * and whether a secret is required (managed-identity / IAM-role need none).
 */

const required = [{ required: true }];

interface TextField {
  name: string;
  label: string;
  required?: boolean;
}

// Always-shown text fields per type (the auth-type-driven parts are below).
const TEXT_FIELDS: Record<ConnectionType, TextField[]> = {
  snowflake: [
    { name: 'account', label: 'Account', required: true },
    { name: 'user', label: 'User', required: true },
    { name: 'database', label: 'Database', required: true },
    { name: 'schema', label: 'Schema', required: true },
    { name: 'warehouse', label: 'Warehouse', required: true },
    { name: 'role', label: 'Role (optional)' },
  ],
  adls_gen2: [
    { name: 'account_url', label: 'Account URL', required: true },
    { name: 'container', label: 'Container', required: true },
  ],
  s3: [
    { name: 'bucket', label: 'Bucket', required: true },
    { name: 'region', label: 'Region', required: true },
  ],
  unity_catalog: [
    { name: 'workspace_url', label: 'Workspace URL', required: true },
    { name: 'warehouse_id', label: 'Warehouse ID', required: true },
  ],
  adf: [
    { name: 'subscription_id', label: 'Subscription ID', required: true },
    { name: 'resource_group', label: 'Resource group', required: true },
    { name: 'factory_name', label: 'Factory name', required: true },
    { name: 'tenant_id', label: 'Tenant ID', required: true },
    { name: 'client_id', label: 'Client ID', required: true },
  ],
  airflow: [{ name: 'base_url', label: 'Base URL', required: true }],
};

function TextFields({ fields }: { fields: TextField[] }) {
  return (
    <>
      {fields.map((f) => (
        <Form.Item
          key={f.name}
          name={['config', f.name]}
          label={f.label}
          rules={f.required ? required : undefined}
        >
          <Input />
        </Form.Item>
      ))}
    </>
  );
}

/** A required secret input (password-masked, or multiline for PEM keys). */
function SecretField({ label, multiline = false }: { label: string; multiline?: boolean }) {
  return (
    <Form.Item name="secret" label={label} rules={required}>
      {multiline ? (
        <Input.TextArea rows={4} autoComplete="off" />
      ) : (
        <Input.Password autoComplete="off" />
      )}
    </Form.Item>
  );
}

function AuthTypeSelect({ options }: { options: { value: string; label: string }[] }) {
  return (
    <Form.Item name={['config', 'auth_type']} label="Auth type" rules={required}>
      <Select options={options} />
    </Form.Item>
  );
}

export function ConnectionTypeFields({ type, form }: { type: ConnectionType; form: FormInstance }) {
  const authType = Form.useWatch(['config', 'auth_type'], form) as string | undefined;
  const base = <TextFields fields={TEXT_FIELDS[type]} />;

  switch (type) {
    case 'snowflake':
      // Password vs key-pair: the secret is the password or the PEM private key.
      return (
        <>
          {base}
          <AuthTypeSelect
            options={[
              { value: 'password', label: 'Password' },
              { value: 'key_pair', label: 'Key pair (RSA)' },
            ]}
          />
          {authType === 'key_pair' ? (
            <SecretField label="Private key (PEM)" multiline />
          ) : (
            <SecretField label="Password" />
          )}
        </>
      );
    case 'adls_gen2':
      // SAS needs a token; managed identity needs no stored secret.
      return (
        <>
          {base}
          <AuthTypeSelect
            options={[
              { value: 'sas', label: 'SAS token' },
              { value: 'managed_identity', label: 'Managed identity' },
            ]}
          />
          {authType !== 'managed_identity' && <SecretField label="SAS token" />}
        </>
      );
    case 's3':
      // Access key needs an id + secret; IAM role needs neither.
      return (
        <>
          {base}
          <AuthTypeSelect
            options={[
              { value: 'access_key', label: 'Access key' },
              { value: 'iam_role', label: 'IAM role' },
            ]}
          />
          {authType !== 'iam_role' && (
            <>
              <Form.Item name={['config', 'access_key_id']} label="Access key ID" rules={required}>
                <Input />
              </Form.Item>
              <SecretField label="Secret access key" />
            </>
          )}
        </>
      );
    case 'airflow':
      // Basic auth needs a username; token auth does not. Both need a secret.
      return (
        <>
          {base}
          <AuthTypeSelect
            options={[
              { value: 'token', label: 'Bearer token' },
              { value: 'basic', label: 'Basic auth' },
            ]}
          />
          {authType === 'basic' && (
            <Form.Item name={['config', 'username']} label="Username" rules={required}>
              <Input />
            </Form.Item>
          )}
          <SecretField label={authType === 'basic' ? 'Password' : 'Bearer token'} />
        </>
      );
    case 'unity_catalog':
      return (
        <>
          {base}
          <SecretField label="Personal access token (PAT)" />
        </>
      );
    case 'adf':
      return (
        <>
          {base}
          <SecretField label="Client secret" />
        </>
      );
  }
}
