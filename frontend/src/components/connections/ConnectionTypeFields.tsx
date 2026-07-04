import { Form, Input, Select, type FormInstance } from 'antd';

import type { ConnectionType } from '../../api/connections';
import { activeAuthOption, CONNECTION_FORM_SPECS, type TextField } from './connectionFormSpec';

/**
 * Renders the type-specific config + secret form fields from CONNECTION_FORM_SPECS.
 * Config fields are namespaced under `config` (name={['config','account']}) so the
 * drawer submits `config` as one object; the write-only credential is `secret`.
 */

const requiredRule = [{ required: true }];

function ConfigTextField({ field }: { field: TextField }) {
  return (
    <Form.Item
      name={['config', field.name]}
      label={field.optional ? `${field.label} (optional)` : field.label}
      rules={field.optional ? undefined : requiredRule}
    >
      <Input />
    </Form.Item>
  );
}

/** The write-only credential input — shared by the create form and ReauthModal. */
export function SecretField({
  label,
  multiline = false,
  extra,
}: {
  label: string;
  multiline?: boolean;
  extra?: string;
}) {
  return (
    <Form.Item name="secret" label={label} rules={requiredRule} extra={extra}>
      {multiline ? (
        <Input.TextArea rows={4} autoComplete="off" />
      ) : (
        <Input.Password autoComplete="off" />
      )}
    </Form.Item>
  );
}

/** Optional second secret part (e.g. key-pair passphrase) — rides `composeSecret`.
 * The form's `requiredMark="optional"` renders the (optional) marker.
 * `preserve={false}` drops the value when the field unmounts (auth-mode switch,
 * modal close) so a stale passphrase can never wrap another mode's secret. */
export function PassphraseField({ label }: { label: string }) {
  return (
    <Form.Item
      name="secretPassphrase"
      label={label}
      preserve={false}
      extra="Only for passphrase-protected keys; leave blank for an unencrypted key."
    >
      <Input.Password autoComplete="off" />
    </Form.Item>
  );
}

export function ConnectionTypeFields({
  type,
  form,
  showSecret = true,
}: {
  type: ConnectionType;
  form: FormInstance;
  /** Edit mode omits the secret — credential rotation is the Re-auth flow. */
  showSecret?: boolean;
}) {
  const spec = CONNECTION_FORM_SPECS[type];
  const authType = Form.useWatch(['config', 'auth_type'], form) as string | undefined;
  const activeAuth = activeAuthOption(type, { auth_type: authType });

  return (
    <>
      {spec.textFields.map((f) => (
        <ConfigTextField key={f.name} field={f} />
      ))}

      {spec.auth && (
        <Form.Item name={['config', 'auth_type']} label="Auth type" rules={requiredRule}>
          <Select options={spec.auth.map((a) => ({ value: a.value, label: a.label }))} />
        </Form.Item>
      )}

      {activeAuth?.extraField && <ConfigTextField field={activeAuth.extraField} />}

      {showSecret &&
        (activeAuth ? (
          <>
            <SecretField label={activeAuth.secretLabel} multiline={activeAuth.multilineSecret} />
            {activeAuth.passphraseLabel && <PassphraseField label={activeAuth.passphraseLabel} />}
          </>
        ) : (
          spec.secretLabel && <SecretField label={spec.secretLabel} />
        ))}
    </>
  );
}
