import { App, Alert, Button, Card, Flex, Select, Spin, Switch, Tag, Typography } from 'antd';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import {
  type AlertOn,
  getNotifications,
  putNotifications,
  type SuiteNotification,
  type SuiteNotificationUpdate,
} from '../../api/notifications';
import {
  linkSuiteChannel,
  listChannels,
  listSuiteChannels,
  type NotificationChannel,
  unlinkSuiteChannel,
} from '../../api/notificationChannels';
import { useAsyncAction } from '../../hooks/useAsyncAction';
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
  const legacy =
    state.status === 'ok' &&
    (state.data.has_webhook || state.data.has_slack_webhook || !!state.data.email_recipients);

  return (
    <Flex vertical gap={16}>
      <Card
        size="small"
        title={
          <Flex vertical gap={2}>
            <Typography.Text strong>Alerting</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
              Whether this suite alerts, on what, and to which of the channels an admin configured.
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
          <Flex vertical gap={16}>
            <NotificationsForm
              // Remount on a config change so the form re-seeds from the loaded values (render-
              // phase reset, no setState-in-effect); an unchanged reload keeps the same key.
              key={`${state.data.enabled}:${state.data.alert_on}`}
              suiteId={suiteId}
              canManage={canManage}
              initialEnabled={state.data.enabled}
              initialAlertOn={state.data.alert_on}
              onChanged={reload}
            />
            <ChannelPicker suiteId={suiteId} canManage={canManage} />
          </Flex>
        )}
      </Card>
      {legacy && state.status === 'ok' && (
        <LegacyDestinations
          suiteId={suiteId}
          canManage={canManage}
          config={state.data}
          onChanged={reload}
        />
      )}
    </Flex>
  );
}

/**
 * Reusable-channel linking (#1761, admin-managed destinations from Settings → Notifications).
 * Coexists with the legacy per-suite webhook above rather than replacing it — both paths deliver
 * independently server-side.
 */
function ChannelPicker({ suiteId, canManage }: { suiteId: string; canManage: boolean }) {
  const linked = useAsyncData(() => listSuiteChannels(suiteId));

  return (
    <Flex vertical gap={4}>
      <Typography.Text type="secondary">Channels</Typography.Text>
      {canManage ? (
        // `ManagedChannelPicker` mounts synchronously off `canManage` (not gated on
        // `linked` resolving), so its OWN listChannels fetch starts in parallel with
        // `linked`'s — the pre-#1879 parallelism, preserved for the users who actually
        // exercise this picker.
        <ManagedChannelPicker suiteId={suiteId} linked={linked} />
      ) : (
        <>
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
            (linked.state.data.length === 0 ? (
              <Typography.Text type="secondary">No channels linked.</Typography.Text>
            ) : (
              <Flex gap={4} wrap>
                {linked.state.data.map((c) => (
                  <Tag key={c.id}>{c.name}</Tag>
                ))}
              </Flex>
            ))}
        </>
      )}
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Channels are created once by a workspace admin under{' '}
        <Link to="/admin/settings">Settings → Notification channels</Link> and linked here. A suite
        with none linked still alerts through the workspace defaults.
      </Typography.Text>
    </Flex>
  );
}

/** The editable multi-select — mounted only for a `canManage` caller (#1879), so its
 *  `listChannels` fetch (the full workspace list, needed for the option set) never
 *  runs for a viewer. Takes `linked`'s async state as a prop rather than re-fetching,
 *  so the two GETs still fire in parallel (mounting this component on `canManage`
 *  alone, not on `linked` having already resolved, is what keeps them concurrent —
 *  see the #1879-review follow-up that caught the sequential-fetch regression in an
 *  earlier version of this fix). */
