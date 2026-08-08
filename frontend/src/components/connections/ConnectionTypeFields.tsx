import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { Button, Flex, Form, Input, Select, Switch, type FormInstance } from 'antd';
import { useEffect, useRef, useState } from 'react';

import type { ConnectionType } from '../../api/connections';
import { activeAuthOption, CONNECTION_FORM_SPECS, type TextField } from './connectionFormSpec';

/**
 * Renders the type-specific config + secret form fields from CONNECTION_FORM_SPECS.
 * Config fields are namespaced under `config` (name={['config','account']}) so the
 * drawer submits `config` as one object; the write-only credential is `secret`.
 *
 * None of the fields below append their own "(optional)" suffix — the enclosing
 * `Form`s (ConnectionForm, ReauthModal) render with `requiredMark="optional"`,
 * which already appends the marker to every field with no `required` rule. Doing
 * both doubles it (#1066).
 */

const requiredRule = [{ required: true }];

function ConfigTextField({
  field,
  forceRequired = false,
}: {
  field: TextField;
  forceRequired?: boolean;
}) {
  const optional = field.optional && !forceRequired;
  if (field.type === 'toggle') {
    // A boolean config flag (e.g. `inventory_sync`, ADR 0040). `valuePropName`
    // wires the Switch's `checked` into the form value; an untouched toggle
    // simply omits the key, which the backend defaults to false.
    //
    // No `rules`, so the form's requiredMark="optional" appends "(optional)" —
    // deliberate: it is true (unchecked is always valid) and consistent with
    // every other rule-less field (#1066's rule bans DOUBLING the marker, not
    // showing it). `forceRequired` is a text-field concept and is intentionally
    // not honored here — a toggle is never required.
    return (
      <Form.Item
        name={['config', field.name]}
        label={field.label}
        extra={field.extra}
        valuePropName="checked"
      >
        <Switch />
      </Form.Item>
    );
  }
  return (
    <Form.Item
      name={['config', field.name]}
      label={field.label}
      rules={optional ? undefined : requiredRule}
      extra={field.extra}
    >
      {field.type === 'tags' ? (
        <Select mode="tags" tokenSeparators={[',']} placeholder="Add one or more…" />
      ) : (
        <Input />
      )}
    </Form.Item>
  );
}

/** The write-only credential input — shared by the create form and ReauthModal. */
export function SecretField({
  label,
  multiline = false,
  extra,
  optional = false,
}: {
  label: string;
  multiline?: boolean;
  extra?: string;
  /** The credential isn't required (e.g. a dbt connection on a local file:// path). */
  optional?: boolean;
}) {
  return (
    <Form.Item
      name="secret"
      label={label}
      rules={optional ? undefined : requiredRule}
      extra={extra}
    >
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

interface PropertyRow {
  id: number;
  key: string;
  value: string;
}

function rowsToDict(rows: PropertyRow[]): Record<string, string> {
  const dict: Record<string, string> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (key) dict[key] = row.value;
  }
  return dict;
}

function dictToRows(dict: Record<string, string> | undefined): PropertyRow[] {
  return Object.entries(dict ?? {}).map(([key, value], i) => ({ id: i, key, value }));
}

/**
 * Add/remove key-value editor for a free-form, NON-SECRET `properties` dict
 * (Iceberg's catalog/storage options, #1181 — e.g. `s3.endpoint`,
 * `s3.path-style-access`). A plain antd Form.Item child (`value`/`onChange`
 * wired in by the enclosing `<Form.Item name={[...]}>`), so it slots into the
 * same nested-`config` binding every other field here uses.
 *
 * Rows are tracked in local state (not derived fresh from `value` on every
 * render) so two blank/in-progress rows can coexist while typing — collapsing
 * straight through a `Record<string, string>` would lose one the instant both
 * keys are still empty strings. `value` is resynced from ONLY when it changes
 * for a reason other than this component's own last `onChange` (an external
 * reset: the surrounding form loading a saved connection, or seeding a fresh
 * type's defaults) — comparing the serialized dict, not the object reference,
 * since Form.Item hands back a new object each render regardless.
 */
export function PropertiesEditor({
  value,
  onChange,
}: {
  value?: Record<string, string>;
  onChange?: (value: Record<string, string>) => void;
}) {
  const [rows, setRows] = useState<PropertyRow[]>(() => dictToRows(value));
  const nextId = useRef(rows.length);
  const lastEmitted = useRef(JSON.stringify(rowsToDict(rows)));

  useEffect(() => {
    const incoming = JSON.stringify(value ?? {});
    if (incoming === lastEmitted.current) return; // our own round-trip — not a reset
    const next = dictToRows(value);
    setRows(next);
    nextId.current = next.length;
    lastEmitted.current = incoming;
  }, [value]);

  const emit = (next: PropertyRow[]) => {
    setRows(next);
    const dict = rowsToDict(next);
    lastEmitted.current = JSON.stringify(dict);
    onChange?.(dict);
  };

  return (
    <Flex vertical gap={8}>
      {rows.map((row) => (
        <Flex key={row.id} gap={8}>
          <Input
            placeholder="Property (e.g. s3.endpoint)"
            value={row.key}
            onChange={(e) =>
              emit(rows.map((r) => (r.id === row.id ? { ...r, key: e.target.value } : r)))
            }
          />
          <Input
            placeholder="Value"
            value={row.value}
            onChange={(e) =>
              emit(rows.map((r) => (r.id === row.id ? { ...r, value: e.target.value } : r)))
            }
          />
          <Button
            icon={<DeleteOutlined />}
            onClick={() => emit(rows.filter((r) => r.id !== row.id))}
            aria-label="Remove property"
          />
        </Flex>
      ))}
      <Button
        type="dashed"
        icon={<PlusOutlined />}
        onClick={() => {
          const row = { id: nextId.current++, key: '', value: '' };
          emit([...rows, row]);
        }}
      >
        Add property
      </Button>
    </Flex>
  );
}

/** The write-only SECOND credential (e.g. an Iceberg SQL/hive catalog's DB
 * password, #1181) — shown in both create and edit, since unlike the primary
 * credential there is no dedicated reauth flow for it; PATCH is its only
 * rotation path. Always optional: not every sql/hive catalog needs one
 * (e.g. a local sqlite catalog). */
export function CatalogSecretField({ label, extra }: { label: string; extra?: string }) {
  return (
    <Form.Item name="catalogSecret" label={label} extra={extra}>
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
  // Watches the WHOLE config (not just one field, unlike `authType` above) —
  // `secondSecret.showWhen` is a predicate over arbitrary config, and Iceberg's
  // is keyed on `catalog_type`, but the spec is deliberately not narrowed to
  // that one field so a future type's `showWhen` isn't boxed into the same shape.
  const config = Form.useWatch('config', form) as Record<string, unknown> | undefined;

  return (
    <>
      {spec.textFields.map((f) => (
        <ConfigTextField
          key={f.name}
          field={f}
          forceRequired={activeAuth?.requiredFields?.includes(f.name)}
        />
      ))}

      {spec.propertiesField && (
        <Form.Item
          name={['config', 'properties']}
          label={spec.propertiesField.label}
          extra={spec.propertiesField.extra}
        >
          <PropertiesEditor />
        </Form.Item>
      )}

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
          spec.secretLabel && (
            <SecretField label={spec.secretLabel} optional={spec.optionalSecret} />
          )
        ))}

      {spec.secondSecret?.showWhen(config) && (
        <CatalogSecretField label={spec.secondSecret.label} extra={spec.secondSecret.extra} />
      )}
    </>
  );
}
