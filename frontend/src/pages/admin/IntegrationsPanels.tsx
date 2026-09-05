import { App, Button, Card, Flex, Modal, Switch, Tag, Typography } from 'antd';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import {
  type AdminWebhook,
  type InventorySyncRow,
  type PollHealth,
  getAdminHealth,
  listInventorySync,
  pollNow,
  regenerateWebhookSecret,
  runInventorySync,
  setInventorySync,
  type WebhookRegeneration,
} from '../../api/admin';
import { PROVIDER_LABELS } from '../../api/triggerBindings';
import { formatTimestamp } from '../../components/results/resultsFormat';
import { useAsyncAction } from '../../hooks/useAsyncAction';
import { useAsyncData } from '../../hooks/useAsyncData';
import { errorMessage } from '../../utils/errors';
import { mapAsync } from './asyncHelpers';
import { DataTable } from './parts';

const POLL_STATUS: Record<PollHealth['status'], { color: string; label: string }> = {
  on_cadence: { color: 'green', label: 'On cadence' },
  stalled: { color: 'orange', label: 'Stalled' },
  failing: { color: 'red', label: 'Failing' },
  unknown: { color: 'default', label: 'Unknown — never polled' },
};

const SYNC_STATUS: Record<InventorySyncRow['status'], { color: string; label: string }> = {
  never_synced: { color: 'default', label: 'Never synced' },
  synced: { color: 'green', label: 'Synced' },
  failing: { color: 'red', label: 'Failing' },
};

/** Regenerate button + the one-time reveal of the new value. */
export function RegenerateSecretButton({ webhook }: { webhook: AdminWebhook }) {
  const { modal } = App.useApp();
  const [result, setResult] = useState<WebhookRegeneration | null>(null);
  const { run, loading } = useAsyncAction('Could not regenerate the webhook secret');
  const noun = webhook.provider === 'adf' ? 'secret' : 'signing key';

  const onClick = () =>
    modal.confirm({
      title: `Regenerate the ${PROVIDER_LABELS[webhook.provider]} webhook ${noun}?`,
      content:
        'The current value keeps working for a short grace window so you can update the provider side first. After that, callbacks using the old value are rejected.',
      okText: 'Regenerate',
      onOk: () => run(async () => setResult(await regenerateWebhookSecret(webhook.provider))),
    });

  return (
    <>
      <Button size="small" onClick={onClick} loading={loading}>
        Regenerate {noun}
      </Button>
      <Modal
        title={`New ${PROVIDER_LABELS[webhook.provider]} webhook ${noun}`}
        open={result !== null}
        onCancel={() => setResult(null)}
        footer={
          <Button type="primary" onClick={() => setResult(null)}>
            I have copied it
          </Button>
        }
      >
        {result && (
          <Flex vertical gap={12}>
            <Typography.Text strong>This value is shown once and never again.</Typography.Text>
            <Typography.Text code copyable={{ text: result.value }}>
              {result.value}
            </Typography.Text>
            {result.inbound_url && (
              <Typography.Text type="secondary">
                Full URL:{' '}
                <Typography.Text code copyable={{ text: result.inbound_url }}>
                  {result.inbound_url}
                </Typography.Text>
              </Typography.Text>
            )}
            <Typography.Text type="secondary">
              {result.grace_until
                ? `The previous value keeps working until ${formatTimestamp(result.grace_until)}. Update the provider side before then.`
                : 'There was no previous value to keep, or the grace window is off — only this value is accepted.'}
            </Typography.Text>
          </Flex>
        )}
      </Modal>
    </>
  );
}

