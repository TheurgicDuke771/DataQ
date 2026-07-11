import { App, Button, Card, Empty, Flex, Popconfirm, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';

import {
  type Incident,
  acknowledgeIncident,
  listIncidents,
  resolveIncident,
} from '../../api/incidents';
import { useAsyncData } from '../../hooks/useAsyncData';
import { errorMessage } from '../../utils/errors';
import { AsyncBody } from '../AsyncBody';
import { formatTimestamp } from '../results/resultsFormat';

/**
 * Incidents section on the asset page (ADR 0034 #761) — the *active* incidents
 * (open / acknowledged) on this asset, with occurrence count and edit-gated
 * ack/resolve. Resolved incidents are omitted here (the durable history + a full
 * incidents page defer to #773's navigation inversion — this is the "what is
 * broken right now" view).
 *
 * `permissionBySuite` maps each composing suite to the caller's level; ack/resolve
 * render only for `edit`/`admin`/`owner` — nav convenience, not the security
 * boundary (the backend 403s an unpermitted action regardless).
 */
export function IncidentsPanel({
  assetId,
  permissionBySuite,
}: {
  assetId: string;
  permissionBySuite: Record<string, string>;
}) {
  const { state, reload } = useAsyncData(() => listIncidents({ asset_id: assetId }));
  return (
    <Card size="small" title="Incidents">
      <AsyncBody
        state={state}
        loadingText="Loading incidents…"
        errorTitle="Failed to load incidents"
      >
        {(incidents) => (
          <IncidentsTable
            incidents={incidents.filter((i) => i.status !== 'resolved')}
            permissionBySuite={permissionBySuite}
            onChanged={reload}
          />
        )}
      </AsyncBody>
    </Card>
  );
}

const ACTING_LEVELS = new Set(['edit', 'admin', 'owner']);

const STATUS_COLOR: Record<string, string> = {
  open: 'red',
  acknowledged: 'gold',
};

const SEVERITY_COLOR: Record<string, string> = {
  warn: 'gold',
  fail: 'red',
  critical: 'magenta',
};

function IncidentsTable({
  incidents,
  permissionBySuite,
  onChanged,
}: {
  incidents: Incident[];
  permissionBySuite: Record<string, string>;
  onChanged: () => void;
}) {
  const { message } = App.useApp();
  const [busyId, setBusyId] = useState<string | null>(null);

  const act = async (
    incident: Incident,
    action: 'ack' | 'resolve',
    fn: (id: string) => Promise<unknown>,
  ) => {
    setBusyId(incident.id);
    try {
      await fn(incident.id);
      message.success(action === 'ack' ? 'Incident acknowledged' : 'Incident resolved');
      onChanged();
    } catch (err) {
      message.error(`Action failed: ${errorMessage(err)}`);
    } finally {
      setBusyId(null);
    }
  };

  const columns: ColumnsType<Incident> = [
    {
      title: 'Check',
      dataIndex: 'check_name',
      render: (name: string | null) =>
        name ?? <Typography.Text type="secondary">—</Typography.Text>,
    },
    {
      title: 'State',
      dataIndex: 'status',
      width: 130,
      render: (status: string) => <Tag color={STATUS_COLOR[status]}>{status}</Tag>,
    },
    {
      title: 'Severity',
      dataIndex: 'latest_status',
      width: 100,
      render: (sev: string | null) =>
        sev ? <Tag color={SEVERITY_COLOR[sev] ?? 'default'}>{sev}</Tag> : '—',
    },
    {
      title: 'Occurrences',
      dataIndex: 'occurrence_count',
      width: 110,
      align: 'center',
    },
    {
      title: 'Last seen',
      dataIndex: 'last_seen_at',
      width: 190,
      render: (ts: string) => formatTimestamp(ts),
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 190,
      render: (_: unknown, incident) => {
        const level = permissionBySuite[incident.suite_id];
        if (!level || !ACTING_LEVELS.has(level)) {
          return <Typography.Text type="secondary">View only</Typography.Text>;
        }
        const busy = busyId === incident.id;
        return (
          <Flex gap={8}>
            {incident.status === 'open' && (
              <Button
                size="small"
                loading={busy}
                onClick={() => void act(incident, 'ack', acknowledgeIncident)}
              >
                Acknowledge
              </Button>
            )}
            <Popconfirm
              title="Resolve this incident?"
              okText="Resolve"
              onConfirm={() => void act(incident, 'resolve', resolveIncident)}
            >
              <Button size="small" type="primary" loading={busy}>
                Resolve
              </Button>
            </Popconfirm>
          </Flex>
        );
      },
    },
  ];

  if (incidents.length === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No open incidents." />;
  }
  return (
    <Table<Incident>
      scroll={{ x: 'max-content' }}
      rowKey="id"
      size="small"
      columns={columns}
      dataSource={incidents}
      pagination={false}
    />
  );
}
