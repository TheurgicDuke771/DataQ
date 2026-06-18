import { DownOutlined, LogoutOutlined } from '@ant-design/icons';
import { Avatar, Dropdown, Flex, Layout, Menu, Spin, Tag, Typography } from 'antd';
import type { MenuProps } from 'antd';
import { lazy, Suspense } from 'react';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';

import { AuthGate } from './auth/AuthGate';
import { authMode } from './auth/config';
import { useCurrentUser } from './auth/useCurrentUser';
import { useIsWorkspaceAdmin } from './auth/useMe';
import { getMsalInstance } from './auth/msalInstance';
import { BRAND, SHELL } from './theme';

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
const Results = lazy(() => import('./pages/Results').then((m) => ({ default: m.Results })));
const Home = lazy(() => import('./pages/Home').then((m) => ({ default: m.Home })));
const Admin = lazy(() => import('./pages/Admin').then((m) => ({ default: m.Admin })));

const { Header, Sider, Content } = Layout;

const NAV_ITEMS = [
  { key: '/connections', label: <Link to="/connections">Connections</Link> },
  { key: '/suites', label: <Link to="/suites">Suites</Link> },
  { key: '/results', label: <Link to="/results">Results</Link> },
  { key: '/profile', label: <Link to="/profile">Profile</Link> },
];
// Shown only to workspace admins (server-driven via /me). The route is always
// registered — a non-admin who deep-links to /admin hits the page's Forbidden
// state — so this gate is for nav convenience, not the security boundary.
const ADMIN_NAV_ITEM = { key: '/admin', label: <Link to="/admin">Admin</Link> };

export function App() {
  const location = useLocation();
  const isAdmin = useIsWorkspaceAdmin();
  const navItems = isAdmin ? [...NAV_ITEMS, ADMIN_NAV_ITEM] : NAV_ITEMS;
  // Highlight the nav item whose path matches the current location — exact, or a
  // sub-path at a segment boundary (so `/suites` matches `/suites/123` but not a
  // sibling like `/suites-archive`). Plain startsWith would mis-highlight those.
  const selectedKeys = navItems
    .map((i) => i.key)
    .filter((k) => location.pathname === k || location.pathname.startsWith(`${k}/`));

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          borderBottom: `1px solid ${BRAND.border}`,
        }}
      >
        <Link to="/" aria-label="DataQ home" style={{ flex: 1 }}>
          <Flex align="center" gap={10}>
            <BrandMark />
            <Typography.Text strong style={{ fontSize: 17, color: BRAND.ink }}>
              DataQ
            </Typography.Text>
          </Flex>
        </Link>
        <UserMenu />
      </Header>
      <Layout>
        <Sider
          width={SHELL.siderWidth}
          theme="light"
          breakpoint="lg"
          collapsedWidth={0}
          style={{ borderInlineEnd: `1px solid ${BRAND.border}` }}
        >
          <Menu
            mode="inline"
            selectedKeys={selectedKeys}
            items={navItems}
            style={{ height: '100%', borderInlineEnd: 0, paddingTop: 8 }}
          />
        </Sider>
        <Content style={{ padding: 24, position: 'relative' }}>
          <BrandWatermark />
          <div style={{ position: 'relative' }}>
            <AuthGate>
              <Suspense fallback={<Spin size="large" style={{ marginTop: 80 }} />}>
                <Routes>
                  <Route path="/" element={<Navigate to="/connections" replace />} />
                  <Route path="/connections" element={<Connections />} />
                  <Route path="/connections/new" element={<ConnectionNew />} />
                  <Route path="/suites" element={<Suites />} />
                  <Route path="/suites/:suiteId" element={<Suites />} />
                  <Route path="/suites/:suiteId/checks/new" element={<CheckNew />} />
                  <Route path="/results" element={<Results />} />
                  <Route path="/profile" element={<Home />} />
                  <Route path="/admin" element={<Admin />} />
                  <Route path="*" element={<Navigate to="/connections" replace />} />
                </Routes>
              </Suspense>
            </AuthGate>
          </div>
        </Content>
      </Layout>
    </Layout>
  );
}