/** Per-connection poll staleness from `/admin/health`, with Poll all now. */
export function PollingHealthSection() {
  const { message } = App.useApp();
  const { state, reload } = useAsyncData(() => getAdminHealth());
  const { run, loading } = useAsyncAction('Poll now failed');

  const onPollAll = () =>
    void run(async () => {
      const res = await pollNow();
      message.success(
        res.dispatched.length === 0
          ? 'No orchestration connections to poll.'
          : `Queued ${res.dispatched.length} poll${res.dispatched.length === 1 ? '' : 's'}.`,
      );
      reload();
    });

  return (
    <Card
      title="Polling health (10-min fallback)"
      size="small"
      extra={
        <Button size="small" onClick={onPollAll} loading={loading}>
          Poll all now
        </Button>
      }
    >
      <DataTable<PollHealth>
        state={mapAsync(state, (h) => h.polling)}
        rowKey={(r) => r.connection_id}
        errorMessage="Could not load polling health"
        columns={[
          {
            title: 'Connection',
            dataIndex: 'name',
            render: (name: string, row) => (
              <Link to={`/connections/${row.connection_id}`}>{name}</Link>
            ),
          },
          {
            title: 'Provider',
            dataIndex: 'provider',
            render: (p: PollHealth['provider']) => PROVIDER_LABELS[p],
          },
          {
            title: 'Last poll',
            dataIndex: 'last_polled_at',
            render: (v: string | null) => (v ? formatTimestamp(v) : '—'),
          },
          {
            title: 'Status',
            dataIndex: 'status',
            render: (s: PollHealth['status'], row) => (
              <Flex vertical gap={2}>
                <Tag color={POLL_STATUS[s].color}>{POLL_STATUS[s].label}</Tag>
                {row.last_error && (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {row.last_error}
                  </Typography.Text>
                )}
              </Flex>
            ),
          },
          {
            title: 'Next poll',
            dataIndex: 'next_expected_at',
            render: (v: string | null) => (v ? formatTimestamp(v) : '—'),
          },
        ]}
      />
    </Card>
  );
}

/** Per-connection inventory sync: toggle, last sync, discovered and unmonitored counts. */
export function InventorySyncSection() {
  const { message } = App.useApp();
  const { state, reload } = useAsyncData(() => listInventorySync());
  const [busy, setBusy] = useState<string | null>(null);

  const act = async (id: string, fn: () => Promise<void>) => {
    setBusy(id);
    try {
      await fn();
      reload();
    } catch (err) {
      message.error(errorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card title="Warehouse inventory sync" size="small">
      <Flex vertical gap={12}>
        <Typography.Text type="secondary">
          A synced connection lists every table the warehouse has, so an unmonitored table is
          visible rather than invisible. Counts stay blank until a sync has run.
        </Typography.Text>
        <DataTable<InventorySyncRow>
          state={state}
          rowKey={(r) => r.connection_id}
          errorMessage="Could not load inventory-sync state"
          columns={[
            {
              title: 'Connection',
              dataIndex: 'name',
              render: (name: string, row) => (
                <Link to={`/connections/${row.connection_id}`}>
                  {name} <Typography.Text type="secondary">({row.env})</Typography.Text>
                </Link>
              ),
            },
            {
              title: 'Sync',
              dataIndex: 'enabled',
              render: (enabled: boolean, row) => (
                <Switch
                  size="small"
                  checked={enabled}
                  loading={busy === row.connection_id}
                  aria-label={`Inventory sync for ${row.name}`}
                  onChange={(next) =>
                    void act(row.connection_id, async () => {
                      await setInventorySync(row.connection_id, next);
                    })
                  }
                />
              ),
            },
            {
              title: 'Status',
              dataIndex: 'status',
              render: (s: InventorySyncRow['status'], row) => (
                <Flex vertical gap={2}>
                  <Tag color={SYNC_STATUS[s].color}>{SYNC_STATUS[s].label}</Tag>
                  {row.last_error && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {row.last_error}
                    </Typography.Text>
                  )}
                </Flex>
              ),
            },
            {
              title: 'Last sync',
              dataIndex: 'last_attempted_at',
              render: (v: string | null) => (v ? formatTimestamp(v) : '—'),
            },
            {
              title: 'Tables discovered',
              dataIndex: 'tables_discovered',
              render: (v: number | null) => v ?? '—',
            },
            {
              title: 'Unmonitored',
              dataIndex: 'unmonitored',
              render: (v: number | null) => v ?? '—',
            },
            {
              title: '',
              key: 'run',
              render: (_: unknown, row) => (
                <Button
                  size="small"
                  disabled={!row.enabled}
                  loading={busy === row.connection_id}
                  onClick={() =>
                    void act(row.connection_id, async () => {
                      await runInventorySync(row.connection_id);
                      message.success(`Sync queued for ${row.name}.`);
                    })
                  }
                >
                  Run now
                </Button>
              ),
            },
          ]}
        />
      </Flex>
    </Card>
  );
}
