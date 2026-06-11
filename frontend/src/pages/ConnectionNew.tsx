import { App, Button, Card, Flex, Form, Input, Select, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import {
  CONNECTION_ENVS,
  CONNECTION_KIND_LABELS,
  CONNECTION_KINDS,
  CONNECTION_TYPE_LABELS,
  type ConnectionCreate,
  type ConnectionKind,
  type ConnectionType,
  createConnection,
  envLabel,
  typesOfKind,
} from '../api/connections';
import { ConnectionTypeFields } from '../components/connections/ConnectionTypeFields';
import { initialConfigForType } from '../components/connections/connectionFormSpec';

interface FormValues {
  name: string;
  env: ConnectionCreate['env'];
  config?: Record<string, unknown>;
  secret?: string;
}

/**
 * Dedicated full-page add-connection flow (GX-Cloud style): pick a type from the
 * datasource / orchestration sections, then fill the type-specific form. Editing
 * an existing connection still uses the lighter drawer on the Connections page.
 */
export function ConnectionNew() {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [type, setType] = useState<ConnectionType>();
  const [form] = Form.useForm<FormValues>();
  const [submitting, setSubmitting] = useState(false);

  // Seed the new type's config defaults (e.g. the auth-type) and clear any
  // fields left over from a previously-picked type. Runs in an effect — *after*
  // the <Form> mounts (it only renders once `type` is truthy) — so the form
  // store is connected when we write to it, and so re-picking a type can't leak
  // the prior type's name/env (the single useForm store persists across the
  // picker round-trip; resetFields wipes it clean).
  useEffect(() => {
    if (!type) return;
    form.resetFields();
    form.setFieldsValue({ config: initialConfigForType(type) });
  }, [type, form]);

  const onFinish = async (values: FormValues) => {
    if (!type) return;
    setSubmitting(true);
    try {
      await createConnection({
        name: values.name,
        type,
        env: values.env,
        config: values.config ?? {},
        secret: values.secret || undefined,
      });
      message.success(`Connection “${values.name}” created`);
      navigate('/connections');
    } catch (err) {
      message.error(`Create failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Flex vertical gap={24} style={{ maxWidth: 640 }}>
      <Flex justify="space-between" align="center" gap={12}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          {type ? `New ${CONNECTION_TYPE_LABELS[type]} connection` : 'New connection'}
        </Typography.Title>
        <Button onClick={() => (type ? setType(undefined) : navigate('/connections'))}>
          {type ? 'Back' : 'Cancel'}
        </Button>
      </Flex>

      {type ? (
        <Form form={form} layout="vertical" onFinish={onFinish} requiredMark="optional">
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="env" label="Environment" rules={[{ required: true }]}>
            <Select
              options={CONNECTION_ENVS.map((e) => ({ value: e, label: envLabel(e) }))}
              placeholder="Select an environment"
            />
          </Form.Item>
          <ConnectionTypeFields type={type} form={form} />
          <Flex justify="end" gap={8}>
            <Button onClick={() => setType(undefined)}>Back</Button>
            <Button type="primary" htmlType="submit" loading={submitting}>
              Create
            </Button>
          </Flex>
        </Form>
      ) : (
        <Flex vertical gap={24}>
          {CONNECTION_KINDS.map((kind) => (
            <TypeSection key={kind} kind={kind} types={typesOfKind(kind)} onPick={setType} />
          ))}
        </Flex>
      )}
    </Flex>
  );
}

function TypeSection({
  kind,
  types,
  onPick,
}: {
  kind: ConnectionKind;
  types: ConnectionType[];
  onPick: (type: ConnectionType) => void;
}) {
  return (
    <Flex vertical gap={12}>
      <Typography.Title level={5} style={{ margin: 0 }}>
        {CONNECTION_KIND_LABELS[kind]}
      </Typography.Title>
      <Flex wrap gap={12}>
        {types.map((type) => (
          <Card
            key={type}
            hoverable
            size="small"
            style={{ minWidth: 200 }}
            onClick={() => onPick(type)}
          >
            <Typography.Text strong>{CONNECTION_TYPE_LABELS[type]}</Typography.Text>
          </Card>
        ))}
      </Flex>
    </Flex>
  );
}
