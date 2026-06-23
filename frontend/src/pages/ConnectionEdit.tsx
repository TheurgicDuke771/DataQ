import { Alert, Button, Card, Flex, Spin, Typography } from 'antd';
import { useNavigate, useParams } from 'react-router-dom';

import { CONNECTION_TYPE_LABELS, getConnection } from '../api/connections';
import { ConnectionForm } from '../components/connections/ConnectionForm';
import { useAsyncData } from '../hooks/useAsyncData';

/**
 * Dedicated full-page edit-connection flow (ADR 0022 — replaces the edit drawer).
 * Type + env are immutable and shown read-only; the secret is omitted (rotation is
 * the separate Re-auth flow). Reuses `ConnectionForm` with the create page.
 */
export function ConnectionEdit() {
  const navigate = useNavigate();
  const { connectionId } = useParams<{ connectionId: string }>();
  const { state } = useAsyncData(() => {
    if (!connectionId) throw new Error('no connection');
    return getConnection(connectionId);
  });

  return (
    <Flex vertical gap={24} style={{ maxWidth: 640 }}>
      <Flex justify="space-between" align="center" gap={12}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          {state.status === 'ok'
            ? `Edit ${CONNECTION_TYPE_LABELS[state.data.type]} connection`
            : 'Edit connection'}
        </Typography.Title>
        <Button onClick={() => navigate('/connections')}>Cancel</Button>
      </Flex>

      {state.status === 'loading' && <Spin tip="Loading connection…" />}
      {state.status === 'error' && (
        <Alert type="error" showIcon title="Failed to load connection" description={state.error} />
      )}
      {state.status === 'ok' && (
        <Card size="small">
          <ConnectionForm
            type={state.data.type}
            connection={state.data}
            onCancel={() => navigate('/connections')}
            onSaved={() => navigate('/connections')}
          />
        </Card>
      )}
    </Flex>
  );
}
