import { Alert, Button, Flex, Input, Select, Spin, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';

import {
  type AuditEvent,
  type AuditEventPage,
  type ExternalTransfer,
  getDeploymentPosture,
  listAuditEvents,
} from '../../api/admin';
import { formatTimestamp } from '../../components/results/resultsFormat';
import { Filter } from '../../components/shared/Filter';
import { WINDOW_PRESETS } from '../../components/shared/windowPresets';
import { useAsyncData } from '../../hooks/useAsyncData';
import { mapAsync } from './asyncHelpers';
import { DataTable, Section } from './parts';

/** Compliance surface: the G1 audit log + the #1555 deployment-posture readout. */
export function AdminCompliance() {
  return (
    <Flex vertical gap={16}>
      <AuditLogSection />
      <DeploymentPostureSection />
    </Flex>
  );
}

const AUDIT_PAGE_SIZE = 25;
const AUDIT_DATE_WINDOWS = [{ value: 'all', label: 'All time' }, ...WINDOW_PRESETS] as const;
type AuditDateWindow = (typeof AUDIT_DATE_WINDOWS)[number]['value'];

const ACTION_CLASSES = [
  { value: 'config', label: 'Config change' },
  { value: 'access', label: 'Data access' },
] as const;

function windowSince(window: AuditDateWindow): string | undefined {
  if (window === 'all') return undefined;
  const days = Number(window);
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();
}

// Checked client-side so a stray value (a pasted email) gets "not a valid ID"
// here rather than the backend's generic, un-field-specific 422 message.
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type AuditFilterDraft = {
  actionClass?: 'config' | 'access';
  entityType: string;
  actorUserId: string;
  dateWindow: AuditDateWindow;
};

const EMPTY_AUDIT_FILTERS: AuditFilterDraft = {
  entityType: '',
  actorUserId: '',
  dateWindow: 'all',
};

function AuditLogSection() {
  // Two states deliberately: `draft` is bound to the inputs; `query` is what the
  // fetch uses. Collapsing them let Next (which reloads without going through
  // Search) apply a still-unsubmitted edit to the page fetch.
  const [draft, setDraft] = useState<AuditFilterDraft>(EMPTY_AUDIT_FILTERS);
  const [query, setQuery] = useState<AuditFilterDraft>(EMPTY_AUDIT_FILTERS);
  const [page, setPage] = useState(1);
  const trimmedDraftActor = draft.actorUserId.trim();
  const actorError = trimmedDraftActor !== '' && !UUID_PATTERN.test(trimmedDraftActor);

  const { state, reload } = useAsyncData(() =>
    listAuditEvents({
      action_class: query.actionClass,
      entity_type: query.entityType.trim() || undefined,
      actor_user_id: query.actorUserId.trim() || undefined,
      since: windowSince(query.dateWindow),
      limit: AUDIT_PAGE_SIZE,
      offset: (page - 1) * AUDIT_PAGE_SIZE,
    }),
  );

  // useAsyncData only re-fetches on reload(), so a filter or page change must bump it.
  const search = () => {
    // Drop an invalid actor ID rather than send it; the inline error already warns.
    setQuery({ ...draft, actorUserId: actorError ? '' : draft.actorUserId });
    setPage(1);
    reload();
  };
  const onPageChange = (nextPage: number) => {
    setPage(nextPage);
    reload();
  };

  return (
    <Section title="Audit log">
      <Flex gap={12} wrap align="flex-end">
        <Filter label="Type">
          <Select<'config' | 'access' | 'all'>
            style={{ width: 160 }}
            value={draft.actionClass ?? 'all'}
            onChange={(v) => setDraft((f) => ({ ...f, actionClass: v === 'all' ? undefined : v }))}
            options={[{ value: 'all', label: 'All actions' }, ...ACTION_CLASSES]}
          />
        </Filter>
        <Filter label="Entity type">
          <Input
            style={{ width: 140 }}
            placeholder="e.g. suite"
            value={draft.entityType}
            onChange={(e) => setDraft((f) => ({ ...f, entityType: e.target.value }))}
          />
        </Filter>
        <Filter label="Actor user ID">
          <Input
            style={{ width: 220 }}
            placeholder="UUID"
            status={actorError ? 'error' : undefined}
            value={draft.actorUserId}
            onChange={(e) => setDraft((f) => ({ ...f, actorUserId: e.target.value }))}
          />
          {actorError && (
            <Typography.Text type="danger" style={{ fontSize: 12 }}>
              Not a valid ID — paste the UUID from the Members table, not an email.
            </Typography.Text>
          )}
        </Filter>
        <Filter label="When">
          <Select<AuditDateWindow>
            style={{ width: 160 }}
            value={draft.dateWindow}
            onChange={(v) => setDraft((f) => ({ ...f, dateWindow: v }))}
            options={AUDIT_DATE_WINDOWS.map((w) => ({ value: w.value, label: w.label }))}
          />
        </Filter>
        <Button type="primary" onClick={search}>
          Search
        </Button>
      </Flex>

      {state.status === 'ok' && <AuditRetentionNotice page={state.data} />}

      <DataTable
        state={mapAsync(state, (p) => p.events)}
        columns={AUDIT_EVENT_COLUMNS}
        rowKey={(e) => e.id}
        errorMessage="Failed to load the audit log"
        pagination={
          state.status === 'ok'
            ? {
                current: page,
                pageSize: AUDIT_PAGE_SIZE,
                total: state.data.total,
                onChange: onPageChange,
                showSizeChanger: false,
              }
            : false
        }
      />
    </Section>
  );
}

/** The honesty fields the read API returns alongside the page — without these, an
 *  empty or short page is ambiguous between "nothing happened" and "swept away". */
function AuditRetentionNotice({ page }: { page: AuditEventPage }) {
  if (page.retention_days <= 0) {
    return (
      <Alert
        type="warning"
        showIcon
        title="Retention sweep is disabled (AUDIT_RETENTION_DAYS ≤ 0) — the log is unbounded."
      />
    );
  }
  return (
    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
      Retained {page.retention_days} days
      {page.retained_since ? ` (since ${formatTimestamp(page.retained_since)})` : ''} — events older
      than that have been swept.
    </Typography.Text>
  );
}

const ACTION_CLASS_COLORS: Record<string, string> = { config: 'blue', access: 'purple' };

const AUDIT_EVENT_COLUMNS: ColumnsType<AuditEvent> = [
  {
    title: 'When',
    dataIndex: 'occurred_at',
    render: (v: string) => formatTimestamp(v),
  },
  {
    title: 'Type',
    dataIndex: 'action_class',
    render: (v: string) => <Tag color={ACTION_CLASS_COLORS[v] ?? 'default'}>{v}</Tag>,
  },
  { title: 'Action', dataIndex: 'action' },
  {
    title: 'Entity',
    key: 'entity',
    render: (_, e) => (
      <Flex vertical>
        <Typography.Text>{e.entity_type}</Typography.Text>
        {e.entity_id && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {e.entity_id}
          </Typography.Text>
        )}
      </Flex>
    ),
  },
  {
    title: 'Actor',
    key: 'actor',
    render: (_, e) => e.actor_display ?? <Typography.Text type="secondary">—</Typography.Text>,
  },
];

