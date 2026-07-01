import { Alert, Button, Card, Flex, Spin, Typography } from 'antd';
import { useNavigate, useParams } from 'react-router-dom';

import { listConnections } from '../api/connections';
import { getSuite } from '../api/suites';
import { Page } from '../components/layout/Page';
import { SuiteForm } from '../components/suites/SuiteForm';
import { useAsyncData } from '../hooks/useAsyncData';

/**
 * Dedicated full-page edit-suite flow (ADR 0022 — replaces the edit drawer).
 * Name + description + run target are editable; the connection is fixed (shown
 * read-only by `SuiteForm`). Reuses `SuiteForm` with the new-suite page. The
 * fetch + form live in a view keyed on the suite id so a param-only route change
 * reloads cleanly.
 */
export function SuiteEdit() {
  // Key the view by the id so a param-only navigation between two edit URLs
  // remounts → refetches + reseeds, rather than keeping the previous suite.
  const { suiteId } = useParams<{ suiteId: string }>();
  return <SuiteEditView key={suiteId} suiteId={suiteId} />;
}

function SuiteEditView({ suiteId }: { suiteId?: string }) {
  const navigate = useNavigate();
  const back = () => navigate(suiteId ? `/suites/${suiteId}` : '/suites');
  const { state } = useAsyncData(async () => {
    if (!suiteId) throw new Error('no suite');
    const [suite, connections] = await Promise.all([getSuite(suiteId), listConnections()]);
    return { suite, connections };
  });

  return (
    <Page width={'form'}>
      <Flex justify="space-between" align="center" gap={12}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          {state.status === 'ok' ? `Edit “${state.data.suite.name}”` : 'Edit suite'}
        </Typography.Title>
        <Button onClick={back}>Cancel</Button>
      </Flex>

      {state.status === 'loading' && <Spin description="Loading suite…" />}
      {state.status === 'error' && (
        <Alert type="error" showIcon title="Failed to load suite" description={state.error} />
      )}
      {state.status === 'ok' && (
        <Card size="small">
          <SuiteForm
            suite={state.data.suite}
            connections={state.data.connections}
            onCancel={back}
            onSaved={back}
          />
        </Card>
      )}
    </Page>
  );
}
