import { App, Alert, Button, Card, Flex, Input, Select, Spin, Switch, Tag, Typography } from 'antd';
import { useState } from 'react';

import {
  type AlertOn,
  getNotifications,
  putNotifications,
  type SuiteNotificationUpdate,
} from '../../api/notifications';
import {
  linkSuiteChannel,
  listChannels,
  listSuiteChannels,
  type NotificationChannel,
  unlinkSuiteChannel,
} from '../../api/notificationChannels';
import { useAsyncData } from '../../hooks/useAsyncData';
import { errorMessage } from '../../utils/errors';

const ALERT_ON_OPTIONS: { value: AlertOn; label: string }[] = [
  { value: 'fail', label: 'On fail / critical' },
  { value: 'warn', label: 'On warn and worse' },
  { value: 'always', label: 'Always (every run)' },
];

/**
 * Suite-detail panel for per-suite alerting (W6, fronts `notification_service`; Slack + email per-
 * suite added in #633).
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
    <Flex vertical gap={16}>
      <Card
        size="small"
        title={
          <Flex vertical gap={2}>
            <Typography.Text strong>Legacy per-suite webhook</Typography.Text>
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
            // Remount on a config change so the form re-seeds from the loaded values (render-phase
            // reset, no setState-in-effect); an unchanged reload keeps the same key.
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
      <ChannelPicker suiteId={suiteId} canManage={canManage} />
    </Flex>
  );
}

/**
 * Reusable-channel linking (#1761, admin-managed destinations from Settings → Notifications).
 * Coexists with the legacy per-suite webhook above rather than replacing it — both paths deliver
 * independently server-side.
 */
function ChannelPicker({ suiteId, canManage }: { suiteId: string; canManage: boolean }) {
  // The full workspace channel list is only ever rendered as `<Select>` OPTIONS for
  // someone who can actually link/unlink — a viewer sees just the already-linked tags
  // below. Fetching it unconditionally cost every suite-detail page view an extra GET
  // for viewers, who never use it (#1879). `listChannels` is only called from the
  // `canManage` branch's own component, so a viewer's mount never issues that hook's
  // effect at all — conditionally mounting a CHILD component is Rules-of-Hooks-safe;
  // conditionally calling the hook itself inside one component would not be.
  const linked = useAsyncData(() => listSuiteChannels(suiteId));

  return (
    <Card
      size="small"
      title={
        <Flex vertical gap={2}>
          <Typography.Text strong>Linked channels (admin-managed)</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
            Reusable destinations configured once in Settings and linked to any number of suites.
          </Typography.Text>
        </Flex>
      }
    >
      {linked.state.status === 'loading' && <Spin description="Loading channels…" />}
      {linked.state.status === 'error' && (
        <Alert
          type="error"
          showIcon
          title="Failed to load linked channels"
          description={linked.state.error}
        />
      )}
      {linked.state.status === 'ok' &&
        (canManage ? (
          <ManagedChannelPicker
            suiteId={suiteId}
            initialLinked={linked.state.data}
            onResync={linked.reload}
          />
        ) : linked.state.data.length === 0 ? (
          <Typography.Text type="secondary">No channels linked.</Typography.Text>
        ) : (
          <Flex gap={4} wrap>
            {linked.state.data.map((c) => (
              <Tag key={c.id}>{c.name}</Tag>
            ))}
          </Flex>
        ))}
    </Card>
  );
}

/** The editable multi-select — mounted only for a `canManage` caller (#1879), so its
 *  `listChannels` fetch (the full workspace list, needed for the option set) never
 *  runs for a viewer. */
function ManagedChannelPicker({
  suiteId,
  initialLinked,
  onResync,
}: {
  suiteId: string;
  initialLinked: NotificationChannel[];
  onResync: () => void;
}) {
  const all = useAsyncData(listChannels);

  if (all.state.status === 'loading') return <Spin description="Loading channels…" />;
  if (all.state.status === 'error') {
    return (
      <Alert type="error" showIcon title="Failed to load channels" description={all.state.error} />
    );
  }
  return (
    <ChannelPickerBody
      // Remount when the linked set actually changes underneath us (render-
      // phase reset, no setState-in-effect) — a same-set reload keeps the key.
      key={initialLinked
        .map((c) => c.id)
        .sort()
        .join(',')}
      suiteId={suiteId}
      allChannels={all.state.data}
      initialLinked={initialLinked}
      onResync={onResync}
    />
  );
}

function ChannelPickerBody({
  suiteId,
  allChannels,
  initialLinked,
  onResync,
}: {
  suiteId: string;
  allChannels: NotificationChannel[];
  initialLinked: NotificationChannel[];
  onResync: () => void;
}) {
  const { message } = App.useApp();
  const [selected, setSelected] = useState<string[]>(initialLinked.map((c) => c.id));
  const [saving, setSaving] = useState(false);

  const onChange = async (nextIds: string[]) => {
    const added = nextIds.filter((id) => !selected.includes(id));
    const removed = selected.filter((id) => !nextIds.includes(id));
    setSelected(nextIds); // optimistic — diffed against the prior selection, not blown away/recreated
    setSaving(true);
    try {
      await Promise.all([
        ...added.map((id) => linkSuiteChannel(suiteId, id)),
        ...removed.map((id) => unlinkSuiteChannel(suiteId, id)),
      ]);
    } catch (err) {
      message.error(`Failed to update linked channels: ${errorMessage(err)}`);
      onResync(); // resync the checked set to whatever actually landed server-side
    } finally {
      setSaving(false);
    }
  };

  return (
    <Select
      mode="multiple"
      value={selected}
      onChange={(v) => void onChange(v)}
      disabled={saving}
      loading={saving}
      style={{ width: '100%' }}
      placeholder="No channels linked"
      aria-label="Linked channels"
      options={allChannels.map((c) => ({ value: c.id, label: `${c.name} (${c.type})` }))}
    />
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

  // Shared PUT with one error path (a failure toasts, never silently drops).
  const putWith = async (
    base: { enabled: boolean; alert_on: AlertOn },
    extra: Partial<SuiteNotificationUpdate>,
    successMsg: string,
  ) => {
    setSaving(true);
    try {
      await putNotifications(suiteId, { ...base, ...extra });
      message.success(successMsg);
      onChanged();
      return true;
    } catch (err) {
      message.error(`Save failed: ${errorMessage(err)}`);
      return false;
    } finally {
      setSaving(false);
    }
  };

  const onSave = async () => {
    const teams = webhook.trim();
    const slack = slackWebhook.trim();
    const ok = await putWith(
      { enabled, alert_on: alertOn },
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

  // Clearing a webhook is a focused action — it must NOT persist an unsaved enabled/threshold edit,
  // so it sends the loaded (server-known) enabled/alert_on (#639 review).
  const clearWebhook = (extra: Partial<SuiteNotificationUpdate>, successMsg: string) => () =>
    void putWith({ enabled: initialEnabled, alert_on: initialAlertOn }, extra, successMsg);

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
            <Button
              loading={saving}
              onClick={clearWebhook({ webhook: '' }, 'Teams webhook cleared')}
            >
              Clear Teams
            </Button>
          )}
          {hasSlackWebhook && (
            <Button
              loading={saving}
              onClick={clearWebhook({ slack_webhook: '' }, 'Slack webhook cleared')}
            >
              Clear Slack
            </Button>
          )}
        </Flex>
      )}
    </Flex>
  );
}
