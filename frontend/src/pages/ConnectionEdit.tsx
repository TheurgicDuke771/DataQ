import { HistoryOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Flex, Spin, Typography } from 'antd';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { CONNECTION_TYPE_LABELS, getConnection } from '../api/connections';
import { Page } from '../components/layout/Page';
import { ConnectionForm } from '../components/connections/ConnectionForm';
import { ConnectionHistoryDrawer } from '../components/connections/ConnectionHistoryDrawer';
import { useAsyncData } from '../hooks/useAsyncData';

/**
 * Dedicated full-page edit-connection flow (ADR 0022 — replaces the edit drawer).
 * Type + env are immutable and shown read-only; the secret is omitted (rotation is
 * the separate Re-auth flow). Reuses `ConnectionForm` with the create page. The
 * fetch + form live in a view keyed on the connection id so a param-only route
 * change reloads cleanly.
 */
export function ConnectionEdit() {
  // Key the view by the id so a param-only navigation between two edit URLs
  // (no unmount under react-router) remounts → refetches + reseeds, rather than
  // leaving the previous connection's data in the form.
  const { connectionId } = useParams<{ connectionId: string }>();
  return <ConnectionEditView key={connectionId} connectionId={connectionId} />;
}

function ConnectionEditView({ connectionId }: { connectionId?: string }) {
  const navigate = useNavigate();
  const [historyOpen, setHistoryOpen] = useState(false);
  const { state } = useAsyncData(() => {
    if (!connectionId) throw new Error('no connection');
    return getConnection(connectionId);
  });

  return (
    <Page width={'form'}>
      <Flex justify="space-between" align="center" gap={12} wrap>
        <Typography.Title level={3} style={{ margin: 0 }}>
          {state.status === 'ok'
            ? `Edit ${CONNECTION_TYPE_LABELS[state.data.type]} connection`
            : 'Edit connection'}
        </Typography.Title>
        <Flex gap={8}>
          {state.status === 'ok' && (
            <Button icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>
              History
            </Button>
          )}
          <Button onClick={() => navigate('/connections')}>Cancel</Button>
        </Flex>
      </Flex>

      {state.status === 'loading' && <Spin description="Loading connection…" />}
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

      <ConnectionHistoryDrawer
        open={historyOpen}
        connection={state.status === 'ok' ? state.data : null}
        onClose={() => setHistoryOpen(false)}
      />
    </Page>
  );
}
