import { DeleteOutlined, KeyOutlined, PlusOutlined } from '@ant-design/icons';
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Flex,
  Form,
  Input,
  InputNumber,
  Modal,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';

import {
  type ApiKey,
  type ApiKeyCreated,
  createApiKey,
  listApiKeys,
  PAT_DEFAULT_EXPIRY_DAYS,
  PAT_MAX_EXPIRY_DAYS,
  revokeApiKey,
} from '../../api/apiKeys';
import { useAsyncData } from '../../hooks/useAsyncData';
import { formatTimestamp } from '../results/resultsFormat';
import { errorMessage } from '../../utils/errors';

/**
 * Profile panel for the user's Personal Access Tokens (PATs, ADR 0026 phase 1,
 * #461). A PAT authenticates as you (`Authorization: Bearer dq_live_…`) on the REST
 * API and `/mcp`, inheriting your per-suite access. User-scoped: only your own keys.
 *
 * The plaintext token is shown **exactly once** — on creation, in a copy-once modal;
 * the list only ever shows the `dq_live_…` prefix. Revocation is immediate.
 */
export function ApiKeysPanel() {
  const { state, reload } = useAsyncData(listApiKeys);

  return (
    <Card
      title={
        <Flex gap={8} align="center">
          <KeyOutlined /> Personal access tokens
        </Flex>
      }
      size="small"
    >
      <Flex vertical gap={12}>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          Tokens authenticate as you on the DataQ API and MCP (
          <Typography.Text code>Authorization: Bearer dq_live_…</Typography.Text>), inheriting your
          suite access. The full token is shown once, at creation.
        </Typography.Text>
        <ApiKeysBody state={state} onChanged={reload} />
      </Flex>
    </Card>
  );
}

function ApiKeysBody({
  state,
  onChanged,
}: {
  state: ReturnType<typeof useAsyncData<ApiKey[]>>['state'];
  onChanged: () => void;
}) {
  const [creating, setCreating] = useState(false);

  if (state.status === 'loading') {
    return <Spin description="Loading tokens…" />;
  }
  if (state.status === 'error') {
    return <Alert type="error" showIcon title="Failed to load tokens" description={state.error} />;
  }
  const keys = state.data;

  return (
    <Flex vertical gap={12}>
      <Flex justify="flex-end">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>
          New token
        </Button>
      </Flex>
      {keys.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No tokens yet." />
      ) : (
        <ApiKeyTable keys={keys} onChanged={onChanged} />
      )}
      <CreateTokenModal open={creating} onClose={() => setCreating(false)} onCreated={onChanged} />
    </Flex>
  );
}

/** Active / Expired / Revoked, derived from the metadata (no separate status field). */
function keyStatus(key: ApiKey): { label: string; color: string } {
  if (key.revoked_at) return { label: 'Revoked', color: 'default' };
  if (new Date(key.expires_at).getTime() < Date.now()) return { label: 'Expired', color: 'error' };
  return { label: 'Active', color: 'success' };
}

