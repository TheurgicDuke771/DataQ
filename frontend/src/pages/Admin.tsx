import { AppstoreOutlined, KeyOutlined, TeamOutlined } from '@ant-design/icons';
import {
  Alert,
  Button,
  Card,
  Col,
  Flex,
  Input,
  Row,
  Select,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType, TablePaginationConfig } from 'antd/es/table';
import { useState } from 'react';

import {
  type AdminAccess,
  type AdminSuite,
  type AdminUser,
  type AuditEvent,
  type AuditEventPage,
  type ExternalTransfer,
  getDeploymentPosture,
  listAdminAccess,
  listAdminSuites,
  listAdminUsers,
  listAuditEvents,
} from '../api/admin';
import { useMe } from '../auth/useMe';
import { LlmSettingsPanel } from '../components/admin/LlmSettingsPanel';
import { RoleEditor } from '../components/admin/RoleEditor';
import { MetricCard } from '../components/dashboard/MetricCard';
import { PageError } from '../components/feedback/PageError';
import { Forbidden } from '../components/Forbidden';
import { Page } from '../components/layout/Page';
import { formatTimestamp } from '../components/results/resultsFormat';
import { Filter } from '../components/shared/Filter';
import { WINDOW_PRESETS } from '../components/shared/windowPresets';
import { type AsyncState, useAsyncData } from '../hooks/useAsyncData';

/** Workspace-admin control centre (#173): all suites / members / access overview. */
export function Admin() {
  const me = useMe();

  if (me.status === 'loading') {
    return <Spin size="large" style={{ marginTop: 80 }} />;
  }
  if (me.status === 'error') {
    return (
      <PageError
        error={me.error}
        kind={me.kind}
        httpStatus={me.httpStatus}
        requestId={me.requestId}
      />
    );
  }
  if (!me.data.is_workspace_admin) {
    return <Forbidden message="The admin overview is restricted to workspace admins." />;
  }

  return <AdminOverview />;
}

