import { App, Alert, Button, Card, Flex, Input, Select, Spin, Switch, Tag, Typography } from 'antd';
import { useState } from 'react';

import { type AlertOn, getNotifications, putNotifications } from '../../api/notifications';
import { useAsyncData } from '../../hooks/useAsyncData';

const ALERT_ON_OPTIONS: { value: AlertOn; label: string }[] = [
  { value: 'fail', label: 'On fail / critical' },
  { value: 'warn', label: 'On warn and worse' },
  { value: 'always', label: 'Always (every run)' },
];

/**
 * Suite-detail panel for per-suite Teams alerting (W6, fronts `notification_service`).
 * Controls whether outcomes are delivered, the threshold (`alert_on`), and the
 * Teams webhook. The webhook is **write-only** — the API never returns it, so the
 * field shows whether one is set and only writes when you type a new one (blank
 * = unchanged; "Clear webhook" removes it). `view` reads; `edit`+ (`canManage`)
 * mutates, matching the backend gate.
 */
export function NotificationsPanel({
  suiteId,
  canManage,
}: {
  suiteId: string;
  canManage: boolean;
}) {
  const { state, reload } = useAsyncData(() => getNotifications(suiteId));

  return (
    <Card
      size="small"
      title={
        <Flex vertical gap={2}>
          <Typography.Text strong>Notifications</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
            Send this suite's run outcomes to Microsoft Teams.
          </Typography.Text>
        </Flex>
      }
    >
      {state.status === 'loading' ? (
        <Spin tip="Loading notifications…" />
      ) : state.status === 'error' ? (
        <Alert
          type="error"
          showIcon
          message="Failed to load notifications"
          description={state.error}
        />
      ) : (
        <NotificationsForm
          // Remount on a config change so the form re-seeds from the loaded
          // values (render-phase reset, no setState-in-effect); an unchanged
          // reload keeps the same key, preserving any in-progress edits.
          key={`${state.data.enabled}:${state.data.alert_on}:${state.data.has_webhook}`}
          suiteId={suiteId}
          canManage={canManage}
          initialEnabled={state.data.enabled}
          initialAlertOn={state.data.alert_on}
          hasWebhook={state.data.has_webhook}
          onChanged={reload}
        />
      )}
    </Card>
  );
}

function NotificationsForm({
  suiteId,
  canManage,
  initialEnabled,
  initialAlertOn,
  hasWebhook,
  onChanged,
}: {
  suiteId: string;
  canManage: boolean;
  initialEnabled: boolean;
  initialAlertOn: AlertOn;
  hasWebhook: boolean;
  onChanged: () => void;
}) {
  const { message } = App.useApp();
  const [enabled, setEnabled] = useState(initialEnabled);
  const [alertOn, setAlertOn] = useState<AlertOn>(initialAlertOn);
  const [webhook, setWebhook] = useState('');
  const [saving, setSaving] = useState(false);

  const onSave = async () => {
    setSaving(true);
    try {
      // Only send `webhook` when the user typed one (blank = leave it unchanged).
      const trimmed = webhook.trim();
      await putNotifications(suiteId, {
        enabled,
        alert_on: alertOn,
        ...(trimmed ? { webhook: trimmed } : {}),
      });
      message.success('Notifications saved');
      setWebhook('');
      onChanged();
    } catch (err) {
      message.error(`Save failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setSaving(false);
    }
  };

  const onClearWebhook = async () => {
    setSaving(true);
    try {
      await putNotifications(suiteId, { enabled, alert_on: alertOn, webhook: '' });
      message.success('Webhook cleared (falls back to the workspace webhook)');
      onChanged();
    } catch (err) {
      message.error(`Clear failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Flex vertical gap={16}>
      <Flex align="center" gap={12}>
        <Switch
          checked={enabled}
          disabled={!canManage}
          onChange={setEnabled}
          aria-label="Enable notifications"
        />
        <Typography.Text>Send alerts for this suite</Typography.Text>
      </Flex>

      <Flex vertical gap={4}>
        <Typography.Text type="secondary">Alert threshold</Typography.Text>
        <Select<AlertOn>
          value={alertOn}
          onChange={setAlertOn}
          disabled={!canManage || !enabled}
          options={ALERT_ON_OPTIONS}
          style={{ maxWidth: 280 }}
          aria-label="Alert threshold"
        />
      </Flex>

      <Flex vertical gap={4}>
        <Flex align="center" gap={8}>
          <Typography.Text type="secondary">Teams webhook</Typography.Text>
          <Tag color={hasWebhook ? 'success' : 'default'}>{hasWebhook ? 'set' : 'not set'}</Tag>
        </Flex>
        <Input.Password
          value={webhook}
          disabled={!canManage}
          onChange={(e) => setWebhook(e.target.value)}
          placeholder={
            hasWebhook
              ? 'Enter a new https URL to replace it'
              : 'https://… (falls back to workspace)'
          }
          aria-label="Teams webhook URL"
          style={{ maxWidth: 480 }}
        />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          The URL is stored as a secret and never shown again. Leave blank to keep the current one.
        </Typography.Text>
      </Flex>

      {canManage && (
        <Flex gap={8}>
          <Button type="primary" loading={saving} onClick={onSave}>
            Save
          </Button>
          {hasWebhook && (
            <Button loading={saving} onClick={onClearWebhook}>
              Clear webhook
            </Button>
          )}
        </Flex>
      )}
    </Flex>
  );
}