function ApiKeyTable({ keys, onChanged }: { keys: ApiKey[]; onChanged: () => void }) {
  const { message, modal } = App.useApp();
  const [busyId, setBusyId] = useState<string | null>(null);

  const onRevoke = (key: ApiKey) => {
    modal.confirm({
      title: `Revoke token “${key.name}”?`,
      content: 'It stops authenticating immediately. Anything using it will start getting 401s.',
      okText: 'Revoke',
      okType: 'danger',
      onOk: async () => {
        setBusyId(key.id);
        try {
          await revokeApiKey(key.id);
          message.success(`“${key.name}” revoked`);
          onChanged();
        } catch (err) {
          // Surface the failure (never silent) and let the confirm close; the key
          // stays listed (no refetch on failure) so the user can retry. We don't
          // re-throw to keep the modal open — antd 6 leaves an onOk rejection
          // unhandled, and a toast + intact list is clearer anyway.
          message.error(`Revoke failed: ${errorMessage(err)}`);
        } finally {
          setBusyId(null);
        }
      },
    });
  };

  const columns: ColumnsType<ApiKey> = [
    { title: 'Name', dataIndex: 'name' },
    {
      title: 'Token',
      dataIndex: 'key_prefix',
      render: (prefix: string) => <Typography.Text code>{prefix}…</Typography.Text>,
    },
    { title: 'Created', dataIndex: 'created_at', render: (t: string) => formatTimestamp(t) },
    { title: 'Expires', dataIndex: 'expires_at', render: (t: string) => formatTimestamp(t) },
    {
      title: 'Last used',
      dataIndex: 'last_used_at',
      render: (t: string | null) => formatTimestamp(t),
    },
    {
      title: 'Status',
      key: 'status',
      width: 96,
      render: (_: unknown, key) => {
        const s = keyStatus(key);
        return <Tag color={s.color}>{s.label}</Tag>;
      },
    },
    {
      title: '',
      key: 'actions',
      width: 48,
      render: (_: unknown, key) =>
        key.revoked_at ? null : (
          <Button
            size="small"
            type="text"
            danger
            icon={<DeleteOutlined />}
            loading={busyId === key.id}
            onClick={() => onRevoke(key)}
            aria-label={`Revoke ${key.name}`}
          />
        ),
    },
  ];

  return (
    <Table<ApiKey>
      scroll={{ x: 'max-content' }}
      rowKey="id"
      size="small"
      columns={columns}
      dataSource={keys}
      pagination={false}
    />
  );
}

function CreateTokenModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const { message } = App.useApp();
  const [name, setName] = useState('');
  const [expiry, setExpiry] = useState<number | null>(PAT_DEFAULT_EXPIRY_DAYS);
  const [saving, setSaving] = useState(false);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);

  const reset = () => {
    setName('');
    setExpiry(PAT_DEFAULT_EXPIRY_DAYS);
    setCreated(null);
  };

  const onSubmit = async () => {
    const label = name.trim();
    if (!label || !expiry) return;
    setSaving(true);
    try {
      const key = await createApiKey({ name: label, expires_in_days: expiry });
      setCreated(key); // switch to the show-once view; list refresh happens on close
      onCreated();
    } catch (err) {
      message.error(`Create failed: ${errorMessage(err)}`);
    } finally {
      setSaving(false);
    }
  };

  const close = () => {
    reset();
    onClose();
  };

  // Two states in one modal: the create form, then the show-once token reveal.
  return (
    <Modal
      open={open}
      title={created ? 'Copy your token now' : 'New personal access token'}
      onCancel={close}
      // Unmount body on close so the revealed plaintext token leaves the DOM
      // entirely (not just hidden) — reinforces show-once.
      destroyOnHidden
      mask={{ closable: !created }} // once created, force an explicit acknowledge
      footer={
        created ? (
          <Button type="primary" onClick={close}>
            Done
          </Button>
        ) : (
          [
            <Button key="cancel" onClick={close}>
              Cancel
            </Button>,
            <Button
              key="create"
              type="primary"
              loading={saving}
              disabled={!name.trim() || !expiry}
              onClick={onSubmit}
            >
              Create
            </Button>,
          ]
        )
      }
    >
      {created ? (
        <Flex vertical gap={12}>
          <Alert
            type="warning"
            showIcon
            title="This token is shown only once"
            description="Copy it now and store it securely — you won't be able to see it again."
          />
          <Typography.Paragraph
            code
            copyable={{ text: created.token }}
            style={{ margin: 0, wordBreak: 'break-all' }}
          >
            {created.token}
          </Typography.Paragraph>
        </Flex>
      ) : (
        <Form layout="vertical">
          <Form.Item label="Name" required tooltip="A label to recognise this token later.">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. ci-smoke, laptop-cli"
              maxLength={128}
              onPressEnter={onSubmit}
              aria-label="Token name"
            />
          </Form.Item>
          <Form.Item
            label="Expires in (days)"
            required
            tooltip={`Tokens must expire. 1–${PAT_MAX_EXPIRY_DAYS} days.`}
          >
            <InputNumber
              value={expiry}
              onChange={setExpiry}
              min={1}
              max={PAT_MAX_EXPIRY_DAYS}
              precision={0}
              style={{ width: '100%' }}
              aria-label="Expiry in days"
            />
          </Form.Item>
        </Form>
      )}
    </Modal>
  );
}
