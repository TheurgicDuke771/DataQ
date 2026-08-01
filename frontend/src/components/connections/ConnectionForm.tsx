import { App, Badge, Button, Flex, Form, Input, Select, Tag, Typography } from 'antd';
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
  testConnection,
  testDraftConnection,
  updateConnection,
} from '../../api/connections';
import { ConnectionTypeFields } from './ConnectionTypeFields';
import { activeAuthOption, composeSecret, initialConfigForType } from './connectionFormSpec';
import { useAsyncAction } from '../../hooks/useAsyncAction';
import { errorMessage } from '../../utils/errors';

interface FormValues {
  name: string;
  env: ConnectionCreate['env'];
  config?: Record<string, unknown>;
  secret?: string;
  secretPassphrase?: string;
}

/** Inline result of the "Test connection" button — a card-free version of the
 *  Connections list page's health badge (Connections.tsx `HealthState`). */
type TestState = 'idle' | 'testing' | 'ok' | 'failed';

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
  const isEdit = connection !== undefined;
  const { run, loading: submitting } = useAsyncAction(`${isEdit ? 'Update' : 'Create'} failed`);
  const [testState, setTestState] = useState<TestState>('idle');
  const [testError, setTestError] = useState<string>();

  // Seed the form: an edit prefills name + config; a create seeds the new type's
  // config defaults (e.g. the auth-type) and clears any fields left over from a
  // previously-picked type. Re-runs on `type`/`connection` so re-picking a type
  // on the new page can't leak the prior type's fields. `testState` needs no
  // reset here: `ConnectionForm` only ever sees a *different* type or
  // connection across a remount (the picker↔form ternary on the new page, the
  // `key={connectionId}` remount on the edit page), so its `useState('idle')`
  // initial value already starts fresh every time this effect's inputs change.
  useEffect(() => {
    form.resetFields();
    if (connection) {
      form.setFieldsValue({ name: connection.name, config: connection.config });
    } else {
      form.setFieldsValue({ config: initialConfigForType(type) });
    }
  }, [type, connection, form]);

  // Only the selected auth mode's passphrase rides along — a value typed under
  // a previously-picked mode is preserved in the form store after its field
  // unmounts and must not wrap the secret. Shared by Create/Save and Test so
  // the two can never compose the secret differently.
  const buildSecret = (values: FormValues): string | undefined =>
    values.secret
      ? composeSecret(
          values.secret,
          activeAuthOption(type, values.config)?.passphraseLabel
            ? values.secretPassphrase
            : undefined,
        )
      : undefined;

  // A field changed after a test ran — the green/red badge no longer
  // describes what would actually be saved (repo precedent: Connections.tsx
  // `clearHealth`, "the prior pass/fail no longer holds"). Edit mode is
  // included on purpose: `onTest` there re-tests the SAVED connection, but an
  // unsaved config edit still invalidates that verdict's relevance to what a
  // Save would persist next — leaving a stale "Connected" badge up while the
  // form no longer matches it would be exactly the lie #351 review flagged.
  const onValuesChange = () => {
    if (testState !== 'idle') {
      setTestState('idle');
      setTestError(undefined);
    }
  };

  const onFinish = (values: FormValues) =>
    run(async () => {
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
            secret: buildSecret(values),
          });
      message.success(`Connection “${values.name}” ${isEdit ? 'updated' : 'created'}`);
      onSaved(saved);
    });

  // Create mode: probe the config/secret just typed — nothing is persisted
  // (#351). Edit mode: any config edits here are still unsaved, so testing
  // them would report on a connection that doesn't exist yet; instead this
  // re-tests the SAVED connection (identical call to the Connections list
  // page's Test button) and the label says so, rather than silently testing
  // the wrong thing.
  const onTest = async () => {
    if (isEdit) {
      setTestState('testing');
      setTestError(undefined);
      try {
        const { ok } = await testConnection(connection.id);
        setTestState(ok ? 'ok' : 'failed');
      } catch (err) {
        setTestState('failed');
        setTestError(errorMessage(err));
      }
      return;
    }
    let values: FormValues;
    try {
      values = await form.validateFields();
    } catch {
      return; // invalid fields already surfaced inline by antd
    }
    setTestState('testing');
    setTestError(undefined);
    try {
      const { ok } = await testDraftConnection({
        type,
        env: values.env,
        config: values.config ?? {},
        secret: buildSecret(values),
      });
      setTestState(ok ? 'ok' : 'failed');
    } catch (err) {
      setTestState('failed');
      setTestError(errorMessage(err));
    }
  };

  return (
    <Form
      form={form}
      layout="vertical"
      onFinish={onFinish}
      onValuesChange={onValuesChange}
      requiredMark="optional"
    >
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
      <Flex align="center" gap={8} style={{ marginBottom: 24 }}>
        <Button loading={testState === 'testing'} onClick={onTest}>
          {isEdit ? 'Test saved connection' : 'Test connection'}
        </Button>
        {testState === 'ok' && <Badge status="success" text="Connected" />}
        {testState === 'failed' && <Badge status="error" text={testError ?? 'Connection failed'} />}
      </Flex>
      <Flex justify="end" gap={8}>
        <Button onClick={onCancel}>{isEdit ? 'Cancel' : 'Back'}</Button>
        <Button type="primary" htmlType="submit" loading={submitting}>
          {isEdit ? 'Save' : 'Create'}
        </Button>
      </Flex>
    </Form>
  );
}
