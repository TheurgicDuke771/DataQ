import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons';
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Empty,
  Flex,
  Form,
  Input,
  Modal,
  Select,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';

import {
  type ChannelCreatePayload,
  type ChannelType,
  type ChannelUpdatePayload,
  CHANNEL_TYPE_LABELS,
  CHANNEL_TYPES,
  createChannel,
  deleteChannel,
  listChannels,
  type NotificationChannel,
  updateChannel,
} from '../../api/notificationChannels';
import { useAsyncData } from '../../hooks/useAsyncData';
import { useConfirmDelete } from '../../hooks/useConfirmDelete';
import { AsyncBody } from '../AsyncBody';
import { errorMessage } from '../../utils/errors';

/**
 * Admin → Settings: reusable notification channels (#1514/#1662/#1663/#1761) — a destination
 * defined once and linked from many suites (the per-suite picker lives on
 * `NotificationsPanel`). Channel CRUD is Admin-only server-side; this card is only ever mounted
 * behind the Settings page's own `is_workspace_admin` gate.
 */
export function NotificationChannelsPanel() {
  const { state, reload } = useAsyncData(listChannels);
  const [modal, setModal] = useState<{ channel?: NotificationChannel } | null>(null);

  return (
    <Card
      size="small"
      title={
        <Flex vertical gap={2}>
          <Typography.Text strong>Notification channels</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
            Reusable Teams/Slack/email/webhook destinations, linked to any number of suites.
          </Typography.Text>
        </Flex>
      }
    >
      <AsyncBody
        state={state}
        loadingText="Loading channels…"
        errorTitle="Failed to load notification channels"
      >
        {(channels) => (
          <Flex vertical gap={12}>
            <Flex justify="flex-end">
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setModal({})}>
                New channel
              </Button>
            </Flex>
            {channels.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No channels yet." />
            ) : (
              <ChannelTable
                channels={channels}
                onEdit={(channel) => setModal({ channel })}
                onChanged={reload}
              />
            )}
            {modal !== null && (
              <ChannelFormModal
                // Mount fresh per open so the form's initial state (seeded from
                // `channel` in each `useState`) actually reflects which channel —
                // or none, for create — was clicked; an always-mounted modal
                // would seed once from the first render and never re-seed.
                key={modal.channel?.id ?? 'new'}
                open
                channel={modal.channel}
                onClose={() => setModal(null)}
                onSaved={reload}
              />
            )}
          </Flex>
        )}
      </AsyncBody>
    </Card>
  );
}

function destinationSummary(c: NotificationChannel): string {
  switch (c.type) {
    case 'teams':
    case 'slack':
      return c.has_webhook ? 'webhook set' : 'no webhook';
    case 'email':
      return c.email_recipients || 'no recipients';
    case 'webhook':
      return c.webhook_url || 'no URL';
    default:
      return '';
  }
}

function ChannelTable({
  channels,
  onEdit,
  onChanged,
}: {
  channels: NotificationChannel[];
  onEdit: (channel: NotificationChannel) => void;
  onChanged: () => void;
}) {
  const confirmDelete = useConfirmDelete();

  const onDelete = (channel: NotificationChannel) =>
    confirmDelete({
      label: channel.name,
      content: 'Suites linked to this channel must be unlinked first.',
      onDelete: () => deleteChannel(channel.id),
      onDone: onChanged,
      // The backend's ChannelInUseError message already names the suite count
      // and reason ("N suite(s) still reference this channel — unlink them
      // first") — the interceptor puts it straight on err.message, so the
      // default `${errorPrefix}: ${message}` toast is already the clean,
      // specific message, not a generic failure.
      errorPrefix: 'Delete failed',
    });

  const columns: ColumnsType<NotificationChannel> = [
    { title: 'Name', dataIndex: 'name' },
    {
      title: 'Type',
      dataIndex: 'type',
      render: (t: ChannelType) => <Tag>{CHANNEL_TYPE_LABELS[t]}</Tag>,
    },
    { title: 'Destination', key: 'destination', render: (_, c) => destinationSummary(c) },
    {
      title: 'HMAC signing',
      key: 'hmac',
      render: (_, c) =>
        c.type === 'webhook' ? (
          <Tag color={c.has_hmac_secret ? 'success' : 'default'}>
            {c.has_hmac_secret ? 'set' : 'not set'}
          </Tag>
        ) : (
          <Typography.Text type="secondary">—</Typography.Text>
        ),
    },
    {
      title: 'Auth header',
      key: 'authHeader',
      render: (_, c) =>
        c.type === 'webhook' && c.auth_header_name ? (
          <Tag color={c.has_auth_header ? 'success' : 'default'}>{c.auth_header_name}</Tag>
        ) : (
          <Typography.Text type="secondary">—</Typography.Text>
        ),
    },
    {
      title: '',
      key: 'actions',
      width: 80,
      render: (_, c) => (
        <Flex gap={4}>
          <Button
            size="small"
            type="text"
            icon={<EditOutlined />}
            onClick={() => onEdit(c)}
            aria-label={`Edit ${c.name}`}
          />
          <Button
            size="small"
            type="text"
            danger
            icon={<DeleteOutlined />}
            onClick={() => onDelete(c)}
            aria-label={`Delete ${c.name}`}
          />
        </Flex>
      ),
    },
  ];

  return (
    <Table<NotificationChannel>
      scroll={{ x: 'max-content' }}
      rowKey="id"
      size="small"
      columns={columns}
      dataSource={channels}
      pagination={false}
    />
  );
}

