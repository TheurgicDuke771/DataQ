import { Alert, Card, Descriptions, Spin, Typography } from 'antd';

import { useMe } from '../auth/useMe';

export function Home() {
  // Reuse the shared /me fetch (MeProvider) rather than fetching again — it's
  // already resolved by the time the Profile page renders.
  const state = useMe();

  if (state.status === 'loading') {
    return <Spin tip="Loading /api/v1/me…" size="large" style={{ marginTop: 80 }} />;
  }
  if (state.status === 'error') {
    return (
      <Alert
        type="error"
        showIcon
        title="Failed to load /api/v1/me"
        description={state.error}
        style={{ margin: 24 }}
      />
    );
  }
  return (
    <Card title="Authenticated as">
      <Descriptions column={1} bordered size="small">
        <Descriptions.Item label="Display name">{state.data.display_name ?? '—'}</Descriptions.Item>
        <Descriptions.Item label="Email">{state.data.email}</Descriptions.Item>
        <Descriptions.Item label="AAD object id">
          <Typography.Text code copyable>
            {state.data.aad_object_id}
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="Last seen">{state.data.last_seen_at ?? '—'}</Descriptions.Item>
      </Descriptions>
    </Card>
  );
}
