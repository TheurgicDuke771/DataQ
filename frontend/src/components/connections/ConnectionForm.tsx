import { App, Button, Flex, Form, Input, Select, Tag, Typography } from 'antd';
import { useEffect, useState } from 'react';

import {
  CONNECTION_ENVS,
  CONNECTION_TYPE_LABELS,
  type Connection,
  type ConnectionCreate,
  type ConnectionType,
  createConnection,
  ENV_COLORS,
  envLabel,
  updateConnection,
} from '../../api/connections';
import { ConnectionTypeFields } from './ConnectionTypeFields';
import { activeAuthOption, composeSecret, initialConfigForType } from './connectionFormSpec';

interface FormValues {
  name: string;
  env: ConnectionCreate['env'];
  config?: Record<string, unknown>;
  secret?: string;
  secretPassphrase?: string;
}

/**
 * Create or edit a connection — the form body shared by the `/connections/new`
 * page (a type is picked first, then this renders) and the `/connections/:id/edit`
 * page (the drawer is retired in W6, ADR 0022). `connection === undefined` is
 * create mode (env is chosen + the credential is captured); editing locks type +
 * env (the backend `ConnectionUpdate` rejects changing them) and omits the secret
 * — credential rotation is the separate Re-auth flow.
 */
export function ConnectionForm({
  type,
  connection,
  onSaved,
  onCancel,
}: {
  /** The connection type — picked on the new page, fixed from the row on edit. */
  type: ConnectionType;
  connection?: Connection;
  /** Called with the saved connection (created or updated). */
  onSaved: (connection: Connection) => void;
  onCancel: () => void;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm<FormValues>();
  const [submitting, setSubmitting] = useState(false);
  const isEdit = connection !== undefined;

  // Seed the form: an edit prefills name + config; a create seeds the new type's
  // config defaults (e.g. the auth-type) and clears any fields left over from a
  // previously-picked type. Re-runs on `type`/`connection` so re-picking a type
  // on the new page can't leak the prior type's fields.
  useEffect(() => {
    form.resetFields();
    if (connection) {
      form.setFieldsValue({ name: connection.name, config: connection.config });
    } else {
      form.setFieldsValue({ config: initialConfigForType(type) });
    }
  }, [type, connection, form]);

  const onFinish = async (values: FormValues) => {
    setSubmitting(true);
    try {
      const saved = isEdit
        ? await updateConnection(connection.id, {
            name: values.name,
            config: values.config ?? {},
          })
        : await createConnection({
            name: values.name,
            type,
            env: values.env,
            config: values.config ?? {},
            // Only the selected auth mode's passphrase rides along — a value
            // typed under a previously-picked mode is preserved in the form
            // store after its field unmounts and must not wrap the secret.
            secret: values.secret
              ? composeSecret(
                  values.secret,
                  activeAuthOption(type, values.config)?.passphraseLabel
                    ? values.secretPassphrase
                    : undefined,
                )
              : undefined,
          });
      message.success(`Connection “${values.name}” ${isEdit ? 'updated' : 'created'}`);
      onSaved(saved);
    } catch (err) {
      message.error(
        `${isEdit ? 'Update' : 'Create'} failed: ${err instanceof Error ? err.message : 'unknown error'}`,
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Form form={form} layout="vertical" onFinish={onFinish} requiredMark="optional">
      <Form.Item name="name" label="Name" rules={[{ required: true }]}>
        <Input />
      </Form.Item>
      {isEdit ? (
        // Type + env are fixed once a connection is created — show, don't edit.
        <>
          <Form.Item label="Type">
            <Typography.Text>{CONNECTION_TYPE_LABELS[type]}</Typography.Text>
          </Form.Item>
          <Form.Item label="Environment">
            <Tag color={ENV_COLORS[connection.env]}>{envLabel(connection.env)}</Tag>
          </Form.Item>
        </>
      ) : (
        <Form.Item name="env" label="Environment" rules={[{ required: true }]}>
          <Select
            options={CONNECTION_ENVS.map((e) => ({ value: e, label: envLabel(e) }))}
            placeholder="Select an environment"
          />
        </Form.Item>
      )}
      <ConnectionTypeFields type={type} form={form} showSecret={!isEdit} />
      <Flex justify="end" gap={8}>
        <Button onClick={onCancel}>{isEdit ? 'Cancel' : 'Back'}</Button>
        <Button type="primary" htmlType="submit" loading={submitting}>
          {isEdit ? 'Save' : 'Create'}
        </Button>
      </Flex>
    </Form>
  );
}