/** Hooks live here so they only run for an admin (Admin renders this after the gate). */
function AdminOverview() {
  const suites = useAsyncData(listAdminSuites);
  const users = useAsyncData(listAdminUsers);
  const access = useAsyncData(listAdminAccess);
  // Rows the admin has just re-roled, keyed by id.
  const [rerolled, setRerolled] = useState<Record<string, AdminUser>>({});
  const userState = overlayUsers(users.state, rerolled);

  return (
    <Page>
      <Typography.Title level={3} style={{ margin: 0 }}>
        Admin
      </Typography.Title>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={8}>
          <MetricCard
            label="Suites"
            value={count(suites.state)}
            loading={suites.state.status === 'loading'}
            icon={<AppstoreOutlined />}
          />
        </Col>
        <Col xs={24} sm={8}>
          <MetricCard
            label="Members"
            value={count(users.state)}
            loading={users.state.status === 'loading'}
            icon={<TeamOutlined />}
          />
        </Col>
        <Col xs={24} sm={8}>
          <MetricCard
            label="Access grants"
            value={count(access.state)}
            loading={access.state.status === 'loading'}
            icon={<KeyOutlined />}
          />
        </Col>
      </Row>

      <Section title="All suites">
        <DataTable
          state={suites.state}
          columns={SUITE_COLUMNS}
          rowKey={(s) => s.id}
          errorMessage="Failed to load suites"
        />
      </Section>

      <Section title="Members & access">
        <DataTable
          state={userState}
          columns={userColumns((updated) =>
            setRerolled((prev) => ({ ...prev, [updated.id]: updated })),
          )}
          rowKey={(u) => u.id}
          errorMessage="Failed to load members"
        />
        <DataTable
          state={access.state}
          columns={ACCESS_COLUMNS}
          // A user appears once per suite (owner or a single share row).
          rowKey={(a) => `${a.suite_id}:${a.user_id}`}
          errorMessage="Failed to load access overview"
        />
      </Section>

      <AuditLogSection />

      <DeploymentPostureSection />

      <LlmSettingsPanel />
    </Page>
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

function count<T>(state: AsyncState<T[]>): number | null {
  return state.status === 'ok' ? state.data.length : null;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card title={title} size="small">
      <Flex vertical gap={16}>
        {children}
      </Flex>
    </Card>
  );
}

/** Load/error/table boilerplate for an already-fetched admin dataset. */
function DataTable<T extends object>({
  state,
  columns,
  rowKey,
  errorMessage,
  pagination,
}: {
  state: AsyncState<T[]>;
  columns: ColumnsType<T>;
  rowKey: (row: T) => string;
  errorMessage: string;
  /** Defaults to a 20-row client page. Pass a real config for server-side
   *  pagination, or `false` to turn it off. */
  pagination?: TablePaginationConfig | false;
}) {
  if (state.status === 'loading') return <Spin size="large" />;
  if (state.status === 'error') {
    // Sub-panel inside a working page (one of three admin tabs) → inline Alert,
    // not the full-page error the /me failure above warrants (#910).
    return <Alert type="error" showIcon title={errorMessage} description={state.error} />;
  }
  return (
    <Table
      scroll={{ x: 'max-content' }}
      dataSource={state.data}
      columns={columns}
      rowKey={rowKey}
      size="small"
      pagination={pagination ?? { pageSize: 20, hideOnSinglePage: true }}
    />
  );
}

/** Project an `ok` AsyncState's data, passing `loading`/`error` through unchanged. */
function mapAsync<T, U>(state: AsyncState<T>, fn: (data: T) => U): AsyncState<U> {
  return state.status === 'ok' ? { ...state, data: fn(state.data) } : state;
}

/** Name over email, falling back to the email alone when no display name. */
function Identity({ name, email }: { name: string | null; email: string }) {
  if (!name) return <Typography.Text>{email}</Typography.Text>;
  return (
    <Flex vertical>
      <Typography.Text>{name}</Typography.Text>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {email}
      </Typography.Text>
    </Flex>
  );
}

const PERMISSION_COLORS: Record<string, string> = {
  owner: 'gold',
  admin: 'volcano',
  edit: 'blue',
  view: 'default',
};

const SUITE_COLUMNS: ColumnsType<AdminSuite> = [
  { title: 'Suite', dataIndex: 'name' },
  {
    title: 'Owner',
    key: 'owner',
    render: (_, s) => <Identity name={s.owner_name} email={s.owner_email} />,
  },
  {
    title: 'Datasource',
    key: 'datasource',
    render: (_, s) => (
      <Flex align="center" gap={6}>
        <Typography.Text>{s.connection_name}</Typography.Text>
        <Tag>{s.connection_type}</Tag>
      </Flex>
    ),
  },
  { title: 'Env', dataIndex: 'env', render: (env: string) => <Tag>{env}</Tag> },
  { title: 'Checks', dataIndex: 'check_count', align: 'right' },
  { title: 'Shared with', dataIndex: 'share_count', align: 'right' },
  { title: 'Created', dataIndex: 'created_at', render: (v: string) => formatTimestamp(v) },
];

/** Replace any row the admin has just re-roled with the server's own response. */
function overlayUsers(
  state: AsyncState<AdminUser[]>,
  rerolled: Record<string, AdminUser>,
): AsyncState<AdminUser[]> {
  if (state.status !== 'ok' || Object.keys(rerolled).length === 0) return state;
  return { ...state, data: state.data.map((u) => rerolled[u.id] ?? u) };
}

/** A factory, not a constant, because the role cell needs the update callback.
 *  Everything else stays declarative. */
const userColumns = (onChanged: (u: AdminUser) => void): ColumnsType<AdminUser> => [
  {
    title: 'Member',
    key: 'user',
    render: (_, u) => <Identity name={u.display_name} email={u.email} />,
  },
  {
    title: 'Role',
    key: 'role',
    render: (_, u) => <RoleEditor user={u} onChanged={onChanged} />,
  },
  { title: 'Suites owned', dataIndex: 'owned_suite_count', align: 'right' },
  { title: 'Shared with them', dataIndex: 'shared_suite_count', align: 'right' },
  {
    title: 'Last seen',
    dataIndex: 'last_seen_at',
    render: (v: string | null) => formatTimestamp(v),
  },
];

const ACCESS_COLUMNS: ColumnsType<AdminAccess> = [
  { title: 'Suite', dataIndex: 'suite_name' },
  {
    title: 'User',
    key: 'user',
    render: (_, a) => <Identity name={a.user_name} email={a.user_email} />,
  },
  {
    title: 'Permission',
    dataIndex: 'permission',
    render: (p: string) => <Tag color={PERMISSION_COLORS[p] ?? 'default'}>{p}</Tag>,
  },
];
