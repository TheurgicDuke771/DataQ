import { Button, Flex, Layout, Menu, Spin, Tag, Typography } from 'antd';
import { lazy, Suspense } from 'react';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';

import { AuthGate } from './auth/AuthGate';
import { authMode } from './auth/config';
import { useCurrentUser } from './auth/useCurrentUser';
import { getMsalInstance } from './auth/msalInstance';

// Route components are code-split so the initial bundle doesn't ship every page
// (and antd-heavy pages only load on navigation). Named exports → map to default.
const Connections = lazy(() =>
  import('./pages/Connections').then((m) => ({ default: m.Connections })),
);
const ConnectionNew = lazy(() =>
  import('./pages/ConnectionNew').then((m) => ({ default: m.ConnectionNew })),
);
const Suites = lazy(() => import('./pages/Suites').then((m) => ({ default: m.Suites })));
const CheckNew = lazy(() => import('./pages/CheckNew').then((m) => ({ default: m.CheckNew })));
const Home = lazy(() => import('./pages/Home').then((m) => ({ default: m.Home })));

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
            <Suspense fallback={<Spin size="large" style={{ marginTop: 80 }} />}>
              <Routes>
                <Route path="/" element={<Navigate to="/connections" replace />} />
                <Route path="/connections" element={<Connections />} />
                <Route path="/connections/new" element={<ConnectionNew />} />
                <Route path="/suites" element={<Suites />} />
                <Route path="/suites/:suiteId" element={<Suites />} />
                <Route path="/suites/:suiteId/checks/new" element={<CheckNew />} />
                <Route path="/profile" element={<Home />} />
                <Route path="*" element={<Navigate to="/connections" replace />} />
              </Routes>
            </Suspense>
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