/** #1555: read-only render of the deployment posture payload. */
function DeploymentPostureSection() {
  const { state } = useAsyncData(getDeploymentPosture);
  return (
    <Section title="Deployment & data residency">
      {state.status === 'loading' && <Spin size="large" />}
      {state.status === 'error' && (
        <Alert
          type="error"
          showIcon
          title="Failed to load deployment posture"
          description={state.error}
        />
      )}
      {state.status === 'ok' && (
        <Flex vertical gap={16}>
          <Flex vertical gap={4}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Declared region
            </Typography.Text>
            <Typography.Text>
              {state.data.region ?? (
                <Typography.Text type="secondary">Not declared</Typography.Text>
              )}
            </Typography.Text>
          </Flex>
          <Flex vertical gap={4}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Zero-sample privacy mode
            </Typography.Text>
            <Tag color={state.data.zero_sample_mode ? 'green' : 'default'}>
              {state.data.zero_sample_mode ? 'on — no failing-row samples persisted' : 'off'}
            </Tag>
          </Flex>
          <Table<ExternalTransfer>
            scroll={{ x: 'max-content' }}
            dataSource={state.data.external_transfers}
            rowKey={(t) => t.name}
            size="small"
            pagination={false}
            columns={[
              { title: 'Vector', dataIndex: 'name' },
              {
                title: 'Status',
                dataIndex: 'enabled',
                render: (enabled: boolean) => (
                  <Tag color={enabled ? 'volcano' : 'default'}>{enabled ? 'live' : 'off'}</Tag>
                ),
              },
              { title: 'Detail', dataIndex: 'detail' },
            ]}
          />
        </Flex>
      )}
    </Section>
  );
}
