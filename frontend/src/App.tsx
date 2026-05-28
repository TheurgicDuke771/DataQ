import { Button, Flex, Layout, Tag, Typography } from 'antd';

import { AuthGate } from './auth/AuthGate';
import { authMode } from './auth/config';
import { useCurrentUser } from './auth/useCurrentUser';
import { getMsalInstance } from './auth/msalInstance';

const { Header, Content } = Layout;

export function App() {
  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <Typography.Title level={4} style={{ color: '#fff', margin: 0, flex: 1 }}>
          DataQ
        </Typography.Title>
        <UserChip />
      </Header>
      <Content style={{ padding: 24 }}>
        <AuthGate>
          <Home />
        </AuthGate>
      </Content>
    </Layout>
  );
}

function UserChip() {
  const user = useCurrentUser();
  if (!user) return null;

  const onLogout = () => {
    if (authMode !== 'real') return;
    const instance = getMsalInstance();
    void instance?.logoutRedirect({ account: instance.getAllAccounts()[0] });
  };

  return (
    <Flex align="center" gap={12}>
      {user.isDev && <Tag color="orange">DEV BYPASS</Tag>}
      <Typography.Text style={{ color: '#fff' }}>{user.name}</Typography.Text>
      {!user.isDev && (
        <Button size="small" onClick={onLogout}>
          Sign out
        </Button>
      )}
    </Flex>
  );
}

function Home() {
  return (
    <Typography.Paragraph>
      Welcome to DataQ. Backend <code>/api/v1/me</code> wiring lands in PR 3c.3.
    </Typography.Paragraph>
  );
}
