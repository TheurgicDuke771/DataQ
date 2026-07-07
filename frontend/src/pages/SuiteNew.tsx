import { Alert, Button, Card, Flex, Spin, Typography } from 'antd';
import { useNavigate } from 'react-router-dom';

import { CONNECTION_KIND, type Connection, listConnections } from '../api/connections';
import { Page } from '../components/layout/Page';
import { SuiteForm } from '../components/suites/SuiteForm';
import { type AsyncState, useAsyncData } from '../hooks/useAsyncData';

/**
 * Dedicated full-page new-suite flow (ADR 0022 — replaces the create drawer):
 * name + description, datasource connection, and the run target. On create,
 * continues to the Add Check page so the suite is never left empty.
 */
export function SuiteNew() {
  const navigate = useNavigate();
  const { state } = useAsyncData(listConnections);

  return (
    <Page width={'form'}>
      <Flex justify="space-between" align="center" gap={12} wrap>
        <Flex vertical gap={2}>
          <Typography.Title level={3} style={{ margin: 0 }}>
            New suite
          </Typography.Title>
          <Typography.Text type="secondary">
            Define a validation suite, then add its checks.
          </Typography.Text>
        </Flex>
        <Button onClick={() => navigate('/suites')}>Cancel</Button>
      </Flex>

      <Card size="small">
        <SuiteBody state={state} navigate={navigate} />
      </Card>
    </Page>
  );
}

function SuiteBody({
  state,
  navigate,
}: {
  state: AsyncState<Connection[]>;
  navigate: ReturnType<typeof useNavigate>;
}) {
  if (state.status === 'loading') {
    return <Spin description="Loading connections…" />;
  }
  if (state.status === 'error') {
    return (
      <Alert type="error" showIcon title="Failed to load connections" description={state.error} />
    );
  }
  const connections = state.data;
  const hasDatasource = connections.some((c) => CONNECTION_KIND[c.type] === 'datasource');
  if (!hasDatasource) {
    return (
      <Alert
        type="info"
        showIcon
        title="No datasource connections yet"
        description="A suite runs against a datasource. Add a Snowflake / flat-file / Unity Catalog connection first."
      />
    );
  }
  return (
    <SuiteForm
      connections={connections}
      onCancel={() => navigate('/suites')}
      // Continue to Add Check so a freshly-created suite is never left empty.
      onSaved={(suite) => navigate(`/suites/${suite.id}/checks/new`)}
    />
  );
}
