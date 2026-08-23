import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { Button, Flex, Form, Input, Select, Switch, type FormInstance } from 'antd';
import { useEffect, useRef, useState } from 'react';

import type { ConnectionType } from '../../api/connections';
import { activeAuthOption, CONNECTION_FORM_SPECS, type TextField } from './connectionFormSpec';

/** Renders the type-specific config + secret form fields from CONNECTION_FORM_SPECS. */

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
    // A boolean config flag (e.g.
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

/** Optional second secret part (e.g. key-pair passphrase) — rides `composeSecret`. */
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
 * Add/remove key-value editor for a free-form, NON-SECRET `properties` dict (Iceberg's
 * catalog/storage options, #1181 — e.g.
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

/**
 * The write-only SECOND credential (e.g. an Iceberg SQL/hive catalog's DB password, #1181) — shown
 * in both create and edit.
 */
export function CatalogSecretField({ label, extra }: { label: string; extra?: string }) {
  return (
    <Form.Item name="catalogSecret" label={label} extra={extra} preserve={false}>
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
  // `secondSecret.showWhen` is a predicate over arbitrary config, and Iceberg's is keyed on
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
