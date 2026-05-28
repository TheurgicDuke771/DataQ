import { Alert, Card, Descriptions, Spin, Typography } from 'antd';
import { useEffect, useState } from 'react';

import { fetchMe, type MeResponse } from '../api/me';

type LoadState =
  | { status: 'loading' }
  | { status: 'ok'; me: MeResponse }
  | { status: 'error'; error: string };

export function Home() {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    fetchMe()
      .then((me) => {
        if (!cancelled) setState({ status: 'ok', me });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : String(err);
          setState({ status: 'error', error: msg });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
        <Descriptions.Item label="Display name">{state.me.display_name ?? '—'}</Descriptions.Item>
        <Descriptions.Item label="Email">{state.me.email}</Descriptions.Item>
        <Descriptions.Item label="AAD object id">
          <Typography.Text code copyable>
            {state.me.aad_object_id}
          </Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="Last seen">{state.me.last_seen_at ?? '—'}</Descriptions.Item>
      </Descriptions>
    </Card>
  );
}
