import { Alert, Card, Descriptions, Flex, Table, Tag, Typography } from 'antd';
import { Link } from 'react-router-dom';

import {
  getWorkspaceConfig,
  type RetentionSetting,
  type SignInMode,
  type SignupAllowlist,
  type WorkspaceConfig,
} from '../../api/admin';
import { AsyncBody } from '../../components/AsyncBody';
import { useAsyncData } from '../../hooks/useAsyncData';
import { docsUrl } from '../../utils/docs';

const ROLE_LABELS = { member: 'Member', viewer: 'Viewer' } as const;

const SIGN_IN_MODE_LABELS: Record<SignInMode, string> = {
  azure_ad: 'Microsoft Entra ID (Azure AD)',
  oidc: 'Identity provider (OIDC)',
  otp: 'Email code',
  dev_bypass: 'Developer bypass',
  none: 'Not configured',
};

/**
 * The deployment-set values an admin can read here but only change by editing the
 * deployment's configuration and restarting: who may sign in, and how long data is kept.
 */
export function WorkspaceConfigPanels() {
  const { state, reload } = useAsyncData(getWorkspaceConfig);
  return (
    <>
      <Card title="Sign-in access" size="small">
        <AsyncBody
          state={state}
          loadingText="Loading the sign-in configuration…"
          errorTitle="Could not load the sign-in configuration"
          onRetry={reload}
        >
          {(config) => <SignInAccess config={config} />}
        </AsyncBody>
      </Card>
      <Card title="Data retention" size="small">
        <AsyncBody
          state={state}
          loadingText="Loading the retention configuration…"
          errorTitle="Could not load the retention configuration"
          onRetry={reload}
        >
          {(config) => <Retention retention={config.retention} />}
        </AsyncBody>
      </Card>
    </>
  );
}

/** Plain-words status, so the row never relies on a colour to carry its meaning. */
function allowlistStatus(
  row: SignupAllowlist,
  config: WorkspaceConfig,
): { text: string; color?: string } {
  if (row.grants === 'sign_in_and_admin_role') {
    return { text: 'Admits and grants the Admin role', color: 'gold' };
  }
  if (!row.active) return { text: 'Not read — not the sign-in mode in use' };
  if (config.idp_decides_signup && row.values.length === 0) {
    return { text: 'Read at sign-in — empty, so the identity provider decides', color: 'orange' };
  }
  return { text: 'Read at sign-in', color: 'green' };
}

function SignInAccess({ config }: { config: WorkspaceConfig }) {
  return (
    <Flex vertical gap={12}>
      <Typography.Text type="secondary">
        Read-only. These lists come from the deployment&apos;s configuration and change only when it
        is edited and the app restarted. Day-to-day admission is managed in{' '}
        <Link to="/admin/members" className="dq-text-link">
          Members
        </Link>
        ; an address an active list names signs in whether or not the member list has it, and can
        only be removed from the configuration.
      </Typography.Text>
      <Descriptions column={1} size="small" aria-label="Sign-in mode">
        <Descriptions.Item label="Sign-in mode">
          {SIGN_IN_MODE_LABELS[config.sign_in_mode]}
        </Descriptions.Item>
      </Descriptions>
      {config.dev_bypass_active && (
        <Alert
          type="info"
          showIcon
          title="Developer bypass is the active sign-in"
          description="Nobody is gated by an allowlist on this stack: every request runs as the bypass identity. Production deployments sign in through an identity provider or email codes."
        />
      )}
      {config.idp_decides_signup && (
        <Alert
          type="warning"
          showIcon
          title="The identity provider decides who may sign in"
          description={
            config.sign_in_mode === 'azure_ad'
              ? 'No app-side allowlist is read in this mode: every account in the tenant that can obtain a token is admitted and provisioned on first sign-in. Membership is governed in the tenant.'
              : 'OIDC_ALLOWED_EMAILS and OIDC_ALLOWED_DOMAINS are both empty, so every identity the issuer vouches for is admitted and provisioned on first sign-in. Set one of them to make this app, not the issuer, the gate.'
          }
        />
      )}
      <Table<SignupAllowlist>
        aria-label="Sign-in allowlists"
        size="small"
        pagination={false}
        rowKey="variable"
        dataSource={config.signup_allowlists}
        columns={[
          {
            title: 'Setting',
            dataIndex: 'variable',
            render: (variable: string) => <Typography.Text code>{variable}</Typography.Text>,
          },
          {
            title: 'Values',
            dataIndex: 'values',
            render: (values: string[], row) =>
              values.length === 0 ? (
                <Typography.Text type="secondary">
                  {row.kind === 'domains' ? 'No domains' : 'No addresses'}
                </Typography.Text>
              ) : (
                <Flex gap={4} wrap>
                  {values.map((v) => (
                    <Tag key={v} style={{ marginInlineEnd: 0 }}>
                      {v}
                    </Tag>
                  ))}
                </Flex>
              ),
          },
          {
            title: 'Status',
            key: 'status',
            render: (_, row) => {
              const { text, color } = allowlistStatus(row, config);
              return <Tag color={color}>{text}</Tag>;
            },
          },
        ]}
      />
      <Descriptions column={1} size="small" aria-label="Role on first sign-in">
        <Descriptions.Item label="Role on first sign-in (identity provider)">
          {ROLE_LABELS[config.oidc_default_role]}
        </Descriptions.Item>
        <Descriptions.Item label="Role on first sign-in (email code)">
          {ROLE_LABELS[config.otp_default_role]}
        </Descriptions.Item>
      </Descriptions>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        The first-sign-in role applies once, when the account is created; the role editor under
        Members is authoritative afterwards. Every variable is documented in the{' '}
        <a
          href={docsUrl('guides/admin/#seed-and-emergency-access')}
          target="_blank"
          rel="noreferrer"
          className="dq-text-link"
        >
          admin guide
        </a>
        .
      </Typography.Text>
    </Flex>
  );
}

function Retention({ retention }: { retention: RetentionSetting[] }) {
  return (
    <Flex vertical gap={12}>
      <Typography.Text type="secondary">
        Read-only. Each clock is set in the deployment&apos;s configuration and enforced by a daily
        sweep; shortening one takes effect at the next sweep, lengthening one cannot bring back what
        an earlier sweep removed.
      </Typography.Text>
      <Descriptions column={1} size="small" bordered aria-label="Retention clocks">
        {retention.map((row) => (
          <Descriptions.Item
            key={row.variable}
            label={
              <Flex vertical gap={2}>
                <Typography.Text strong>
                  {row.value} {row.unit}
                </Typography.Text>
                <Typography.Text code style={{ fontSize: 12 }}>
                  {row.variable}
                </Typography.Text>
              </Flex>
            }
          >
            {row.detail}
          </Descriptions.Item>
        ))}
      </Descriptions>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Run and result rows are never deleted by retention — only the failing-row samples inside
        them, so metric trends survive. Zero-sample mode (above) stops samples being stored at all.
      </Typography.Text>
    </Flex>
  );
}