/**
 * A yin-yang app glyph (two-tone indigo) so the header reads as a product, not a
 * bare title — the balance motif nods at DataQ's pass/fail, expected/observed
 * duality. Self-contained colours (dark + light indigo) keep it legible on the
 * white header.
 */
function BrandMark({ size = 30 }: { size?: number }) {
  const dark = BRAND.primary;
  const light = BRAND.primarySoft; // indigo-200
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" role="img" aria-label="DataQ logo">
      <circle cx="50" cy="50" r="49" fill={light} stroke={BRAND.border} strokeWidth="1" />
      {/* The dark half: right lobe + the two interlocking teardrops. */}
      <path
        d="M50 1 a49 49 0 0 1 0 98 a24.5 24.5 0 0 1 0 -49 a24.5 24.5 0 0 0 0 -49 Z"
        fill={dark}
      />
      <circle cx="50" cy="25.5" r="9" fill={light} />
      <circle cx="50" cy="74.5" r="9" fill={dark} />
    </svg>
  );
}

/** Up-to-two-letter initials for the avatar (e.g. "Dev Bypass User" → "DB"). */
function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
}

/**
 * A very subtle brand watermark behind every page: the yin-yang mark bled off
 * the content area's bottom-right corner at low opacity. Decorative only
 * (`aria-hidden`, no pointer events). The mark is clipped by *this* layer
 * (`inset:0; overflow:hidden`), not by the Content itself — so real page
 * content can still overflow/scroll normally; only the watermark is clipped.
 */
function BrandWatermark() {
  return (
    <div
      aria-hidden
      style={{
        position: 'absolute',
        inset: 0,
        overflow: 'hidden',
        pointerEvents: 'none',
      }}
    >
      <div style={{ position: 'absolute', right: -70, bottom: -70, opacity: 0.05, lineHeight: 0 }}>
        <BrandMark size={460} />
      </div>
    </div>
  );
}

/**
 * Header identity + account menu: an avatar/name button that opens a dropdown
 * with the signed-in identity and a Sign out action. Under dev-bypass there is
 * no real session, so Sign out is shown disabled (the affordance is visible, but
 * honest about there being nothing to end) rather than hidden entirely.
 */
function UserMenu() {
  const user = useCurrentUser();
  if (!user) return null;

  const onLogout = () => {
    if (authMode !== 'real') return;
    const instance = getMsalInstance();
    void instance?.logoutRedirect({ account: instance.getAllAccounts()[0] });
  };

  const items: MenuProps['items'] = [
    {
      type: 'group',
      label: (
        <Flex vertical gap={2} style={{ padding: '4px 4px 8px' }}>
          <Typography.Text strong style={{ color: BRAND.ink }}>
            {user.name}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {user.username}
          </Typography.Text>
          {user.isDev && (
            <Tag color="orange" style={{ marginTop: 4, width: 'fit-content' }}>
              DEV BYPASS
            </Tag>
          )}
        </Flex>
      ),
    },
    { type: 'divider' },
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: user.isDev ? 'Sign out (dev bypass)' : 'Sign out',
      danger: !user.isDev,
      disabled: user.isDev,
      onClick: onLogout,
    },
  ];

  return (
    <Dropdown menu={{ items }} trigger={['click']} placement="bottomRight">
      <Flex align="center" gap={8} style={{ cursor: 'pointer' }}>
        <Avatar size="small" style={{ backgroundColor: BRAND.primary, flexShrink: 0 }}>
          {initialsOf(user.name)}
        </Avatar>
        <Typography.Text style={{ color: BRAND.ink }}>{user.name}</Typography.Text>
        <DownOutlined style={{ fontSize: 10, color: '#8c8c8c' }} />
      </Flex>
    </Dropdown>
  );
}
