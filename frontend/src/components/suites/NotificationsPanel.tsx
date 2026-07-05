import { App, Alert, Button, Card, Flex, Input, Select, Spin, Switch, Tag, Typography } from 'antd';
import { useState } from 'react';

import {
  type AlertOn,
  getNotifications,
  putNotifications,
  type SuiteNotificationUpdate,
} from '../../api/notifications';
import { useAsyncData } from '../../hooks/useAsyncData';

const ALERT_ON_OPTIONS: { value: AlertOn; label: string }[] = [
  { value: 'fail', label: 'On fail / critical' },
  { value: 'warn', label: 'On warn and worse' },
  { value: 'always', label: 'Always (every run)' },
];

/**
 * Suite-detail panel for per-suite alerting (W6, fronts `notification_service`;
 * Slack + email per-suite added in #633). Controls whether outcomes are delivered,
 * the threshold (`alert_on`), and the per-suite destinations. The Teams + Slack
 * webhooks are **write-only** secrets — the API never returns them, so each field
 * shows whether one is set and only writes when you type a new one (blank =
 * unchanged; "Clear" removes it → workspace fallback). Email recipients aren't a
 * secret, so they're prefilled and edited in place. `view` reads; `edit`+
 * (`canManage`) mutates, matching the backend gate.
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
            Send this suite's run outcomes to Microsoft Teams, Slack, or email.
          </Typography.Text>
        </Flex>
      }
    >
      {state.status === 'loading' ? (
        <Spin description="Loading notifications…" />
      ) : state.status === 'error' ? (
        <Alert
          type="error"
          showIcon
          title="Failed to load notifications"
          description={state.error}
        />
      ) : (
        <NotificationsForm
          // Remount on a config change so the form re-seeds from the loaded values
          // (render-phase reset, no setState-in-effect); an unchanged reload keeps
          // the same key, preserving any in-progress edits.
          key={
            `${state.data.enabled}:${state.data.alert_on}:${state.data.has_webhook}` +
            `:${state.data.has_slack_webhook}:${state.data.email_recipients ?? ''}`
          }
          suiteId={suiteId}
          canManage={canManage}
          initialEnabled={state.data.enabled}
          initialAlertOn={state.data.alert_on}
          hasWebhook={state.data.has_webhook}
          hasSlackWebhook={state.data.has_slack_webhook}
          initialEmail={state.data.email_recipients ?? ''}
          onChanged={reload}
        />
      )}
    </Card>
  );
}

/** A write-only secret webhook field (Teams / Slack): shows set/not-set, never the
 *  value; a blank input leaves the stored secret unchanged. */
function WebhookField({
  label,
  ariaLabel,
  isSet,
  value,
  onChange,
  disabled,
}: {
  label: string;
  ariaLabel: string;
  isSet: boolean;
  value: string;
  onChange: (v: string) => void;
  disabled: boolean;
}) {
  return (
    <Flex vertical gap={4}>
      <Flex align="center" gap={8}>
        <Typography.Text type="secondary">{label}</Typography.Text>
        <Tag color={isSet ? 'success' : 'default'}>{isSet ? 'set' : 'not set'}</Tag>
      </Flex>
      <Input.Password
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        placeholder={
          isSet ? 'Enter a new https URL to replace it' : 'https://… (falls back to workspace)'
        }
        aria-label={ariaLabel}
        style={{ maxWidth: 480 }}
      />
    </Flex>
  );
}

function NotificationsForm({
  suiteId,
  canManage,
  initialEnabled,
  initialAlertOn,
  hasWebhook,
  hasSlackWebhook,
  initialEmail,
  onChanged,
}: {
  suiteId: string;
  canManage: boolean;
  initialEnabled: boolean;
  initialAlertOn: AlertOn;
  hasWebhook: boolean;
  hasSlackWebhook: boolean;
  initialEmail: string;
  onChanged: () => void;
}) {
  const { message } = App.useApp();
  const [enabled, setEnabled] = useState(initialEnabled);
  const [alertOn, setAlertOn] = useState<AlertOn>(initialAlertOn);
  const [webhook, setWebhook] = useState('');
  const [slackWebhook, setSlackWebhook] = useState('');
  const [email, setEmail] = useState(initialEmail);
  const [saving, setSaving] = useState(false);

  // Single save/clear entry point so all channels share the same error handling
  // (a failure surfaces a toast and never silently drops).
  const put = async (extra: Partial<SuiteNotificationUpdate>, successMsg: string) => {
    setSaving(true);
    try {
      await putNotifications(suiteId, { enabled, alert_on: alertOn, ...extra });
      message.success(successMsg);
      onChanged();
      return true;
    } catch (err) {
      message.error(`Save failed: ${err instanceof Error ? err.message : 'unknown error'}`);
      return false;
    } finally {
      setSaving(false);
    }
  };

  const onSave = async () => {
    const teams = webhook.trim();
    const slack = slackWebhook.trim();
    const ok = await put(
      {
        // Write-only secrets: only send when the user typed a new value.
        ...(teams ? { webhook: teams } : {}),
        ...(slack ? { slack_webhook: slack } : {}),
        // Email is returned + editable → send the current value (WYSIWYG; "" clears).
        email_recipients: email.trim(),
      },
      'Notifications saved',
    );
    if (ok) {
      setWebhook('');
      setSlackWebhook('');
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

      <WebhookField
        label="Teams webhook"
        ariaLabel="Teams webhook URL"
        isSet={hasWebhook}
        value={webhook}
        onChange={setWebhook}
        disabled={!canManage}
      />
      <WebhookField
        label="Slack webhook"
        ariaLabel="Slack webhook URL"
        isSet={hasSlackWebhook}
        value={slackWebhook}
        onChange={setSlackWebhook}
        disabled={!canManage}
      />

      <Flex vertical gap={4}>
        <Typography.Text type="secondary">Email recipients</Typography.Text>
        <Input
          value={email}
          disabled={!canManage}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="a@example.com, b@example.com (falls back to workspace)"
          aria-label="Email recipients"
          style={{ maxWidth: 480 }}
        />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Comma-separated addresses. Clear to fall back to the workspace recipients.
        </Typography.Text>
      </Flex>

      {canManage && (
        <Flex gap={8} wrap>
          <Button type="primary" loading={saving} onClick={onSave}>
            Save
          </Button>
          {hasWebhook && (
            <Button loading={saving} onClick={() => put({ webhook: '' }, 'Teams webhook cleared')}>
              Clear Teams
            </Button>
          )}
          {hasSlackWebhook && (
            <Button
              loading={saving}
              onClick={() => put({ slack_webhook: '' }, 'Slack webhook cleared')}
            >
              Clear Slack
            </Button>
          )}
        </Flex>
      )}
    </Flex>
  );
}
