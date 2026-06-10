import { Button, Flex, Layout, Menu, Tag, Typography } from 'antd';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';

import { AuthGate } from './auth/AuthGate';
import { authMode } from './auth/config';
import { useCurrentUser } from './auth/useCurrentUser';
import { getMsalInstance } from './auth/msalInstance';
import { Connections } from './pages/Connections';
import { Home } from './pages/Home';
import { Suites } from './pages/Suites';

const { Header, Sider, Content } = Layout;

const NAV_ITEMS = [
  { key: '/connections', label: <Link to="/connections">Connections</Link> },
  { key: '/suites', label: <Link to="/suites">Suites</Link> },
  { key: '/profile', label: <Link to="/profile">Profile</Link> },
];

export function App() {
  const location = useLocation();
  // Highlight the nav item whose path prefixes the current location.
  const selectedKeys = NAV_ITEMS.map((i) => i.key).filter((k) => location.pathname.startsWith(k));

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <Typography.Title level={4} style={{ color: '#fff', margin: 0, flex: 1 }}>
          DataQ
        </Typography.Title>
        <UserChip />
      </Header>
      <Layout>
        <Sider width={200} theme="light" breakpoint="lg" collapsedWidth={0}>
          <Menu
            mode="inline"
            selectedKeys={selectedKeys}
            items={NAV_ITEMS}
            style={{ height: '100%', borderInlineEnd: 0 }}
          />
        </Sider>
        <Content style={{ padding: 24 }}>
          <AuthGate>
            <Routes>
              <Route path="/" element={<Navigate to="/connections" replace />} />
              <Route path="/connections" element={<Connections />} />
              <Route path="/suites" element={<Suites />} />
              <Route path="/profile" element={<Home />} />
              <Route path="*" element={<Navigate to="/connections" replace />} />
            </Routes>
          </AuthGate>
        </Content>
      </Layout>
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
