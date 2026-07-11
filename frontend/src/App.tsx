import {
  ApiOutlined,
  BarChartOutlined,
  ContainerOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  DownOutlined,
  LogoutOutlined,
  MenuOutlined,
  ReadOutlined,
  SafetyOutlined,
  SettingOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { Avatar, Button, Dropdown, Flex, Layout, Menu, Spin, Tag, Typography } from 'antd';
import type { MenuProps } from 'antd';
import { lazy, Suspense, useState } from 'react';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';

import { AuthGate } from './auth/AuthGate';
import { authMode } from './auth/config';
import { useCurrentUser } from './auth/useCurrentUser';
import { useIsWorkspaceAdmin } from './auth/useMe';
import { logout } from './auth/authClient';
import { BrandMark } from './components/BrandMark';
import { BRAND, SHELL } from './theme';

// Route components are code-split so the initial bundle doesn't ship every page
// (and antd-heavy pages only load on navigation). Named exports → map to default.
const Dashboard = lazy(() => import('./pages/Dashboard').then((m) => ({ default: m.Dashboard })));
const Connections = lazy(() =>
  import('./pages/Connections').then((m) => ({ default: m.Connections })),
);
const ConnectionNew = lazy(() =>
  import('./pages/ConnectionNew').then((m) => ({ default: m.ConnectionNew })),
);
const ConnectionEdit = lazy(() =>
  import('./pages/ConnectionEdit').then((m) => ({ default: m.ConnectionEdit })),
);
const Suites = lazy(() => import('./pages/Suites').then((m) => ({ default: m.Suites })));
const SuiteNew = lazy(() => import('./pages/SuiteNew').then((m) => ({ default: m.SuiteNew })));
const SuiteEdit = lazy(() => import('./pages/SuiteEdit').then((m) => ({ default: m.SuiteEdit })));
const CheckNew = lazy(() => import('./pages/CheckNew').then((m) => ({ default: m.CheckNew })));
const CheckEdit = lazy(() => import('./pages/CheckEdit').then((m) => ({ default: m.CheckEdit })));
const Assets = lazy(() => import('./pages/Assets').then((m) => ({ default: m.Assets })));
const AssetDetail = lazy(() =>
  import('./pages/AssetDetail').then((m) => ({ default: m.AssetDetail })),
);
const Results = lazy(() => import('./pages/Results').then((m) => ({ default: m.Results })));
const RunDetail = lazy(() => import('./pages/RunDetail').then((m) => ({ default: m.RunDetail })));
const Profile = lazy(() => import('./pages/Profile').then((m) => ({ default: m.Profile })));
const Admin = lazy(() => import('./pages/Admin').then((m) => ({ default: m.Admin })));
const Settings = lazy(() => import('./pages/Settings').then((m) => ({ default: m.Settings })));
const NotFound = lazy(() => import('./pages/NotFound').then((m) => ({ default: m.NotFound })));

const { Header, Sider, Content } = Layout;

// Primary nav (top of the sider).
const NAV_ITEMS = [
  { key: '/dashboard', icon: <DashboardOutlined />, label: <Link to="/dashboard">Dashboard</Link> },
  { key: '/connections', icon: <ApiOutlined />, label: <Link to="/connections">Connections</Link> },
  { key: '/suites', icon: <ContainerOutlined />, label: <Link to="/suites">Suites</Link> },
  { key: '/assets', icon: <DatabaseOutlined />, label: <Link to="/assets">Assets</Link> },
  { key: '/results', icon: <BarChartOutlined />, label: <Link to="/results">Results</Link> },
  { key: '/profile', icon: <UserOutlined />, label: <Link to="/profile">Profile</Link> },
];
// Footer nav (pinned to the bottom). Admin + Settings show only to workspace
// admins (server-driven via /me) — the routes stay registered either way, so a
// non-admin who deep-links hits the page's Forbidden state; this gate is for nav
// convenience, not the security boundary.
const ADMIN_FOOTER_ITEMS = [
  { key: '/admin', icon: <SafetyOutlined />, label: <Link to="/admin">Admin</Link> },
  { key: '/settings', icon: <SettingOutlined />, label: <Link to="/settings">Settings</Link> },
];
// Published docs site (MkDocs Material → GitHub Pages). External link, opens in
// a new tab; never a "selected" nav key since it leaves the app.
const DOCS_URL = 'https://theurgicduke771.github.io/DataQ/';
const DOC_ITEM = {
  key: 'documentation',
  icon: <ReadOutlined />,
  label: (
    <a href={DOCS_URL} target="_blank" rel="noreferrer">
      Documentation
    </a>
  ),
};
// Keys that can be "selected" (the disabled Documentation placeholder can't).
const SELECTABLE_KEYS = [...NAV_ITEMS, ...ADMIN_FOOTER_ITEMS].map((i) => i.key);

export function App() {
  const location = useLocation();
  const isAdmin = useIsWorkspaceAdmin();
  // Narrow-viewport nav (#617): below the `lg` breakpoint the Sider collapses to
  // zero width. AntD's built-in zero-width trigger floats over every page's
  // heading, so it's disabled (`trigger={null}`) and replaced by a ☰ toggle in
  // the Header, which has reserved space.
  const [navCollapsed, setNavCollapsed] = useState(false);
  const [narrow, setNarrow] = useState(false);
  const footerItems = isAdmin ? [...ADMIN_FOOTER_ITEMS, DOC_ITEM] : [DOC_ITEM];
  // Highlight the nav item whose path matches the current location — exact, or a
  // sub-path at a segment boundary (so `/suites` matches `/suites/123` but not a
  // sibling like `/suites-archive`). Plain startsWith would mis-highlight those.
  const selectedKeys = SELECTABLE_KEYS.filter(
    (k) => location.pathname === k || location.pathname.startsWith(`${k}/`),
  );

  // Auth gates the whole shell: an unauthenticated user gets the full-screen
  // LoginPage with no header/sider chrome; the Layout only renders once signed in.
  return (
    <AuthGate>
      {/* Fixed app shell: the Layout is exactly the viewport height and doesn't
          scroll — the header and sider stay put, and only <Content> scrolls. */}
      <Layout style={{ height: '100vh', overflow: 'hidden' }}>
        <Header
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 16,
            borderBottom: `1px solid ${BRAND.border}`,
          }}
        >
          {narrow && (
            <Button
              type="text"
              icon={<MenuOutlined />}
              aria-label="Toggle navigation"
              onClick={() => setNavCollapsed((c) => !c)}
              style={{ marginInlineStart: -8 }}
            />
          )}
          <Link to="/" aria-label="DataQ home" style={{ flex: 1 }}>
            <Flex align="center" gap={10}>
              <BrandMark />
              {/* nowrap: squeezed between the hamburger and the user chip on a
                  narrow header, the brand otherwise wraps vertically (#692). */}
              <Typography.Text
                strong
                style={{ fontSize: 17, color: BRAND.ink, whiteSpace: 'nowrap' }}
              >
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
            trigger={null}
            collapsed={navCollapsed}
            onBreakpoint={(broken) => {
              setNarrow(broken);
              setNavCollapsed(broken);
            }}
            style={{ borderInlineEnd: `1px solid ${BRAND.border}`, height: '100%' }}
          >
            {/* Primary nav up top, footer group (Admin · Settings · Documentation)
              pinned to the bottom by the flex layout, separated by a hairline. The
              primary nav takes the slack and scrolls if it ever exceeds the height,
              so the footer stays put. */}
            <Flex vertical style={{ height: '100%' }}>
              <Menu
                mode="inline"
                selectedKeys={selectedKeys}
                items={NAV_ITEMS}
                onClick={() => narrow && setNavCollapsed(true)}
                style={{
                  borderInlineEnd: 0,
                  paddingTop: 8,
                  flex: 1,
                  minHeight: 0,
                  overflowY: 'auto',
                }}
              />
              <Menu
                mode="inline"
                selectedKeys={selectedKeys}
                items={footerItems}
                onClick={() => narrow && setNavCollapsed(true)}
                style={{
                  borderInlineEnd: 0,
                  borderTop: `1px solid ${BRAND.border}`,
                  paddingBlock: 8,
                }}
              />
            </Flex>
          </Sider>
          {/* The only scroll container: header + sider stay fixed, this scrolls. */}
          <Content style={{ padding: 24, position: 'relative', overflowY: 'auto' }}>
            <BrandWatermark />
            <div style={{ position: 'relative' }}>
              <Suspense fallback={<Spin size="large" style={{ marginTop: 80 }} />}>
                <Routes>
                  <Route path="/" element={<Navigate to="/dashboard" replace />} />
                  <Route path="/dashboard" element={<Dashboard />} />
                  <Route path="/connections" element={<Connections />} />
                  <Route path="/connections/new" element={<ConnectionNew />} />
                  <Route path="/connections/:connectionId/edit" element={<ConnectionEdit />} />
                  <Route path="/suites" element={<Suites />} />
                  <Route path="/suites/new" element={<SuiteNew />} />
                  <Route path="/suites/:suiteId" element={<Suites />} />
                  <Route path="/suites/:suiteId/edit" element={<SuiteEdit />} />
                  <Route path="/suites/:suiteId/checks/new" element={<CheckNew />} />
                  <Route path="/suites/:suiteId/checks/:checkId/edit" element={<CheckEdit />} />
                  <Route path="/assets" element={<Assets />} />
                  <Route path="/assets/:assetId" element={<AssetDetail />} />
                  <Route path="/results" element={<Results />} />
                  <Route path="/results/:runId" element={<RunDetail />} />
                  <Route path="/profile" element={<Profile />} />
                  <Route path="/admin" element={<Admin />} />
                  <Route path="/settings" element={<Settings />} />
                  {/* Unknown route → in-brand 404 (not a silent redirect). */}
                  <Route path="*" element={<NotFound />} />
                </Routes>
              </Suspense>
            </div>
          </Content>
        </Layout>
      </Layout>
    </AuthGate>
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
    void logout();
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