/** Parses the payload-template textarea; `undefined` for blank, throws for bad JSON. */
function parseTemplate(text: string): Record<string, unknown> | undefined {
  const trimmed = text.trim();
  if (!trimmed) return undefined;
  const parsed: unknown = JSON.parse(trimmed);
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new Error('must be a JSON object');
  }
  return parsed as Record<string, unknown>;
}

function ChannelFormModal({
  open,
  channel,
  onClose,
  onSaved,
}: {
  open: boolean;
  /** `undefined` = create. */
  channel?: NotificationChannel;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { message } = App.useApp();
  const isEdit = channel !== undefined;

  const [name, setName] = useState(channel?.name ?? '');
  const [type, setType] = useState<ChannelType>(channel?.type ?? 'teams');
  const [webhook, setWebhook] = useState('');
  const [clearWebhook, setClearWebhook] = useState(false);
  const [emailRecipients, setEmailRecipients] = useState(channel?.email_recipients ?? '');
  const [webhookUrl, setWebhookUrl] = useState(channel?.webhook_url ?? '');
  const [payloadTemplate, setPayloadTemplate] = useState(
    channel?.payload_template ? JSON.stringify(channel.payload_template, null, 2) : '',
  );
  const [clearPayloadTemplate, setClearPayloadTemplate] = useState(false);
  const [authHeaderName, setAuthHeaderName] = useState(channel?.auth_header_name ?? '');
  const [authHeaderValue, setAuthHeaderValue] = useState('');
  const [clearAuthHeaderValue, setClearAuthHeaderValue] = useState(false);
  const [regenerateHmac, setRegenerateHmac] = useState(false);
  const [templateError, setTemplateError] = useState<string>();
  const [saving, setSaving] = useState(false);
  // The freshly minted plaintext HMAC key, shown exactly once — never persisted
  // beyond this modal's local state, cleared on close.
  const [revealedSecret, setRevealedSecret] = useState<string | null>(null);

  // No reset-on-close: the modal unmounts (a fresh `key` per open in the parent) the instant
  // `onClose` sets `modal` to null, so any state reset here would never be observed (#1878 review
  // — this used to be dead code).

  const onSubmit = async () => {
    let template: Record<string, unknown> | undefined;
    try {
      template = parseTemplate(payloadTemplate);
      setTemplateError(undefined);
    } catch {
      setTemplateError('Payload template must be valid JSON (an object)');
      return;
    }
    setSaving(true);
    try {
      let saved: NotificationChannel;
      if (isEdit) {
        const payload: ChannelUpdatePayload = { name: name.trim() || undefined };
        if (clearWebhook) payload.webhook = '';
        else if (webhook.trim()) payload.webhook = webhook.trim();
        if (emailRecipients !== (channel.email_recipients ?? '')) {
          payload.email_recipients = emailRecipients.trim();
        }
        if (webhookUrl !== (channel.webhook_url ?? '')) payload.webhook_url = webhookUrl.trim();
        if (clearPayloadTemplate) payload.clear_payload_template = true;
        else if (template !== undefined) payload.payload_template = template;
        if (authHeaderName !== (channel.auth_header_name ?? '')) {
          payload.auth_header_name = authHeaderName.trim();
        }
        if (clearAuthHeaderValue) payload.auth_header_value = '';
        else if (authHeaderValue.trim()) payload.auth_header_value = authHeaderValue.trim();
        if (regenerateHmac) payload.regenerate_hmac_secret = true;
        saved = await updateChannel(channel.id, payload);
      } else {
        const payload: ChannelCreatePayload = { name: name.trim(), type };
        if (webhook.trim()) payload.webhook = webhook.trim();
        if (emailRecipients.trim()) payload.email_recipients = emailRecipients.trim();
        if (webhookUrl.trim()) payload.webhook_url = webhookUrl.trim();
        if (template !== undefined) payload.payload_template = template;
        if (authHeaderName.trim()) payload.auth_header_name = authHeaderName.trim();
        if (authHeaderValue.trim()) payload.auth_header_value = authHeaderValue.trim();
        saved = await createChannel(payload);
      }
      message.success(isEdit ? 'Channel saved' : 'Channel created');
      onSaved();
      if (saved.hmac_secret) {
        // Two states in one modal: form → the one-time secret reveal (mirrors
        // ApiKeysPanel's CreateTokenModal show-once pattern).
        setRevealedSecret(saved.hmac_secret);
      } else {
        onClose();
      }
    } catch (err) {
      message.error(`${isEdit ? 'Save' : 'Create'} failed: ${errorMessage(err)}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={
        revealedSecret
          ? 'Copy the HMAC signing key now'
          : isEdit
            ? `Edit “${channel?.name}”`
            : 'New channel'
      }
      onCancel={onClose}
      // Unmount on close so the revealed plaintext key leaves the DOM entirely,
      // not just hidden — reinforces show-once (same as ApiKeysPanel).
      destroyOnHidden
      mask={{ closable: !revealedSecret }}
      footer={
        revealedSecret ? (
          <Button type="primary" onClick={onClose}>
            Done
          </Button>
        ) : (
          [
            <Button key="cancel" onClick={onClose}>
              Cancel
            </Button>,
            <Button
              key="save"
              type="primary"
              loading={saving}
              disabled={!name.trim()}
              onClick={onSubmit}
            >
              {isEdit ? 'Save' : 'Create'}
            </Button>,
          ]
        )
      }
    >
      {revealedSecret ? (
        <Flex vertical gap={12}>
          <Alert
            type="warning"
            showIcon
            title="This HMAC signing key is shown only once"
            description="Copy it now and store it securely — DataQ signs outbound webhook payloads with it and cannot show it again."
          />
          <Typography.Paragraph
            code
            copyable={{ text: revealedSecret }}
            style={{ margin: 0, wordBreak: 'break-all' }}
          >
            {revealedSecret}
          </Typography.Paragraph>
        </Flex>
      ) : (
        <Form layout="vertical">
          <Form.Item label="Name" required>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={128}
              aria-label="Channel name"
            />
          </Form.Item>
          <Form.Item label="Type">
            {isEdit ? (
              <Tag>{CHANNEL_TYPE_LABELS[type]}</Tag>
            ) : (
              <Select<ChannelType>
                value={type}
                onChange={(next) => {
                  // A create-only field belongs to exactly one type — switching types with a
                  // filled-in field from the PREVIOUS type left in place sends a payload the
                  // backend's `_validate_destination` rejects (422, #1878 review): both `webhook`
                  // and `webhook_url` set at once looks like nothing the user actually did.
                  setType(next);
                  setWebhook('');
                  setWebhookUrl('');
                  setEmailRecipients('');
                  setAuthHeaderName('');
                  setAuthHeaderValue('');
                  setPayloadTemplate('');
                }}
                aria-label="Channel type"
                options={CHANNEL_TYPES.map((t) => ({ value: t, label: CHANNEL_TYPE_LABELS[t] }))}
              />
            )}
          </Form.Item>

          {(type === 'teams' || type === 'slack') && (
            <Form.Item label={`${CHANNEL_TYPE_LABELS[type]} webhook URL`}>
              <Flex vertical gap={4}>
                <Input.Password
                  value={webhook}
                  disabled={clearWebhook}
                  onChange={(e) => setWebhook(e.target.value)}
                  placeholder={
                    isEdit && channel.has_webhook
                      ? 'Stored — leave blank to keep, or type a new URL to replace it'
                      : 'https://…'
                  }
                  aria-label="Webhook URL"
                />
                {isEdit && channel.has_webhook && (
                  <Checkbox
                    checked={clearWebhook}
                    onChange={(e) => setClearWebhook(e.target.checked)}
                  >
                    Clear the stored webhook
                  </Checkbox>
                )}
              </Flex>
            </Form.Item>
          )}

          {type === 'email' && (
            <Form.Item label="Recipients">
              <Input
                value={emailRecipients}
                onChange={(e) => setEmailRecipients(e.target.value)}
                placeholder="a@example.com, b@example.com"
                aria-label="Email recipients"
              />
            </Form.Item>
          )}

          {type === 'webhook' && (
            <>
              <Form.Item label="Webhook URL">
                <Input
                  value={webhookUrl}
                  onChange={(e) => setWebhookUrl(e.target.value)}
                  placeholder="https://…"
                  aria-label="Webhook destination URL"
                />
              </Form.Item>
              {isEdit && (
                <Form.Item>
                  <Checkbox
                    checked={regenerateHmac}
                    onChange={(e) => setRegenerateHmac(e.target.checked)}
                  >
                    Regenerate HMAC signing key (invalidates the current one)
                  </Checkbox>
                </Form.Item>
              )}
              <Form.Item
                label="Payload template (optional)"
                validateStatus={templateError ? 'error' : undefined}
                help={templateError}
              >
                <Flex vertical gap={4}>
                  <Input.TextArea
                    value={payloadTemplate}
                    disabled={clearPayloadTemplate}
                    onChange={(e) => setPayloadTemplate(e.target.value)}
                    rows={4}
                    placeholder='{"summary": "{{suite.name}} {{run.status}}"}'
                    aria-label="Payload template"
                  />
                  {isEdit && channel.has_payload_template && (
                    <Checkbox
                      checked={clearPayloadTemplate}
                      onChange={(e) => setClearPayloadTemplate(e.target.checked)}
                    >
                      Remove the template
                    </Checkbox>
                  )}
                </Flex>
              </Form.Item>
              <Form.Item
                label="Extra auth header name (optional)"
                extra={
                  isEdit && channel.has_auth_header
                    ? 'Blanking this also deletes the stored header value below — the two are one credential slot, not independent fields.'
                    : undefined
                }
              >
                <Input
                  value={authHeaderName}
                  onChange={(e) => setAuthHeaderName(e.target.value)}
                  placeholder="e.g. X-Api-Key"
                  aria-label="Auth header name"
                />
              </Form.Item>
              <Form.Item
                label="Extra auth header value"
                extra={
                  // The backend's credential-redirect guard (#1401 class) refuses a webhook_url
                  // change on a channel with a stored auth header unless the value is re-supplied
                  // in the SAME edit — "leave blank to keep" is only true when the URL is
                  // unchanged (#1878 review).
                  isEdit &&
                  channel.has_auth_header &&
                  webhookUrl !== (channel.webhook_url ?? '') &&
                  !authHeaderValue.trim() &&
                  !clearAuthHeaderValue ? (
                    <Typography.Text type="warning" style={{ fontSize: 12 }}>
                      The webhook URL changed — re-enter the stored header value here too, or the
                      save will be refused (a credential can't silently follow a destination
                      change).
                    </Typography.Text>
                  ) : undefined
                }
              >
                <Flex vertical gap={4}>
                  <Input.Password
                    value={authHeaderValue}
                    disabled={clearAuthHeaderValue}
                    onChange={(e) => setAuthHeaderValue(e.target.value)}
                    placeholder={
                      isEdit && channel.has_auth_header
                        ? 'Stored — leave blank to keep, or type a new value to replace it'
                        : 'Write-only'
                    }
                    aria-label="Auth header value"
                  />
                  {isEdit && channel.has_auth_header && (
                    <Checkbox
                      checked={clearAuthHeaderValue}
                      onChange={(e) => setClearAuthHeaderValue(e.target.checked)}
                    >
                      Clear the stored header value
                    </Checkbox>
                  )}
                </Flex>
              </Form.Item>
            </>
          )}
        </Form>
      )}
    </Modal>
  );
}