function ManagedChannelPicker({
  suiteId,
  linked,
}: {
  suiteId: string;
  linked: ReturnType<typeof useAsyncData<NotificationChannel[]>>;
}) {
  const all = useAsyncData(listChannels);

  if (all.state.status === 'loading' || linked.state.status === 'loading') {
    return <Spin description="Loading channels…" />;
  }
  if (all.state.status === 'error') {
    return (
      <Alert type="error" showIcon title="Failed to load channels" description={all.state.error} />
    );
  }
  if (linked.state.status === 'error') {
    return (
      <Alert
        type="error"
        showIcon
        title="Failed to load linked channels"
        description={linked.state.error}
      />
    );
  }
  return (
    <ChannelPickerBody
      // Remount when the linked set actually changes underneath us (render-
      // phase reset, no setState-in-effect) — a same-set reload keeps the key.
      key={linked.state.data
        .map((c) => c.id)
        .sort()
        .join(',')}
      suiteId={suiteId}
      allChannels={all.state.data}
      initialLinked={linked.state.data}
      onResync={linked.reload}
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

/** Shown only while a suite still carries a per-suite webhook or recipient list from
 *  before channels existed. Nothing here can be SET — an editor can only clear it and
 *  link a channel instead; setting stays an Admin escape hatch on the API. */
function LegacyDestinations({
  suiteId,
  canManage,
  config,
  onChanged,
}: {
  suiteId: string;
  canManage: boolean;
  config: SuiteNotification;
  onChanged: () => void;
}) {
  const { run, loading } = useAsyncAction('Could not clear the destination');
  // Clearing sends the loaded (server-known) enabled/alert_on so it never persists an
  // unsaved edit from the form above (#639 review).
  const clear = (extra: Partial<SuiteNotificationUpdate>) => () =>
    void run(async () => {
      await putNotifications(suiteId, {
        enabled: config.enabled,
        alert_on: config.alert_on,
        ...extra,
      });
      onChanged();
    });
  const rows: { label: string; set: boolean; extra: Partial<SuiteNotificationUpdate> }[] = [
    { label: 'Teams webhook', set: config.has_webhook, extra: { webhook: '' } },
    { label: 'Slack webhook', set: config.has_slack_webhook, extra: { slack_webhook: '' } },
    {
      label: `Email recipients${config.email_recipients ? ` (${config.email_recipients})` : ''}`,
      set: !!config.email_recipients,
      extra: { email_recipients: '' },
    },
  ];
  return (
    <Card
      size="small"
      title={
        <Flex vertical gap={2}>
          <Typography.Text strong>Legacy inline destinations</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
            Set on this suite before channels existed. Move it to a channel above, then clear it.
          </Typography.Text>
        </Flex>
      }
    >
      <Flex vertical gap={8}>
        {rows
          .filter((r) => r.set)
          .map((r) => (
            <Flex key={r.label} align="center" gap={8} wrap>
              <Tag color="warning">{r.label}</Tag>
              {canManage && (
                <Button size="small" loading={loading} onClick={clear(r.extra)}>
                  Clear
                </Button>
              )}
            </Flex>
          ))}
      </Flex>
    </Card>
  );
}

function NotificationsForm({
  suiteId,
  canManage,
  initialEnabled,
  initialAlertOn,
  onChanged,
}: {
  suiteId: string;
  canManage: boolean;
  initialEnabled: boolean;
  initialAlertOn: AlertOn;
  onChanged: () => void;
}) {
  const { message } = App.useApp();
  const [enabled, setEnabled] = useState(initialEnabled);
  const [alertOn, setAlertOn] = useState<AlertOn>(initialAlertOn);
  const { run, loading: saving } = useAsyncAction('Save failed');

  const onSave = () =>
    void run(async () => {
      await putNotifications(suiteId, { enabled, alert_on: alertOn });
      message.success('Notifications saved');
      onChanged();
    });

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

      {canManage && (
        <Button
          type="primary"
          loading={saving}
          onClick={onSave}
          style={{ alignSelf: 'flex-start' }}
        >
          Save
        </Button>
      )}
    </Flex>
  );
}
