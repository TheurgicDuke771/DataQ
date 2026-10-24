import { App, Avatar, Card, Descriptions, Flex, Spin, Tag, Typography } from 'antd';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import type { WorkspaceRole } from '../api/admin';
import type { MeResponse } from '../api/me';
import { listSuites, type Suite } from '../api/suites';
import { authMethodLabel } from '../auth/config';
import { useMe } from '../auth/useMe';
import { useSaveDisplayName } from '../auth/useSaveDisplayName';
import { AsyncBody } from '../components/AsyncBody';
import { ApiKeysPanel } from '../components/profile/ApiKeysPanel';
import { Page } from '../components/layout/Page';
import { PageError } from '../components/feedback/PageError';
import { SimpleList } from '../components/SimpleList';
import { useAsyncData } from '../hooks/useAsyncData';
import { errorMessage } from '../utils/errors';

const ROLE_LABELS: Record<WorkspaceRole, string> = {
  admin: 'Workspace admin',
  member: 'Member',
  viewer: 'Viewer',
};

/** What each workspace role lets its holder do, in the user's own terms (ADR 0033). */
const ROLE_SUMMARIES: Record<WorkspaceRole, string> = {
  admin:
    'You can manage connections, members and workspace settings, and act on every suite in the workspace.',
  member:
    'You can create suites and author checks on connections an admin has set up, and run or edit the suites shared with you at that level.',
  viewer:
    'Read-only: you can open the suites shared with you and read their results, but not change or run anything.',
};

/** The caller's effective role — the server resolves a break-glass allowlist admin as `admin`. */
function effectiveRole(me: MeResponse): WorkspaceRole {
  return me.is_workspace_admin ? 'admin' : me.role;
}

/** Profile (`/profile`, ADR 0022 ProfileScreen): identity, access summary, owned suites, API keys. */
export function Profile() {
  const me = useMe();
  const saveDisplayName = useSaveDisplayName();
  const { message } = App.useApp();
  const [savingName, setSavingName] = useState(false);
  const suites = useAsyncData((signal) => listSuites(undefined, signal));

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

  const { display_name, email, last_seen_at } = me.data;
  const role = effectiveRole(me.data);
  const name = display_name ?? email;
  const initial = (name || '?').trim().charAt(0).toUpperCase();

  // Self-service override (#1139) — editable in EVERY mode, not just `otp`.
  const onNameChange = async (value: string) => {
    const trimmed = value.trim();
    if (!trimmed || trimmed === name) return;
    setSavingName(true);
    try {
      await saveDisplayName(trimmed);
      message.success('Name updated.');
    } catch (err) {
      message.error(`Could not update your name: ${errorMessage(err)}`);
    } finally {
      setSavingName(false);
    }
  };

  return (
    <Page width="form" gap={16}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        Profile
      </Typography.Title>

      <Card>
        <Flex gap={16} align="center">
          <Avatar size={56} style={{ backgroundColor: 'var(--dq-primary)' }} aria-hidden>
            {initial}
          </Avatar>
          <Flex vertical gap={2}>
            <Typography.Text
              strong
              style={{ fontSize: 18 }}
              editable={{
                onChange: (value) => void onNameChange(value),
                tooltip: 'Edit your display name',
                maxLength: 256,
                triggerType: ['icon', 'text'],
              }}
              aria-busy={savingName}
            >
              {name}
            </Typography.Text>
            <Typography.Text type="secondary">{email}</Typography.Text>
            <span>
              <Tag color={role === 'admin' ? 'gold' : 'default'}>{ROLE_LABELS[role]}</Tag>
            </span>
          </Flex>
        </Flex>
      </Card>

      <Card title={<CardHeading>Workspace</CardHeading>} size="small">
        <Descriptions column={1} size="small">
          <Descriptions.Item label="Authentication">{authMethodLabel}</Descriptions.Item>
          <Descriptions.Item label="Role">{ROLE_LABELS[role]}</Descriptions.Item>
          <Descriptions.Item label="Last seen">{last_seen_at ?? '—'}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title={<CardHeading>Your access</CardHeading>} size="small">
        <Flex vertical gap={12}>
          <Typography.Text>{ROLE_SUMMARIES[role]}</Typography.Text>
          <AsyncBody
            state={suites.state}
            loadingText="Counting your suites…"
            errorTitle="Could not load your suite access"
            onRetry={suites.reload}
          >
            {(all) => <AccessSummary suites={all} role={role} />}
          </AsyncBody>
        </Flex>
      </Card>

      <Card title={<CardHeading>Suites you own</CardHeading>} size="small">
        <AsyncBody
          state={suites.state}
          loadingText="Loading your suites…"
          errorTitle="Could not load your suites"
          onRetry={suites.reload}
        >
          {(all) => <OwnedSuites suites={all.filter((s) => s.my_permission === 'owner')} />}
        </AsyncBody>
      </Card>

      <ApiKeysPanel />
    </Page>
  );
}

/** A real heading inside antd's Card title slot, so the page outline reads Profile → sections. */
function CardHeading({ children }: { children: string }) {
  return (
    <Typography.Title level={4} style={{ margin: 0, fontSize: 'inherit', fontWeight: 600 }}>
      {children}
    </Typography.Title>
  );
}

const ACCESS_LEVELS = [
  ['owner', 'Owned by you'],
  ['admin', 'Shared with you as admin'],
  ['edit', 'Shared with you to edit'],
  ['view', 'Shared with you to view'],
] as const;

function AccessSummary({ suites, role }: { suites: Suite[]; role: WorkspaceRole }) {
  const counts = new Map<string, number>();
  for (const s of suites) {
    if (s.my_permission) counts.set(s.my_permission, (counts.get(s.my_permission) ?? 0) + 1);
  }
  return (
    <Flex vertical gap={8}>
      <Descriptions column={1} size="small" aria-label="Suites by access level">
        {ACCESS_LEVELS.map(([level, label]) => (
          <Descriptions.Item key={level} label={label}>
            {counts.get(level) ?? 0}
          </Descriptions.Item>
        ))}
      </Descriptions>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {role === 'admin'
          ? 'As a workspace admin you also see every suite nobody shared with you; those are not counted here.'
          : 'Suites nobody has shared with you are not visible to you and are not counted here.'}{' '}
        Each suite&apos;s sharing is managed by its owner from the suite itself.
      </Typography.Text>
    </Flex>
  );
}

function OwnedSuites({ suites }: { suites: Suite[] }) {
  if (suites.length === 0) {
    return (
      <Typography.Text type="secondary">
        You don&apos;t own any suites. A suite you create from <Link to="/suites">Suites</Link> is
        yours; one transferred to you by a workspace admin is too.
      </Typography.Text>
    );
  }
  const sorted = [...suites].sort((a, b) => a.name.localeCompare(b.name));
  return (
    <SimpleList
      dataSource={sorted}
      rowKey="id"
      size="small"
      aria-label="Suites you own"
      renderItem={(suite) => (
        <SimpleList.Item style={{ paddingBlock: 8 }}>
          <Flex vertical gap={2}>
            <Link to={`/suites/${suite.id}`} className="dq-text-link">
              {suite.name}
            </Link>
            {suite.description && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }} ellipsis>
                {suite.description}
              </Typography.Text>
            )}
          </Flex>
        </SimpleList.Item>
      )}
    />
  );
}
